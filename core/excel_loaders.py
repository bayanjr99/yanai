"""
Load agreements and employer costs from Excel files.

Both loaders are deliberately lenient about column names – they try many
Hebrew and English variants so the user doesn't have to rename anything.

Agreement billing types
-----------------------
hourly         – total_hours × rate
daily          – days × rate  (no overtime)
daily_plus_ot  – days × rate  +  overtime_hours × ot_rate
                 Created automatically when a client/site has BOTH a ימים row
                 AND a שעות row in the agreements Excel.
                 ot_threshold defaults to 10 h/day.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional
import pandas as pd


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    """Return the first column in `df` that matches any candidate (case-insensitive)."""
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        key = c.strip().lower()
        if key in lower_map:
            return lower_map[key]
    return None


def _get_float(row, col: Optional[str], default: float = 0.0) -> float:
    if col is None or pd.isna(row[col]):
        return default
    raw = str(row[col]).replace(",", "").strip()
    # Handle "65 / 80" format → take the first number
    raw = re.split(r"[/\\|]", raw)[0].strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _get_str(row, col: Optional[str], default: str = "") -> str:
    if col is None:
        return default
    val = row[col]
    if pd.isna(val):
        return default
    return str(val).strip()


# ---------------------------------------------------------------------------
# Agreements
# ---------------------------------------------------------------------------

_AGR_COLS: dict[str, list[str]] = {
    "client":         ["לקוח", "client", "שם לקוח", "customer", "customerName"],
    "site":           ["אתר", "site", "פרויקט", "שם פרויקט", "locality", "localityName", "שם אתר"],
    "country":        ["מדינה", "country", "nationality", "לאום"],
    "billing_type":   ["סוג", "type", "סוג חיוב", "billing_type", "סוג תעריף"],
    "rate":           ["מחיר", "price", "rate", "תעריף", "מחיר שעה", "מחיר יום", "תעריף שעה", "תעריף יום"],
    "teken_raw":      ["תקן", "standard", "תקן חודשי", "min_hours", "שעות מינימום"],
    "daily_min":      ["תקן יומי", "daily_min", "מינ יומי", "שעות יומי", "מינימום יומי"],
    "include_breaks": ["לאוסיף הפסקות לחיוב", "include_breaks", "הפסקות בחיוב", "הפסקות"],
    "ot_125_rate":    ["מחיר 125", "125%", "ot_125", "מחיר 125%", "תוספת 125"],
    "ot_150_rate":    ["מחיר 150", "150%", "ot_150", "מחיר 150%", "תוספת 150"],
}


def _billing_type(raw: str) -> str:
    r = str(raw).strip().lower()
    if any(w in r for w in ["יום", "יומי", "ימים", "day", "daily"]):
        return "daily"
    return "hourly"


def _parse_teken(text: str) -> tuple[float, float]:
    """
    Parse the free-text תקן (standard/minimum) field.

    Returns (monthly_min, daily_min).

    Examples
    --------
    "השלמה ל-236 שעות"         → (236, 0)
    "השלמה ל-220 שעות"         → (220, 0)
    "תקן יומי: 10 שעות ביום"   → (0, 10)
    "10 שעות ביום חול...236"   → (236, 10)
    """
    if not isinstance(text, str) or not text.strip() or text.strip() in ("-", "nan"):
        return 0.0, 0.0

    monthly_min = 0.0
    daily_min   = 0.0

    # monthly: "ל-236 שעות" or "236 שעות" at end
    m = re.search(r"ל[-–]?\s*(\d{2,3})\s*שעות", text)
    if m:
        monthly_min = float(m.group(1))
    else:
        # bare number followed by שעות
        m = re.search(r"(\d{3})\s*שעות", text)
        if m:
            monthly_min = float(m.group(1))

    # daily: "N שעות ביום"
    m = re.search(r"(\d{1,2})\s*שעות\s*ביום", text)
    if m:
        daily_min = float(m.group(1))

    return monthly_min, daily_min


def _to_bool(val) -> bool:
    if pd.isna(val):
        return False
    return str(val).strip().lower() in {"כן", "yes", "true", "1", "v", "✓", "x"}


def _combine_dual_rate(agreements: list[dict]) -> list[dict]:
    """
    When the same (client, site) has both a 'daily' row and an 'hourly' row,
    merge them into a single 'daily_plus_ot' agreement:
      - rate        = daily base rate (from the daily row)
      - ot_rate     = overtime hourly rate (from the hourly row)
      - ot_threshold = 10 h/day (hours per day before OT kicks in)

    All other rows are kept as-is.
    """
    # Group by (client, site, country) — country-specific agreements are separate
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ag in agreements:
        groups[(ag["client"], ag["site"], ag.get("country", ""))].append(ag)

    result: list[dict] = []
    for ags in groups.values():
        daily_rows   = [a for a in ags if a["billing_type"] == "daily"]
        hourly_rows  = [a for a in ags if a["billing_type"] == "hourly"]

        if daily_rows and hourly_rows:
            # Build a combined daily_plus_ot agreement
            base = daily_rows[0].copy()
            base["billing_type"]  = "daily_plus_ot"
            base["ot_rate"]       = hourly_rows[0]["rate"]
            base["ot_threshold"]  = 10.0   # standard hours/day before overtime
            # Keep the higher monthly/daily_min from either row
            base["monthly_min"] = max(base.get("monthly_min", 0),
                                      hourly_rows[0].get("monthly_min", 0))
            base["daily_min"]   = max(base.get("daily_min", 0),
                                      hourly_rows[0].get("daily_min", 0))
            result.append(base)
        else:
            result.extend(ags)

    return result


def load_agreements(path: str) -> list[dict]:
    """
    Load agreements Excel.

    Returns a list of agreement dicts with keys:
      client, site, billing_type ('hourly' / 'daily' / 'daily_plus_ot'),
      rate, ot_rate, ot_threshold,
      monthly_min, daily_min,
      include_breaks, ot_125_rate, ot_150_rate
    """
    df = pd.read_excel(path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    col = {field: _find_col(df, candidates) for field, candidates in _AGR_COLS.items()}

    if col["client"] is None:
        raise ValueError(
            f"לא נמצאה עמודת לקוח בקובץ הסכמים.\n"
            f"עמודות שנמצאו: {list(df.columns)}\n"
            f"וודא שיש עמודה בשם 'לקוח' או 'client'."
        )

    raw_agreements: list[dict] = []
    for _, row in df.iterrows():
        client = _get_str(row, col["client"])
        if not client:
            continue

        btype_raw            = _get_str(row, col["billing_type"], "שעות")
        billing_type_computed = _billing_type(btype_raw)

        # ── Rate parsing: handle "750/75" (daily_rate/ot_rate) in one cell ──
        rate_raw          = _get_str(row, col["rate"]) if col["rate"] else ""
        rate_value        = _get_float(row, col["rate"])   # first part only (safe default)
        ot_rate_from_slash = 0.0
        ot_threshold_from_slash = 0.0

        if "/" in rate_raw and billing_type_computed in ("daily", "hourly"):
            parts = re.split(r"[/\\|]", rate_raw.replace(",", ""))
            if len(parts) >= 2:
                try:
                    r1 = float(parts[0].strip())
                    r2 = float(parts[1].strip())
                    if r1 > 0 and r2 > 0:
                        rate_value             = r1
                        ot_rate_from_slash      = r2
                        ot_threshold_from_slash = 10.0   # default threshold
                        billing_type_computed   = "daily_plus_ot"
                except ValueError:
                    pass   # leave rate_value as first-part result

        # Parse monthly_min / daily_min from the free-text תקן column
        teken_text  = _get_str(row, col["teken_raw"])
        monthly_min_parsed, daily_min_parsed = _parse_teken(teken_text)

        # Also try numeric תקן if already a number
        monthly_min_num = _get_float(row, col["teken_raw"])
        monthly_min = monthly_min_num if monthly_min_num > 0 else monthly_min_parsed

        daily_min_explicit = _get_float(row, col["daily_min"])
        daily_min = daily_min_explicit if daily_min_explicit > 0 else daily_min_parsed

        raw_agreements.append({
            "client":         client,
            "site":           _get_str(row, col["site"]),
            "country":        _get_str(row, col["country"]),
            "billing_type":   billing_type_computed,
            "rate":           rate_value,
            "ot_rate":        ot_rate_from_slash,
            "ot_threshold":   ot_threshold_from_slash,
            "monthly_min":    monthly_min,
            "daily_min":      daily_min,
            "include_breaks": _to_bool(row[col["include_breaks"]]) if col["include_breaks"] else False,
            "ot_125_rate":    _get_float(row, col["ot_125_rate"]),
            "ot_150_rate":    _get_float(row, col["ot_150_rate"]),
        })

    agreements = _combine_dual_rate(raw_agreements)

    # ── Fix 4: warn if include_breaks column was never found ─────────────────
    if col["include_breaks"] is None:
        import warnings
        warnings.warn(
            "עמודת 'לאוסיף הפסקות לחיוב' לא נמצאה בקובץ הסכמים — "
            "include_breaks=False לכל ההסכמים.",
            stacklevel=2,
        )

    return agreements


# ---------------------------------------------------------------------------
# Employer costs
# ---------------------------------------------------------------------------

_COST_COLS: dict[str, list[str]] = {
    "employee_id": ["מס עובד", "מספר עובד", "employee_id", "id", "מס' עובד", "emp_id"],
    "client":      ["שם לקוח", "customer", "customerName", "CustomerName", "לקוח", "client"],
    "site":        ["שם פרויקט", "locality", "localityName", "LocalityName", "אתר", "פרויקט", "site"],
    "country":     ["מדינה", "country", "nationality", "לאום"],
    "cost":        ["עלות", "cost", "עלות מעביד", "total_cost", 'עלות סה"כ', "עלות כוללת"],
}


def load_costs(path: str) -> dict[str, list[dict]]:
    """
    Load employer costs Excel.

    Returns
    -------
    dict: employee_id (str) → list of {"client", "site", "cost"} dicts.
    An employee can appear more than once (for different sites/clients).
    """
    df = pd.read_excel(path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    col = {field: _find_col(df, candidates) for field, candidates in _COST_COLS.items()}

    if col["employee_id"] is None:
        raise ValueError(
            f"לא נמצאה עמודת מספר עובד בקובץ עלויות.\n"
            f"עמודות שנמצאו: {list(df.columns)}"
        )

    costs: dict[str, list[dict]] = {}
    for _, row in df.iterrows():
        emp_id = _get_str(row, col["employee_id"])
        if not emp_id:
            continue

        entry = {
            "client":  _get_str(row, col["client"]),
            "site":    _get_str(row, col["site"]),
            "country": _get_str(row, col["country"]),
            "cost":    _get_float(row, col["cost"]),
        }

        if emp_id not in costs:
            costs[emp_id] = []
        costs[emp_id].append(entry)

    return costs


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

def load_overrides(path: str) -> dict[tuple[str, str], float]:
    """
    Load rate overrides from overrides.xlsx (optional file).

    Format: employee_id | site | rate

    Returns
    -------
    dict: (employee_id, site) → override_rate
    Returns empty dict if file does not exist.
    """
    import os
    if not os.path.exists(path):
        return {}

    df = pd.read_excel(path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    emp_col  = _find_col(df, ["מס' עובד", "מס עובד", "employee_id", "emp_id"])
    site_col = _find_col(df, ["אתר", "site", "locality"])
    rate_col = _find_col(df, ["מחיר", "rate", "תעריף"])

    if emp_col is None or rate_col is None:
        return {}

    overrides: dict[tuple[str, str], float] = {}
    for _, row in df.iterrows():
        emp_id = _get_str(row, emp_col)
        site   = _get_str(row, site_col) if site_col else ""
        rate   = _get_float(row, rate_col)
        if emp_id and rate > 0:
            overrides[(emp_id, site)] = rate

    return overrides
