"""
Rules Engine — Phase 1.

Single entry point: apply_rules(daily_row, agreement, overrides) → DayResult

Billing rules (applied in this order)
--------------------------------------
1. include_breaks  : if True, add break_hours to billable hours.
2. daily_min       : per-day minimum only.
                     completion_day = max(0, daily_min - billable_hours)
                     NEVER multiply daily_min × days.
3. billing_amount  : computed here for the day.
                     For daily_plus_ot: base + ot_hours × ot_rate.
                     For daily: base rate only (no per-day completion).
                     For hourly: (billable + completion) × rate.

monthly_min is NOT applied here. It is applied in the aggregation step
after all daily rows are summed, ensuring it is never used per-day.

Fail-safe rules
---------------
- rate = 0 → blocked = True (do not bill, send to issues)
- no agreement  → blocked = True
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DayResult:
    hours_to_pay:    float   # raw hours from PDF
    break_hours:     float   # break hours extracted from PDF
    billable_hours:  float   # hours used as billing basis (after breaks if applicable)
    ot_hours:        float   # overtime above threshold (daily_plus_ot only)
    completion_day:  float   # completion added this day (from daily_min)
    billing_amount:  float   # billing amount for this day
    billing_type:    str     # hourly | daily | daily_plus_ot
    rate:            float
    ot_rate:         float
    agreement_used:  str     # "{client} / {site}" label for debug
    blocked:         bool    # True → don't bill, send to issues
    block_reason:    str     # populated when blocked=True


def apply_rules(
    hours_to_pay:   float,
    break_hours:    float,
    agreement:      dict | None,
    override_rate:  float | None = None,   # from overrides.xlsx
) -> DayResult:
    """
    Apply all billing rules to a single daily row.

    Parameters
    ----------
    hours_to_pay   : שעות לתשלום from PDF (already the correct column)
    break_hours    : הפסקה hours from PDF
    agreement      : matched agreement dict, or None
    override_rate  : explicit rate from overrides.xlsx (takes priority over agreement rate)

    Returns
    -------
    DayResult with all computed values.
    """
    # ── Fail-safe: no agreement ──────────────────────────────────────────────
    if agreement is None:
        return DayResult(
            hours_to_pay=hours_to_pay,
            break_hours=break_hours,
            billable_hours=hours_to_pay,
            ot_hours=0.0,
            completion_day=0.0,
            billing_amount=0.0,
            billing_type="hourly",
            rate=0.0,
            ot_rate=0.0,
            agreement_used="",
            blocked=True,
            block_reason="הסכם חסר",
        )

    billing_type  = str(agreement.get("billing_type", "hourly"))
    rate          = float(override_rate) if override_rate is not None \
                    else float(agreement.get("rate") or 0)
    ot_rate       = float(agreement.get("ot_rate")      or 0)
    ot_threshold  = float(agreement.get("ot_threshold") or 10)
    daily_min     = float(agreement.get("daily_min")    or 0)
    include_breaks = bool(agreement.get("include_breaks"))

    ag_label = f"{agreement.get('client', '')} / {agreement.get('site', '')}"

    # ── Fail-safe: rate = 0 ──────────────────────────────────────────────────
    if rate == 0:
        return DayResult(
            hours_to_pay=hours_to_pay,
            break_hours=break_hours,
            billable_hours=hours_to_pay,
            ot_hours=0.0,
            completion_day=0.0,
            billing_amount=0.0,
            billing_type=billing_type,
            rate=0.0,
            ot_rate=ot_rate,
            agreement_used=ag_label,
            blocked=True,
            block_reason="תעריף הסכם הוא 0 ₪",
        )

    # ── Rule 1: include_breaks ───────────────────────────────────────────────
    # Andromeda PDF gives:  hours_to_pay = hours_worked - break_hours
    # If include_breaks=True the agreement says client pays for break time too,
    # so we add breaks back.  Otherwise hours_to_pay is used as-is.
    billable = hours_to_pay + (break_hours if include_breaks else 0.0)

    # ── Rule 2 + Rule 3: compute per-day billing ────────────────────────────
    ot_hours       = 0.0
    completion_day = 0.0

    if billing_type == "daily_plus_ot":
        ot_hours       = max(0.0, billable - ot_threshold)
        billing_amount = round(rate + ot_hours * ot_rate, 2)

    elif billing_type == "daily":
        billing_amount = round(rate, 2)

    else:
        # hourly: apply daily_min per day (NEVER days * daily_min)
        completion_day = max(0.0, daily_min - billable) if daily_min > 0 else 0.0
        billing_amount = round((billable + completion_day) * rate, 2)

    return DayResult(
        hours_to_pay=hours_to_pay,
        break_hours=break_hours,
        billable_hours=billable,
        ot_hours=round(ot_hours, 3),
        completion_day=round(completion_day, 3),
        billing_amount=billing_amount,
        billing_type=billing_type,
        rate=rate,
        ot_rate=ot_rate,
        agreement_used=ag_label,
        blocked=False,
        block_reason="",
    )


