"""
Cost-allocation invariants — high-value regression tests for the merge layer
in ``core.cost_analysis``. These guard the key business rules that previously
had no test coverage:

  1. ``sum(allocated_cost per site)`` ≈ ``employer_cost``  (per month, employee)
  2. ``employer_cost`` is CONSTANT across rows for the same (month, employee)
  3. ``billing_amount`` is UNIQUE per (month, client)
  4. ``cost_per_hour`` is identical across all rows of the same (month, employee)

The tests run against the canonical cache at ``output/cache/processed_data.parquet``
and skip when it isn't present (keeps CI green on clean checkouts).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

CACHE = Path(__file__).parent.parent / "output" / "cache" / "processed_data.parquet"

pytestmark = pytest.mark.skipif(
    not CACHE.exists(),
    reason=f"{CACHE} not built — run preprocessor.build_and_save() first",
)


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return pd.read_parquet(CACHE)


# ─────────────────────────────────────────────────────────────────────────────
# 1. employer_cost should be CONSTANT within each (month, employee_id)
# ─────────────────────────────────────────────────────────────────────────────
def test_employer_cost_constant_per_employee(df: pd.DataFrame):
    if "employer_cost" not in df.columns:
        pytest.skip("employer_cost column not in cache (old build)")

    bad = (df.groupby(["month", "employee_id"])["employer_cost"]
            .nunique()
            .gt(1)
            .sum())
    assert bad == 0, (
        f"{bad} (month, employee) groups have inconsistent employer_cost. "
        "This will inflate/deflate totals when summed across sites."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. allocated_cost sums (across sites) to employer_cost (within tolerance)
# ─────────────────────────────────────────────────────────────────────────────
def test_allocated_cost_sums_to_employer_cost(df: pd.DataFrame):
    if not {"allocated_cost", "employer_cost", "total_hours",
            "emp_total_hours"}.issubset(df.columns):
        pytest.skip("required columns missing (old build)")

    # Only check employees who actually worked hours (zero-hour employees
    # legitimately have allocated_cost=0 but employer_cost>0 — they are the
    # 'hidden overhead' surfaced in the Conclusions tab).
    worked = df[df["emp_total_hours"] > 0]

    chk = (worked.groupby(["month", "employee_id"])
           .agg(sum_alloc=("allocated_cost", "sum"),
                emp_cost=("employer_cost", "first")))
    chk["diff"] = (chk["sum_alloc"] - chk["emp_cost"]).abs()
    # Allow ₪2 per employee for floating-point round-off across sites.
    bad = chk[chk["diff"] > 2.0]
    assert bad.empty, (
        f"{len(bad)} (month, employee) pairs have "
        f"|sum(allocated_cost) − employer_cost| > ₪2.\n"
        f"Worst:\n{bad.nlargest(5, 'diff').to_string()}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. billing_amount should be UNIQUE per (month, client)
# ─────────────────────────────────────────────────────────────────────────────
def test_billing_amount_unique_per_month_client(df: pd.DataFrame):
    if "billing_amount" not in df.columns:
        pytest.skip("billing_amount column not in cache")

    # Drop NaNs (no income for that month-client)
    sub = df.dropna(subset=["billing_amount"])
    bad = (sub.groupby(["month", "client"])["billing_amount"]
           .nunique()
           .gt(1)
           .sum())
    assert bad == 0, (
        f"{bad} (month, client) groups have multiple distinct "
        "billing_amount values. The income column must be constant per "
        "month-client (it's the same invoice amount split across rows)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. cost_per_hour should be identical across rows of the same (month, employee)
# ─────────────────────────────────────────────────────────────────────────────
def test_cost_per_hour_consistent(df: pd.DataFrame):
    if "cost_per_hour" not in df.columns:
        pytest.skip("cost_per_hour column not in cache")

    cph_var = (df.groupby(["month", "employee_id"])["cost_per_hour"]
               .apply(lambda s: s.dropna().nunique())
               .gt(1)
               .sum())
    assert cph_var == 0, (
        f"{cph_var} (month, employee) groups have varying cost_per_hour. "
        "It's a per-employee rate and must be constant across their site rows."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. No negative hours, costs, or billing (sanity)
# ─────────────────────────────────────────────────────────────────────────────
def test_no_negative_values(df: pd.DataFrame):
    issues = []
    for col in ("total_hours", "cost", "allocated_cost", "employer_cost"):
        if col in df.columns:
            n = (df[col] < 0).sum()
            if n:
                issues.append(f"{n} rows with negative {col}")
    if "billing_amount" in df.columns:
        # billing can legitimately be negative ONLY in credit-note rows
        n = (df["billing_amount"] < 0).sum()
        if n > 50:
            issues.append(f"{n} rows with negative billing_amount (unusual)")
    assert not issues, " · ".join(issues)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Every month folder represented in cache has expected sources
# ─────────────────────────────────────────────────────────────────────────────
def test_months_have_data(df: pd.DataFrame):
    """Each month present in the cache should have non-empty rows and at
    least one employee. Catches silently-empty months that pass through the
    preprocessor."""
    by_month = df.groupby("month").agg(
        rows=("employee_id", "size"),
        emps=("employee_id", "nunique"),
    )
    empty = by_month[(by_month["rows"] == 0) | (by_month["emps"] == 0)]
    assert empty.empty, f"Months with no data: {empty.index.tolist()}"
