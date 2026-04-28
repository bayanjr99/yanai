"""
Billing pipeline — internal BI system.

Orchestrates all core modules in-memory and maintains:
  data/master_full.parquet  — internal analytics dataset
  data/master_full.xlsx     — Power BI export (clean English schema)

Data layout scanned:
  data/MM-YYYY/            — primary (hours.pdf / hours.xlsx + costs.xlsx)
  data/months/MM-YYYY/     — secondary (hours.xlsx / billing.xlsx + costs.xlsx)
"""

from __future__ import annotations

import os
import re as _re
from dataclasses import dataclass, field

import pandas as pd

from core.pdf_parser     import parse_pdf
from core.excel_loaders  import load_agreements, load_costs, load_overrides, _find_col
from core.validation     import validate_pdf, results_to_dicts, ValidationError, validate_billing_results
from core.rules_engine   import apply_rules
from core.matcher        import resolve_client, find_agreement, is_internal
from core.billing_engine import calculate


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_ROOT    = os.getenv("DATA_ROOT", "data")
MASTER_PATH  = os.path.join(DATA_ROOT, "master_full.parquet")
MASTER_XLSX  = os.path.join(DATA_ROOT, "master_full.xlsx")

_AGREEMENTS_CANDIDATES = [
    os.path.join(DATA_ROOT, "agreements.xlsx"),
    os.path.join(DATA_ROOT, "agreements", "agreements.xlsx"),
]
_OVERRIDES_CANDIDATES = [
    os.path.join(DATA_ROOT, "overrides.xlsx"),
]

_MONTH_FOLDER_RE = _re.compile(r"^\d{2}-\d{4}$")

# Power BI export column mapping: internal → export name
_EXPORT_COLS = {
    "month":          "month",
    "client":         "client",
    "site":           "site",
    "employee_id":    "employee_id",
    "employee_name":  "employee_name",
    "days":           "days",
    "total_hours":    "hours",
    "billing_amount": "billing",
    "cost":           "cost",
    "profit":         "profit",
    "margin_pct":     "margin",
    "completion_added": "completion",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    detail_df:  pd.DataFrame
    daily_df:   pd.DataFrame
    issues_df:  pd.DataFrame
    validation: list[dict] = field(default_factory=list)
    month_str:  str = ""


# ---------------------------------------------------------------------------
# Month directory helpers
# ---------------------------------------------------------------------------

def _first_existing(*paths: str) -> str | None:
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _find_month_dir(month: str, data_root: str = DATA_ROOT) -> str | None:
    """
    Find the folder that contains source data for a given month.
    Checks data/months/MM-YYYY/ first, then data/MM-YYYY/.
    """
    candidates = [
        os.path.join(data_root, "months", month),
        os.path.join(data_root, month),
    ]
    for c in candidates:
        if not os.path.isdir(c):
            continue
        # Has source data (hours or billing)?
        if any(os.path.exists(os.path.join(c, f))
               for f in ("hours.pdf", "hours.xlsx", "billing.xlsx")):
            return c
    return None


def list_available_months(data_root: str = DATA_ROOT) -> list[str]:
    """
    Return sorted list of MM-YYYY months that have source data in
    data/MM-YYYY/ or data/months/MM-YYYY/.
    """
    found: set[str] = set()

    def _scan(root: str) -> None:
        if not os.path.isdir(root):
            return
        for name in os.listdir(root):
            if not _MONTH_FOLDER_RE.match(name):
                continue
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            if any(os.path.exists(os.path.join(path, f))
                   for f in ("hours.pdf", "hours.xlsx", "billing.xlsx")):
                found.add(name)

    _scan(os.path.join(data_root, "months"))
    _scan(data_root)
    return sorted(found)


def month_file_mtime(month: str, data_root: str = DATA_ROOT) -> float:
    """Return the latest mtime of all source files for a month (cache key)."""
    month_dir = _find_month_dir(month, data_root) or ""
    candidates = [
        os.path.join(month_dir, f)
        for f in ("hours.pdf", "hours.xlsx", "billing.xlsx", "costs.xlsx")
    ] + list(_AGREEMENTS_CANDIDATES) + list(_OVERRIDES_CANDIDATES)
    mtimes = [os.path.getmtime(p) for p in candidates if os.path.exists(p)]
    return max(mtimes) if mtimes else 0.0


# ---------------------------------------------------------------------------
# Internal billing logic (PDF / Excel → daily rows → monthly aggregation)
# ---------------------------------------------------------------------------

def _bill_daily(
    daily_df: pd.DataFrame,
    agreements: list[dict],
    costs: dict,
    overrides: dict | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    if overrides is None:
        overrides = {}
    detail_rows: list[dict] = []
    issue_rows:  list[dict] = []
    seen_issues: set[tuple] = set()

    for _, row in daily_df.iterrows():
        emp_id      = str(row["employee_id"])
        emp_name    = str(row["employee_name"])
        site        = str(row["site"])
        hours       = float(row["hours_to_pay"])
        break_hours = float(row.get("break_hours") or 0)
        work_date   = row["date"]

        client, _, worker_country = resolve_client(emp_id, site, costs)

        if not client:
            key = (emp_id, site, "עובד חסר בעלויות")
            if key not in seen_issues:
                seen_issues.add(key)
                issue_rows.append({
                    "employee_id":   emp_id,
                    "employee_name": emp_name,
                    "site":          site,
                    "issue_type":    "עובד חסר בעלויות",
                    "description":   f"עובד {emp_id} ({emp_name}) לא נמצא בקובץ עלות מעביד.",
                    "suggested_fix": "הוסף עובד לקובץ costs.xlsx",
                })
            client         = site
            worker_country = ""

        if is_internal(client):
            continue

        agreement, match_reason = find_agreement(client, site, agreements, country=worker_country)
        override_rate = overrides.get((emp_id, site)) or overrides.get((emp_id, ""))
        result = apply_rules(hours, break_hours, agreement, override_rate)

        if result.blocked:
            key = (emp_id, site, result.block_reason)
            if key not in seen_issues:
                seen_issues.add(key)
                issue_rows.append({
                    "employee_id":   emp_id,
                    "employee_name": emp_name,
                    "site":          site,
                    "issue_type":    result.block_reason,
                    "description":   f"עובד {emp_id} אתר '{site}': {result.block_reason}.",
                    "suggested_fix": "הוסף הסכם בקובץ agreements.xlsx",
                })

        monthly_min = float(agreement.get("monthly_min") or 0) if agreement else 0.0
        daily_min   = float(agreement.get("daily_min")   or 0) if agreement else 0.0

        detail_rows.append({
            "employee_id":        emp_id,
            "employee_name":      emp_name,
            "date":               work_date,
            "site":               site,
            "country":            worker_country,
            "client":             client,
            "match_reason":       match_reason if agreement else "אין הסכם",
            "billing_type":       result.billing_type,
            "rate":               result.rate,
            "ot_rate":            result.ot_rate,
            "ot_threshold":       float(agreement.get("ot_threshold") or 10) if agreement else 0.0,
            "daily_min":          daily_min,
            "monthly_min":        monthly_min,
            "include_breaks":     bool(agreement.get("include_breaks") if agreement else False),
            "agreement_used":     result.agreement_used,
            "hours_to_pay":       result.hours_to_pay,
            "break_hours":        result.break_hours,
            "billable_hours_day": result.billable_hours,
            "ot_hours_day":       result.ot_hours,
            "completion_day":     result.completion_day,
            "billing_day":        result.billing_amount,
            "blocked":            result.blocked,
            "block_reason":       result.block_reason,
        })

    return pd.DataFrame(detail_rows), issue_rows


def _aggregate(
    detail_df: pd.DataFrame,
    costs: dict,
    issue_rows: list[dict],
) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame()

    grp_keys = ["employee_id", "employee_name", "site"]
    agg = detail_df.groupby(grp_keys, as_index=False).agg(
        client            = ("client",             "first"),
        match_reason      = ("match_reason",       "first"),
        billing_type      = ("billing_type",       "first"),
        rate              = ("rate",               "first"),
        ot_rate           = ("ot_rate",            "first"),
        ot_threshold      = ("ot_threshold",       "first"),
        monthly_min       = ("monthly_min",        "first"),
        daily_min         = ("daily_min",          "first"),
        days              = ("date",               "count"),
        total_hours       = ("hours_to_pay",       "sum"),
        total_break_hours = ("break_hours",        "sum"),
        ot_hours          = ("ot_hours_day",       "sum"),
        completion_day    = ("completion_day",     "sum"),
        billable_sub      = ("billable_hours_day", "sum"),
        billing_sub       = ("billing_day",        "sum"),
    )

    agg = agg.sort_values(["employee_id", "monthly_min"], ascending=[True, False]).reset_index(drop=True)

    emp_billable_totals: dict[str, float] = agg.groupby("employee_id")["billable_sub"].sum().to_dict()
    seen_monthly_completion: set[str] = set()
    final_rows: list[dict] = []

    for _, row in agg.iterrows():
        billing_type = str(row["billing_type"])
        rate         = float(row["rate"])
        monthly_min  = float(row["monthly_min"] or 0)
        daily_min    = float(row["daily_min"]   or 0)
        days         = int(row["days"])
        billable     = float(row["billable_sub"])
        billing_amt  = float(row["billing_sub"])
        completion_monthly = 0.0
        emp_id_str   = str(row["employee_id"])

        if (billing_type == "hourly"
                and monthly_min > 0
                and daily_min == 0
                and emp_id_str not in seen_monthly_completion):
            emp_total = emp_billable_totals.get(emp_id_str, billable)
            if emp_total < monthly_min:
                completion_monthly = monthly_min - emp_total
                seen_monthly_completion.add(emp_id_str)
                billable    += completion_monthly
                billing_amt  = round(billable * rate, 2)

        site = str(row["site"])
        _, emp_cost, _ = resolve_client(emp_id_str, site, costs)

        completion_added = round(float(row["completion_day"]) + completion_monthly, 2)
        profit           = round(billing_amt - emp_cost, 2)
        margin_pct       = round(profit / billing_amt * 100, 1) if billing_amt > 0 else 0.0

        if billing_amt == 0 and float(row["total_hours"]) > 0:
            issue_rows.append({
                "employee_id":   emp_id_str,
                "employee_name": str(row["employee_name"]),
                "site":          site,
                "issue_type":    "חיוב אפס עם שעות",
                "description":   f"חיוב 0 ₪ למרות {row['total_hours']:.1f} שעות.",
            })

        final_rows.append({
            "employee_id":      emp_id_str,
            "employee_name":    str(row["employee_name"]),
            "client":           str(row["client"]),
            "site":             site,
            "match_reason":     str(row["match_reason"]),
            "billing_type":     billing_type,
            "rate":             rate,
            "ot_rate":          float(row.get("ot_rate") or 0),
            "ot_threshold":     float(row.get("ot_threshold") or 0),
            "monthly_min":      monthly_min,
            "daily_min":        daily_min,
            "days":             days,
            "total_hours":      round(float(row["total_hours"]), 2),
            "break_hours":      round(float(row.get("total_break_hours") or 0), 2),
            "ot_hours":         round(float(row.get("ot_hours") or 0), 2),
            "billable_hours":   round(billable, 2),
            "completion_added": completion_added,
            "billing_amount":   billing_amt,
            "cost":             emp_cost,
            "profit":           profit,
            "margin_pct":       margin_pct,
        })

    result_df = pd.DataFrame(final_rows)
    issue_rows.extend(validate_billing_results(result_df))
    return result_df


def _load_hours_excel(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    COL_MAP: dict[str, list[str]] = {
        "employee_id":   ["מספר עובד", "מס עובד", "employee_id", "id", "מס' עובד"],
        "employee_name": ["שם עובד", "עובד", "employee_name", "שם"],
        "site":          ["שם פרויקט", "פרויקט", "אתר", "site", "locality"],
        "days":          ["ימי עבודה", "ימים", "days"],
        "total_hours":   ['סה"כ שעות', "total_hours", "שעות", "שעות עבודה"],
    }
    result = {}
    for field_name, candidates in COL_MAP.items():
        col = _find_col(df, candidates)
        if col:
            if field_name in ("employee_id", "employee_name", "site"):
                result[field_name] = df[col].astype(str).str.strip()
            else:
                result[field_name] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""), errors="coerce"
                ).fillna(0.0)
        else:
            result[field_name] = "" if field_name in ("employee_id", "employee_name", "site") else 0.0
    return pd.DataFrame(result)


def _bill_monthly(
    monthly_df: pd.DataFrame,
    agreements: list[dict],
    costs: dict,
) -> tuple[pd.DataFrame, list[dict]]:
    detail_rows: list[dict] = []
    issue_rows:  list[dict] = []

    for _, row in monthly_df.iterrows():
        emp_id   = str(row.get("employee_id", "")).strip()
        emp_name = str(row.get("employee_name", "")).strip()
        site     = str(row.get("site", "")).strip()

        client, emp_cost, worker_country = resolve_client(emp_id, site, costs)

        if not client:
            issue_rows.append({
                "employee_id":   emp_id,
                "employee_name": emp_name,
                "site":          site,
                "issue_type":    "עובד לא נמצא בקובץ עלויות",
                "description":   f"עובד {emp_id} לא קיים בקובץ עלות מעביד.",
            })
            client = site
            worker_country = ""

        if is_internal(client):
            continue

        agreement, match_reason = find_agreement(client, site, agreements, country=worker_country)

        if agreement is None:
            issue_rows.append({
                "employee_id":   emp_id,
                "employee_name": emp_name,
                "site":          site,
                "issue_type":    "הסכם חסר",
                "description":   f"לא נמצא הסכם ללקוח '{client}' אתר '{site}'.",
            })
            billing_amount = billable_hours = completion_added = 0.0
        else:
            res = calculate(row.to_dict(), agreement)
            billing_amount   = res.billing_amount
            billable_hours   = res.billable_hours
            completion_added = res.completion_added
            if billing_amount == 0 and float(row.get("total_hours") or 0) > 0:
                issue_rows.append({
                    "employee_id":   emp_id,
                    "employee_name": emp_name,
                    "site":          site,
                    "issue_type":    "חיוב אפס עם שעות",
                    "description":   f"חיוב 0 ₪ למרות {row.get('total_hours', 0):.1f}h.",
                })

        profit     = billing_amount - emp_cost
        margin_pct = round(profit / billing_amount * 100, 1) if billing_amount > 0 else 0.0

        detail_rows.append({
            "employee_id":      emp_id,
            "employee_name":    emp_name,
            "client":           client,
            "site":             site,
            "match_reason":     match_reason if agreement else "אין הסכם",
            "days":             float(row.get("days") or 0),
            "total_hours":      float(row.get("total_hours") or 0),
            "billable_hours":   billable_hours,
            "completion_added": completion_added,
            "billing_amount":   billing_amount,
            "cost":             emp_cost,
            "profit":           profit,
            "margin_pct":       margin_pct,
        })

    return pd.DataFrame(detail_rows), issue_rows


# ---------------------------------------------------------------------------
# billing.xlsx loader — pre-computed billing data
# ---------------------------------------------------------------------------

def _load_billing_xlsx(path: str) -> pd.DataFrame:
    """
    Load a pre-computed billing.xlsx file directly.
    Accepts flexible Hebrew/English column names.
    Returns a DataFrame with the same schema as detail_df.
    """
    df = pd.read_excel(path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    COL_MAP: dict[str, list[str]] = {
        "employee_id":    ["מספר עובד", "מס עובד", "employee_id", "id", "מס' עובד"],
        "employee_name":  ["שם עובד", "עובד", "employee_name"],
        "client":         ["לקוח", "client", "שם לקוח", "customer"],
        "site":           ["אתר", "site", "פרויקט", "locality"],
        "days":           ["ימים", "ימי עבודה", "days"],
        "total_hours":    ["שעות", "שעות עבודה", "total_hours", "hours", 'סה"כ שעות'],
        "billing_amount": ["חיוב", "billing", "billing_amount", "חיוב ₪", "סכום לחיוב"],
        "cost":           ["עלות", "cost", "עלות מעביד", "עלות ₪"],
    }

    result: dict = {}
    for field_name, candidates in COL_MAP.items():
        col = _find_col(df, candidates)
        if col:
            if field_name in ("employee_id", "employee_name", "client", "site"):
                result[field_name] = df[col].astype(str).str.strip()
            else:
                result[field_name] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""), errors="coerce"
                ).fillna(0.0)
        else:
            result[field_name] = "" if field_name in ("employee_id", "employee_name", "client", "site") else 0.0

    out = pd.DataFrame(result)
    out = out[out["client"].astype(str).str.strip() != ""].copy()

    if out.empty:
        return out

    # Derive profit + margin
    out["profit"]     = out["billing_amount"] - out["cost"]
    out["margin_pct"] = (
        out["profit"] / out["billing_amount"].replace(0, float("nan")) * 100
    ).round(1).fillna(0.0)

    return out


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_full_pipeline(
    hours_path: str,
    agreements_path: str,
    costs_path: str,
    overrides_path: str | None = None,
) -> PipelineResult:
    """Run the complete billing pipeline in-memory for a single hours file."""
    agreements = load_agreements(agreements_path)
    costs      = load_costs(costs_path)
    overrides: dict = {}
    if overrides_path and os.path.exists(overrides_path):
        overrides = load_overrides(overrides_path)

    is_pdf = hours_path.lower().endswith(".pdf")
    validation_dicts: list[dict] = []
    issue_rows: list[dict] = []

    if is_pdf and os.path.exists(hours_path):
        val_results      = validate_pdf(hours_path)
        validation_dicts = results_to_dicts(val_results)
        daily_raw_df     = parse_pdf(hours_path)
        if daily_raw_df.empty:
            raise ValueError("לא נמצאו שורות יומיות ב-PDF.")
        detail_daily_df, issue_rows = _bill_daily(daily_raw_df, agreements, costs, overrides)
    elif not is_pdf and os.path.exists(hours_path):
        monthly_df      = _load_hours_excel(hours_path)
        detail_monthly, issue_rows = _bill_monthly(monthly_df, agreements, costs)
        return PipelineResult(
            detail_df  = detail_monthly,
            daily_df   = pd.DataFrame(),
            issues_df  = pd.DataFrame(issue_rows),
            validation = validation_dicts,
            month_str  = "",
        )
    else:
        raise FileNotFoundError(f"קובץ שעות לא נמצא: {hours_path}")

    detail_df = _aggregate(detail_daily_df, costs, issue_rows)

    month_str = ""
    if not detail_daily_df.empty and "date" in detail_daily_df.columns:
        first_date = pd.to_datetime(detail_daily_df["date"].min())
        month_str  = first_date.strftime("%Y-%m")

    if month_str and not detail_df.empty:
        detail_df["month"] = month_str

    issues_df = pd.DataFrame(issue_rows)

    if validation_dicts and not detail_df.empty:
        pdf_total = sum(float(v.get("שעות שנקראו") or 0) for v in validation_dicts)
        sys_total = float(detail_df["total_hours"].sum())
        if pdf_total > 0 and abs(sys_total - pdf_total) > 0.1:
            issue_rows.append({
                "employee_id":   "ALL",
                "employee_name": "מערכת",
                "site":          "",
                "issue_type":    "אי-התאמת שעות",
                "description":   (
                    f"שעות מערכת {sys_total:.2f}h ≠ שעות PDF {pdf_total:.2f}h "
                    f"(הפרש {abs(sys_total - pdf_total):.2f}h)"
                ),
            })
            issues_df = pd.DataFrame(issue_rows)

    return PipelineResult(
        detail_df  = detail_df,
        daily_df   = detail_daily_df,
        issues_df  = issues_df,
        validation = validation_dicts,
        month_str  = month_str,
    )


def run_month_pipeline(month: str, data_root: str = DATA_ROOT) -> PipelineResult:
    """
    Run billing for a specific MM-YYYY month.
    If billing.xlsx exists in the month folder, loads it directly.
    Otherwise runs the full PDF/Excel pipeline.
    """
    month_dir = _find_month_dir(month, data_root)
    if month_dir is None:
        raise FileNotFoundError(f"תיקיית חודש לא קיימת או ריקה: {month}")

    billing_path    = os.path.join(month_dir, "billing.xlsx")
    pdf_path        = os.path.join(month_dir, "hours.pdf")
    excel_path      = os.path.join(month_dir, "hours.xlsx")
    costs_path      = os.path.join(month_dir, "costs.xlsx")
    agreements_path = _first_existing(*_AGREEMENTS_CANDIDATES)
    overrides_path  = _first_existing(*_OVERRIDES_CANDIDATES)

    # Fast path: pre-computed billing.xlsx
    if os.path.exists(billing_path):
        df = _load_billing_xlsx(billing_path)
        if df.empty:
            raise ValueError(f"billing.xlsx ריק: {billing_path}")
        return PipelineResult(
            detail_df  = df,
            daily_df   = pd.DataFrame(),
            issues_df  = pd.DataFrame(),
            validation = [],
            month_str  = "",
        )

    # Full pipeline path
    if agreements_path is None:
        raise FileNotFoundError("לא נמצא קובץ הסכמים. הכנס agreements.xlsx לתיקיית data/.")
    if not os.path.exists(costs_path):
        raise FileNotFoundError(f"לא נמצא קובץ עלויות עבור {month}.")

    hours_path = pdf_path if os.path.exists(pdf_path) else excel_path
    return run_full_pipeline(
        hours_path      = hours_path,
        agreements_path = agreements_path,
        costs_path      = costs_path,
        overrides_path  = overrides_path,
    )


# ---------------------------------------------------------------------------
# Master dataset
# ---------------------------------------------------------------------------

def _ensure_financials(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure profit and margin_pct columns are present and correct."""
    if "profit" not in df.columns:
        if "billing_amount" in df.columns and "cost" in df.columns:
            df["profit"] = df["billing_amount"] - df["cost"]
        else:
            df["profit"] = 0.0
    if "margin_pct" not in df.columns:
        if "billing_amount" in df.columns:
            df["margin_pct"] = (
                df["profit"] / df["billing_amount"].replace(0, float("nan")) * 100
            ).round(1).fillna(0.0)
        else:
            df["margin_pct"] = 0.0
    return df


def _save_master_xlsx(master: pd.DataFrame) -> None:
    """Save master dataset as Excel with clean English column names for Power BI."""
    avail  = {k: v for k, v in _EXPORT_COLS.items() if k in master.columns}
    export = master[list(avail.keys())].rename(columns=avail).copy()

    # Ensure no nulls in critical fields
    for col in ("client", "site", "employee_id", "employee_name"):
        if col in export.columns:
            export[col] = export[col].fillna("").astype(str)
    for col in ("hours", "billing", "cost", "profit", "margin", "days", "completion"):
        if col in export.columns:
            export[col] = pd.to_numeric(export[col], errors="coerce").fillna(0.0).round(2)

    export.sort_values(["month", "client", "employee_name"], inplace=True, ignore_index=True)
    export.to_excel(MASTER_XLSX, index=False, sheet_name="master_full")


def build_master_full(data_root: str = DATA_ROOT) -> tuple[pd.DataFrame, list[str]]:
    """
    Scan all available month folders, compute billing for each, merge,
    and save to:
      data/master_full.parquet  — internal analytics
      data/master_full.xlsx     — Power BI export

    Returns (master_df, error_list).
    Missing-file errors are non-fatal and collected.
    """
    months = list_available_months(data_root)
    if not months:
        return pd.DataFrame(), []

    all_slices: list[pd.DataFrame] = []
    errors: list[str] = []

    for month in months:
        try:
            result = run_month_pipeline(month, data_root=data_root)
            df = result.detail_df.copy()
            if df.empty:
                errors.append(f"{month}: ריק לאחר חישוב")
                continue
            df["month"] = month
            df = _ensure_financials(df)
            all_slices.append(df)
        except FileNotFoundError as e:
            errors.append(f"{month}: {e}")
        except Exception as e:
            errors.append(f"{month}: {e}")

    if not all_slices:
        return pd.DataFrame(), errors

    master = pd.concat(all_slices, ignore_index=True)
    master = _ensure_financials(master)

    os.makedirs(os.path.dirname(os.path.abspath(MASTER_PATH)) or ".", exist_ok=True)
    master.to_parquet(MASTER_PATH, index=False)
    try:
        _save_master_xlsx(master)
    except Exception as e:
        errors.append(f"xlsx export error: {e}")

    return master, errors


def update_master(detail_df: pd.DataFrame, month: str) -> None:
    """Upsert a single month's rows into master_full.parquet and master_full.xlsx."""
    slim = detail_df.copy()
    slim["month"] = month
    slim = _ensure_financials(slim)

    if os.path.exists(MASTER_PATH):
        try:
            existing = pd.read_parquet(MASTER_PATH)
            existing = existing[existing["month"] != month]
            master   = pd.concat([existing, slim], ignore_index=True)
        except Exception:
            master = slim
    else:
        master = slim

    os.makedirs(os.path.dirname(os.path.abspath(MASTER_PATH)) or ".", exist_ok=True)
    master.to_parquet(MASTER_PATH, index=False)
    try:
        _save_master_xlsx(master)
    except Exception:
        pass


def get_all_data() -> pd.DataFrame:
    """Load master_full.parquet. Returns empty DataFrame if not found."""
    if os.path.exists(MASTER_PATH):
        try:
            return pd.read_parquet(MASTER_PATH)
        except Exception:
            pass
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def filter_by_month(df: pd.DataFrame, month: str) -> pd.DataFrame:
    if df.empty or "month" not in df.columns:
        return df
    return df[df["month"] == month]


def filter_by_client(df: pd.DataFrame, clients) -> pd.DataFrame:
    if df.empty or "client" not in df.columns:
        return df
    if isinstance(clients, str):
        clients = [clients]
    return df[df["client"].isin(clients)]


# ---------------------------------------------------------------------------
# Analytics helpers (operate on master_full data)
# ---------------------------------------------------------------------------

def get_profit_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly totals: month | billing_amount | profit | cost | total_hours."""
    if df.empty or "month" not in df.columns:
        return pd.DataFrame()
    agg_cols = {c: "sum" for c in ("billing_amount", "profit", "cost", "total_hours") if c in df.columns}
    if not agg_cols:
        return pd.DataFrame()
    return df.groupby("month", as_index=False).agg(agg_cols).sort_values("month")


def get_top_clients(df: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    if df.empty or "client" not in df.columns or "billing_amount" not in df.columns:
        return pd.DataFrame()
    return (
        df.groupby("client", as_index=False)
        .agg(billing_amount=("billing_amount", "sum"), profit=("profit", "sum"))
        .nlargest(n, "billing_amount")
        .reset_index(drop=True)
    )


def get_top_employees(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if df.empty or "employee_name" not in df.columns:
        return pd.DataFrame()
    agg_cols = {c: (c, "sum") for c in ("cost", "total_hours", "profit") if c in df.columns}
    if not agg_cols:
        return pd.DataFrame()
    return (
        df.groupby("employee_name", as_index=False)
        .agg(**agg_cols)
        .nlargest(n, "cost")
        .reset_index(drop=True)
    )
