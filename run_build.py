"""
run_build.py — standalone data pipeline runner (no Streamlit required).

Refreshes the canonical cache at ``output/cache/processed_data.parquet``
(read by the dashboard) and the legacy ``output/master/master_full.parquet``
(kept for Power BI export users).

Usage:
    python run_build.py                # rebuild everything
    python run_build.py --cache-only   # only the dashboard cache (faster)
    python run_build.py --legacy-only  # only the Power BI master file
    python run_build.py --onedrive "C:/Users/Me/OneDrive/BI_Data"
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time


# Make sure the billing_system package is importable when run from any directory
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _sep() -> None:
    print("-" * 60, flush=True)


# ---------------------------------------------------------------------------
# Canonical cache build (dashboard source of truth)
# ---------------------------------------------------------------------------

def build_cache() -> int:
    """Rebuild ``output/cache/processed_data.parquet`` via the modern
    Andromeda + standards + income pipeline. Returns exit code."""
    _sep()
    _log("Building dashboard cache (processed_data.parquet + income.parquet)...")
    _sep()

    try:
        from core.preprocessor import build_and_save
    except ImportError as e:
        _log(f"ERROR: cannot import preprocessor: {e}")
        return 1

    t0 = time.time()
    try:
        build_and_save()
    except Exception as e:
        _log(f"ERROR during build: {e}")
        import traceback
        traceback.print_exc()
        return 1
    elapsed = time.time() - t0

    _sep()
    _log(f"Cache build complete in {elapsed:.1f}s")
    cache_path = os.path.join(_HERE, "output", "cache", "processed_data.parquet")
    if os.path.exists(cache_path):
        size = os.path.getsize(cache_path) / 1024
        _log(f"  output/cache/processed_data.parquet — {size:,.1f} KB")
    return 0


# ---------------------------------------------------------------------------
# Legacy master_full build (Power BI export — older format, optional)
# ---------------------------------------------------------------------------

def build_legacy_master(data_root: str, onedrive_path: str | None) -> int:
    """Run the legacy ``pipeline.build_master_full`` for Power BI users.

    Skips gracefully if the project has migrated away from the legacy file
    naming (``hours.pdf`` / ``agreements.xlsx``) — those users only need
    the cache built by ``build_cache``.
    """
    _sep()
    _log("Building legacy master_full.parquet (for Power BI)...")
    _sep()

    try:
        from pipeline import (
            DATA_ROOT, MASTER_DIR, MASTER_PATH, MASTER_XLSX, CALENDAR_XLSX,
            list_available_months, build_master_full, validate_master,
            build_calendar,
        )
    except ImportError as e:
        _log(f"WARN: legacy pipeline unavailable ({e}); skipping.")
        return 0

    months = list_available_months(data_root)
    if not months:
        _log("WARN: no month folders found for legacy pipeline; skipping.")
        return 0

    _log(f"Found {len(months)} month(s): {', '.join(months)}")

    t0 = time.time()
    master, errors = build_master_full(data_root)

    skipped = 0
    for err in errors:
        # Treat "missing agreements/empty folder" as expected when migrated
        if "תיקיית חודש לא קיימת" in err or "לא נמצא קובץ הסכמים" in err:
            skipped += 1
            continue
        _log(f"WARN  {err}")

    if master.empty:
        _log(f"INFO: legacy build produced no rows ({skipped} month(s) "
             f"skipped — likely migrated to new format). This is expected; "
             f"the dashboard reads from the cache, not master_full.")
        return 0

    elapsed = time.time() - t0
    _sep()
    _log(f"Legacy build complete in {elapsed:.1f}s")
    _log(f"  Rows: {len(master):,}  Months: {master['month'].nunique()}  "
         f"Clients: {master['client'].nunique()}")

    # Financials summary
    total_billing = float(master["billing"].sum()) if "billing" in master.columns else 0
    total_cost    = float(master["cost"].sum())    if "cost"    in master.columns else 0
    total_profit  = float(master["profit"].sum())  if "profit"  in master.columns else 0
    margin        = total_profit / total_billing * 100 if total_billing > 0 else 0
    _log(f"  Billing: ₪{total_billing:>12,.0f}   Cost: ₪{total_cost:>12,.0f}   "
         f"Profit: ₪{total_profit:>12,.0f}   Margin: {margin:.1f}%")

    # Validation
    warnings = validate_master(master)
    if warnings:
        _log("Validation warnings:")
        for w in warnings:
            _log(f"  ! {w}")

    _log(f"Saved: {os.path.abspath(MASTER_PATH)}")
    _log(f"Saved: {os.path.abspath(MASTER_XLSX)}")

    if os.path.exists(CALENDAR_XLSX):
        cal = build_calendar(master)
        _log(f"Saved: {os.path.abspath(CALENDAR_XLSX)} ({len(cal):,} days)")

    # Optional OneDrive copy
    if onedrive_path:
        _sep()
        _log(f"Copying to OneDrive: {onedrive_path}")
        try:
            os.makedirs(onedrive_path, exist_ok=True)
            summaries_path = os.path.join(MASTER_DIR, "summaries.xlsx")
            for src in (MASTER_XLSX, CALENDAR_XLSX, summaries_path):
                if os.path.exists(src):
                    dest = os.path.join(onedrive_path, os.path.basename(src))
                    shutil.copy2(src, dest)
                    _log(f"  Copied: {dest}")
        except Exception as e:
            _log(f"  ERROR copying to OneDrive: {e}")

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Rebuild the billing_system data cache and exports.",
    )
    parser.add_argument(
        "--data-root", default="data",
        help="Path to data folder (default: data/)",
    )
    parser.add_argument(
        "--cache-only", action="store_true",
        help="Only rebuild the dashboard cache; skip the legacy Power BI master.",
    )
    parser.add_argument(
        "--legacy-only", action="store_true",
        help="Only rebuild the legacy Power BI master_full; skip the cache.",
    )
    parser.add_argument(
        "--onedrive", default=None, metavar="PATH",
        help="Optional OneDrive folder to also copy legacy outputs to.",
    )
    args = parser.parse_args()

    rc = 0

    if not args.legacy_only:
        rc = build_cache()
        if rc != 0:
            sys.exit(rc)

    if not args.cache_only:
        # Legacy build is best-effort — warnings, not failures
        build_legacy_master(args.data_root, args.onedrive)

    _sep()
    _log("Done. Dashboard cache is ready; refresh the browser.")
    sys.exit(rc)
