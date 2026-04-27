"""
Unified billing pipeline — orchestrates all core modules in-memory.

No billing logic lives here; this is pure orchestration.
Wraps main.py's private pipeline functions so callers get DataFrames
back directly without going through the filesystem.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pandas as pd

from core.pdf_parser     import parse_pdf
from core.excel_loaders  import load_agreements, load_costs, load_overrides
from core.validation     import validate_pdf, results_to_dicts, ValidationError
from core.debug_writer   import write_debug
from core.report_builder import save_reports, save_organized_reports, save_clean_export

# Import orchestration internals from main — logic stays there, untouched.
from main import _bill_daily, _aggregate, _load_hours_excel, _bill_monthly


@dataclass
class PipelineResult:
    """All outputs from a single billing pipeline run."""
    detail_df:  pd.DataFrame                  # Monthly aggregated (employee × site)
    daily_df:   pd.DataFrame                  # Raw daily rows from rules engine
    issues_df:  pd.DataFrame                  # Data-quality issues
    validation: list[dict] = field(default_factory=list)  # Per-employee PDF validation
    month_str:  str = ""                      # YYYY-MM derived from parsed dates


def run_full_pipeline(
    pdf_path: str,
    agreements_path: str,
    costs_path: str,
    overrides_path: str | None = None,
    output_dir: str | None = None,
) -> PipelineResult:
    """
    Run the complete billing pipeline in-memory.

    Steps
    -----
    1. Load reference data (agreements, costs, overrides)
    2. Validate PDF  →  raises ValidationError on unrecoverable mismatch
    3. Parse PDF → daily rows
    4. Apply rules + calculate daily billing  (_bill_daily / rules_engine)
    5. Aggregate daily → monthly  (_aggregate)
    6. Optionally save Excel reports if output_dir is provided

    Returns a PipelineResult; never writes files unless output_dir is set.
    """
    # ── 1. Reference data ────────────────────────────────────────────────────
    agreements = load_agreements(agreements_path)
    costs      = load_costs(costs_path)
    overrides: dict = {}
    if overrides_path and os.path.exists(overrides_path):
        overrides = load_overrides(overrides_path)

    # ── 2. Validate PDF ───────────────────────────────────────────────────────
    validation_dicts: list[dict] = []
    if pdf_path and os.path.exists(pdf_path):
        val_results      = validate_pdf(pdf_path)   # raises ValidationError on FAIL
        validation_dicts = results_to_dicts(val_results)

    # ── 3 + 4. Parse and bill daily ──────────────────────────────────────────
    detail_daily_df = pd.DataFrame()
    issue_rows: list[dict] = []

    hours_xlsx = pdf_path.replace(".pdf", ".xlsx") if pdf_path else ""

    if pdf_path and os.path.exists(pdf_path):
        daily_raw_df = parse_pdf(pdf_path)
        if daily_raw_df.empty:
            raise ValueError("לא נמצאו שורות יומיות ב-PDF.")
        detail_daily_df, issue_rows = _bill_daily(
            daily_raw_df, agreements, costs, overrides
        )
    elif hours_xlsx and os.path.exists(hours_xlsx):
        monthly_df = _load_hours_excel(hours_xlsx)
        detail_daily_df = pd.DataFrame()           # no daily rows for Excel path
        detail_monthly, issue_rows = _bill_monthly(monthly_df, agreements, costs)
        issues_df = pd.DataFrame(issue_rows)
        if output_dir:
            save_reports(detail_monthly, issues_df, output_dir)
            write_debug([], validation_dicts, output_dir)
        return PipelineResult(
            detail_df  = detail_monthly,
            daily_df   = pd.DataFrame(),
            issues_df  = issues_df,
            validation = validation_dicts,
            month_str  = "",
        )
    else:
        raise FileNotFoundError(
            "לא נמצא קובץ שעות. נדרש hours.pdf (Andromeda) בתיקיית data/."
        )

    # ── 5. Aggregate daily → monthly ─────────────────────────────────────────
    detail_df = _aggregate(detail_daily_df, costs, issue_rows)

    # ── Derive month string ───────────────────────────────────────────────────
    month_str = ""
    if not detail_daily_df.empty and "date" in detail_daily_df.columns:
        first_date = pd.to_datetime(detail_daily_df["date"].min())
        month_str  = first_date.strftime("%Y-%m")

    if month_str and not detail_df.empty:
        detail_df["month"] = month_str

    issues_df = pd.DataFrame(issue_rows)

    # ── Post-billing cross-check: system hours vs PDF-validated hours ─────────
    if validation_dicts and not detail_df.empty:
        pdf_total = sum(
            float(v.get("שעות שנקראו") or 0) for v in validation_dicts
        )
        sys_total = float(detail_df["total_hours"].sum())
        if pdf_total > 0 and abs(sys_total - pdf_total) > 0.1:
            issue_rows.append({
                "employee_id":   "ALL",
                "employee_name": "מערכת",
                "site":          "",
                "issue_type":    "אי-התאמת שעות",
                "description": (
                    f"שעות מערכת {sys_total:.2f}h ≠ שעות PDF {pdf_total:.2f}h "
                    f"(הפרש {abs(sys_total - pdf_total):.2f}h) — בדוק חישוב"
                ),
            })
            issues_df = pd.DataFrame(issue_rows)

    # ── 6. Save reports (optional) ────────────────────────────────────────────
    if output_dir:
        from datetime import datetime
        _month = month_str or datetime.now().strftime("%Y-%m")
        save_organized_reports(detail_df, issues_df, output_dir, _month)
        month_out = os.path.join(output_dir, _month)
        write_debug(
            detail_daily_df.to_dict("records") if not detail_daily_df.empty else [],
            validation_dicts,
            month_out,
        )
        if not detail_daily_df.empty:
            save_clean_export(
                detail_daily_df,
                os.path.join(month_out, "clean.xlsx"),
            )

    return PipelineResult(
        detail_df  = detail_df,
        daily_df   = detail_daily_df,
        issues_df  = issues_df,
        validation = validation_dicts,
        month_str  = month_str,
    )


# ---------------------------------------------------------------------------
# Month-based helpers (existing MM-YYYY folder structure under data/)
# ---------------------------------------------------------------------------

import re as _re

DATA_ROOT    = os.getenv("DATA_ROOT",   "data")
OUTPUT_ROOT  = os.getenv("OUTPUT_ROOT", "output")
# History + master dataset live inside DATA_ROOT (persists on Render disk)
MASTER_PATH  = os.path.join(DATA_ROOT, "master.parquet")
HISTORY_ROOT = os.path.join(DATA_ROOT, "months")

# Agreements and overrides live at the data/ root (existing convention)
_AGREEMENTS_CANDIDATES = [
    os.path.join(DATA_ROOT, "agreements.xlsx"),
    os.path.join(DATA_ROOT, "agreements", "agreements.xlsx"),
]
_OVERRIDES_CANDIDATES = [
    os.path.join(DATA_ROOT, "overrides.xlsx"),
    os.path.join(DATA_ROOT, "agreements", "overrides.xlsx"),
]

# Matches existing folder names like 01-2025, 12-2026
_MONTH_FOLDER_RE = _re.compile(r"^\d{2}-\d{4}$")


def _first_existing(*paths: str) -> str | None:
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def list_available_months(data_root: str = DATA_ROOT) -> list[str]:
    """
    Scan data_root/ for MM-YYYY folders that contain a hours file.
    Returns sorted list (e.g. ['01-2025', '02-2025', ..., '02-2026']).
    """
    if not os.path.isdir(data_root):
        return []

    result: list[str] = []
    for name in sorted(os.listdir(data_root)):
        if not _MONTH_FOLDER_RE.match(name):
            continue
        month_path = os.path.join(data_root, name)
        if not os.path.isdir(month_path):
            continue
        has_hours = any(
            os.path.exists(os.path.join(month_path, f))
            for f in ("hours.pdf", "hours.xlsx")
        )
        if has_hours:
            result.append(name)
    return result


def month_file_mtime(month: str, data_root: str = DATA_ROOT) -> float:
    """Return latest mtime of all source files for a month (used as cache key)."""
    month_dir  = os.path.join(data_root, month)
    candidates = [
        os.path.join(month_dir, f)
        for f in ("hours.pdf", "hours.xlsx", "costs.xlsx")
    ] + list(_AGREEMENTS_CANDIDATES) + list(_OVERRIDES_CANDIDATES)

    mtimes = [os.path.getmtime(p) for p in candidates if os.path.exists(p)]
    return max(mtimes) if mtimes else 0.0


def run_month_pipeline(
    month: str,
    data_root: str = DATA_ROOT,
    output_root: str = OUTPUT_ROOT,
) -> PipelineResult:
    """
    Run the billing pipeline for a specific MM-YYYY month.

    Loads:
      {data_root}/{month}/hours.pdf  (or hours.xlsx)
      {data_root}/{month}/costs.xlsx
      {data_root}/agreements.xlsx
      {data_root}/overrides.xlsx     (optional)

    Saves output to {output_root}/{month}/.
    """
    month_dir       = os.path.join(data_root, month)
    pdf_path        = os.path.join(month_dir, "hours.pdf")
    excel_path      = os.path.join(month_dir, "hours.xlsx")
    costs_path      = os.path.join(month_dir, "costs.xlsx")
    agreements_path = _first_existing(*_AGREEMENTS_CANDIDATES)
    overrides_path  = _first_existing(*_OVERRIDES_CANDIDATES)

    if not os.path.isdir(month_dir):
        raise FileNotFoundError(f"תיקיית חודש לא קיימת: {month_dir}")
    if agreements_path is None:
        raise FileNotFoundError(
            "לא נמצא קובץ הסכמים. הכנס agreements.xlsx לתיקיית data/."
        )
    if not os.path.exists(costs_path):
        raise FileNotFoundError(
            f"לא נמצא קובץ עלויות עבור {month}. "
            f"הכנס costs.xlsx לתיקיית {month_dir}/"
        )

    hours_path = pdf_path if os.path.exists(pdf_path) else excel_path

    return run_full_pipeline(
        pdf_path        = hours_path,
        agreements_path = agreements_path,
        costs_path      = costs_path,
        overrides_path  = overrides_path,
        output_dir      = output_root,
    )


# ---------------------------------------------------------------------------
# Monthly history — persist results inside /data/months/{MM-YYYY}/
# ---------------------------------------------------------------------------

def get_available_months() -> list[str]:
    """
    List all months in HISTORY_ROOT that have saved results (kpis.json).
    Returns sorted list e.g. ['01-2025', '02-2025', ..., '02-2026'].
    """
    if not os.path.isdir(HISTORY_ROOT):
        return []
    return sorted(
        d for d in os.listdir(HISTORY_ROOT)
        if os.path.isdir(os.path.join(HISTORY_ROOT, d))
        and os.path.exists(os.path.join(HISTORY_ROOT, d, "kpis.json"))
    )


def save_month_history(
    month: str,
    result: PipelineResult,
    kpis: dict,
) -> None:
    """
    Persist all monthly results to HISTORY_ROOT/{month}/ so they
    survive Render restarts (disk mount at /data).

    Saves:
      kpis.json          — KPI totals (billing, cost, profit, counts)
      result.xlsx        — full billing detail (from report_builder)
      profitability.xlsx — profitability per client
      issues.xlsx        — data-quality issues
      clients.csv        — per-client billing/profit for fast comparison
    """
    month_dir = os.path.join(HISTORY_ROOT, month)
    os.makedirs(month_dir, exist_ok=True)

    # kpis.json
    import json as _json
    with open(os.path.join(month_dir, "kpis.json"), "w") as f:
        _json.dump(kpis, f)

    # Excel reports
    try:
        save_organized_reports(result.detail_df, result.issues_df, HISTORY_ROOT, month)
    except Exception:
        pass

    # Per-client CSV (for fast comparison without re-reading Excel)
    if not result.detail_df.empty:
        try:
            client_df = (
                result.detail_df
                .groupby("client", as_index=False)
                .agg(billing_amount=("billing_amount", "sum"),
                     cost          =("cost",           "sum"),
                     profit        =("profit",         "sum"))
            )
            client_df["month"] = month
            client_df.to_csv(os.path.join(month_dir, "clients.csv"), index=False)
        except Exception:
            pass


def load_month_kpis(month: str) -> dict | None:
    """Return saved kpis.json for a month, or None."""
    import json as _json
    p = os.path.join(HISTORY_ROOT, month, "kpis.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return _json.load(f)
        except Exception:
            pass
    return None


def load_month_clients(month: str) -> "pd.DataFrame":
    """Return saved per-client CSV for a month, or empty DataFrame."""
    import pandas as _pd
    p = os.path.join(HISTORY_ROOT, month, "clients.csv")
    if os.path.exists(p):
        try:
            return _pd.read_csv(p)
        except Exception:
            pass
    return _pd.DataFrame()


# ---------------------------------------------------------------------------
# Master dataset  (/data/master.parquet)
# ---------------------------------------------------------------------------

_MASTER_COLS = {
    "client":         "client",
    "employee_id":    "employee_id",
    "employee_name":  "employee_name",
    "site":           "site",
    "total_hours":    "hours",
    "billing_amount": "billing",
    "cost":           "cost",
    "profit":         "profit",
    "margin_pct":     "margin",
}


def update_master(detail_df: "pd.DataFrame", month: str) -> None:
    """
    Upsert month's rows into master.parquet.
    Old rows for this month are replaced; all other months stay.
    Safe to call after every pipeline run.
    """
    import pandas as _pd

    avail = {k: v for k, v in _MASTER_COLS.items() if v in detail_df.columns}
    slim  = detail_df[list(avail.values())].copy().rename(columns={v: k for k, v in avail.items()})
    slim["month"] = month

    if os.path.exists(MASTER_PATH):
        try:
            existing = _pd.read_parquet(MASTER_PATH)
            existing = existing[existing["month"] != month]
            master   = _pd.concat([existing, slim], ignore_index=True)
        except Exception:
            master = slim
    else:
        master = slim

    os.makedirs(os.path.dirname(MASTER_PATH) or ".", exist_ok=True)
    master.to_parquet(MASTER_PATH, index=False)


def get_all_data() -> "pd.DataFrame":
    """Load master.parquet. Returns empty DataFrame if file doesn't exist."""
    import pandas as _pd
    if os.path.exists(MASTER_PATH):
        try:
            return _pd.read_parquet(MASTER_PATH)
        except Exception:
            pass
    return _pd.DataFrame()


def filter_by_month(df: "pd.DataFrame", month: str) -> "pd.DataFrame":
    if df.empty or "month" not in df.columns:
        return df
    return df[df["month"] == month]


def filter_by_client(df: "pd.DataFrame", clients) -> "pd.DataFrame":
    if df.empty or "client" not in df.columns:
        return df
    if isinstance(clients, str):
        clients = [clients]
    return df[df["client"].isin(clients)]


def get_profit_trend(df: "pd.DataFrame") -> "pd.DataFrame":
    """Return monthly totals: month | billing | profit | cost."""
    import pandas as _pd
    if df.empty:
        return _pd.DataFrame()
    cols = {c: c for c in ["billing", "profit", "cost", "hours"] if c in df.columns}
    if "month" not in df.columns or not cols:
        return _pd.DataFrame()
    return (
        df.groupby("month", as_index=False)
        .agg({c: "sum" for c in cols})
        .sort_values("month")
        .rename(columns={"billing": "total_billing", "profit": "total_profit"})
    )


def get_top_clients(df: "pd.DataFrame", n: int = 5) -> "pd.DataFrame":
    """Return top N clients by total billing across all months in df."""
    import pandas as _pd
    if df.empty or "client" not in df.columns or "billing" not in df.columns:
        return _pd.DataFrame()
    return (
        df.groupby("client", as_index=False)
        .agg(billing=("billing", "sum"), profit=("profit", "sum"))
        .nlargest(n, "billing")
        .reset_index(drop=True)
    )


def load_trend_df() -> "pd.DataFrame":
    """
    Build a time-series DataFrame from all saved kpis.json files.
    Suitable for trend charts.
    """
    import pandas as _pd, json as _json
    rows = []
    for m in get_available_months():
        p = os.path.join(HISTORY_ROOT, m, "kpis.json")
        try:
            with open(p) as f:
                d = _json.load(f)
            d["month"] = m
            rows.append(d)
        except Exception:
            pass
    return _pd.DataFrame(rows) if rows else _pd.DataFrame()
