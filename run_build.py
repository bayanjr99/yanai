"""
run_build.py — standalone data pipeline runner (no Streamlit required).

Usage:
    python run_build.py
    python run_build.py --onedrive "C:/Users/Me/OneDrive/BI_Data"
    python run_build.py --data-root /custom/data/path
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

import pandas as pd

# Make sure the billing_system package is importable when run from any directory
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from pipeline import (
    DATA_ROOT, OUTPUT_ROOT, MASTER_DIR,
    MASTER_PATH, MASTER_XLSX, CALENDAR_XLSX,
    list_available_months,
    build_master_full,
    build_summary_tables,
    build_calendar,
    validate_master,
    get_all_data,
)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _sep() -> None:
    print("-" * 60, flush=True)


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def run(data_root: str = DATA_ROOT, onedrive_path: str | None = None) -> int:
    """
    Run the full build and export.

    Returns 0 on success, 1 if all months failed.
    """
    _sep()
    _log("Starting master_full build...")
    _log(f"Data root : {os.path.abspath(data_root)}")
    _log(f"Output    : {os.path.abspath(MASTER_PATH)}")
    _log(f"Excel     : {os.path.abspath(MASTER_XLSX)}")
    _sep()

    # ── Discover months ───────────────────────────────────────────────────────
    months = list_available_months(data_root)
    if not months:
        _log("ERROR: No month folders found. Expected data/MM-YYYY/ with hours.pdf/xlsx + costs.xlsx")
        return 1

    _log(f"Found {len(months)} month(s): {', '.join(months)}")
    _sep()

    # ── Build master ──────────────────────────────────────────────────────────
    t0 = time.time()
    master, errors = build_master_full(data_root)

    for err in errors:
        _log(f"WARN  {err}")

    if master.empty:
        _log("ERROR: master_full is empty — no data was computed.")
        return 1

    elapsed = time.time() - t0
    _sep()
    _log(f"Build complete in {elapsed:.1f}s")
    _log(f"  Rows   : {len(master):,}")
    _log(f"  Months : {master['month'].nunique()} ({', '.join(sorted(master['month'].unique()))})")
    _log(f"  Clients: {master['client'].nunique()}")
    _log(f"  Columns: {', '.join(master.columns.tolist())}")
    _sep()

    # ── Financials summary ────────────────────────────────────────────────────
    total_billing = float(master["billing"].sum()) if "billing" in master.columns else 0
    total_cost    = float(master["cost"].sum())    if "cost"    in master.columns else 0
    total_profit  = float(master["profit"].sum())  if "profit"  in master.columns else 0
    margin        = total_profit / total_billing * 100 if total_billing > 0 else 0

    _log(f"  Total billing : ₪{total_billing:>12,.0f}")
    _log(f"  Total cost    : ₪{total_cost:>12,.0f}")
    _log(f"  Total profit  : ₪{total_profit:>12,.0f}")
    _log(f"  Overall margin: {margin:>11.1f}%")
    _sep()

    # ── Validation ────────────────────────────────────────────────────────────
    warnings = validate_master(master)
    if warnings:
        _log("Validation warnings:")
        for w in warnings:
            _log(f"  ! {w}")
    else:
        _log("Validation: OK (no issues)")
    _sep()

    # ── Output files ──────────────────────────────────────────────────────────
    _log(f"Saved: {os.path.abspath(MASTER_PATH)}")
    _log(f"Saved: {os.path.abspath(MASTER_XLSX)}")

    summaries_path = os.path.join(MASTER_DIR, "summaries.xlsx")
    if os.path.exists(summaries_path):
        _log(f"Saved: {os.path.abspath(summaries_path)}")

    if os.path.exists(CALENDAR_XLSX):
        cal = build_calendar(master)
        _log(f"Saved: {os.path.abspath(CALENDAR_XLSX)}  ({len(cal):,} days, {cal['year'].nunique() if not cal.empty else 0} years)")

    # ── Optional OneDrive copy ────────────────────────────────────────────────
    if onedrive_path:
        _sep()
        _log(f"Copying to OneDrive: {onedrive_path}")
        try:
            os.makedirs(onedrive_path, exist_ok=True)
            for src in (MASTER_XLSX, CALENDAR_XLSX, summaries_path):
                if os.path.exists(src):
                    dest = os.path.join(onedrive_path, os.path.basename(src))
                    shutil.copy2(src, dest)
                    _log(f"  Copied: {dest}")
        except Exception as e:
            _log(f"  ERROR copying to OneDrive: {e}")

    _sep()
    _log("Done. Files are ready for Power BI import.")
    return 0


# ---------------------------------------------------------------------------
# Summary tables preview
# ---------------------------------------------------------------------------

def _print_summary(master: pd.DataFrame) -> None:
    tables = build_summary_tables(master)
    for name, df in tables.items():
        print(f"\n{'='*50}")
        print(f"  {name}")
        print(f"{'='*50}")
        print(df.to_string(index=False, max_rows=20))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild master_full dataset for Power BI")
    parser.add_argument(
        "--data-root",
        default=DATA_ROOT,
        help=f"Path to data folder (default: {DATA_ROOT})",
    )
    parser.add_argument(
        "--onedrive",
        default=None,
        metavar="PATH",
        help="Optional OneDrive folder to also copy outputs to",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print summary tables to console after build",
    )
    args = parser.parse_args()

    exit_code = run(data_root=args.data_root, onedrive_path=args.onedrive)

    if args.summary and exit_code == 0:
        master = get_all_data()
        _print_summary(master)

    sys.exit(exit_code)
