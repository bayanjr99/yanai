"""
core/preprocessor.py — One-shot data processing pipeline.

Parses all monthly source files (PDF + Excel), runs the full cost-analysis
pipeline, applies country mapping, integrates standards (תקן.xlsx), and
saves everything to Parquet.  The dashboard loads from this file instead
of re-parsing PDFs on every run.

Outputs
-------
  output/cache/processed_data.parquet   — full merged dataset (one row per employee × site × month)
  output/cache/warnings.parquet         — data-quality warnings
  output/cache/build_meta.json          — build statistics and per-month log
"""

from __future__ import annotations

import os
import re
import sys
import warnings as _warnings
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent.parent   # billing_system/
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

CACHE_DIR      = _HERE / "output" / "cache"
CACHE_PATH     = CACHE_DIR / "processed_data.parquet"
WARN_PATH      = CACHE_DIR / "warnings.parquet"
META_PATH      = CACHE_DIR / "build_meta.json"
DATA_ROOT      = _HERE / "data"
STANDARDS_PATH = DATA_ROOT / "תקן.xlsx"

_MONTH_RE = re.compile(r"^\d{2}-\d{4}$")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _mkey(m: str) -> tuple:
    try:
        mm, yy = str(m).split("-")
        return int(yy), int(mm)
    except Exception:
        return 9999, 99


def _to_float(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


# ── Staleness detection ───────────────────────────────────────────────────────

def _source_files(data_root: Path = DATA_ROOT) -> list[Path]:
    files: list[Path] = []
    for folder in data_root.iterdir():
        if not folder.is_dir() or not _MONTH_RE.match(folder.name):
            continue
        for f in folder.iterdir():
            if f.suffix.lower() in (".pdf", ".xlsx", ".xls"):
                files.append(f)
    # Also watch the standards file
    if STANDARDS_PATH.exists():
        files.append(STANDARDS_PATH)
    return files


def _max_source_mtime(data_root: Path = DATA_ROOT) -> float:
    mtimes = []
    for f in _source_files(data_root):
        try:
            mtimes.append(f.stat().st_mtime)
        except OSError:
            pass
    return max(mtimes, default=0.0)


def needs_refresh(data_root: Path = DATA_ROOT) -> bool:
    if not CACHE_PATH.exists():
        return True
    return _max_source_mtime(data_root) > CACHE_PATH.stat().st_mtime


def cache_mtime() -> float:
    try:
        return CACHE_PATH.stat().st_mtime
    except OSError:
        return 0.0


# ── Country mapping ───────────────────────────────────────────────────────────

def _build_country_map(data_root: Path = DATA_ROOT) -> dict[str, tuple[str, str]]:
    """
    Scan ALL months' Excel hours files to build a global
      { employee_id → (country, source) }
    mapping.

    Uses Excel only (not PDF) because only Excel files have the מדינה column.
    If the same employee appears in multiple months, the first non-empty value wins.
    Conflicts (different countries for the same employee) are logged.
    """
    from core.cost_analysis import load_hours_from_billing_xlsx

    country_map: dict[str, tuple[str, str]] = {}
    conflicts:   dict[str, list[str]] = {}

    for folder in sorted(data_root.iterdir(), key=lambda p: _mkey(p.name)):
        if not folder.is_dir() or not _MONTH_RE.match(folder.name):
            continue
        month = folder.name
        for xname in ("hours.xlsx", "hours.xls"):
            xp = folder / xname
            if not xp.exists():
                continue
            try:
                h = load_hours_from_billing_xlsx(str(xp), month)
            except Exception:
                continue
            if h.empty or "country" not in h.columns:
                break
            for _, row in h.iterrows():
                emp_id  = str(row["employee_id"]).strip()
                country = str(row.get("country", "")).strip()
                if not country or country in ("nan", "None"):
                    continue
                if emp_id not in country_map:
                    country_map[emp_id] = (country, "excel")
                elif country_map[emp_id][0] != country:
                    conflicts.setdefault(emp_id, [country_map[emp_id][0]])
                    conflicts[emp_id].append(country)
            break   # found the Excel file for this month

    return country_map, conflicts


def _apply_country(
    df: pd.DataFrame,
    country_map: dict[str, tuple[str, str]],
) -> pd.DataFrame:
    """
    Apply the global country map to every row in df.
    Adds/overwrites `country` and `country_source` columns.
    """
    df = df.copy()

    def _lookup(emp_id: str):
        return country_map.get(str(emp_id).strip(), ("Unknown", "default"))

    mapped       = df["employee_id"].map(_lookup)
    df["country"]        = [v[0] for v in mapped]
    df["country_source"] = [v[1] for v in mapped]
    return df


# ── Standards helpers removed — see core/standards_v2.py ─────────────────────
# (deleted: _LEGAL_SUFFIXES, _norm_name, _compact, _parse_hourly_rate,
#  _parse_std_hours, _billing_type, load_standards, _MIN_TOKEN_OVERLAP,
#  _client_score, _SCORE_LABEL, _SCORE_CONFIDENCE, _find_standard,
#  _apply_standards — logic replaced by core/standards_v2.apply_standards)


# ── Client / site fill ───────────────────────────────────────────────────────

_COSTS_NULL = {"", "nan", "none", "null", "n/a", "-"}


def _build_costs_client_site_map(data_root: Path = DATA_ROOT) -> dict[str, dict]:
    """
    Scan all months' costs.xlsx/costs.pdf files and build a global
      employee_id → {"client": ..., "site": ...}
    map.  This is the authoritative Excel source for client/site per employee.

    If the same employee appears in multiple months the first non-empty value
    for each field wins (earliest month order).
    """
    from core.cost_analysis import load_costs_xlsx, load_costs_pdf

    emp_map: dict[str, dict] = {}

    for folder in sorted(data_root.iterdir(), key=lambda p: _mkey(p.name)):
        if not folder.is_dir() or not _MONTH_RE.match(folder.name):
            continue

        costs_df: pd.DataFrame | None = None
        for cname in ("costs.xlsx", "costs.pdf", "cost.pdf", "cost.xlsx"):
            cp = folder / cname
            if not cp.exists():
                continue
            try:
                costs_df = (
                    load_costs_xlsx(str(cp))
                    if cname.endswith((".xlsx", ".xls"))
                    else load_costs_pdf(str(cp))
                )
                break
            except Exception:
                continue

        if costs_df is None or costs_df.empty:
            continue

        for _, row in costs_df.iterrows():
            emp_id = str(row.get("employee_id", "") or "").strip()
            if not emp_id or emp_id.lower() in _COSTS_NULL:
                continue
            # Remove trailing ".0" from float-read ids
            if emp_id.endswith(".0") and emp_id[:-2].isdigit():
                emp_id = emp_id[:-2]

            client = str(row.get("client", "") or "").strip()
            site   = str(row.get("site",   "") or "").strip()
            if client.lower() in _COSTS_NULL: client = ""
            if site.lower()   in _COSTS_NULL: site   = ""

            if emp_id not in emp_map:
                emp_map[emp_id] = {"client": client, "site": site}
            else:
                if not emp_map[emp_id]["client"] and client:
                    emp_map[emp_id]["client"] = client
                if not emp_map[emp_id]["site"] and site:
                    emp_map[emp_id]["site"] = site

    return emp_map


def _fill_missing_client_site(
    df: pd.DataFrame,
    build_log: list[str],
    excel_map: dict[str, dict] | None = None,
) -> pd.DataFrame:
    """
    Fill / override client and site using two sources in priority order:

      1. Excel (costs.xlsx) map by employee_id  → client_source = "excel"
      2. Cross-month employee map (mode)         → client_source = "filled_from_map"
      3. Still missing                           → client_source = "missing"

    When excel_map is supplied (recommended) it is applied to ALL rows, not only
    empty ones — so Excel is always considered the authoritative source.
    """
    df = df.copy()

    def _clean(s) -> str:
        v = str(s).strip() if pd.notna(s) else ""
        return "" if v.lower() in _COSTS_NULL else v

    df["client"] = df["client"].apply(_clean)
    df["site"]   = df["site"].apply(_clean)

    # Collapse pipeline-level client-name variants (e.g. costs.pdf writes
    # 'ולפמן תעשיות בעמ' for one site while Andromeda Excel uses
    # 'ולפמן תעשיות בע"מ' for the rest — both refer to the same company).
    try:
        from core.client_mapping import canonicalize_pipeline_name
        df["client"] = df["client"].apply(canonicalize_pipeline_name)
    except Exception:
        pass

    # Normalise employee_id (remove trailing ".0")
    def _norm(v) -> str:
        s = str(v).strip()
        if s.endswith(".0") and s[:-2].isdigit():
            s = s[:-2]
        return s

    df["employee_id"] = df["employee_id"].apply(_norm)

    # Initialise source columns
    df["client_source"] = df["client"].apply(lambda v: "pdf" if v else "missing")
    df["site_source"]   = df["site"].apply(  lambda v: "pdf" if v else "missing")

    # ── Priority 1: Excel (costs.xlsx) override ───────────────────────────────
    excel_client_filled = 0
    excel_site_filled   = 0
    if excel_map:
        def _xval(emp_id: str, field: str) -> str:
            return (excel_map.get(emp_id) or {}).get(field, "")

        xl_client = df["employee_id"].map(lambda i: _xval(i, "client"))
        xl_site   = df["employee_id"].map(lambda i: _xval(i, "site"))

        has_xl_c = xl_client.ne("")
        has_xl_s = xl_site.ne("")

        # Override all rows where Excel has a value
        df.loc[has_xl_c, "client"]        = xl_client[has_xl_c]
        df.loc[has_xl_c, "client_source"] = "excel"
        excel_client_filled = int(has_xl_c.sum())

        df.loc[has_xl_s, "site"]        = xl_site[has_xl_s]
        df.loc[has_xl_s, "site_source"] = "excel"
        excel_site_filled = int(has_xl_s.sum())

    # ── Priority 2: cross-month employee map (fallback) ───────────────────────
    has_client = df[df["client"] != ""]
    has_site   = df[df["site"]   != ""]

    def _mode_map(sub: pd.DataFrame, col: str) -> dict:
        return (
            sub.groupby("employee_id")[col]
            .agg(lambda s: s.mode().iloc[0] if len(s) > 0 else "")
            .to_dict()
        )

    client_mode = _mode_map(has_client, "client")
    site_mode   = _mode_map(has_site,   "site")

    mask_c = df["client"] == ""
    df.loc[mask_c, "client"] = df.loc[mask_c, "employee_id"].map(client_mode).fillna("")
    xfilled_c = int((mask_c & (df["client"] != "")).sum())
    df.loc[mask_c & (df["client"] != ""), "client_source"] = "filled_from_map"
    df.loc[df["client"] == "", "client_source"] = "missing"

    mask_s = df["site"] == ""
    df.loc[mask_s, "site"] = df.loc[mask_s, "employee_id"].map(site_mode).fillna("")
    xfilled_s = int((mask_s & (df["site"] != "")).sum())
    df.loc[mask_s & (df["site"] != ""), "site_source"] = "filled_from_map"
    df.loc[df["site"] == "", "site_source"] = "missing"

    still_c = int((df["client"] == "").sum())
    still_s = int((df["site"]   == "").sum())

    build_log.append(
        f"OK client/site fill: "
        f"excel={excel_client_filled}c/{excel_site_filled}s overridden | "
        f"cross-month={xfilled_c}c/{xfilled_s}s filled | "
        f"still missing={still_c}c/{still_s}s"
    )
    return df


# ── Insight fields ────────────────────────────────────────────────────────────

def _compute_insights(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add analytical fields that explain WHY cost is high/low for each row.

    New columns
    -----------
    overtime_ratio   float   (h125+h150+h175+h200) / total_hours  [0..1]
    fee_ratio        float   adjusted_levy / employer_cost         [0..1]
    utilization      float   total_hours / std_hours_month         [0..∞, nan if no std]
    cost_driver      str     pipe-separated Hebrew explanation text
    """
    df = df.copy()

    safe_h = df["total_hours"].replace(0, float("nan"))
    safe_c = df["employer_cost"].replace(0, float("nan"))

    ot_cols = [c for c in ("h125", "h150", "h175", "h200") if c in df.columns]
    df["overtime_ratio"] = (
        df[ot_cols].sum(axis=1) / safe_h if ot_cols else 0.0
    ).round(4)

    df["fee_ratio"] = (
        df["adjusted_levy"] / safe_c if "adjusted_levy" in df.columns else float("nan")
    ).round(4)

    if "std_hours_month" in df.columns:
        df["utilization"] = (df["total_hours"] / df["std_hours_month"].replace(0, float("nan"))).round(3)
    else:
        df["utilization"] = float("nan")

    def _driver(row) -> tuple[str, int]:
        """Return (explanation_string, reason_count) for COST problems only.

        Utilization < 1 is NOT a cost problem — it means the company bills the
        std_hours_month floor and earns extra profit. It belongs in profit_driver.
        """
        reasons: list[str] = []
        ot   = float(row.get("overtime_ratio") or 0)
        hrs  = float(row.get("total_hours")    or 0)
        fee  = float(row.get("fee_ratio")      or 0)

        if ot > 0.3:
            reasons.append(f"שעות נוספות גבוהות ({ot*100:.0f}%)")
        if 0 < hrs < 50:
            reasons.append(f"מעט שעות ({hrs:.0f}ש')")
        if fee > 0.2:
            reasons.append(f"אגרות גבוהות ({fee*100:.0f}%)")

        if not reasons:
            return "תקין", 0
        return " + ".join(reasons), len(reasons)

    result           = df.apply(_driver, axis=1, result_type="expand")
    df["cost_driver"]       = result[0]
    df["cost_driver_count"] = result[1].astype(int)

    # shortage_revenue: extra profit from billing the floor when actual < std
    if "shortage_hours" in df.columns and "hourly_rate" in df.columns:
        df["shortage_revenue"] = (
            df["shortage_hours"].fillna(0) * df["hourly_rate"].fillna(0)
        ).round(2)
    else:
        df["shortage_revenue"] = 0.0

    # profit_driver: POSITIVE signals (low utilization = billing floor profit)
    def _positive(row) -> str:
        util = row.get("utilization")
        if pd.notna(util) and 0 < float(util) < 0.85:
            return f"רווח אקסטרה מתקן ({(1-float(util))*100:.0f}%)"
        return ""

    df["profit_driver"] = df.apply(_positive, axis=1)

    return df


# ── Validation layer ──────────────────────────────────────────────────────────

def _build_warnings(df: pd.DataFrame, excel_only_ids: set[str]) -> pd.DataFrame:
    """
    Generate comprehensive data-quality warnings from the merged dataset.

    Categories:
      missing_client    — no client assigned
      missing_site      — no site assigned
      missing_country   — country = Unknown
      excel_only        — employee exists only in Excel (not in PDF)
      duplicate         — same employee × month × site appears more than once
      zero_hours        — total_hours == 0 with employer_cost > 0
      abnormal_hours    — h100 > 280 (unrealistic for one month)
      no_standard       — no matching rule in תקן.xlsx
      high_shortage     — shortage_hours > 100
    """
    rows: list[dict] = []

    def _w(month: str, emp_id: str, category: str, detail: str) -> None:
        rows.append({"month": month, "employee_id": emp_id,
                     "category": category, "issue": detail})

    # Missing client / site
    for _, r in df[df["client"].astype(str).str.strip().isin(["", "nan", "None"])].iterrows():
        _w(r["month"], r["employee_id"], "missing_client", "חסר לקוח")
    for _, r in df[df["site"].astype(str).str.strip().isin(["", "nan", "None"])].iterrows():
        _w(r["month"], r["employee_id"], "missing_site", "חסר אתר")

    # Missing country
    for _, r in df[df["country"] == "Unknown"].drop_duplicates("employee_id").iterrows():
        _w(r["month"], r["employee_id"], "missing_country", "מדינה לא ידועה")

    # Excel-only employees
    for emp_id in sorted(excel_only_ids):
        month_sample = df[df["employee_id"] == emp_id]["month"].min()
        _w(month_sample or "?", emp_id, "excel_only",
           "עובד קיים רק בקובץ Excel (לא בPDF)")

    # Duplicates
    dup_mask = df.duplicated(subset=["month", "employee_id", "site"], keep=False)
    for _, r in df[dup_mask].drop_duplicates(["month", "employee_id", "site"]).iterrows():
        _w(r["month"], r["employee_id"], "duplicate",
           f"שורה כפולה: עובד+חודש+אתר ({r['site']})")

    # Zero hours with cost
    for _, r in df[(df["total_hours"] == 0) & (df["employer_cost"] > 0)].iterrows():
        _w(r["month"], r["employee_id"], "zero_hours",
           f"0 שעות עם עלות ₪{r['employer_cost']:,.0f}")

    # Abnormal hours (h100 > 280 suggests data error)
    if "h100" in df.columns:
        for _, r in df[df["h100"] > 280].iterrows():
            _w(r["month"], r["employee_id"], "abnormal_hours",
               f"שעות 100% חריגות: {r['h100']:.1f}")

    # No matching standard
    if "std_hours_month" in df.columns:
        no_std = df[df["std_hours_month"].isna()].drop_duplicates(["client", "site"])
        for _, r in no_std.iterrows():
            _w(r["month"], r["employee_id"], "no_standard",
               f"אין תקן לאתר: {r['client']} / {r['site']}")

    # High shortage
    if "shortage_hours" in df.columns:
        for _, r in df[df["shortage_hours"] > 100].iterrows():
            _w(r["month"], r["employee_id"], "high_shortage",
               f"חסר {r['shortage_hours']:.0f}ש' מהתקן")

    result = pd.DataFrame(rows, columns=["month", "employee_id", "category", "issue"])
    return result.drop_duplicates(subset=["month","employee_id","category"]).reset_index(drop=True)


# ── Build pipeline ────────────────────────────────────────────────────────────

def build_and_save(
    data_root: Path = DATA_ROOT,
    progress_cb=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Full processing pipeline. Slow on first run; saves Parquet so subsequent
    loads are instant.

    Steps:
      1. Process all months (hours PDF + cost PDF → merge + levy adjustment)
      2. Build global country_map from all Excel hours files
      3. Apply country_map → country + country_source columns
      4. Load and apply standards (תקן.xlsx)
      5. Build comprehensive warnings
      6. Save to Parquet + JSON meta

    progress_cb: callable(month, current_idx, total) | None
    """
    from run_cost_analysis import (
        _available_months,
        _find_month_folder,
        _load_hours,
        _load_costs,
    )
    from core.cost_analysis import (
        merge_and_allocate,
        compute_worked_days,
        compute_month_working_days,
    )

    _warnings.filterwarnings("ignore")

    months     = sorted(_available_months(), key=_mkey)
    all_merged: list[pd.DataFrame] = []
    build_log:  list[str]          = []
    excel_only_ids: set[str]       = set()

    # ── Step 1: process each month ────────────────────────────────────────────
    from core.andromeda_loader import load_andromeda_hours, find_andromeda_file

    for idx, month in enumerate(months):
        if progress_cb:
            progress_cb(month, idx, len(months))

        hp, cp = _find_month_folder(month)
        if not cp:
            build_log.append(f"SKIP {month}: missing costs file")
            continue

        try:
            # ── Hours: prefer Andromeda Excel (exact per-client-site split) ──
            h: pd.DataFrame | None = None
            src_tag = "Legacy"

            folder = data_root / month
            andro_path = find_andromeda_file(folder)
            if andro_path is not None:
                # Andromeda Excel file EXISTS — must use it.
                # If it fails (locked, corrupt, open in Excel), STOP the build.
                # Silent fallback to PDF would produce incorrect per-client/site costs.
                try:
                    h = load_andromeda_hours(andro_path, month)
                    if h.empty:
                        raise ValueError("loaded DataFrame is empty")
                    src_tag = "AndromedaExcel"
                except Exception as _ae:
                    _lock_hint = (
                        " — הקובץ כנראה פתוח ב-Excel. סגור אותו ונסה שוב."
                        if any(kw in str(_ae).lower()
                               for kw in ("permission", "locked", "נעול", "כותרות"))
                        else ""
                    )
                    raise RuntimeError(
                        f"\n"
                        f"  ❌  קובץ Andromeda Excel קיים אך לא ניתן לקריאה{_lock_hint}\n"
                        f"  קובץ: {andro_path}\n"
                        f"  שגיאה: {_ae}\n"
                        f"\n"
                        f"  ⚠️  לא ניתן לייצר דוח תקין — עלות עובדים לפי לקוח/אתר\n"
                        f"      תהיה שגויה אם נשתמש ב-PDF fallback.\n"
                        f"\n"
                        f"  פעולה נדרשת: סגור את הקובץ ב-Excel והרץ שוב:\n"
                        f"    python -c \"from core.preprocessor import build_and_save; build_and_save()\"\n"
                    ) from _ae
            else:
                # No Andromeda file for this month (expected: 01-2025, 02-2025).
                # Fall back to legacy PDF/XLS loader.
                if hp is None:
                    build_log.append(f"SKIP {month}: no hours file found")
                    continue
                h = _load_hours(hp, month)
                src_tag = "Legacy"

            if h is None or h.empty:
                build_log.append(f"SKIP {month}: hours DataFrame empty")
                continue

            c               = _load_costs(cp)
            wd              = compute_worked_days(hp) if hp else None
            month_work_days = compute_month_working_days(hp) if hp else None

            # ── Medical deduction (ניכויי רשות - ביטוח רפואי) ────────────
            # costs.pdf reports GROSS employer medical insurance; the accounting
            # P&L line is NET of the worker-billed deduction. We read the
            # deduction from the OLD detailed hours.xls (which DOES have a
            # per-component breakdown) and subtract it per employee from BOTH
            # `medical_insurance` and `employer_cost`. This brings the
            # dashboard cost in line with the accounting P&L.
            #
            # If hours.xls is missing for this month (e.g. 01-2025), we leave
            # the gross values in place — better to show slightly-overstated
            # medical than to skip the month entirely.
            try:
                from core.cost_analysis import load_medical_deductions
                xls_path = data_root / month / "hours.xls"
                if xls_path.exists():
                    med_ded = load_medical_deductions(str(xls_path))
                    if not med_ded.empty and "employee_id" in c.columns:
                        c = c.merge(med_ded, on="employee_id", how="left")
                        c["medical_deduction"] = c["medical_deduction"].fillna(0.0)
                        # Subtract from both the medical line AND the total cost
                        if "medical_insurance" in c.columns:
                            c["medical_insurance"] = (
                                c["medical_insurance"] - c["medical_deduction"]
                            ).round(2)
                        if "employer_cost" in c.columns:
                            c["employer_cost"] = (
                                c["employer_cost"] - c["medical_deduction"]
                            ).round(2)
            except Exception as _mde:
                build_log.append(f"WARN {month}: medical deduction load failed — {_mde}")

            m  = merge_and_allocate(
                h, c, month,
                worked_days_series=wd,
                std_days_per_month=month_work_days or 22,
            )

            # Track Excel-only employees for warnings
            if "source" in h.columns:
                excel_only = set(h[h["source"] == "Excel"]["employee_id"].astype(str))
                excel_only_ids.update(excel_only)

            all_merged.append(m)
            multi_site = int((h.groupby("employee_id").size() > 1).sum())
            build_log.append(
                f"OK {month} [{src_tag}]: "
                f"{h['employee_id'].nunique()}emp "
                f"({multi_site} multi-site)  "
                f"{h['total_hours'].sum():.0f}h  "
                f"₪{c['employer_cost'].sum():,.0f}  "
                f"{len(h)} rows"
            )
        except RuntimeError:
            # Andromeda-locked errors are fatal — re-raise immediately
            raise
        except Exception as exc:
            build_log.append(f"ERR {month}: {exc}")

    if not all_merged:
        empty_df = pd.DataFrame()
        empty_w  = pd.DataFrame(columns=["month", "employee_id", "category", "issue"])
        _save(empty_df, empty_w, build_log)
        return empty_df, empty_w

    # Concatenate
    df = pd.concat(all_merged, ignore_index=True)
    df["month"]  = df["month"].astype(str)
    df["source"] = df["source"].fillna("Excel").astype(str) if "source" in df.columns else "Excel"
    df["source_file_type"] = df["source"]  # explicit alias used by dashboard quality check

    for col in (
        "total_hours", "employer_cost", "allocated_cost", "cost_per_hour",
        "adjusted_levy", "full_monthly_levy", "h100", "h125", "h150",
        "h175", "h200", "work_days", "billable_hours",
    ):
        if col not in df.columns:
            df[col] = 0.0
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # cost = allocated fraction of employer_cost per (employee × client × site)
    # employee_cost = full employer_cost for the employee (identical across their rows)
    df["cost"] = df["allocated_cost"].round(2)
    df["employee_cost"] = df["employer_cost"].round(2)

    _full  = df.groupby(["month", "employee_id"])["employee_cost"].first().sum()
    _alloc = float(df["cost"].sum())
    build_log.append(
        f"OK cost: alloc=₪{_alloc:,.0f}, unique_full=₪{_full:,.0f}, diff={abs(_full-_alloc):.0f}"
    )

    # ── Step 1b: fill / override client & site from Excel (costs.xlsx) ─────────
    if progress_cb:
        progress_cb("excel_map", len(months), len(months) + 3)
    excel_map = _build_costs_client_site_map(data_root)
    build_log.append(
        f"OK excel_map: {len(excel_map)} employees loaded from costs.xlsx files"
    )
    df = _fill_missing_client_site(df, build_log, excel_map=excel_map)

    # ── Step 2 & 3: country mapping ───────────────────────────────────────────
    if progress_cb:
        progress_cb("country_map", len(months), len(months) + 2)

    country_map, country_conflicts = _build_country_map(data_root)
    df = _apply_country(df, country_map)

    if country_conflicts:
        for emp_id, vals in country_conflicts.items():
            build_log.append(
                f"WARN country conflict emp {emp_id}: "
                f"{list(set(vals))} → kept '{country_map.get(emp_id, ('?',))[0]}'"
            )

    # ── Step 4: standards (via standards_v2) ──────────────────────────────────
    if progress_cb:
        progress_cb("standards", len(months) + 1, len(months) + 2)

    from core.standards_v2 import apply_standards
    df = apply_standards(df, data_root)

    n_with_rule   = int(df["match_type"].ne("none").sum())
    n_with_target = int(df["std_hours_month"].notna().sum())
    total_expected = float(df["expected_billing"].sum())
    build_log.append(
        f"OK standards: {n_with_rule}/{len(df)} matched "
        f"({n_with_target} with hour targets) · "
        f"expected billing ILS {total_expected:,.0f}"
    )

    # ── Step 5: insight fields ────────────────────────────────────────────────
    if progress_cb:
        progress_cb("insights", len(months) + 2, len(months) + 3)
    df = _compute_insights(df)
    if "shortage_revenue" in df.columns:
        _sr = float(df["shortage_revenue"].sum())
        build_log.append(f"OK shortage revenue (extra billing-floor profit): ₪{_sr:,.0f}")

    # ── Step 5b: income files (real billing from accounting system) ──────────
    try:
        from core.income_loader import load_income_files
        from core.client_mapping import map_client, canonicalize_pipeline_name

        income = load_income_files(data_root)
        if not income.empty:
            # 1. Map accounting name → pipeline name (income.xlsx → df.client)
            # 2. Then collapse pipeline-level variants so income/cost merges cleanly
            income["client"] = (income["client_full"]
                                .apply(map_client)
                                .apply(canonicalize_pipeline_name))

            # אגר את הצינור לפי (לקוח, חודש)
            df_agg = (df.groupby(["month", "client"], as_index=False)
                        .agg(actual_hours=("total_hours", "sum"),
                             employer_cost=("cost", "sum")))  # use allocated cost, not full employer_cost

            # עמודות income שנמזגות — כל מה שקיים במחלקה החדשה
            _WANT_COLS = [
                "billing_amount", "billed_hours", "billed_days",
                "billing_work", "billing_extras", "billing_credits",
                "billing_type_actual",
                "amount_hourly_hours", "amount_daily_hours",
                "amount_completion_hours", "amount_overtime_hours",
                "amount_housing", "amount_settlement",
                "amount_import_fee", "amount_fee_refund", "amount_credit",
                "qty_hourly_hours", "qty_daily_hours",
                "qty_completion_hours", "qty_overtime_hours",
            ]
            _inc_merge = income[
                ["month", "client"] + [c for c in _WANT_COLS if c in income.columns]
            ].copy()

            # ── Normalized join key ───────────────────────────────────────────
            # Andromeda sometimes uses בע"מ (with ״) and בעמ (without) for the
            # SAME client within the same month. Strip quote variants so both
            # map to the same income row, then split billing proportionally
            # by hours to avoid double-counting.
            _QUOTE_RE = re.compile(r'[״׳"""″′\']')

            def _ck(s: str) -> str:
                return re.sub(r'\s+', ' ', _QUOTE_RE.sub('', str(s))).strip().lower()

            df_agg["_ck"] = df_agg["client"].apply(_ck)
            _inc_merge["_ck"] = _inc_merge["client"].apply(_ck)

            # Aggregate income by normalized key (handles multiple accounting
            # name variants mapping to the same pipeline client)
            _num_inc = [c for c in _WANT_COLS
                        if c in _inc_merge.columns and c != "billing_type_actual"]
            _str_inc = ["billing_type_actual"] if "billing_type_actual" in _inc_merge.columns else []
            _inc_by_ck = (
                _inc_merge.groupby(["month", "_ck"], as_index=False)
                .agg(
                    **{c: (c, "sum")   for c in _num_inc},
                    **{c: (c, "first") for c in _str_inc},
                )
            )

            merged_inc = df_agg.merge(_inc_by_ck, on=["month", "_ck"], how="left")
            merged_inc = merged_inc.drop(columns=["_ck"])

            # ── Proportional split for shared normalized keys ─────────────────
            # When multiple pipeline clients share the same (month, _ck), each
            # got the full billing amount. Re-weight by hours so they only
            # receive their proportional share (e.g., ולפמן בע"מ + בעמ variants).
            _grp_total = merged_inc.groupby(
                ["month", "client"], as_index=False
            )["actual_hours"].sum().rename(columns={"actual_hours": "_grp_h"})
            # identify groups that share the same income source
            _norm_key = merged_inc["client"].apply(_ck)
            merged_inc["_nk"] = _norm_key
            _nk_totals = (
                merged_inc.groupby(["month", "_nk"])["actual_hours"]
                .transform("sum")
                .replace(0, 1)
            )
            _weight = (merged_inc["actual_hours"] / _nk_totals).fillna(1.0)
            _multi = merged_inc.groupby(["month", "_nk"])["_nk"].transform("count") > 1
            for _col in _num_inc:
                if _col in merged_inc.columns:
                    merged_inc.loc[_multi, _col] = (
                        merged_inc.loc[_multi, _col] * _weight[_multi]
                    ).round(2)
            merged_inc = merged_inc.drop(columns=["_nk"])

            # fill NaN → 0 לכל עמודות income
            for _col in _WANT_COLS:
                if _col in merged_inc.columns and _col != "billing_type_actual":
                    merged_inc[_col] = merged_inc[_col].fillna(0)
            if "billing_type_actual" in merged_inc.columns:
                merged_inc["billing_type_actual"] = merged_inc["billing_type_actual"].fillna("none")

            # שדות נגזרים
            merged_inc["profit"]      = merged_inc["billing_amount"] - merged_inc["employer_cost"]
            merged_inc["margin_pct"]  = (
                merged_inc["profit"]
                / merged_inc["billing_amount"].replace(0, float("nan"))
                * 100
            ).round(2)
            merged_inc["billing_gap_hours"] = merged_inc["billed_hours"] - merged_inc["actual_hours"]

            # הסר עמודות קיימות לפני merge
            _drop_all = ["profit", "margin_pct", "billing_gap_hours"] + _WANT_COLS
            for _col in _drop_all:
                if _col in df.columns:
                    df = df.drop(columns=[_col])

            # הוסף חזרה ל-df הראשי
            _final_cols = (
                ["month", "client", "profit", "margin_pct", "billing_gap_hours"]
                + [c for c in _WANT_COLS if c in merged_inc.columns]
            )
            df = df.merge(
                merged_inc[[c for c in _final_cols if c in merged_inc.columns]],
                on=["month", "client"], how="left"
            )

            # לוגים
            total_billed  = float(merged_inc["billing_amount"].sum())
            total_cost    = float(merged_inc["employer_cost"].sum())
            total_profit  = total_billed - total_cost
            total_work    = float(merged_inc["billing_work"].sum())    if "billing_work"    in merged_inc.columns else 0
            total_extras  = float(merged_inc["billing_extras"].sum())  if "billing_extras"  in merged_inc.columns else 0
            total_credits = float(merged_inc["billing_credits"].sum()) if "billing_credits" in merged_inc.columns else 0

            if total_billed > 0:
                print(f"[income] Revenue ILS {total_billed:,.0f}, profit ILS {total_profit:,.0f} ({total_profit/total_billed*100:.1f}%)")
                print(f"[income]   Work:    ILS {total_work:,.0f}")
                print(f"[income]   Extras:  ILS {total_extras:,.0f}")
                print(f"[income]   Credits: ILS {total_credits:,.0f}")
            else:
                print("[income] WARNING: No billing amounts found")

            if "billing_type_actual" in merged_inc.columns:
                _type_counts = merged_inc["billing_type_actual"].value_counts().to_dict()
                hourly_cl = int(merged_inc[merged_inc["billing_type_actual"] == "hourly"]["client"].nunique())
                daily_cl  = int(merged_inc[merged_inc["billing_type_actual"] == "daily"]["client"].nunique())
                mixed_cl  = int(merged_inc[merged_inc["billing_type_actual"] == "mixed"]["client"].nunique())
                print(f"[income]   Hourly clients: {hourly_cl}, Daily: {daily_cl}, Mixed: {mixed_cl}")

            # שמור income.parquet נפרד לשימוש הדשבורד
            income_out = CACHE_DIR / "income.parquet"
            merged_inc.to_parquet(str(income_out), index=False, compression="snappy")
            print(f"[income] Saved to {income_out}")

            n_matched = int((merged_inc["billing_amount"] > 0).sum())
            build_log.append(
                f"OK income: ILS {total_billed:,.0f} revenue · "
                f"{n_matched}/{len(merged_inc)} client-months matched"
            )
        else:
            build_log.append("SKIP income: no income.xlsx files found")

    except FileNotFoundError as e:
        build_log.append(f"SKIP income: {e}")
        print(f"[income] WARNING: No income files found, skipping: {e}")
    except Exception as e:
        build_log.append(f"ERR income: {e}")
        print(f"[income] ERROR loading income: {e}")
        import traceback; traceback.print_exc()

    # ── Step 5c: handled by apply_standards in Step 4
    # ── Step 5d: source quality check ─────────────────────────────────────────
    src_quality = check_source_quality(df, data_root)
    for _sq in src_quality:
        build_log.append(_sq)
        print(f"[preprocessor] {_sq}")

    # ── Step 6: warnings ──────────────────────────────────────────────────────
    warn_df = _build_warnings(df, excel_only_ids)
    build_log.append(f"OK warnings: {len(warn_df)} issues found")

    # ── Step 7: save ──────────────────────────────────────────────────────────
    _save(df, warn_df, build_log)
    return df, warn_df


# ── Save ──────────────────────────────────────────────────────────────────────

AUDIT_PATH = CACHE_DIR / "standards_audit.parquet"


def _build_audit(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate a one-row-per-(client, site) audit table for standards matching.

    Columns:
      client | site | employee_count | std_client | std_site
      match_type | match_score | site_match_type
      hourly_rate | daily_rate | std_hours_month | billing_type
      has_monthly_target
    """
    if df.empty or "match_type" not in df.columns:
        return pd.DataFrame()

    agg_cols = {
        "employee_count": ("employee_id",   "nunique"),
        "std_client":     ("std_client",    "first"),
        "std_site":       ("std_site",      "first"),
        "match_type":       ("match_type",       "first"),
        "match_score":      ("match_score",      "first"),
        "site_match_type":  ("site_match_type",  "first"),
        "match_confidence": ("match_confidence", "first"),
        "hourly_rate":    ("hourly_rate",   "first"),
        "daily_rate":     ("daily_rate",    "first"),
        "std_hours_month":("std_hours_month","first"),
        "billing_type":   ("billing_type",  "first"),
    }
    audit = (
        df.groupby(["client", "site"], as_index=False)
        .agg(**agg_cols)
        .sort_values(["match_score", "client", "site"], ascending=[False, True, True])
    )
    audit["has_monthly_target"] = audit["std_hours_month"].notna()
    return audit.reset_index(drop=True)


def _save(df: pd.DataFrame, warn_df: pd.DataFrame, build_log: list[str]) -> None:
    import json, datetime

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not df.empty:
        df.to_parquet(CACHE_PATH, index=False, compression="snappy")
    if not warn_df.empty:
        warn_df.to_parquet(WARN_PATH, index=False, compression="snappy")
    elif WARN_PATH.exists():
        WARN_PATH.unlink()

    # Standards matching audit table
    audit = _build_audit(df)
    if not audit.empty:
        audit.to_parquet(AUDIT_PATH, index=False, compression="snappy")
    elif AUDIT_PATH.exists():
        AUDIT_PATH.unlink()

    # ── Enhanced meta ─────────────────────────────────────────────────────────
    meta: dict = {
        "built_at":   datetime.datetime.now().isoformat(),
        "rows":       int(len(df)),
        "months":     sorted(df["month"].unique().tolist(), key=_mkey) if not df.empty else [],
        "n_employees": int(df["employee_id"].nunique()) if not df.empty else 0,
        "n_clients":   int(df["client"].nunique()) if not df.empty and "client" in df.columns else 0,
        "n_sites":     int(df["site"].nunique()) if not df.empty and "site" in df.columns else 0,
        "n_warnings":  int(len(warn_df)),
        "country_distribution": (
            df.groupby("country")["employee_id"].nunique()
            .sort_values(ascending=False)
            .to_dict()
            if not df.empty and "country" in df.columns
            else {}
        ),
        "total_cost":   round(float(df["employer_cost"].sum()), 2) if not df.empty else 0,
        "total_hours":  round(float(df["total_hours"].sum()), 2) if not df.empty else 0,
        "total_shortage": round(float(df["shortage_hours"].sum()), 2) if not df.empty and "shortage_hours" in df.columns else 0,
        "total_shortage_revenue": round(float(df["shortage_revenue"].sum()), 2) if not df.empty and "shortage_revenue" in df.columns else 0,
        "log":          build_log,
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Source quality check ──────────────────────────────────────────────────────

def check_source_quality(
    df: pd.DataFrame,
    data_root: Path | None = None,
) -> list[str]:
    """
    Returns warning strings only for months where an Andromeda Excel file
    EXISTS on disk but the parquet was built from PDF/Legacy fallback.

    Months without any Andromeda file (e.g. 01-2025, 02-2025) are NOT flagged
    because PDF is the only available source for those months.
    """
    from core.andromeda_loader import find_andromeda_file

    warnings_out: list[str] = []
    if df.empty or "source" not in df.columns or "month" not in df.columns:
        return warnings_out

    root = data_root or DATA_ROOT

    src_by_month = df.groupby("month")["source"].agg(
        lambda s: s.value_counts().idxmax()
    )
    for month, src in src_by_month.items():
        if src in ("AndromedaExcel",):
            continue  # correct source — no issue
        # Check if an Andromeda file actually exists for this month
        andro_file = find_andromeda_file(root / month)
        if andro_file is not None:
            warnings_out.append(
                f"QUALITY WARNING {month}: Andromeda file exists ({andro_file.name}) "
                f"but parquet was built from source={src}. "
                f"Cost per client/site may be INACCURATE. "
                f"Close any open Excel files and run build_and_save() to fix."
            )
    return warnings_out


# ── Client cost audit ──────────────────────────────────────────────────────────

def audit_client_cost(client_name: str) -> pd.DataFrame:
    """
    Returns a per-employee audit table for a specific client across all months.

    Shows: month, employee_id, employee_name, source_file_type,
           employer_cost (full), total_hours_all_sites, hours_at_client,
           allocation_pct, allocated_cost

    Usage:
        from core.preprocessor import audit_client_cost
        df = audit_client_cost("בראל הנדסה שמעון גנח")
        print(df.to_string())
    """
    df, _ = load_cache()
    client_rows = df[df["client"].str.contains(client_name, na=False)].copy()
    if client_rows.empty:
        return pd.DataFrame(columns=["month","employee_id","employee_name",
                                      "source","employer_cost","total_emp_hours",
                                      "hours_at_client","allocation_pct","allocated_cost"])

    # Total hours per (employee, month) across ALL clients
    emp_month_totals = (
        df.groupby(["month","employee_id"])["total_hours"]
        .sum()
        .rename("total_emp_hours")
        .reset_index()
    )
    audit = client_rows.merge(emp_month_totals, on=["month","employee_id"], how="left")

    audit["hours_at_client"] = audit["total_hours"]
    audit["allocation_pct"]  = (
        audit["hours_at_client"] / audit["total_emp_hours"].replace(0, float("nan")) * 100
    ).round(1)

    src_col = "source_file_type" if "source_file_type" in audit.columns else "source"
    out_cols = ["month","employee_id","employee_name",src_col,
                "employer_cost","total_emp_hours","hours_at_client",
                "allocation_pct","allocated_cost","site"]
    out = audit[[c for c in out_cols if c in audit.columns]].sort_values(
        ["month","employee_id"]
    ).reset_index(drop=True)
    return out


# ── Impact report ─────────────────────────────────────────────────────────────

def compare_builds(old_parquet_path: str) -> pd.DataFrame:
    """
    Compare old vs new processed_data: cost delta per (month, client, site).
    Shows who was over/under-billed due to PDF vs AndromedaExcel source switch.

    Usage:
        from core.preprocessor import compare_builds
        df = compare_builds("output/cache/processed_data_backup_20260507_1100.parquet")
        print(df[df['cost_delta'].abs() > 1000].to_string())
    """
    old = pd.read_parquet(old_parquet_path)
    new, _ = load_cache()

    def _agg(df: pd.DataFrame) -> pd.DataFrame:
        return (
            df.groupby(["month", "client", "site"], as_index=False)
            .agg(
                cost=("cost", "sum"),
                source=("source", "first"),
                employees=("employee_id", "nunique"),
            )
        )

    old_agg = _agg(old).rename(columns={"cost": "cost_old", "source": "source_old"})
    new_agg = _agg(new).rename(columns={"cost": "cost_new", "source": "source_new"})

    merged = old_agg.merge(new_agg, on=["month", "client", "site"], how="outer").fillna(0)
    merged["cost_delta"] = (merged["cost_new"] - merged["cost_old"]).round(2)
    merged["pct_change"] = (
        merged["cost_delta"] / merged["cost_old"].replace(0, float("nan")) * 100
    ).round(1)
    return merged.sort_values("cost_delta", key=abs, ascending=False).reset_index(drop=True)


# ── Fast load ─────────────────────────────────────────────────────────────────

def load_cache() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not CACHE_PATH.exists():
        raise FileNotFoundError(
            f"Cache not found at {CACHE_PATH}. Call build_and_save() first."
        )
    df      = pd.read_parquet(CACHE_PATH)
    warn_df = (
        pd.read_parquet(WARN_PATH)
        if WARN_PATH.exists()
        else pd.DataFrame(columns=["month", "employee_id", "category", "issue"])
    )
    return df, warn_df


def load_audit() -> pd.DataFrame:
    """Load the standards-matching audit table (one row per client × site)."""
    if not AUDIT_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(AUDIT_PATH)


def load_build_meta() -> dict:
    if not META_PATH.exists():
        return {}
    try:
        import json
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
