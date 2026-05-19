"""
Historical analytics — analytics only, no billing logic changes.

Loads monthly final_*.xlsx files from a history directory,
standardizes columns, computes KPIs, month comparisons,
profitability, and anomalies.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

import pandas as pd


# ---------------------------------------------------------------------------
# Column aliases — Hebrew output labels → internal English names
# ---------------------------------------------------------------------------

_COL_ALIASES: dict[str, list[str]] = {
    "employee_id":      ["מס' עובד", "מס עובד", "employee_id"],
    "employee_name":    ["שם עובד", "employee_name"],
    "client":           ["לקוח", "client"],
    "site":             ["אתר", "site"],
    "days":             ["ימים", "ימי עבודה", "days"],
    "total_hours":      ["שעות לתשלום", "שעות עבודה", 'סה"כ שעות', "total_hours", "hours_to_pay"],
    "billable_hours":   ["שעות לחיוב", "billable_hours"],
    "billing_amount":   ["חיוב ₪", "סכום לחיוב ₪", "billing_amount"],
    "cost":             ["עלות מעביד ₪", "עלות מעביד", "cost"],
    "profit":           ["רווח ₪", "profit"],
    "margin_pct":       ["% רווח", "margin_pct"],
    "completion_added": ["שלמות שנוספה", "שלמות", "completion_added"],
}

# Columns every history file must contain (after normalization)
REQUIRED_COLS = ["client", "employee_id", "total_hours", "billing_amount", "cost"]


def classify_margin(margin_pct: float) -> str:
    """Return HIGH / MEDIUM / LOW / LOSS based on margin percentage."""
    if margin_pct > 30:
        return "HIGH"
    if margin_pct >= 10:
        return "MEDIUM"
    if margin_pct >= 0:
        return "LOW"
    return "LOSS"


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _extract_month(filename: str) -> str:
    """
    Extract YYYY-MM from filename.
    Handles:  final_20250215_123456.xlsx → 2025-02
              02-25.xlsx                 → 2025-02
              2025-02_final.xlsx         → 2025-02
    """
    name = os.path.basename(filename)
    m = re.search(r"(\d{4})(\d{2})\d{2}", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"(\d{4})-(\d{2})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"(\d{2})-(\d{2})", name)
    if m:
        mm, yy = m.group(1), m.group(2)
        return f"20{yy}-{mm}"
    return "unknown"


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Hebrew column labels to internal English names."""
    rename: dict[str, str] = {}
    for internal, aliases in _COL_ALIASES.items():
        col = _find_col(df, aliases)
        if col and col != internal:
            rename[col] = internal
    return df.rename(columns=rename)


# ---------------------------------------------------------------------------
# 1. Validate
# ---------------------------------------------------------------------------

def validate_history_files(history_dir: str) -> list[dict]:
    """
    Check each .xlsx file in history_dir for required columns.
    Reads only the header row for speed.
    Returns list of {file, missing, ok}.
    """
    results: list[dict] = []
    if not os.path.isdir(history_dir):
        return results

    for fname in sorted(os.listdir(history_dir)):
        if not fname.endswith(".xlsx"):
            continue
        path = os.path.join(history_dir, fname)
        try:
            xf    = pd.ExcelFile(path)
            sheet = next(
                (s for s in ["פירוט לפי עובד"] if s in xf.sheet_names),
                xf.sheet_names[0],
            )
            # nrows=0 reads only header; header=2 means row index 2 is the header
            df = pd.read_excel(xf, sheet_name=sheet, header=2, nrows=0)
            df.columns = [str(c).strip() for c in df.columns]
            df = _normalize_cols(df)
            missing = [c for c in REQUIRED_COLS if c not in df.columns]
            results.append({"file": fname, "missing": missing, "ok": len(missing) == 0})
        except Exception as e:
            results.append({"file": fname, "missing": ["(שגיאת קריאה)"], "ok": False})

    return results


# ---------------------------------------------------------------------------
# 2. Load
# ---------------------------------------------------------------------------

def load_history(history_dir: str) -> pd.DataFrame:
    """
    Load all *.xlsx files from history_dir.
    Reads the 'פירוט לפי עובד' sheet (header at row 3).
    Returns a combined DataFrame with 'month' and 'source_file' columns.
    """
    if not os.path.isdir(history_dir):
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for fname in sorted(os.listdir(history_dir)):
        if not fname.endswith(".xlsx"):
            continue
        path  = os.path.join(history_dir, fname)
        month = _extract_month(fname)
        try:
            xf    = pd.ExcelFile(path)
            sheet = next(
                (s for s in ["פירוט לפי עובד"] if s in xf.sheet_names),
                xf.sheet_names[0],
            )
            df = pd.read_excel(xf, sheet_name=sheet, header=2)
            df.columns = [str(c).strip() for c in df.columns]
            df = df.dropna(how="all")
            df = _normalize_cols(df)
            df["month"]       = month
            df["source_file"] = fname
            frames.append(df)
        except Exception:
            continue

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# 3. Standardize
# ---------------------------------------------------------------------------

def standardize(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure required columns exist with correct types.
    Missing numeric columns → 0.0.  Missing string columns → ''.
    Derives 'date' (first of month) and 'profit' if missing.
    """
    str_cols = ["employee_id", "employee_name", "client", "site", "month"]
    num_cols = ["days", "total_hours", "billable_hours", "billing_amount", "cost", "profit"]

    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
        else:
            df[col] = ""

    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    if "hours_to_pay" not in df.columns:
        df["hours_to_pay"] = df["total_hours"]

    if "date" not in df.columns:
        def _to_date(m: str):
            try:
                return datetime.strptime(m, "%Y-%m")
            except Exception:
                return pd.NaT
        df["date"] = df["month"].apply(_to_date)

    if df["profit"].sum() == 0 and "billing_amount" in df.columns and "cost" in df.columns:
        df["profit"] = df["billing_amount"] - df["cost"]

    return df


# ---------------------------------------------------------------------------
# 4. Data cleaning
# ---------------------------------------------------------------------------

def clean_data(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Clean history DataFrame after standardize().
    Steps: remove unknown-month rows → remove duplicates → clip negative cost.
    Returns (cleaned_df, stats_dict).
    """
    stats: dict = {}

    # Drop rows with unparseable month
    mask = df["month"] == "unknown"
    stats["removed_unknown_month"] = int(mask.sum())
    df = df[~mask].copy()

    # Remove duplicates on (employee_id, site, month)
    dup_mask = df.duplicated(subset=["employee_id", "site", "month"], keep="first")
    stats["removed_duplicates"] = int(dup_mask.sum())
    df = df[~dup_mask].copy()

    # Clip negative cost values
    if "cost" in df.columns:
        neg = int((df["cost"] < 0).sum())
        stats["fixed_negative_cost"] = neg
        df["cost"] = df["cost"].clip(lower=0)
    else:
        stats["fixed_negative_cost"] = 0

    return df, stats


# ---------------------------------------------------------------------------
# 5. Month comparison
# ---------------------------------------------------------------------------

def compare_months(df: pd.DataFrame, current_month: str) -> dict:
    """
    Compare current_month vs previous month and vs same month last year.
    Returns billing_change_pct, hours_change_pct, yoy_billing_pct, yoy_hours_pct.
    """
    months = sorted(df["month"].unique())

    def _totals(m: str) -> tuple[float, float]:
        sub = df[df["month"] == m]
        return float(sub["billing_amount"].sum()), float(sub["total_hours"].sum())

    def _pct(new: float, old: float):
        return round((new - old) / abs(old) * 100, 1) if old != 0 else None

    curr_b, curr_h = _totals(current_month)
    result: dict = {
        "current_month": current_month,
        "curr_billing":  round(curr_b, 2),
        "curr_hours":    round(curr_h, 2),
    }

    idx = months.index(current_month) if current_month in months else -1
    if idx > 0:
        prev = months[idx - 1]
        pb, ph = _totals(prev)
        result.update({
            "prev_month":         prev,
            "prev_billing":       round(pb, 2),
            "billing_change_pct": _pct(curr_b, pb),
            "hours_change_pct":   _pct(curr_h, ph),
        })

    try:
        yr, mo    = current_month.split("-")
        yoy_month = f"{int(yr)-1}-{mo}"
        if yoy_month in months:
            yb, yh = _totals(yoy_month)
            result.update({
                "yoy_month":       yoy_month,
                "yoy_billing":     round(yb, 2),
                "yoy_billing_pct": _pct(curr_b, yb),
                "yoy_hours_pct":   _pct(curr_h, yh),
            })
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# 6. KPI summary
# ---------------------------------------------------------------------------

def kpi_summary(df: pd.DataFrame, month: str | None = None) -> dict:
    """Aggregate KPIs. Optional month filter."""
    sub = df[df["month"] == month].copy() if month else df.copy()
    if sub.empty:
        return {"total_billing": 0.0, "total_cost": 0.0, "total_profit": 0.0,
                "active_employees": 0, "active_clients": 0}
    return {
        "total_billing":    round(float(sub["billing_amount"].sum()), 2),
        "total_cost":       round(float(sub["cost"].sum()), 2),
        "total_profit":     round(float(sub["profit"].sum()), 2),
        "active_employees": int(sub["employee_id"].nunique()),
        "active_clients":   int(sub["client"].nunique()),
    }


# ---------------------------------------------------------------------------
# 7. Top insights
# ---------------------------------------------------------------------------

def top_insights(df: pd.DataFrame, month: str | None = None, n: int = 5) -> dict:
    """
    Returns top N clients in three categories:
    - profitable: highest profit
    - loss:       loss-making clients (profit < 0) sorted worst first
    - growth:     highest MoM billing growth % (requires month + prev month)

    Internal entities (e.g. ינאי פרסונל — our own company) are excluded from
    the loss list because they carry overhead cost without external billing
    by design, not as a leak. See ``core.internal_entities``.
    """
    from core.internal_entities import is_internal

    sub = df[df["month"] == month].copy() if month else df.copy()

    empty = pd.DataFrame(
        columns=["client", "billing_amount", "profit", "margin_pct"]
    )
    if sub.empty:
        return {"profitable": empty, "loss": empty, "growth": pd.DataFrame()}

    grp = (
        sub.groupby("client", as_index=False)
        .agg(billing_amount=("billing_amount", "sum"),
             cost          =("cost",           "sum"),
             profit        =("profit",         "sum"))
    )
    grp["margin_pct"] = (
        grp["profit"] / grp["billing_amount"].replace(0, float("nan")) * 100
    ).round(1)

    profitable = grp.nlargest(n, "profit")[
        ["client", "billing_amount", "profit", "margin_pct"]
    ].reset_index(drop=True)

    # Exclude internal entities (overhead, not a "loss-making client")
    loss_rows = grp[(grp["profit"] < 0) &
                    (~grp["client"].astype(str).map(is_internal))]
    loss = (
        loss_rows.nsmallest(n, "profit")[
            ["client", "billing_amount", "profit", "margin_pct"]
        ].reset_index(drop=True)
        if not loss_rows.empty
        else empty
    )

    # Growth: requires a specific month and a previous month
    growth = pd.DataFrame(columns=["client", "billing_amount", "growth_pct"])
    if month:
        months = sorted(df["month"].unique())
        idx    = months.index(month) if month in months else -1
        if idx > 0:
            prev_month = months[idx - 1]
            prev = (
                df[df["month"] == prev_month]
                .groupby("client")["billing_amount"].sum()
            )
            grp["prev_billing"] = grp["client"].map(prev).fillna(0)
            mask = grp["prev_billing"] > 0
            grp.loc[mask, "growth_pct"] = (
                (grp.loc[mask, "billing_amount"] - grp.loc[mask, "prev_billing"])
                / grp.loc[mask, "prev_billing"] * 100
            ).round(1)
            growth_rows = grp.dropna(subset=["growth_pct"])
            if not growth_rows.empty:
                growth = (
                    growth_rows.nlargest(n, "growth_pct")[
                        ["client", "billing_amount", "growth_pct"]
                    ].reset_index(drop=True)
                )

    return {"profitable": profitable, "loss": loss, "growth": growth}


# ---------------------------------------------------------------------------
# 8. Anomaly detection
# ---------------------------------------------------------------------------

def detect_anomalies(
    df: pd.DataFrame,
    month: str | None = None,
    hours_threshold: float = 30.0,
    ot_threshold: float = 10.0,
    completion_threshold: float = 20.0,
    low_profit_threshold: float = 5.0,
) -> pd.DataFrame:
    """
    Detects per employee-site row:
    - Zero billing with hours > 0
    - Large MoM hours change (> hours_threshold %)
    - OT hours > ot_threshold (when column present)
    - Missing cost (cost == 0, billing > 0)
    - High completion hours (> completion_threshold)
    - Low profit margin (< low_profit_threshold %)
    """
    months  = sorted(df["month"].unique())
    targets = [month] if month else months
    has_completion = "completion_added" in df.columns
    rows: list[dict] = []

    for m in targets:
        curr = df[df["month"] == m]
        idx  = months.index(m) if m in months else -1

        for _, row in curr.iterrows():
            emp_id   = str(row.get("employee_id", ""))
            emp_name = str(row.get("employee_name", ""))
            client   = str(row.get("client", ""))
            site     = str(row.get("site", ""))
            hours    = float(row.get("total_hours") or 0)
            billing  = float(row.get("billing_amount") or 0)
            cost     = float(row.get("cost") or 0)
            profit   = float(row.get("profit") or 0)

            def _add(atype: str, desc: str) -> None:
                rows.append({
                    "month": m, "employee_id": emp_id, "employee_name": emp_name,
                    "client": client, "site": site,
                    "anomaly_type": atype, "description": desc,
                })

            # Zero billing with hours worked
            if billing == 0 and hours > 0:
                _add("חיוב אפס", f"{hours:.1f} שעות — חיוב ₪0")

            # Missing cost
            if cost == 0 and billing > 0:
                _add("עלות חסרה", f"חיוב ₪{billing:,.0f} — עלות 0 ₪")

            # Low profit margin
            if billing > 0:
                margin = profit / billing * 100
                if margin < low_profit_threshold:
                    _add("רווחיות נמוכה", f"מרג'ין {margin:.1f}%")

            # High completion hours
            if has_completion:
                completion = float(row.get("completion_added") or 0)
                if completion > completion_threshold:
                    _add("שלמות גבוהה", f"{completion:.1f}h שלמות")

            # Large MoM hours change
            if idx > 0:
                prev_m = months[idx - 1]
                prev_rows = df[
                    (df["month"] == prev_m) &
                    (df["employee_id"] == emp_id) &
                    (df["site"] == site)
                ]
                if not prev_rows.empty:
                    prev_h = float(prev_rows["total_hours"].iloc[0])
                    if prev_h > 0:
                        change = abs(hours - prev_h) / prev_h * 100
                        if change >= hours_threshold:
                            direction = "עלייה" if hours > prev_h else "ירידה"
                            _add(
                                f"{direction} חריגה בשעות",
                                f"{prev_h:.1f}h → {hours:.1f}h "
                                f"({(hours - prev_h) / prev_h * 100:+.1f}%)",
                            )

            # Abnormal OT
            if "ot_hours" in df.columns:
                ot = float(row.get("ot_hours") or 0)
                if ot > ot_threshold:
                    _add("שעות נוספות חריגות", f"{ot:.1f} שעות נוספות")

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["month", "employee_id", "employee_name",
                 "client", "site", "anomaly_type", "description"]
    )


# ---------------------------------------------------------------------------
# 9. Dashboard table
# ---------------------------------------------------------------------------

def dashboard_table(df: pd.DataFrame, month: str | None = None) -> pd.DataFrame:
    """
    Returns per-client: billing_amount | cost | profit | margin_pct
                      | billing_change_pct | profit_change_pct
    Both change columns are None when no previous month is available.
    """
    sub = df[df["month"] == month].copy() if month else df.copy()
    if sub.empty:
        return pd.DataFrame()

    grp = (
        sub.groupby("client", as_index=False)
        .agg(
            billing_amount=("billing_amount", "sum"),
            cost          =("cost",           "sum"),
            profit        =("profit",         "sum"),
        )
        .sort_values("billing_amount", ascending=False)
    )
    grp["margin_pct"] = (
        grp["profit"] / grp["billing_amount"].replace(0, float("nan")) * 100
    ).round(1)
    grp["category"] = grp["margin_pct"].fillna(-1).apply(classify_margin)

    grp["billing_change_pct"] = None
    grp["profit_change_pct"]  = None

    if month:
        months = sorted(df["month"].unique())
        idx    = months.index(month) if month in months else -1
        if idx > 0:
            prev_df  = df[df["month"] == months[idx - 1]]
            prev_b   = prev_df.groupby("client")["billing_amount"].sum()
            prev_p   = prev_df.groupby("client")["profit"].sum()

            grp["_pb"] = grp["client"].map(prev_b).fillna(0)
            grp["_pp"] = grp["client"].map(prev_p).fillna(0)

            mask_b = grp["_pb"] > 0
            mask_p = grp["_pp"] != 0

            grp.loc[mask_b, "billing_change_pct"] = (
                (grp.loc[mask_b, "billing_amount"] - grp.loc[mask_b, "_pb"])
                / grp.loc[mask_b, "_pb"] * 100
            ).round(1)

            grp.loc[mask_p, "profit_change_pct"] = (
                (grp.loc[mask_p, "profit"] - grp.loc[mask_p, "_pp"])
                / grp.loc[mask_p, "_pp"].abs() * 100
            ).round(1)

            grp.drop(columns=["_pb", "_pp"], inplace=True)

    return grp


# ---------------------------------------------------------------------------
# Employee profitability
# ---------------------------------------------------------------------------

def profitability_by_employee(
    df: pd.DataFrame, month: str | None = None
) -> pd.DataFrame:
    """
    Aggregate profitability per employee (across all clients).
    Returns: employee_id, employee_name, total_hours, billing_amount,
             cost, profit, margin_pct, category
    Sorted by profit descending.
    """
    sub = df[df["month"] == month].copy() if month else df.copy()
    if sub.empty:
        return pd.DataFrame(
            columns=["employee_id", "employee_name", "total_hours",
                     "billing_amount", "cost", "profit", "margin_pct", "category"]
        )

    grp = (
        sub.groupby(["employee_id", "employee_name"], as_index=False)
        .agg(
            total_hours   =("total_hours",    "sum"),
            billing_amount=("billing_amount", "sum"),
            cost          =("cost",           "sum"),
            profit        =("profit",         "sum"),
        )
    )
    # Recalculate profit where it might be zero due to missing column
    zero_profit = grp["profit"] == 0
    grp.loc[zero_profit, "profit"] = (
        grp.loc[zero_profit, "billing_amount"] - grp.loc[zero_profit, "cost"]
    )

    grp["margin_pct"] = (
        grp["profit"] / grp["billing_amount"].replace(0, float("nan")) * 100
    ).round(1)
    grp["category"] = grp["margin_pct"].fillna(-1).apply(classify_margin)

    return grp.sort_values("profit", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Insights engine
# ---------------------------------------------------------------------------

def insights_engine(
    df: pd.DataFrame, month: str | None = None
) -> pd.DataFrame:
    """
    Generate rule-based recommendations per client.
    Rules:
      - profit < 0                    → "⚠️ לקוח מפסיד — בדיקה דחופה"
      - margin < 10% (billing > 0)    → "שקול העלאת תעריף"
      - cost / billing > 0.85         → "בדוק עלויות עובדים"
      - completion / total_hours > 15%→ "בדוק הסכמי שלמות"
      - no rule fired                 → "✓ מצב תקין"

    Returns: client, billing_amount, cost, profit, margin_pct, category, insight
    Sorted by profit ascending (worst first for actionability).
    """
    sub = df[df["month"] == month].copy() if month else df.copy()
    if sub.empty:
        return pd.DataFrame(
            columns=["client", "billing_amount", "cost", "profit",
                     "margin_pct", "category", "insight"]
        )

    has_completion = "completion_added" in sub.columns

    agg_spec: dict = {
        "billing_amount": ("billing_amount", "sum"),
        "cost":           ("cost",           "sum"),
        "profit":         ("profit",         "sum"),
        "total_hours":    ("total_hours",    "sum"),
    }
    if has_completion:
        agg_spec["completion_added"] = ("completion_added", "sum")

    grp = sub.groupby("client", as_index=False).agg(**agg_spec)

    grp["margin_pct"]  = (
        grp["profit"] / grp["billing_amount"].replace(0, float("nan")) * 100
    ).round(1)
    grp["category"]    = grp["margin_pct"].fillna(-1).apply(classify_margin)
    grp["cost_ratio"]  = grp["cost"] / grp["billing_amount"].replace(0, float("nan"))
    if has_completion:
        grp["comp_ratio"] = (
            grp["completion_added"] / grp["total_hours"].replace(0, float("nan"))
        )

    insight_rows: list[dict] = []
    for _, row in grp.iterrows():
        tips: list[str] = []
        billing = float(row["billing_amount"])
        profit  = float(row["profit"])
        margin  = float(row["margin_pct"]) if pd.notna(row["margin_pct"]) else -999

        if profit < 0:
            tips.append("⚠️ לקוח מפסיד — בדיקה דחופה")
        if billing > 0 and margin < 10:
            tips.append("שקול העלאת תעריף")
        if pd.notna(row.get("cost_ratio")) and float(row["cost_ratio"]) > 0.85:
            tips.append("בדוק עלויות עובדים")
        if has_completion and pd.notna(row.get("comp_ratio")) and float(row["comp_ratio"]) > 0.15:
            tips.append("בדוק הסכמי שלמות")
        if not tips:
            tips.append("✓ מצב תקין")

        insight_rows.append({
            "client":         row["client"],
            "billing_amount": billing,
            "cost":           float(row["cost"]),
            "profit":         profit,
            "margin_pct":     round(margin, 1) if margin != -999 else None,
            "category":       row["category"],
            "insight":        " | ".join(tips),
        })

    result = pd.DataFrame(insight_rows)
    return result.sort_values("profit", ascending=True).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Batch-process historical PDFs through the existing pipeline
# ---------------------------------------------------------------------------

def batch_process_pdfs(
    pdf_dir: str,
    data_dir: str,
    output_dir: str,
) -> tuple[list[str], list[str]]:
    """
    Run each PDF in pdf_dir through the billing pipeline.
    Uses agreements/costs from data_dir.
    Saves final_*.xlsx to output_dir named YYYY-MM_final.xlsx.

    Returns (created_files, failed_files).
    """
    os.makedirs(output_dir, exist_ok=True)
    created: list[str] = []
    failed:  list[str] = []

    pdf_files = sorted(
        f for f in os.listdir(pdf_dir) if f.lower().endswith(".pdf")
    )

    for fname in pdf_files:
        month    = _extract_month(fname)
        pdf_path = os.path.join(pdf_dir, fname)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = os.path.join(tmp, "data")
            tmp_out  = os.path.join(tmp, "output")
            os.makedirs(tmp_data)
            os.makedirs(tmp_out)

            shutil.copy(pdf_path, os.path.join(tmp_data, "hours.pdf"))
            for ref in ["agreements.xlsx", "employees_cost.xlsx", "overrides.xlsx"]:
                src = os.path.join(data_dir, ref)
                if os.path.exists(src):
                    shutil.copy(src, os.path.join(tmp_data, ref))

            result = subprocess.run(
                [sys.executable, "main.py", tmp],
                capture_output=True, text=True, encoding="utf-8",
            )

            if result.returncode != 0:
                failed.append(fname)
                continue

            for out_fname in os.listdir(tmp_out):
                if out_fname.startswith("final_") and out_fname.endswith(".xlsx"):
                    dest = os.path.join(output_dir, f"{month}_final.xlsx")
                    shutil.copy(os.path.join(tmp_out, out_fname), dest)
                    created.append(dest)
                    break
            else:
                failed.append(fname)

    return created, failed
