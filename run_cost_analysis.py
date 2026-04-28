"""
run_cost_analysis.py — CLI runner for employee cost analysis.

Usage:
    python run_cost_analysis.py                     # latest available month
    python run_cost_analysis.py --month 02-2025     # specific month
    python run_cost_analysis.py --month 02-2025 --threshold 250
    python run_cost_analysis.py --all               # all available months

Output:
    output/monthly/MM-YYYY/cost_analysis.xlsx

Reads from:
    data/MM-YYYY/hours.pdf
    data/MM-YYYY/costs.xlsx
"""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from core.cost_analysis import run_month, load_hours_from_pdf, load_costs_xlsx
from pipeline import DATA_ROOT, OUTPUT_ROOT

MONTHLY_OUT = os.path.join(OUTPUT_ROOT, "monthly")
W = 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sep(title: str = "") -> None:
    if title:
        print(f"\n{'─' * 4} {title} {'─' * max(0, W - 6 - len(title))}")
    else:
        print("─" * W)


def _rule() -> None:
    print("═" * W)


def _find_month_folder(month: str) -> tuple[str | None, str | None]:
    """
    Locate hours.pdf and costs.xlsx for a month.
    Checks data/<month>/ and data/months/<month>/.
    Returns (pdf_path, costs_path) or (None, None).
    """
    candidates = [
        os.path.join(DATA_ROOT, month),
        os.path.join(DATA_ROOT, "months", month),
    ]
    for folder in candidates:
        if not os.path.isdir(folder):
            continue
        pdf   = os.path.join(folder, "hours.pdf")
        costs = os.path.join(folder, "costs.xlsx")
        if os.path.exists(pdf) and os.path.exists(costs):
            return pdf, costs
    return None, None


def _available_months() -> list[str]:
    """Return all months that have hours.pdf + costs.xlsx."""
    import re
    pat = re.compile(r"^\d{2}-\d{4}$")
    found: list[str] = []
    for root in (DATA_ROOT, os.path.join(DATA_ROOT, "months")):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if not pat.match(name):
                continue
            folder = os.path.join(root, name)
            if (os.path.exists(os.path.join(folder, "hours.pdf")) and
                    os.path.exists(os.path.join(folder, "costs.xlsx"))):
                if name not in found:
                    found.append(name)

    # Sort chronologically (MM-YYYY → parse as datetime)
    import pandas as pd
    import datetime
    def _key(m: str) -> datetime.date:
        try:
            mm, yy = int(m[:2]), int(m[3:])
            return datetime.date(yy, mm, 1)
        except Exception:
            return datetime.date.min
    return sorted(found, key=_key)


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def _print_summary(s: dict) -> None:
    print(f"  Month         : {s['month']}")
    print(f"  Employees PDF : {s['employees_hours']}")
    print(f"  Employees XLSX: {s['employees_costs']}")
    print(f"  Matched       : {s['employees_merged']}")
    print(f"  Total hours   : {s['total_hours']:,.2f}h")
    print(f"  Total cost    : ₪{s['total_cost']:>12,.0f}")
    print(f"  Avg cost/h    : ₪{s['avg_cost_per_hour']:,.0f}")
    print(f"  Warnings      : {s['warnings']}")
    print(f"  Output        : {s['output_path']}")


def _print_employee_preview(hours_df, costs_df, merged) -> None:
    """Show a quick table of per-employee cost for the CLI."""
    import pandas as pd

    if merged.empty:
        return

    emp_tbl = (
        merged
        .groupby(["employee_id", "employee_name"], as_index=False)
        .agg(
            total_hours  =("total_hours",   "sum"),
            employer_cost=("employer_cost", "first"),
            allocated_cost=("allocated_cost","sum"),
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
    site_df = sheets.get("site_cost")
    if site_df is None or site_df.empty:
        return

    top = (
        site_df
        .groupby("site", as_index=False)
        .agg(hours=("hours","sum"), cost=("allocated_cost","sum"))
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
    # Show max 20 — full list goes into the Excel warnings sheet
    shown = warnings_df.head(20)
    for _, r in shown.iterrows():
        print(f"  ⚠  [{r['employee_id']}] {r['issue']}")
    if len(warnings_df) > 20:
        print(f"  ... and {len(warnings_df) - 20} more — see warnings sheet in Excel")


# ---------------------------------------------------------------------------
# Single-month run
# ---------------------------------------------------------------------------

def run_single(month: str, threshold: float) -> bool:
    pdf_path, costs_path = _find_month_folder(month)
    if pdf_path is None:
        print(f"  ERROR: hours.pdf or costs.xlsx not found for month {month}")
        return False

    month_out_dir = os.path.join(MONTHLY_OUT, month)
    os.makedirs(month_out_dir, exist_ok=True)
    output_path = os.path.join(month_out_dir, "cost_analysis.xlsx")

    _rule()
    print(f"  COST ANALYSIS — {month}")
    _rule()
    print(f"  PDF   : {pdf_path}")
    print(f"  Costs : {costs_path}")
    print()

    print("  Parsing hours PDF...")
    hours_df = load_hours_from_pdf(pdf_path, month)
    print(f"  → {len(hours_df)} rows ({hours_df['employee_id'].nunique()} employees, "
          f"{hours_df['site'].nunique()} sites)")

    print("  Loading costs.xlsx...")
    costs_df = load_costs_xlsx(costs_path)
    print(f"  → {len(costs_df)} employees, total ₪{costs_df['employer_cost'].sum():,.0f}")

    print("  Merging and allocating costs...")
    from core.cost_analysis import merge_and_allocate, build_sheets, detect_warnings, export_to_excel

    merged   = merge_and_allocate(hours_df, costs_df, month)
    sheets   = build_sheets(merged)
    warnings = detect_warnings(hours_df, costs_df, merged, month, threshold)
    export_to_excel(output_path, sheets, warnings)

    _sep("EMPLOYEE COST (top 15)")
    _print_employee_preview(hours_df, costs_df, merged)

    _sep("COST BY SITE (top 8)")
    _print_site_preview(sheets)

    _sep("WARNINGS")
    _print_warnings(warnings)

    _sep("SUMMARY")
    summary = {
        "month":             month,
        "employees_hours":   hours_df["employee_id"].nunique(),
        "employees_costs":   len(costs_df),
        "employees_merged":  merged["employee_id"].nunique() if not merged.empty else 0,
        "total_hours":       round(float(hours_df["total_hours"].sum()), 2),
        "total_cost":        round(float(costs_df["employer_cost"].sum()), 2),
        "avg_cost_per_hour": round(
            float(sheets["employee_cost"]["cost_per_hour"].mean()), 2
        ) if not sheets["employee_cost"].empty else 0,
        "warnings":          len(warnings),
        "output_path":       output_path,
    }
    _print_summary(summary)
    _rule()
    return True


# ---------------------------------------------------------------------------
# All-months run
# ---------------------------------------------------------------------------

def run_all(threshold: float) -> None:
    months = _available_months()
    if not months:
        print("No months with hours.pdf + costs.xlsx found.")
        return

    print(f"Found {len(months)} months: {', '.join(months)}")
    print()

    all_ok, all_fail = [], []
    for month in months:
        pdf_path, costs_path = _find_month_folder(month)
        output_path = os.path.join(MONTHLY_OUT, month, "cost_analysis.xlsx")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        try:
            summary = run_month(pdf_path, costs_path, month, output_path, threshold)
            print(
                f"  ✓ {month}  "
                f"emp={summary['employees_merged']}  "
                f"hours={summary['total_hours']:.0f}h  "
                f"cost=₪{summary['total_cost']:,.0f}  "
                f"warn={summary['warnings']}"
            )
            all_ok.append(month)
        except Exception as e:
            print(f"  ✗ {month}  {e}")
            all_fail.append(month)

    print()
    print(f"Done: {len(all_ok)} OK, {len(all_fail)} failed")
    if all_fail:
        print(f"Failed months: {', '.join(all_fail)}")


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
        help="Run analysis for all available months",
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
        run_all(threshold=args.threshold)
    else:
        month = args.month
        if month is None:
            available = _available_months()
            if not available:
                print("No months found. Place hours.pdf + costs.xlsx in data/MM-YYYY/")
                sys.exit(1)
            month = available[-1]   # default = latest month
            print(f"No --month specified. Using latest: {month}\n")
        run_single(month, threshold=args.threshold)
