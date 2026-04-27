"""
Billing system orchestrator — Phase 1.

Pipeline (PDF path)
-------------------
  1. validate_pdf()  → per-employee hour validation; STOP on unexpected mismatch
  2. parse_pdf()     → daily rows
  3. load overrides  → rate overrides per employee/site
  4. _bill_daily()   → rules_engine.apply_rules() per day
  5. _aggregate()    → monthly sum + monthly_min (never per-day)
  6. save_reports()  → final + issues Excel
  7. write_debug()   → debug_full Excel (always written)
"""

from __future__ import annotations

import os
import sys

import pandas as pd

from core.pdf_parser    import parse_pdf
from core.excel_loaders import load_agreements, load_costs, load_overrides
from core.validation    import validate_pdf, results_to_dicts, ValidationError
from core.rules_engine  import apply_rules, apply_monthly_min
from core.debug_writer  import write_debug
from core.matcher       import resolve_client, find_agreement, is_internal
from core.report_builder import save_reports, save_organized_reports


# ---------------------------------------------------------------------------
# Daily billing  (PDF path)
# ---------------------------------------------------------------------------

def _bill_daily(
    daily_df: pd.DataFrame,
    agreements: list[dict],
    costs: dict,
    overrides: dict | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    For each daily row: resolve client → find agreement → compute
    daily completion and billing.

    Returns (detail_daily_df, issue_rows).

    detail_daily_df columns:
      employee_id, employee_name, date, site, client, match_reason,
      billing_type, rate, daily_min, monthly_min,
      hours_to_pay, completion_day, billable_hours_day, billing_day
    """
    if overrides is None:
        overrides = {}
    detail_rows: list[dict] = []
    issue_rows:  list[dict] = []
    seen_issues: set[tuple] = set()   # avoid duplicate issues for same emp/site

    for _, row in daily_df.iterrows():
        emp_id    = str(row["employee_id"])
        emp_name  = str(row["employee_name"])
        site      = str(row["site"])
        hours       = float(row["hours_to_pay"])
        break_hours = float(row.get("break_hours") or 0)
        work_date   = row["date"]

        client, _, worker_country = resolve_client(emp_id, site, costs)

        if not client:
            key = (emp_id, site, "עובד חסר בעלויות")
            if key not in seen_issues:
                seen_issues.add(key)
                issue_rows.append({
                    "employee_id":   emp_id,
                    "employee_name": emp_name,
                    "site":          site,
                    "issue_type":    "עובד חסר בעלויות",
                    "description":   (
                        f"עובד {emp_id} ({emp_name}) לא נמצא בקובץ עלות מעביד."
                    ),
                    "suggested_fix": "הוסף עובד לקובץ employees_cost.xlsx",
                })
            client         = site
            worker_country = ""

        if is_internal(client):
            continue

        agreement, match_reason = find_agreement(
            client, site, agreements, country=worker_country)

        # Override rate from overrides.xlsx if present
        override_rate = (
            overrides.get((emp_id, site)) or
            overrides.get((emp_id, ""))
        )

        # ── apply_rules handles include_breaks, daily_min, fail-safe ────────
        result = apply_rules(hours, break_hours, agreement, override_rate)

        if result.blocked:
            key = (emp_id, site, result.block_reason)
            if key not in seen_issues:
                seen_issues.add(key)
                issue_rows.append({
                    "employee_id":   emp_id,
                    "employee_name": emp_name,
                    "site":          site,
                    "issue_type":    result.block_reason,
                    "description":   (
                        f"עובד {emp_id} אתר '{site}': {result.block_reason}. "
                        "החיוב יהיה 0 ₪."
                    ),
                    "suggested_fix": (
                        "הוסף הסכם בקובץ agreements.xlsx"
                        if "חסר" in result.block_reason
                        else "עדכן מחיר הסכם"
                    ),
                })

        monthly_min = float(agreement.get("monthly_min") or 0) if agreement else 0.0
        daily_min   = float(agreement.get("daily_min")   or 0) if agreement else 0.0

        detail_rows.append({
            # identity
            "employee_id":        emp_id,
            "employee_name":      emp_name,
            "date":               work_date,
            "site":               site,
            "country":            worker_country,
            "client":             client,
            "match_reason":       match_reason if agreement else "אין הסכם",
            # agreement
            "billing_type":       result.billing_type,
            "rate":               result.rate,
            "ot_rate":            result.ot_rate,
            "ot_threshold":       float(agreement.get("ot_threshold") or 10) if agreement else 0.0,
            "daily_min":          daily_min,
            "monthly_min":        monthly_min,
            "include_breaks":     result.blocked is False and bool(agreement.get("include_breaks") if agreement else False),
            "agreement_used":     result.agreement_used,
            # billing
            "hours_to_pay":       result.hours_to_pay,
            "break_hours":        result.break_hours,
            "billable_hours_day": result.billable_hours,
            "ot_hours_day":       result.ot_hours,
            "completion_day":     result.completion_day,
            "billing_day":        result.billing_amount,
            # fail-safe
            "blocked":            result.blocked,
            "block_reason":       result.block_reason,
        })

    return pd.DataFrame(detail_rows), issue_rows


def _aggregate(
    detail_df: pd.DataFrame,
    costs: dict,
    issue_rows: list[dict],
) -> pd.DataFrame:
    """
    Aggregate daily billing rows to monthly (employee × site).

    After summing daily completion, applies monthly_min if the total
    billable hours are still below the threshold.

    Returns a detail DataFrame compatible with report_builder.save_reports().
    """
    if detail_df.empty:
        return pd.DataFrame()

    grp_keys = ["employee_id", "employee_name", "site"]

    agg = detail_df.groupby(grp_keys, as_index=False).agg(
        client          = ("client",           "first"),
        match_reason    = ("match_reason",     "first"),
        billing_type    = ("billing_type",     "first"),
        rate            = ("rate",             "first"),
        ot_rate         = ("ot_rate",          "first"),
        ot_threshold    = ("ot_threshold",     "first"),
        monthly_min     = ("monthly_min",      "first"),
        daily_min       = ("daily_min",        "first"),
        days            = ("date",             "count"),
        total_hours     = ("hours_to_pay",     "sum"),
        total_break_hours = ("break_hours",    "sum"),
        ot_hours        = ("ot_hours_day",     "sum"),
        completion_day  = ("completion_day",   "sum"),
        billable_sub    = ("billable_hours_day","sum"),
        billing_sub     = ("billing_day",      "sum"),
    )

    # Sort so each employee's site WITH monthly_min appears first.
    # This makes completion always go to the site that carries the guarantee,
    # not to a random site chosen by DataFrame order.
    agg = agg.sort_values(
        ["employee_id", "monthly_min"],
        ascending=[True, False],
    ).reset_index(drop=True)

    # Pre-compute each employee's total billable hours across ALL sites so that
    # monthly_min completion is applied once per employee (not once per site).
    emp_billable_totals: dict[str, float] = (
        agg.groupby("employee_id")["billable_sub"].sum().to_dict()
    )
    seen_monthly_completion: set[str] = set()   # employees who already received completion

    final_rows: list[dict] = []
    for _, row in agg.iterrows():
        billing_type = str(row["billing_type"])
        rate         = float(row["rate"])
        monthly_min  = float(row["monthly_min"] or 0)
        daily_min    = float(row["daily_min"]   or 0)
        days         = int(row["days"])
        billable     = float(row["billable_sub"])
        billing_amt  = float(row["billing_sub"])
        completion_monthly = 0.0

        emp_id_str = str(row["employee_id"])

        # Apply monthly_min ONCE per employee (across all sites they worked at).
        # Using the employee's total hours prevents double-counting when one
        # employee appears at multiple sites.
        if (billing_type == "hourly"
                and monthly_min > 0
                and daily_min == 0
                and emp_id_str not in seen_monthly_completion):
            emp_total = emp_billable_totals.get(emp_id_str, billable)
            if emp_total < monthly_min:
                completion_monthly = monthly_min - emp_total
                seen_monthly_completion.add(emp_id_str)
                billable    += completion_monthly
                billing_amt  = round(billable * rate, 2)

        site   = str(row["site"])
        _, emp_cost, _ = resolve_client(emp_id_str, site, costs)

        completion_added = round(float(row["completion_day"]) + completion_monthly, 2)
        profit     = round(billing_amt - emp_cost, 2)
        margin_pct = round(profit / billing_amt * 100, 1) if billing_amt > 0 else 0.0

        if billing_amt == 0 and float(row["total_hours"]) > 0:
            issue_rows.append({
                "employee_id":   emp_id_str,
                "employee_name": str(row["employee_name"]),
                "site":          site,
                "issue_type":    "חיוב אפס עם שעות",
                "description":   (
                    f"חיוב יצא 0 ₪ למרות {row['total_hours']:.1f} שעות עבודה. "
                    "ייתכן שהמחיר בהסכם הוא 0."
                ),
            })

        final_rows.append({
            "employee_id":      emp_id_str,
            "employee_name":    str(row["employee_name"]),
            "client":           str(row["client"]),
            "site":             site,
            "match_reason":     str(row["match_reason"]),
            "billing_type":     billing_type,
            "rate":             rate,
            "ot_rate":          float(row.get("ot_rate") or 0),
            "ot_threshold":     float(row.get("ot_threshold") or 0),
            "monthly_min":      monthly_min,
            "daily_min":        daily_min,
            "days":             days,
            "total_hours":      round(float(row["total_hours"]), 2),
            "break_hours":      round(float(row.get("total_break_hours") or 0), 2),
            "ot_hours":         round(float(row.get("ot_hours") or 0), 2),
            "billable_hours":   round(billable, 2),
            "completion_added": completion_added,
            "billing_amount":   billing_amt,
            "cost":             emp_cost,
            "profit":           profit,
            "margin_pct":       margin_pct,
        })

    result_df = pd.DataFrame(final_rows)

    # Post-billing sanity validation (completion > 50 %, negative billing)
    from core.validation import validate_billing_results
    issue_rows.extend(validate_billing_results(result_df))

    return result_df


# ---------------------------------------------------------------------------
# Excel fallback  (monthly billing — no daily rows available)
# ---------------------------------------------------------------------------

def _load_hours_excel(path: str) -> pd.DataFrame:
    """Load an Excel hours file as a monthly aggregate DataFrame."""
    from core.excel_loaders import _find_col

    df = pd.read_excel(path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    COL_MAP: dict[str, list[str]] = {
        "employee_id":   ["מספר עובד", "מס עובד", "employee_id", "id", "מס' עובד"],
        "employee_name": ["שם עובד", "עובד", "employee_name", "שם"],
        "site":          ["שם פרויקט", "פרויקט", "אתר", "site", "locality"],
        "days":          ["ימי עבודה", "ימים", "days"],
        "total_hours":   ['סה"כ שעות', "total_hours", "שעות", "שעות עבודה"],
    }

    result = {}
    for field, candidates in COL_MAP.items():
        col = _find_col(df, candidates)
        if col:
            if field in ("employee_id", "employee_name", "site"):
                result[field] = df[col].astype(str).str.strip()
            else:
                result[field] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""), errors="coerce"
                ).fillna(0.0)
        else:
            result[field] = "" if field in ("employee_id", "employee_name", "site") else 0.0

    return pd.DataFrame(result)


def _bill_monthly(
    monthly_df: pd.DataFrame,
    agreements: list[dict],
    costs: dict,
) -> tuple[pd.DataFrame, list[dict]]:
    """Old monthly billing path (used for Excel input only)."""
    from core.billing_engine import calculate

    detail_rows: list[dict] = []
    issue_rows:  list[dict] = []

    for _, row in monthly_df.iterrows():
        emp_id   = str(row.get("employee_id", "")).strip()
        emp_name = str(row.get("employee_name", "")).strip()
        site     = str(row.get("site", "")).strip()

        client, emp_cost, worker_country = resolve_client(emp_id, site, costs)

        if not client:
            issue_rows.append({
                "employee_id":   emp_id,
                "employee_name": emp_name,
                "site":          site,
                "issue_type":    "עובד לא נמצא בקובץ עלויות",
                "description":   f"עובד {emp_id} לא קיים בקובץ עלות מעביד.",
            })
            client = site
            worker_country = ""

        if is_internal(client):
            continue

        agreement, match_reason = find_agreement(client, site, agreements, country=worker_country)

        if agreement is None:
            issue_rows.append({
                "employee_id":   emp_id,
                "employee_name": emp_name,
                "site":          site,
                "issue_type":    "הסכם חסר",
                "description":   f"לא נמצא הסכם ללקוח '{client}' אתר '{site}'.",
            })
            billing_amount = billable_hours = completion_added = 0.0
        else:
            result = calculate(row.to_dict(), agreement)
            billing_amount   = result.billing_amount
            billable_hours   = result.billable_hours
            completion_added = result.completion_added

            if billing_amount == 0 and float(row.get("total_hours") or 0) > 0:
                issue_rows.append({
                    "employee_id":   emp_id,
                    "employee_name": emp_name,
                    "site":          site,
                    "issue_type":    "חיוב אפס עם שעות",
                    "description":   f"חיוב 0 ₪ למרות {row.get('total_hours', 0):.1f}h.",
                })

        profit     = billing_amount - emp_cost
        margin_pct = round(profit / billing_amount * 100, 1) if billing_amount > 0 else 0.0

        detail_rows.append({
            "employee_id":      emp_id,
            "employee_name":    emp_name,
            "client":           client,
            "site":             site,
            "match_reason":     match_reason if agreement else "אין הסכם",
            "days":             float(row.get("days") or 0),
            "total_hours":      float(row.get("total_hours") or 0),
            "billable_hours":   billable_hours,
            "completion_added": completion_added,
            "billing_amount":   billing_amount,
            "cost":             emp_cost,
            "profit":           profit,
            "margin_pct":       margin_pct,
        })

    return pd.DataFrame(detail_rows), issue_rows


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(session_dir: str) -> tuple[str, str]:
    data_dir   = os.path.join(session_dir, "data")
    output_dir = os.path.join(session_dir, "output")

    # ── load reference data ──────────────────────────────────────────────────
    print("טוען נתונים...")
    agreements = load_agreements(os.path.join(data_dir, "agreements.xlsx"))
    costs      = load_costs(os.path.join(data_dir, "employees_cost.xlsx"))
    overrides  = load_overrides(os.path.join(data_dir, "overrides.xlsx"))
    print(f"  {len(agreements)} הסכמים | {len(costs)} עובדים | {len(overrides)} overrides")

    pdf_path   = os.path.join(data_dir, "hours.pdf")
    excel_path = os.path.join(data_dir, "hours.xlsx")

    validation_dicts: list[dict] = []
    detail_daily_df = pd.DataFrame()

    if os.path.exists(pdf_path):
        # ── Step 1: Validate PDF ─────────────────────────────────────────────
        print("  מאמת PDF (שעות לתשלום מול סה\"כ PDF)...")
        try:
            val_results = validate_pdf(pdf_path)
        except ValidationError as e:
            print(f"\n{'='*60}")
            print("❌  VALIDATION FAILED — ביצוע הופסק")
            print(str(e))
            print(f"{'='*60}")
            raise

        validation_dicts = results_to_dicts(val_results)
        n_pass = sum(1 for r in val_results if r.status == "PASS")
        n_exp  = sum(1 for r in val_results if r.status == "EXPECTED")
        n_warn = sum(1 for r in val_results if r.status == "WARN")
        print(f"  אימות: {n_pass} PASS | {n_exp} EXPECTED | {n_warn} WARN")

        # ── Step 2: Parse PDF ────────────────────────────────────────────────
        print(f"  קורא PDF: {pdf_path}")
        daily_df = parse_pdf(pdf_path)
        n_emp = daily_df["employee_id"].nunique() if not daily_df.empty else 0
        print(f"  {len(daily_df)} שורות יומיות מ-{n_emp} עובדים")

        if daily_df.empty:
            raise ValueError("לא נמצאו שורות יומיות ב-PDF.")

        # ── Step 3: Daily billing via rules_engine ───────────────────────────
        print("  מחשב חיוב יומי...")
        detail_daily_df, issue_rows = _bill_daily(
            daily_df, agreements, costs, overrides)
        print("  מאגד לחודשי...")
        detail_df = _aggregate(detail_daily_df, costs, issue_rows)
        print(f"  {len(detail_df)} שורות סופיות (עובד × אתר)")

    elif os.path.exists(excel_path):
        print(f"  קורא Excel (נתיב גיבוי): {excel_path}")
        monthly_df = _load_hours_excel(excel_path)
        detail_df, issue_rows = _bill_monthly(monthly_df, agreements, costs)

    else:
        raise FileNotFoundError(
            "לא נמצא קובץ שעות. "
            "הכנס hours.pdf (מ-Andromeda) או hours.xlsx לתיקיית data/."
        )

    # ── Step 4: Save reports (organized by month) ────────────────────────────
    issues_df = pd.DataFrame(issue_rows)

    # Derive month string from parsed dates (fallback: current month)
    month_str = ""
    if not detail_daily_df.empty and "date" in detail_daily_df.columns:
        month_str = pd.to_datetime(detail_daily_df["date"].min()).strftime("%Y-%m")
    if not month_str:
        from datetime import datetime as _dt
        month_str = _dt.now().strftime("%Y-%m")

    billing_path, issues_path, profit_path = save_organized_reports(
        detail_df, issues_df, output_dir, month_str
    )
    month_dir = os.path.dirname(billing_path)

    # ── Step 5: Write debug file ─────────────────────────────────────────────
    daily_debug_rows = (
        detail_daily_df.to_dict("records") if not detail_daily_df.empty else []
    )
    debug_path = write_debug(daily_debug_rows, validation_dicts, month_dir)

    # ── Summary ──────────────────────────────────────────────────────────────
    total_billing = detail_df["billing_amount"].sum() if not detail_df.empty else 0.0
    total_cost    = detail_df["cost"].sum()            if not detail_df.empty else 0.0
    total_profit  = total_billing - total_cost

    print("\n=== תוצאות ===")
    print(f'סה"כ לחיוב:  ₪{total_billing:>12,.2f}')
    print(f'סה"כ עלויות: ₪{total_cost:>12,.2f}')
    if total_billing > 0:
        print(f'סה"כ רווח:   ₪{total_profit:>12,.2f}  ({total_profit / total_billing * 100:.1f}%)')
    blocked = sum(1 for r in issue_rows if r.get("issue_type") in ("הסכם חסר", "תעריף הסכם הוא 0 ₪"))
    print(f"חריגים: {len(issue_rows)} | חסומים: {blocked}")
    print(f"\nדוח חיוב:   {billing_path}")
    print(f"דוח חריגים: {issues_path}")
    print(f"קובץ debug: {debug_path}")

    return billing_path, issues_path


if __name__ == "__main__":
    session_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    try:
        run(session_dir)
    except Exception as exc:
        print(f"שגיאה: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
