"""
core/cost_analysis.py — Employee cost analysis by employee_id.

Key rule: ALL joins use employee_id (string). Never use employee_name.

Flow:
  1. load_hours_from_pdf()   → aggregate daily PDF rows → monthly hours per employee × site
  2. load_costs_xlsx()       → employer cost per employee from costs.xlsx
  3. merge_and_allocate()    → join on employee_id, split cost by hours ratio
  4. build_sheets()          → create the 4 output DataFrames
  5. detect_warnings()       → data quality issues
  6. export_to_excel()       → write cost_analysis.xlsx
"""

from __future__ import annotations

import re
import warnings as _warnings
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# 1.  Hours from PDF
# ---------------------------------------------------------------------------

def load_hours_from_pdf(pdf_path: str, month: str) -> pd.DataFrame:
    """
    Parse an Andromeda payroll PDF and return one row per (employee_id, site)
    aggregated for the whole month.

    Uses the existing pdf_parser — no name matching, only employee_id.

    Returns columns:
      month, employee_id, employee_name, site,
      work_days, total_hours, break_hours
    """
    from core.pdf_parser import parse_pdf

    daily = parse_pdf(pdf_path)
    if daily.empty:
        return pd.DataFrame(columns=[
            "month", "employee_id", "employee_name", "site",
            "work_days", "total_hours", "break_hours",
        ])

    # Normalize employee_id to string (key column — never touch name)
    daily["employee_id"] = daily["employee_id"].astype(str).str.strip()

    # Aggregate: one row per employee × site
    agg = (
        daily
        .groupby(["employee_id", "employee_name", "site"], as_index=False)
        .agg(
            work_days  =("date",         "nunique"),
            total_hours=("hours_to_pay", "sum"),
            break_hours=("break_hours",  "sum"),
        )
    )

    agg["total_hours"] = agg["total_hours"].round(2)
    agg["break_hours"] = agg["break_hours"].round(2)
    agg["month"]       = month

    cols = ["month", "employee_id", "employee_name", "site",
            "work_days", "total_hours", "break_hours"]
    return agg[cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 2.  Costs from Excel
# ---------------------------------------------------------------------------

def _find_emp_id_col(df: pd.DataFrame) -> str:
    """
    Find the employee_id column by:
      1. Name matching (Hebrew / English variants)
      2. Value scan: column whose non-null values are all 3-6 digit integers
    """
    name_hints = ["מס עובד", "מספר עובד", "employee_id", "emp_id", "empid"]
    for col in df.columns:
        col_lower = str(col).lower().replace(" ", "")
        if any(h.replace(" ", "") in col_lower for h in name_hints):
            return col

    # Value-based detection: 4–6 digit integers
    for col in df.columns:
        sample = df[col].dropna().astype(str).str.strip().head(10)
        if len(sample) >= 3 and all(re.fullmatch(r"\d{3,6}", v) for v in sample):
            return col

    # Positional fallback: column index 2 (always holds emp_id in our files)
    return df.columns[2]


def _find_cost_col(df: pd.DataFrame) -> str:
    """
    Find the employer_cost column.
    Prefers last column named 'עלות' / 'cost'; falls back to last numeric column.
    """
    cost_hints = ["עלות", "cost", "employer_cost", "total_cost"]
    for col in reversed(list(df.columns)):
        col_lower = str(col).lower()
        if any(h in col_lower for h in cost_hints):
            return col
    # Fallback: last column
    return df.columns[-1]


def load_costs_xlsx(path: str) -> pd.DataFrame:
    """
    Load employer costs from costs.xlsx.

    Column layout (robust — works even when Hebrew is garbled):
      [0] CustomerName  → client
      [1] LocalityName  → site
      [2] מס עובד       → employee_id   (detected dynamically)
      [4] ברוטו         → gross_salary
      [13] עלות          → employer_cost (detected dynamically)

    Returns columns:
      employee_id, employee_name (if found), client, site,
      gross_salary, employer_cost
    """
    df = pd.read_excel(path, dtype=str)

    emp_id_col = _find_emp_id_col(df)
    cost_col   = _find_cost_col(df)

    # client / site — always columns 0 and 1
    client_col = df.columns[0]
    site_col   = df.columns[1]

    # gross_salary — column index 4 (ברוטו) when available
    gross_col = df.columns[4] if len(df.columns) > 4 else None

    result = pd.DataFrame({
        "employee_id":   df[emp_id_col].astype(str).str.strip(),
        "client":        df[client_col].astype(str).str.strip(),
        "site":          df[site_col].astype(str).str.strip(),
        "employer_cost": pd.to_numeric(df[cost_col],  errors="coerce").fillna(0.0),
    })

    if gross_col:
        result["gross_salary"] = pd.to_numeric(df[gross_col], errors="coerce").fillna(0.0)
    else:
        result["gross_salary"] = 0.0

    # Keep only rows with a valid numeric employee_id
    result = result[result["employee_id"].str.fullmatch(r"\d{3,6}")].copy()
    result["employer_cost"] = pd.to_numeric(result["employer_cost"], errors="coerce").fillna(0.0)
    result["gross_salary"]  = pd.to_numeric(result["gross_salary"],  errors="coerce").fillna(0.0)

    # FIX: sum all cost rows for the same employee_id — never use keep="first"
    # An employee may appear in multiple rows (different client/site assignments)
    # but their total employer cost is the SUM of all those rows.
    result = (
        result
        .groupby("employee_id", as_index=False)
        .agg(
            employer_cost=("employer_cost", "sum"),
            gross_salary =("gross_salary",  "sum"),
            client       =("client",        "first"),  # administrative reference only
            site         =("site",          "first"),
        )
    )
    result["employer_cost"] = result["employer_cost"].round(2)
    result["gross_salary"]  = result["gross_salary"].round(2)

    return result.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3.  Merge + cost allocation
# ---------------------------------------------------------------------------

def merge_and_allocate(
    hours_df: pd.DataFrame,
    costs_df: pd.DataFrame,
    month: str,
) -> pd.DataFrame:
    """
    Join hours and costs on employee_id.
    Allocate employer_cost across sites proportionally to hours worked.

    Rule:
      allocated_cost(site) = employer_cost × (site_hours / total_hours)
      cost_per_hour(site)  = allocated_cost / site_hours   (0 if hours == 0)

    Never joins on employee_name.
    """
    if hours_df.empty:
        return pd.DataFrame()

    h = hours_df.copy()
    c = costs_df.copy()

    h["employee_id"] = h["employee_id"].astype(str).str.strip()
    c["employee_id"] = c["employee_id"].astype(str).str.strip()

    # Total hours per employee (across all sites this month)
    emp_totals = (
        h.groupby("employee_id", as_index=False)["total_hours"]
        .sum()
        .rename(columns={"total_hours": "emp_total_hours"})
    )

    # costs_df is already deduplicated + summed by load_costs_xlsx()
    # Left join: employees in hours but NOT in costs get employer_cost = 0
    # Employees in costs but NOT in hours are excluded (left join on hours)
    merged = (
        h
        .merge(emp_totals, on="employee_id", how="left")
        .merge(
            c[["employee_id", "employer_cost", "client"]],
            on="employee_id",
            how="left",          # keeps all hour rows; missing cost → NaN → 0
        )
    )
    merged["employer_cost"] = merged["employer_cost"].fillna(0.0)

    # Allocate employer_cost across sites by hours ratio
    _safe_total = merged["emp_total_hours"].replace(0, float("nan"))
    merged["allocated_cost"] = (
        merged["employer_cost"] * merged["total_hours"] / _safe_total
    ).round(2).fillna(0.0)

    # Cost per hour at site level (safe: 0 when hours = 0)
    _safe_hours = merged["total_hours"].replace(0, float("nan"))
    merged["cost_per_hour"] = (
        merged["allocated_cost"] / _safe_hours
    ).round(2).fillna(0.0)

    merged["month"] = month

    return merged.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 4.  Build output sheets
# ---------------------------------------------------------------------------

def build_sheets(merged: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Build the four DataFrames for cost_analysis.xlsx.

    Keys: 'employee_cost', 'site_cost', 'client_cost'
    """
    if merged.empty:
        empty = pd.DataFrame()
        return {
            "employee_cost": empty,
            "site_cost":     empty,
            "client_cost":   empty,
        }

    # ── Sheet 1: employee_cost ────────────────────────────────────────────────
    # employer_cost is already summed per employee_id in load_costs_xlsx()
    emp_cost = (
        merged
        .groupby(["month", "employee_id"], as_index=False)
        .agg(
            employee_name=("employee_name", "first"),   # from PDF — display only
            total_hours  =("total_hours",   "sum"),
            employer_cost=("employer_cost", "first"),   # identical across sites
        )
    )
    _safe = emp_cost["total_hours"].replace(0, float("nan"))
    emp_cost["cost_per_hour"] = (
        emp_cost["employer_cost"] / _safe
    ).round(2).fillna(0.0)
    emp_cost["total_hours"]   = emp_cost["total_hours"].round(2)
    emp_cost["employer_cost"] = emp_cost["employer_cost"].round(2)

    # Enforce output column order
    emp_cost = emp_cost[[
        "month", "employee_id", "employee_name",
        "total_hours", "employer_cost", "cost_per_hour",
    ]].sort_values(["month", "employee_id"]).reset_index(drop=True)

    # ── Sheet 2: site_cost ────────────────────────────────────────────────────
    site_cost_cols = [c for c in
        ["month", "client", "site", "employee_id", "total_hours",
         "allocated_cost", "cost_per_hour"]
        if c in merged.columns
    ]
    site_cost = merged[site_cost_cols].copy().rename(columns={"total_hours": "hours"})
    site_cost = site_cost.sort_values(
        ["month", "client", "site", "employee_id"]
    ).reset_index(drop=True)

    # ── Sheet 3: client_cost ──────────────────────────────────────────────────
    if "client" in merged.columns:
        client_cost = (
            merged
            .dropna(subset=["client"])
            .query("client != ''")
            .groupby(["month", "client"], as_index=False)
            .agg(
                total_hours=("total_hours",    "sum"),
                total_cost =("allocated_cost", "sum"),
            )
        )
        _safe_c = client_cost["total_hours"].replace(0, float("nan"))
        client_cost["avg_cost_per_hour"] = (
            client_cost["total_cost"] / _safe_c
        ).round(2).fillna(0.0)
        client_cost["total_hours"] = client_cost["total_hours"].round(2)
        client_cost["total_cost"]  = client_cost["total_cost"].round(2)
        client_cost = client_cost[
            ["month", "client", "total_hours", "total_cost", "avg_cost_per_hour"]
        ].sort_values(["month", "total_cost"], ascending=[True, False]).reset_index(drop=True)
    else:
        client_cost = pd.DataFrame(
            columns=["month", "client", "total_hours", "total_cost", "avg_cost_per_hour"]
        )

    return {
        "employee_cost": emp_cost,
        "site_cost":     site_cost,
        "client_cost":   client_cost,
    }


# ---------------------------------------------------------------------------
# 5.  Warnings / validation
# ---------------------------------------------------------------------------

def detect_warnings(
    hours_df: pd.DataFrame,
    costs_df: pd.DataFrame,
    merged: pd.DataFrame,
    month: str,
    cost_per_hour_threshold: float = 250.0,
) -> pd.DataFrame:
    """
    Return a DataFrame of data quality issues.

    Checks:
      1. Employee in hours PDF but missing from costs.xlsx → cost set to 0
      2. Employee has zero hours this month with positive cost → excluded, warned
      3. cost_per_hour > threshold (default 250)
    """
    rows: list[dict] = []

    def _warn(emp_id: str, issue: str) -> None:
        rows.append({"month": month, "employee_id": emp_id, "issue": issue})

    if hours_df.empty:
        return pd.DataFrame(columns=["month", "employee_id", "issue"])

    hours_ids = set(hours_df["employee_id"].astype(str).str.strip())
    costs_ids = set(costs_df["employee_id"].astype(str).str.strip()) if not costs_df.empty else set()

    # 1. In hours but not in costs → kept with employer_cost = 0
    for eid in sorted(hours_ids - costs_ids):
        _warn(eid, "Missing cost data — employer_cost set to 0")

    if not merged.empty:
        # 2. Zero hours with positive employer cost (edge case)
        zero_h = merged[(merged["total_hours"] == 0) & (merged["employer_cost"] > 0)]
        for _, r in zero_h.drop_duplicates("employee_id").iterrows():
            _warn(
                str(r["employee_id"]),
                f"Zero hours but employer_cost ₪{r['employer_cost']:,.0f} — cost_per_hour set to 0",
            )

        # 3. Unusually high cost_per_hour
        high_rate = merged[merged["cost_per_hour"] > cost_per_hour_threshold]
        for _, r in high_rate.drop_duplicates("employee_id").iterrows():
            _warn(
                str(r["employee_id"]),
                f"High cost_per_hour ₪{r['cost_per_hour']:.0f}/h "
                f"(threshold ₪{cost_per_hour_threshold:.0f}/h)",
            )

    df = pd.DataFrame(rows, columns=["month", "employee_id", "issue"])
    return df.drop_duplicates().reset_index(drop=True)


# ---------------------------------------------------------------------------
# 6.  Excel export
# ---------------------------------------------------------------------------

def export_to_excel(
    output_path: str,
    sheets: dict[str, pd.DataFrame],
    warnings_df: pd.DataFrame,
) -> None:
    """
    Write cost_analysis.xlsx with 4 sheets:
      employee_cost | site_cost | client_cost | warnings
    """
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        sheet_order = ["employee_cost", "site_cost", "client_cost"]
        for name in sheet_order:
            df = sheets.get(name, pd.DataFrame())
            df.to_excel(writer, sheet_name=name, index=False)

        warnings_df.to_excel(writer, sheet_name="warnings", index=False)


# ---------------------------------------------------------------------------
# 7.  High-level runner (single month)
# ---------------------------------------------------------------------------

def run_month(
    pdf_path: str,
    costs_path: str,
    month: str,
    output_path: str,
    cost_per_hour_threshold: float = 250.0,
) -> dict:
    """
    Full pipeline for one month:
      parse PDF → load costs → merge → build sheets → detect warnings → export

    Returns a summary dict for the CLI to print.
    """
    hours_df = load_hours_from_pdf(pdf_path, month)
    costs_df = load_costs_xlsx(costs_path)
    merged   = merge_and_allocate(hours_df, costs_df, month)
    sheets   = build_sheets(merged)
    warnings = detect_warnings(hours_df, costs_df, merged, month, cost_per_hour_threshold)
    export_to_excel(output_path, sheets, warnings)

    # Summary stats
    emp_cost = sheets.get("employee_cost", pd.DataFrame())
    return {
        "month":           month,
        "employees_hours": hours_df["employee_id"].nunique() if not hours_df.empty else 0,
        "employees_costs": len(costs_df),
        "employees_merged": merged["employee_id"].nunique() if not merged.empty else 0,
        "total_hours":     round(float(hours_df["total_hours"].sum()), 2) if not hours_df.empty else 0,
        "total_cost":      round(float(costs_df["employer_cost"].sum()), 2) if not costs_df.empty else 0,
        "avg_cost_per_hour": round(
            float(emp_cost["cost_per_hour"].mean()), 2
        ) if not emp_cost.empty else 0,
        "warnings":        len(warnings),
        "output_path":     output_path,
    }
