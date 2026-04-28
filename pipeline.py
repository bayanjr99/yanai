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

DATA_ROOT     = os.getenv("DATA_ROOT", "data")
MASTER_PATH   = os.path.join(DATA_ROOT, "master_full.parquet")
MASTER_XLSX   = os.path.join(DATA_ROOT, "master_full.xlsx")
CALENDAR_XLSX = os.path.join(DATA_ROOT, "calendar.xlsx")

_AGREEMENTS_CANDIDATES = [
    os.path.join(DATA_ROOT, "agreements.xlsx"),
    os.path.join(DATA_ROOT, "agreements", "agreements.xlsx"),
]
_OVERRIDES_CANDIDATES = [
    os.path.join(DATA_ROOT, "overrides.xlsx"),
]

_MONTH_FOLDER_RE = _re.compile(r"^\d{2}-\d{4}$")

# Internal detail_df column → canonical master column name
_RENAME_MAP = {
    "total_hours":    "hours",
    "billing_amount": "billing",
    "margin_pct":     "margin",
}

# Canonical master schema — exact order for Power BI fact table
_MASTER_SCHEMA = [
    "row_id",
    "month", "date", "year",
    "client", "site",
    "employee_id", "employee_name",
    "days", "hours",
    "billing", "cost", "profit", "margin",
    "profit_per_hour", "cost_per_hour", "revenue_per_hour",
    "profit_per_employee", "profit_per_client",
    "completion_added",
]


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
# Master dataset — cleaning, export, summaries, validation
# ---------------------------------------------------------------------------

def _clean_master(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transform raw detail_df rows into the canonical master schema:
      1. Rename internal columns to clean English names
      2. Derive profit + margin (always recalculated)
      3. Add date (first day of month as datetime) and year
      4. Add per-hour metrics (safe division)
      5. Remove null/empty clients
      6. Fill numeric nulls with 0; string nulls with ""
      7. Drop duplicates on (month, employee_id, site)
      8. Reorder to _MASTER_SCHEMA
    """
    df = df.copy()

    # 1. Rename internal names → clean names
    df = df.rename(columns={k: v for k, v in _RENAME_MAP.items() if k in df.columns})

    # Ensure core columns exist
    for col in ("billing", "cost"):
        if col not in df.columns:
            df[col] = 0.0

    # 2. Always recalculate profit + margin from billing/cost
    df["profit"] = (
        pd.to_numeric(df["billing"], errors="coerce").fillna(0) -
        pd.to_numeric(df["cost"],    errors="coerce").fillna(0)
    ).round(2)
    _b = pd.to_numeric(df["billing"], errors="coerce").replace(0, float("nan"))
    df["margin"] = (df["profit"] / _b * 100).round(2).fillna(0.0)

    # 3. Date + year from month (MM-YYYY)
    if "month" in df.columns:
        df["date"] = pd.to_datetime(df["month"], format="%m-%Y", errors="coerce")
        df["year"] = df["date"].dt.year.astype("Int64")
    else:
        df["date"] = pd.NaT
        df["year"] = pd.NA

    # 4. Per-hour metrics (safe division)
    _h = pd.to_numeric(df.get("hours", 0), errors="coerce").replace(0, float("nan"))
    df["profit_per_hour"]  = (df["profit"]  / _h).round(2).fillna(0.0)
    df["cost_per_hour"]    = (df["cost"]    / _h).round(2).fillna(0.0)
    df["revenue_per_hour"] = (df["billing"] / _h).round(2).fillna(0.0)

    # 5. Remove null/empty clients
    if "client" in df.columns:
        df = df[df["client"].notna() & (df["client"].astype(str).str.strip() != "")]

    # 6. Fill numeric nulls with 0; string nulls with ""
    num_cols = ["days", "hours", "billing", "cost", "profit", "margin",
                "profit_per_hour", "cost_per_hour", "revenue_per_hour"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    for col in ("client", "site", "employee_id", "employee_name"):
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    # 7. Drop duplicates on natural key (keep last — most recently computed wins)
    key_cols = [c for c in ("month", "employee_id", "site") if c in df.columns]
    if key_cols:
        df = df.drop_duplicates(subset=key_cols, keep="last")

    df = df.reset_index(drop=True)

    # 8. Grouped profit metrics (transform = per-row context for Power BI slicing)
    if "employee_name" in df.columns and "profit" in df.columns:
        df["profit_per_employee"] = (
            df.groupby("employee_name")["profit"].transform("sum").round(2)
        )
    else:
        df["profit_per_employee"] = 0.0

    if "client" in df.columns and "profit" in df.columns:
        df["profit_per_client"] = (
            df.groupby("client")["profit"].transform("sum").round(2)
        )
    else:
        df["profit_per_client"] = 0.0

    # 9. Row ID — unique integer key for Power BI relationships
    df.insert(0, "row_id", range(len(df)))

    # 10. Reorder: canonical schema first, keep any extra columns at end
    ordered = [c for c in _MASTER_SCHEMA if c in df.columns]
    extra   = [c for c in df.columns if c not in _MASTER_SCHEMA]
    df = df[ordered + extra]

    return df


def _save_master_xlsx(master: pd.DataFrame) -> None:
    """Save master_full.xlsx — flat table with clean schema for Power BI."""
    # Only canonical columns in the export
    export_cols = [c for c in _MASTER_SCHEMA if c in master.columns]
    export = master[export_cols].copy()

    # Format date as string (Excel-friendly)
    if "date" in export.columns:
        export["date"] = pd.to_datetime(export["date"]).dt.strftime("%Y-%m-%d")

    sort_cols = [c for c in ("month", "client", "employee_name") if c in export.columns]
    if sort_cols:
        export.sort_values(sort_cols, inplace=True, ignore_index=True)

    export.to_excel(MASTER_XLSX, index=False, sheet_name="master_full")


def build_calendar(master: pd.DataFrame) -> pd.DataFrame:
    """
    Build a calendar dimension table spanning the full date range of master.
    Returns one row per day with standard BI time-intelligence columns.

    Columns: date, year, month, month_name, month_name_he, quarter,
             week_of_year, year_month, day_of_week, is_weekend
    """
    if "date" not in master.columns or master["date"].isna().all():
        return pd.DataFrame()

    min_dt = pd.to_datetime(master["date"].dropna().min())
    max_dt = pd.to_datetime(master["date"].dropna().max())

    # Extend to full calendar years so Power BI time-intelligence works
    min_dt = min_dt.replace(month=1, day=1)
    max_dt = max_dt.replace(month=12, day=31)

    dates = pd.date_range(start=min_dt, end=max_dt, freq="D")
    cal   = pd.DataFrame({"date": dates})

    cal["year"]         = cal["date"].dt.year
    cal["month"]        = cal["date"].dt.month
    cal["month_name"]   = cal["date"].dt.strftime("%B")          # English
    cal["month_name_he"] = cal["date"].dt.month.map({            # Hebrew
        1: "ינואר", 2: "פברואר", 3: "מרץ",   4: "אפריל",
        5: "מאי",   6: "יוני",   7: "יולי",  8: "אוגוסט",
        9: "ספטמבר",10: "אוקטובר",11: "נובמבר",12: "דצמבר",
    })
    cal["quarter"]      = cal["date"].dt.quarter
    cal["week_of_year"] = cal["date"].dt.isocalendar().week.astype(int)
    cal["year_month"]   = cal["date"].dt.strftime("%Y-%m")       # e.g. 2025-02
    cal["day_of_week"]  = cal["date"].dt.day_name()
    cal["is_weekend"]   = cal["date"].dt.dayofweek >= 5

    # Store date as string so Excel renders it correctly
    cal["date"] = cal["date"].dt.strftime("%Y-%m-%d")

    return cal


def _save_calendar(master: pd.DataFrame) -> None:
    """Write calendar.xlsx — date dimension table for Power BI."""
    cal = build_calendar(master)
    if not cal.empty:
        cal.to_excel(CALENDAR_XLSX, index=False, sheet_name="calendar")


def build_master_full(data_root: str = DATA_ROOT) -> tuple[pd.DataFrame, list[str]]:
    """
    Scan all month folders, compute billing for each, clean, merge, and save:
      data/master_full.parquet  — canonical internal dataset
      data/master_full.xlsx     — Power BI flat export

    Returns (master_df, error_list). Missing-file errors are non-fatal.
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
            all_slices.append(df)
        except FileNotFoundError as e:
            errors.append(f"{month}: {e}")
        except Exception as e:
            errors.append(f"{month}: {e}")

    if not all_slices:
        return pd.DataFrame(), errors

    master = _clean_master(pd.concat(all_slices, ignore_index=True))

    # Validate
    warnings = validate_master(master)
    errors.extend(warnings)

    _dir = os.path.dirname(os.path.abspath(MASTER_PATH)) or "."
    os.makedirs(_dir, exist_ok=True)
    master.to_parquet(MASTER_PATH, index=False)
    try:
        _save_master_xlsx(master)
    except Exception as e:
        errors.append(f"xlsx export error: {e}")

    # Save summary tables
    try:
        _save_summary_tables(master)
    except Exception as e:
        errors.append(f"summary tables error: {e}")

    # Save calendar dimension
    try:
        _save_calendar(master)
    except Exception as e:
        errors.append(f"calendar error: {e}")

    return master, errors


def update_master(detail_df: pd.DataFrame, month: str) -> None:
    """Upsert a single month into master_full.parquet + .xlsx + summaries."""
    slim = detail_df.copy()
    slim["month"] = month

    if os.path.exists(MASTER_PATH):
        try:
            existing = pd.read_parquet(MASTER_PATH)
            existing = existing[existing["month"] != month]
            combined = pd.concat([existing, slim], ignore_index=True)
        except Exception:
            combined = slim
    else:
        combined = slim

    master = _clean_master(combined)
    _dir = os.path.dirname(os.path.abspath(MASTER_PATH)) or "."
    os.makedirs(_dir, exist_ok=True)
    master.to_parquet(MASTER_PATH, index=False)
    try:
        _save_master_xlsx(master)
    except Exception:
        pass
    try:
        _save_summary_tables(master)
    except Exception:
        pass
    try:
        _save_calendar(master)
    except Exception:
        pass


def validate_master(master: pd.DataFrame) -> list[str]:
    """
    Validate master dataset for Power BI readiness.
    Returns list of warning strings (all non-fatal).
    """
    warnings: list[str] = []

    # 1. Required columns
    required = ["row_id", "month", "date", "year", "client", "billing", "cost", "profit"]
    missing  = [c for c in required if c not in master.columns]
    if missing:
        warnings.append(f"עמודות חסרות: {', '.join(missing)}")
        return warnings

    # 2. Duplicate row_id (breaks Power BI relationships)
    if master["row_id"].duplicated().any():
        warnings.append(
            f"{master['row_id'].duplicated().sum()} כפולות ב-row_id — "
            "Power BI relationships ייכשלו"
        )

    # 3. Null dates (breaks time intelligence)
    null_dates = master["date"].isna().sum()
    if null_dates:
        warnings.append(
            f"{null_dates} שורות ללא תאריך — Time Intelligence ב-Power BI תיפגע"
        )

    # 4. Null clients
    null_clients = master["client"].isna() | (master["client"].astype(str).str.strip() == "")
    if null_clients.any():
        warnings.append(f"{null_clients.sum()} שורות ללא לקוח הוסרו מ-Master")

    # 5. Non-numeric in numeric columns
    for col in ("billing", "cost", "profit", "hours"):
        if col in master.columns:
            bad = pd.to_numeric(master[col], errors="coerce").isna().sum()
            if bad:
                warnings.append(f"{bad} ערכים לא-מספריים בעמודה '{col}'")

    # 6. Cost > 2× billing (data integrity)
    suspicious = master[(master["billing"] > 0) & (master["cost"] > master["billing"] * 2)]
    if not suspicious.empty:
        warnings.append(
            f"{len(suspicious)} שורות עם עלות > 2× חיוב — בדוק נתונים"
        )

    # 7. All-zero billing
    if (master["billing"] == 0).all():
        warnings.append("כל שורות המאסטר עם חיוב 0 — בדוק הסכמים")

    return warnings


def build_summary_tables(master: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Build three summary tables from master data:
      monthly_summary  — by month
      client_summary   — by client
      employee_summary — by employee
    """
    if master.empty:
        return {}

    tables: dict[str, pd.DataFrame] = {}

    def _margin(profit_col: pd.Series, billing_col: pd.Series) -> pd.Series:
        return (profit_col / billing_col.replace(0, float("nan")) * 100).round(2).fillna(0.0)

    if "month" in master.columns:
        m = master.groupby("month", as_index=False).agg(
            total_revenue=("billing", "sum"),
            total_cost   =("cost",    "sum"),
            total_profit =("profit",  "sum"),
            total_hours  =("hours",   "sum"),
        ).sort_values("month")
        m["margin"] = _margin(m["total_profit"], m["total_revenue"])
        tables["monthly_summary"] = m

    if "client" in master.columns:
        c = master.groupby("client", as_index=False).agg(
            total_revenue=("billing", "sum"),
            total_cost   =("cost",    "sum"),
            total_profit =("profit",  "sum"),
            total_hours  =("hours",   "sum"),
        ).sort_values("total_revenue", ascending=False)
        c["margin"] = _margin(c["total_profit"], c["total_revenue"])
        tables["client_summary"] = c

    if "employee_name" in master.columns:
        e = master.groupby("employee_name", as_index=False).agg(
            total_cost   =("cost",    "sum"),
            total_hours  =("hours",   "sum"),
            total_billing=("billing", "sum"),
        ).sort_values("total_cost", ascending=False)
        tables["employee_summary"] = e

    return tables


def _save_summary_tables(master: pd.DataFrame) -> None:
    """Write monthly/client/employee summaries as sheets in data/summaries.xlsx."""
    tables = build_summary_tables(master)
    if not tables:
        return
    summaries_path = os.path.join(DATA_ROOT, "summaries.xlsx")
    with pd.ExcelWriter(summaries_path, engine="openpyxl") as writer:
        for sheet, df in tables.items():
            df.to_excel(writer, sheet_name=sheet, index=False)


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
# Analytics helpers — operate on clean master_full data
# (column names: billing, cost, profit, hours, margin)
# ---------------------------------------------------------------------------

def get_profit_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly totals: month | billing | profit | cost | hours."""
    if df.empty or "month" not in df.columns:
        return pd.DataFrame()
    agg_cols = {c: "sum" for c in ("billing", "profit", "cost", "hours") if c in df.columns}
    if not agg_cols:
        return pd.DataFrame()
    trend = df.groupby("month", as_index=False).agg(agg_cols).sort_values("month")
    if "profit" in trend.columns and "billing" in trend.columns:
        trend["margin"] = (
            trend["profit"] / trend["billing"].replace(0, float("nan")) * 100
        ).round(1).fillna(0.0)
    return trend


def get_top_clients(df: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    if df.empty or "client" not in df.columns or "billing" not in df.columns:
        return pd.DataFrame()
    grp = (
        df.groupby("client", as_index=False)
        .agg(billing=("billing", "sum"), profit=("profit", "sum"),
             cost=("cost", "sum"), hours=("hours", "sum"))
        .nlargest(n, "billing")
        .reset_index(drop=True)
    )
    grp["margin"] = (
        grp["profit"] / grp["billing"].replace(0, float("nan")) * 100
    ).round(1).fillna(0.0)
    return grp


def get_top_employees(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if df.empty or "employee_name" not in df.columns:
        return pd.DataFrame()
    agg_cols = {c: (c, "sum") for c in ("cost", "hours", "profit") if c in df.columns}
    if not agg_cols:
        return pd.DataFrame()
    return (
        df.groupby("employee_name", as_index=False)
        .agg(**agg_cols)
        .nlargest(n, "cost")
        .reset_index(drop=True)
    )
