"""
Aggregate daily rows (from pdf_parser) into monthly totals per (employee, site).

Input  – DataFrame from parse_pdf()
         columns: employee_id, employee_name, date, site, hours_to_pay

Output – DataFrame with one row per (employee_id, site):
         employee_id, employee_name, site, days, total_hours
"""

import pandas as pd


MONTHLY_COLS = [
    "employee_id", "employee_name", "site",
    "days", "total_hours",
]


def aggregate(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Convert daily-level rows to monthly aggregates per (employee_id, site)."""
    if daily_df.empty:
        return pd.DataFrame(columns=MONTHLY_COLS)

    if "hours_to_pay" not in daily_df.columns:
        daily_df = daily_df.copy()
        daily_df["hours_to_pay"] = 0.0

    result = daily_df.groupby(
        ["employee_id", "employee_name", "site"], as_index=False
    ).agg(
        days        =("date", "count"),
        total_hours =("hours_to_pay", "sum"),
    )

    result["total_hours"] = result["total_hours"].round(2)
    return result[MONTHLY_COLS].reset_index(drop=True)
