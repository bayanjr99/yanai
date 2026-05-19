"""
Apply billing rules from an agreement to one (employee × site) monthly row.

Billing modes
-------------
hourly – charge per billable hour
  billable   = total_hours + completion
  completion = max(0, monthly_min - total_hours)
            OR max(0, days × daily_min - total_hours)

daily  – charge per worked day
  base = days × rate
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class BillingResult:
    billing_amount:   float
    billable_hours:   float
    completion_added: float
    breakdown:        dict = field(default_factory=dict)


def calculate(row: dict, agreement: dict) -> BillingResult:
    """
    Parameters
    ----------
    row       : one row from aggregator output (dict / Series.to_dict())
    agreement : one entry from load_agreements()
    """
    rate         = float(agreement.get("rate") or 0)
    billing_type = agreement.get("billing_type", "hourly")
    monthly_min  = float(agreement.get("monthly_min") or 0)
    daily_min    = float(agreement.get("daily_min") or 0)

    days        = float(row.get("days") or 0)
    total_hours = float(row.get("total_hours") or 0)

    if billing_type == "daily":
        return _daily(days, total_hours, rate)
    return _hourly(days, total_hours, rate, monthly_min, daily_min)


# ---------------------------------------------------------------------------
# Daily billing
# ---------------------------------------------------------------------------

def _daily(days: float, total_hours: float, rate: float) -> BillingResult:
    base = days * rate
    return BillingResult(
        billing_amount=_r(base),
        billable_hours=_r(total_hours),
        completion_added=0.0,
        breakdown={
            "mode":  "daily",
            "days":  days,
            "rate":  rate,
            "base":  _r(base),
        },
    )


# ---------------------------------------------------------------------------
# Hourly billing
# ---------------------------------------------------------------------------

def _hourly(
    days: float, total_hours: float, rate: float,
    monthly_min: float, daily_min: float,
) -> BillingResult:
    # NOTE on daily_min semantics: this engine receives pre-aggregated monthly
    # totals (not daily rows), so daily_min is applied as a monthly floor:
    #   expected = days × daily_min.
    # In contrast, rules_engine.py applies daily_min PER DAY to individual rows
    # before aggregation. Both are correct for their respective input shapes.
    billable   = total_hours
    completion = 0.0

    if monthly_min > 0 and billable < monthly_min:
        completion = monthly_min - billable
    elif daily_min > 0 and days > 0:
        expected = days * daily_min
        if billable < expected:
            completion = expected - billable

    billable += completion
    base      = billable * rate

    return BillingResult(
        billing_amount=_r(base),
        billable_hours=_r(billable),
        completion_added=_r(completion),
        breakdown={
            "mode":         "hourly",
            "hours_worked": _r(total_hours),
            "completion":   _r(completion),
            "billable":     _r(billable),
            "rate":         rate,
            "base":         _r(base),
        },
    )


def _r(v: float) -> float:
    return round(v, 2)
