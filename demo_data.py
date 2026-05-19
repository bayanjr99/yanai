"""
demo_data.py — Generates realistic synthetic demo data for the cloud dashboard.

Produces a DataFrame matching the processed_data.parquet schema used by cloud_app.py.
All values are synthetic — suitable for showcasing the dashboard without real data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Client definitions: (name, site, billing_rate, base_ot_ratio, is_problematic)
# is_problematic=True → cost multiplier > 1 → client will run at a loss
# ---------------------------------------------------------------------------
_CLIENTS = [
    ("ולפמן תעשיות בע\"מ",          "מפעל ולפמן",         75, 0.28, False),
    ("מ.אילון אביב נכסים",           "בית שקמה-כללי",      65, 0.24, False),
    ("קבוצת טלאור כראדי בע\"מ",      "שגב שלום",           75, 0.31, False),
    ("א.ש עבודות בידוד ופחחות",      "א.ש עבודות בידוד",   68, 0.34, False),
    ("נח רפפורט 1990 בע\"מ",         "ת\"א מרכז",           70, 0.29, False),
    ("סלמאן והבה ובניו בע\"מ",        "צפון",               72, 0.26, False),
    ("עוז סלמאן טכנולוגיות בע\"מ",   "עוז-טק",             80, 0.21, False),
    ("צלעג הנדסת בניין בע\"מ",       "צלעג-מרכז",          68, 0.33, False),
    ("ינאי פרסונל",                  "לא לדיווח",            0, 0.22, True),   # internal cost
    ("נתיבים דרום בע\"מ",            "דרום",               58, 0.39, True),   # high OT + low rate
    ("גלי זיו לבניין",               "גלי-צפון",            55, 0.36, True),   # low rate
    ("אחים בן רחמים (צפון) בעמ",     "אחים בן רחמים",      62, 0.44, True),   # very high OT
]

_MONTHS = [f"{m:02d}-2025" for m in range(1, 13)]

_COUNTRIES = ["הודו", "סרי לנקה (צילון)", "נפאל", "בנגלדש"]
_COUNTRY_W  = [0.55, 0.25, 0.12, 0.08]


def generate_demo(seed: int = 42) -> pd.DataFrame:
    """Return a DataFrame of synthetic billing/cost data (≈ 1 800 rows)."""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    emp_counter = 1001

    for client, site, billing_rate, base_ot, is_bad in _CLIENTS:
        # 6 – 14 employees per client
        n_emps   = int(rng.integers(6, 15))
        emp_ids  = [str(emp_counter + i) for i in range(n_emps)]
        emp_names = [f"EMPLOYEE {emp_counter + i}" for i in range(n_emps)]
        emp_countries = rng.choice(_COUNTRIES, size=n_emps, p=_COUNTRY_W)
        emp_counter += n_emps

        # Cost multiplier: > 1 means employer cost > client revenue → loss
        cost_mult = rng.uniform(1.10, 1.45) if is_bad else rng.uniform(0.55, 0.85)

        for month in _MONTHS:
            # Some employees may be absent
            active_n = max(3, int(n_emps * rng.uniform(0.70, 1.0)))
            active_ids   = emp_ids[:active_n]
            active_names = emp_names[:active_n]
            active_ctry  = emp_countries[:active_n]

            for eid, ename, country in zip(active_ids, active_names, active_ctry):
                # Hours
                base_h   = rng.uniform(185, 265)
                ot_ratio = float(np.clip(rng.normal(base_ot, 0.04), 0.05, 0.60))
                h100     = base_h * (1 - ot_ratio)
                h125     = base_h * ot_ratio * 0.55
                h150     = base_h * ot_ratio * 0.35
                h175     = base_h * ot_ratio * 0.07
                h200     = base_h * ot_ratio * 0.03
                total_h  = h100 + h125 + h150 + h175 + h200

                # Cost
                base_cph   = rng.uniform(42, 68)
                employer_c = total_h * base_cph * cost_mult
                cph        = employer_c / total_h if total_h > 0 else 0

                # Standard / shortage
                std_hours: float | None = 236.0 if billing_rate > 0 else None
                shortage  = max(0.0, (std_hours or 0) - total_h)

                rows.append({
                    "month":           month,
                    "employee_id":     eid,
                    "employee_name":   ename,
                    "client":          client,
                    "site":            site,
                    "country":         country,
                    "work_days":       int(total_h / 9),
                    "total_hours":     round(total_h,  2),
                    "h100":            round(h100,     2),
                    "h125":            round(h125,     2),
                    "h150":            round(h150,     2),
                    "h175":            round(h175,     2),
                    "h200":            round(h200,     2),
                    "employer_cost":   round(employer_c, 2),
                    "allocated_cost":  round(employer_c, 2),
                    "cost_per_hour":   round(cph,      2),
                    "hourly_rate":     float(billing_rate),
                    "daily_rate":      0.0,
                    "overtime_ratio":  round(ot_ratio, 4),
                    "shortage_hours":  round(shortage, 2),
                    "std_hours_month": std_hours if std_hours else float("nan"),
                    "billing_type":    "hourly" if billing_rate > 0 else "internal",
                    "source":          "Demo",
                })

    df = pd.DataFrame(rows)
    df["month"] = df["month"].astype(str)
    return df
