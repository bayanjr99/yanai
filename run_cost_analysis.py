"""
run_cost_analysis.py — CLI runner for employee cost analysis.

Usage:
    python run_cost_analysis.py                     # latest available month
    python run_cost_analysis.py --month 02-2025     # specific month
    python run_cost_analysis.py --month 02-2025 --threshold 250
    python run_cost_analysis.py --all               # all available months → single master file

Input:
    data/months/MM-YYYY/hours.pdf   (or hours.xlsx)
    data/months/MM-YYYY/costs.xlsx

Output:
    Single month  → output/monthly/MM-YYYY/cost_analysis_MM-YYYY.xlsx
    Multi-month   → output/master/cost_analysis_YYYY.xlsx
                    output/master/cost_analysis_YYYY1_YYYY2.xlsx
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from core.cost_analysis import (
    load_hours_from_pdf,
    load_hours_from_xlsx,
    load_hours_from_billing_xlsx,
    load_hours_from_payroll_pdf,
    load_costs_xlsx,
    load_costs_pdf,
    load_costs_simple_pdf,
    compute_worked_days,
    merge_and_allocate,
    build_sheets,
    detect_warnings,
    export_to_excel,
    export_hebrew_report,
)
from pipeline import DATA_ROOT, OUTPUT_ROOT

MONTHLY_OUT  = os.path.join(OUTPUT_ROOT, "monthly")
MASTER_OUT   = os.path.join(OUTPUT_ROOT, "master")
MONTHS_ROOT  = os.path.join(DATA_ROOT, "months")
W = 64


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _find_month_folder(month: str) -> tuple[str | None, str | None]:
    """
    Locate hours file and costs file for a given month.

    Hours file priority (first found wins):
      1. hours1.pdf           — Andromeda payroll-detail PDF (primary)
      2. MM-YY.pdf            — same format, named after the month
      3. hours.pdf            — legacy Andromeda daily PDF
      4. hours.xlsx / hours.xls — Excel fallback

    Costs file priority:
      costs.xlsx → costs.pdf → cost.pdf → cost.xlsx

    Checks data/months/MM-YYYY/ first, then data/MM-YYYY/.
    Returns (hours_path, costs_path) or (None, None).
    """
    mm, yyyy = month[:2], month[3:]
    yy = yyyy[2:]                          # "2025" → "25"
    monthly_pdf_name = f"{mm}-{yy}.pdf"    # e.g. "01-25.pdf"

    candidates = [
        os.path.join(MONTHS_ROOT, month),
        os.path.join(DATA_ROOT, month),
    ]
    for folder in candidates:
        if not os.path.isdir(folder):
            continue

        # Costs
        costs = None
        for cname in ("costs.xlsx", "costs.pdf", "cost.pdf", "cost.xlsx"):
            c = os.path.join(folder, cname)
            if os.path.exists(c):
                costs = c
                break
        if costs is None:
            continue

        # Hours — payroll PDF first, then Excel
        for hname in ("hours1.pdf", monthly_pdf_name, "hours.pdf",
                       "hours.xlsx", "hours.xls"):
            h = os.path.join(folder, hname)
            if os.path.exists(h):
                return h, costs

    return None, None


def _available_months() -> list[str]:
    """Return chronologically sorted months that have hours + costs."""
    pat = re.compile(r"^\d{2}-\d{4}$")
    found: set[str] = set()
    for root in (MONTHS_ROOT, DATA_ROOT):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if not pat.match(name):
                continue
            folder = os.path.join(root, name)
            if not os.path.isdir(folder):
                continue
            has_costs = any(
                os.path.exists(os.path.join(folder, f))
                for f in ("costs.xlsx", "costs.pdf", "cost.pdf", "cost.xlsx")
            )
            has_hours = any(
                os.path.exists(os.path.join(folder, f))
                for f in ("hours.pdf", "hours.xlsx", "hours.xls")
            )
            if has_costs and has_hours:
                found.add(name)

    def _key(m: str) -> datetime.date:
        try:
            mm, yy = int(m[:2]), int(m[3:])
            return datetime.date(yy, mm, 1)
        except Exception:
            return datetime.date.min

    return sorted(found, key=_key)


def _smart_output_path(months: list[str]) -> str:
    """
    Return the output file path based on how many months are being processed.
      1 month  → output/monthly/MM-YYYY/cost_analysis_MM-YYYY.xlsx
      N months → output/master/cost_analysis_YYYY.xlsx  (or YYYY1_YYYY2.xlsx)
    """
    if len(months) == 1:
        mm_yyyy  = months[0]
        out_dir  = os.path.join(MONTHLY_OUT, mm_yyyy)
        os.makedirs(out_dir, exist_ok=True)
        return os.path.join(out_dir, f"cost_analysis_{mm_yyyy}.xlsx")

    years     = sorted({m[3:] for m in months})
    year_str  = "_".join(years)
    os.makedirs(MASTER_OUT, exist_ok=True)
    return os.path.join(MASTER_OUT, f"cost_analysis_{year_str}.xlsx")


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def _sep(title: str = "") -> None:
    if title:
        print(f"\n{'─' * 4} {title} {'─' * max(0, W - 6 - len(title))}")
    else:
        print("─" * W)


def _rule() -> None:
    print("═" * W)


def _print_employee_preview(merged) -> None:
    import pandas as pd
    if merged.empty:
        return
    emp_tbl = (
        merged
        .groupby(["employee_id", "employee_name"], as_index=False)
        .agg(
            total_hours   =("total_hours",    "sum"),
            employer_cost =("employer_cost",  "first"),
            allocated_cost=("allocated_cost", "sum"),
        )
    )
    safe = emp_tbl["total_hours"].replace(0, float("nan"))
    emp_tbl["cost_per_hour"] = (emp_tbl["employer_cost"] / safe).round(0).fillna(0)
    emp_tbl = emp_tbl.sort_values("employer_cost", ascending=False).head(15)

    print(f"\n  {'ID':>6}  {'Name':30}  {'Hours':>7}  {'Cost':>10}  {'₪/h':>6}")
    print(f"  {'─'*6}  {'─'*30}  {'─'*7}  {'─'*10}  {'─'*6}")
    for _, r in emp_tbl.iterrows():
        print(
            f"  {r['employee_id']:>6}  "
            f"{str(r['employee_name'])[:30]:30}  "
            f"{r['total_hours']:>7.1f}  "
            f"₪{r['employer_cost']:>9,.0f}  "
            f"₪{r['cost_per_hour']:>5.0f}"
        )


def _print_site_preview(sheets: dict) -> None:
    site_df = sheets.get("site_summary")
    if site_df is None or site_df.empty:
        return
    top = (
        site_df
        .groupby("site", as_index=False)
        .agg(hours=("hours", "sum"), cost=("allocated_cost", "sum"))
        .nlargest(8, "cost")
    )
    print(f"\n  {'Site':35}  {'Hours':>7}  {'Cost':>10}  {'₪/h':>6}")
    print(f"  {'─'*35}  {'─'*7}  {'─'*10}  {'─'*6}")
    for _, r in top.iterrows():
        cph = r["cost"] / r["hours"] if r["hours"] > 0 else 0
        print(
            f"  {str(r['site'])[:35]:35}  "
            f"{r['hours']:>7.1f}  "
            f"₪{r['cost']:>9,.0f}  "
            f"₪{cph:>5.0f}"
        )


def _print_warnings(warnings_df) -> None:
    if warnings_df.empty:
        print("  ✓ No warnings")
        return
    shown = warnings_df.head(20)
    for _, r in shown.iterrows():
        print(f"  ⚠  [{r['employee_id']}] {r['issue']}")
    if len(warnings_df) > 20:
        print(f"  ... and {len(warnings_df) - 20} more — see warnings sheet in Excel")


def _print_final_summary(
    months: list[str],
    total_employees: int,
    total_hours: float,
    total_cost: float,
    avg_cph: float,
    output_path: str,
    n_warnings: int,
) -> None:
    _sep("SUMMARY")
    print(f"  Months processed : {', '.join(months)}")
    print(f"  Total employees  : {total_employees}")
    print(f"  Total hours      : {total_hours:,.2f}h")
    print(f"  Total cost       : ₪{total_cost:>12,.0f}")
    print(f"  Avg cost/hour    : ₪{avg_cph:,.0f}")
    print(f"  Warnings         : {n_warnings}")
    print(f"  Output           : {output_path}")
    _rule()


# ---------------------------------------------------------------------------
# Single-month run
# ---------------------------------------------------------------------------

def _load_hours(hours_path: str, month: str) -> "pd.DataFrame":
    """
    Load employee hours for one month.

    Priority:
      1. Andromeda payroll-detail PDF (hours1.pdf / MM-YY.pdf) — most accurate
      2. Billing Excel (.xlsx / .xls with 100%/125%/… columns)
      3. Generic Excel fallback
      4. Daily payroll PDF (legacy)

    When a payroll PDF is the primary source, any Excel file in the same
    folder is also loaded and used to fill in employees missing from the PDF.

    A "source" column is added: "PDF" | "Excel".
    """
    import os, pandas as _pd

    ext = hours_path.lower()
    folder = os.path.dirname(hours_path)
    mm, yyyy = month[:2], month[3:]
    yy = yyyy[2:]
    monthly_pdf_name = f"{mm}-{yy}.pdf"

    # ── Case 1: primary is a payroll-detail PDF ───────────────────────────────
    is_payroll_pdf = (
        ext.endswith(".pdf") and
        any(os.path.basename(hours_path) == n
            for n in ("hours1.pdf", monthly_pdf_name))
    )

    if is_payroll_pdf:
        pdf_hours = load_hours_from_payroll_pdf(hours_path, month)
        if not pdf_hours.empty:
            pdf_hours["source"] = "PDF"

        # Load Excel: authoritative source for client / site / country metadata.
        # The PDF section-header extraction can misassign employees to the wrong
        # client section; the XLS לקוח/אתר columns are always correct.
        excel_hours = _pd.DataFrame()
        for xname in ("hours.xlsx", "hours.xls"):
            xp = os.path.join(folder, xname)
            if os.path.exists(xp):
                excel_hours = load_hours_from_billing_xlsx(xp, month)
                if excel_hours.empty:
                    excel_hours = load_hours_from_xlsx(xp, month)
                break

        if pdf_hours.empty and excel_hours.empty:
            return _pd.DataFrame()

        if pdf_hours.empty:
            excel_hours["source"] = "Excel"
            return excel_hours

        if not excel_hours.empty:
            # Build employee_id → {client, site, country} map from XLS
            meta_cols = [c for c in ("client", "site", "country")
                         if c in excel_hours.columns]
            xls_meta: dict = (
                excel_hours
                .set_index("employee_id")[meta_cols]
                .to_dict("index")
            )

            # Override PDF client/site/country with XLS values per employee_id.
            # PDF is used for hours only (h100/h125/h150/work_days).
            for col in meta_cols:
                pdf_hours[col] = pdf_hours["employee_id"].apply(
                    lambda eid, c=col: xls_meta.get(str(eid), {}).get(c) or ""
                )

            # Carry h175/h200 from Excel — PDF only extracts h100/h125/h150.
            for ot_col in ("h175", "h200"):
                if ot_col in excel_hours.columns:
                    ot_map = (
                        excel_hours.groupby("employee_id")[ot_col]
                        .sum()
                        .to_dict()
                    )
                    pdf_hours[ot_col] = (
                        pdf_hours["employee_id"]
                        .apply(lambda eid, c=ot_col: ot_map.get(str(eid), 0.0))
                        .fillna(0.0)
                    )

            # Add employees present in XLS but absent from PDF
            pdf_ids = set(pdf_hours["employee_id"].unique())
            excel_only = excel_hours[~excel_hours["employee_id"].isin(pdf_ids)].copy()
            excel_only["source"] = "Excel"
        else:
            excel_only = _pd.DataFrame()

        if excel_only.empty:
            return pdf_hours

        # Align columns before concat
        all_cols = list(dict.fromkeys(
            list(pdf_hours.columns) + list(excel_only.columns)
        ))
        return _pd.concat(
            [pdf_hours.reindex(columns=all_cols),
             excel_only.reindex(columns=all_cols)],
            ignore_index=True,
        )

    # ── Case 2: primary is an Excel file ─────────────────────────────────────
    if ext.endswith((".xlsx", ".xls")):
        h = load_hours_from_billing_xlsx(hours_path, month)
        if not h.empty:
            h["source"] = "Excel"
            return h
        h = load_hours_from_xlsx(hours_path, month)
        h["source"] = "Excel"
        return h

    # ── Case 3: legacy daily payroll PDF ─────────────────────────────────────
    h = load_hours_from_pdf(hours_path, month)
    h["source"] = "PDF"
    return h


def _load_costs(costs_path: str):
    if costs_path.lower().endswith(".pdf"):
        # Try Andromeda detailed PDF (passport-column detection) first.
        # Falls back to generic table-based PDF loader when that returns empty.
        c = load_costs_pdf(costs_path)
        if not c.empty:
            return c
        return load_costs_simple_pdf(costs_path)
    return load_costs_xlsx(costs_path)


def run_single(month: str, threshold: float) -> bool:
    hours_path, costs_path = _find_month_folder(month)
    if hours_path is None:
        print(f"  ERROR: hours.pdf/xlsx or costs.xlsx not found for month {month}")
        print(f"  Expected: {os.path.join(MONTHS_ROOT, month)}/")
        return False

    output_path = _smart_output_path([month])

    _rule()
    print(f"  COST ANALYSIS — {month}")
    _rule()
    print(f"  Hours : {hours_path}")
    print(f"  Costs : {costs_path}")
    print()

    print("  Parsing hours file...")
    hours_df = _load_hours(hours_path, month)
    print(f"  → {len(hours_df)} rows ({hours_df['employee_id'].nunique()} employees, "
          f"{hours_df['site'].nunique()} sites)")

    costs_label = "costs.pdf" if costs_path.endswith(".pdf") else "costs.xlsx"
    print(f"  Loading {costs_label}...")
    costs_df = _load_costs(costs_path)
    print(f"  → {len(costs_df)} employees, total ₪{costs_df['employer_cost'].sum():,.0f}")

    print("  Merging and allocating costs...")
    worked_days_s = compute_worked_days(hours_path)
    merged   = merge_and_allocate(hours_df, costs_df, month,
                                  worked_days_series=worked_days_s)
    sheets   = build_sheets(merged)
    warnings = detect_warnings(hours_df, costs_df, merged, month, threshold)
    export_to_excel(output_path, sheets, warnings)

    _sep("EMPLOYEE COST (top 15)")
    _print_employee_preview(merged)

    _sep("COST BY SITE (top 8)")
    _print_site_preview(sheets)

    _sep("WARNINGS")
    _print_warnings(warnings)

    # Hebrew professional report (same path, replaces plain export)
    export_hebrew_report(output_path, sheets, warnings, title_suffix=month)

    emp_sheet = sheets.get("employee_summary")
    avg_cph   = float(emp_sheet["cost_per_hour"].mean()) if emp_sheet is not None and not emp_sheet.empty else 0.0

    _print_final_summary(
        months          = [month],
        total_employees = merged["employee_id"].nunique() if not merged.empty else 0,
        total_hours     = float(hours_df["total_hours"].sum()) if not hours_df.empty else 0.0,
        total_cost      = float(costs_df["employer_cost"].sum()) if not costs_df.empty else 0.0,
        avg_cph         = avg_cph,
        output_path     = output_path,
        n_warnings      = len(warnings),
    )
    return True


# ---------------------------------------------------------------------------
# All-months run → single combined output file
# ---------------------------------------------------------------------------

def run_all(threshold: float, year: str | None = None) -> None:
    import pandas as pd

    months = _available_months()
    if year:
        months = [m for m in months if m.endswith(f"-{year}")]
    if not months:
        target = f"year {year}" if year else MONTHS_ROOT
        print(f"No months with hours + costs found for {target}.")
        return

    print(f"Found {len(months)} months: {', '.join(months)}")
    print()

    all_merged:   list[pd.DataFrame] = []
    all_hours:    list[pd.DataFrame] = []
    all_costs:    list[pd.DataFrame] = []
    failed:       list[str]          = []

    for month in months:
        hours_path, costs_path = _find_month_folder(month)
        try:
            h  = _load_hours(hours_path, month)
            c  = _load_costs(costs_path)
            wd = compute_worked_days(hours_path)
            m  = merge_and_allocate(h, c, month, worked_days_series=wd)
            all_merged.append(m)
            all_hours.append(h)
            all_costs.append(c)
            print(
                f"  ✓ {month}  "
                f"emp={h['employee_id'].nunique()}  "
                f"hours={h['total_hours'].sum():.0f}h  "
                f"cost=₪{c['employer_cost'].sum():,.0f}"
            )
        except Exception as e:
            print(f"  ✗ {month}  {e}")
            failed.append(month)

    if not all_merged:
        print("\nNo data processed successfully.")
        return

    combined_merged = pd.concat(all_merged, ignore_index=True)
    combined_hours  = pd.concat(all_hours,  ignore_index=True)
    combined_costs  = pd.concat(all_costs,  ignore_index=True)

    sheets   = build_sheets(combined_merged)
    warnings = pd.concat(
        [detect_warnings(
            all_hours[i], all_costs[i], all_merged[i],
            months[i], threshold,
         )
         for i in range(len(all_merged))],
        ignore_index=True,
    ).drop_duplicates()

    output_path = _smart_output_path(months)

    # Hebrew master report
    years = sorted({m[3:] for m in months})
    year_str = "_".join(years)
    hebrew_path = os.path.join(MASTER_OUT, f"cost_report_{year_str}.xlsx")
    os.makedirs(MASTER_OUT, exist_ok=True)
    export_hebrew_report(hebrew_path, sheets, warnings,
                         title_suffix=" | ".join(years))

    if failed:
        print(f"\n  Failed months: {', '.join(failed)}")

    emp_sheet = sheets.get("employee_summary")
    avg_cph   = float(emp_sheet["cost_per_hour"].mean()) if emp_sheet is not None and not emp_sheet.empty else 0.0

    _print_final_summary(
        months          = months,
        total_employees = combined_merged["employee_id"].nunique(),
        total_hours     = float(combined_hours["total_hours"].sum()),
        total_cost      = float(combined_costs["employer_cost"].sum()),
        avg_cph         = avg_cph,
        output_path     = hebrew_path,
        n_warnings      = len(warnings),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Employee cost analysis by employee_id")
    parser.add_argument(
        "--month",
        default=None,
        metavar="MM-YYYY",
        help="Month to analyze (default: latest available)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run analysis for all available months → single combined output file",
    )
    parser.add_argument(
        "--year",
        default=None,
        metavar="YYYY",
        help="Filter to a specific year (e.g. 2025); use with --all",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=200.0,
        metavar="N",
        help="Cost-per-hour warning threshold (default 200)",
    )
    args = parser.parse_args()

    if args.all:
        run_all(threshold=args.threshold, year=args.year)
    else:
        month = args.month
        if month is None:
            available = _available_months()
            if not available:
                print(f"No months found. Place hours.pdf/xlsx + costs.xlsx in {MONTHS_ROOT}/MM-YYYY/")
                sys.exit(1)
            month = available[-1]
            print(f"No --month specified. Using latest: {month}\n")
        run_single(month, threshold=args.threshold)
