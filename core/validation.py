"""
Validation Engine — Phase 1.

Validates:
  1. Per-employee: sum(hours_to_pay) matches PDF monthly total.
  2. Per-client: billing totals are internally consistent.

Mismatch classification
-----------------------
PASS      – difference ≤ 0.05 h (floating-point tolerance)
EXPECTED  – difference explained by intentionally excluded rows
            (לא לדיווח / לא רלוונטי / חופשה days that appear in PDF total
             but are correctly excluded from billing)
FAIL      – unexpected difference that indicates a parsing bug → STOP

The engine raises ValidationError on any FAIL.
On EXPECTED or PASS it returns a list of ValidationResult records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from pypdf import PdfReader

from core.pdf_parser import _parse_page  # type: ignore[attr-defined]


# ─── regex to detect intentionally excluded lines ────────────────────────────
_EXCLUDED_RE = re.compile(
    r"לא\s+לדיווח|לא\s+רלוונטי|חופשה|מחלה",
    re.UNICODE,
)


# ─── result dataclass ─────────────────────────────────────────────────────────
@dataclass
class ValidationResult:
    page:          int
    employee_id:   str
    employee_name: str
    rows_parsed:   int
    parsed_sum:    float
    pdf_total:     Optional[float]
    diff:          Optional[float]
    status:        str          # PASS | EXPECTED | FAIL | WARN
    note:          str = ""


class ValidationError(RuntimeError):
    """Raised when a hard (unexpected) mismatch is found."""
    def __init__(self, failures: list[ValidationResult]):
        self.failures = failures
        lines = [
            f"  Page {r.page} emp {r.employee_id} ({r.employee_name}): "
            f"parsed={r.parsed_sum:.2f}h  PDF={r.pdf_total:.2f}h  Δ={r.diff:.2f}h"
            for r in failures
        ]
        super().__init__(
            "Validation FAILED — unexpected hour mismatches detected.\n"
            "These employees have fewer parsed hours than the PDF total,\n"
            "without any excluded rows (לא לדיווח / חופשה) to explain the gap.\n"
            "This likely indicates a PDF parsing error.\n\n"
            + "\n".join(lines)
        )


# ─── public API ───────────────────────────────────────────────────────────────

def validate_pdf(pdf_path: str, tolerance: float = 0.05) -> list[ValidationResult]:
    """
    Parse every page and compare sum(hours_to_pay) with the PDF total.

    Parameters
    ----------
    pdf_path  : path to Andromeda PDF
    tolerance : max allowed difference (default 0.05 h)

    Returns
    -------
    List of ValidationResult (one per page that has rows or a PDF total).

    Raises
    ------
    ValidationError – if any page has an unexpected mismatch.
    """
    reader  = PdfReader(pdf_path)
    results: list[ValidationResult] = []
    failures: list[ValidationResult] = []

    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        rows, pdf_total = _parse_page(text)

        if not rows and pdf_total is None:
            continue  # blank / cover page

        if not rows:
            # No parsed rows but a PDF total exists
            has_excluded = bool(_EXCLUDED_RE.search(text))
            status = "EXPECTED" if has_excluded else "WARN"
            results.append(ValidationResult(
                page=page_num, employee_id="?", employee_name="?",
                rows_parsed=0, parsed_sum=0.0,
                pdf_total=pdf_total,
                diff=pdf_total if pdf_total else None,
                status=status,
                note="כל ימי העבודה הוחרגו" if has_excluded else "אין שורות לניתוח",
            ))
            continue

        emp_id   = rows[0]["employee_id"]
        emp_name = rows[0]["employee_name"]
        parsed   = round(sum(r["hours_to_pay"] for r in rows), 2)

        if pdf_total is None:
            results.append(ValidationResult(
                page=page_num, employee_id=emp_id, employee_name=emp_name,
                rows_parsed=len(rows), parsed_sum=parsed,
                pdf_total=None, diff=None,
                status="WARN",
                note="לא נמצאה שורת סה\"כ ב-PDF",
            ))
            continue

        diff = round(abs(parsed - pdf_total), 3)

        if diff <= tolerance:
            status = "PASS"
            note   = ""
        else:
            has_excluded = bool(_EXCLUDED_RE.search(text))
            # EXPECTED only when excluded days explain a shortfall (parsed < pdf_total).
            # If parsed > pdf_total, excluded days cannot explain the surplus — that's a bug.
            if has_excluded and parsed < pdf_total:
                status = "EXPECTED"
                note   = f"הפרש {diff:.2f}h מוסבר ע\"י ימים מוחרגים (לא לדיווח / חופשה)"
            else:
                status = "FAIL"
                note   = f"הפרש {diff:.2f}h לא מוסבר — ייתכן באג ב-parser"

        vr = ValidationResult(
            page=page_num, employee_id=emp_id, employee_name=emp_name,
            rows_parsed=len(rows), parsed_sum=parsed,
            pdf_total=pdf_total, diff=diff,
            status=status, note=note,
        )
        results.append(vr)
        if status == "FAIL":
            failures.append(vr)

    if failures:
        raise ValidationError(failures)

    return results


def validate_billing_results(detail_df) -> list[dict]:
    """
    Post-billing sanity checks on the aggregated monthly detail DataFrame.

    Checks
    ------
    1. completion_added > 50 % of total_hours  → "השלמה חריגה"
    2. billing_amount < 0 with hours > 0        → "חיוב שלילי"

    Returns list of issue dicts compatible with main.py issue_rows format.
    """
    import pandas as pd

    issues: list[dict] = []
    for _, row in detail_df.iterrows():
        emp_id   = str(row.get("employee_id",   ""))
        emp_name = str(row.get("employee_name", ""))
        site     = str(row.get("site",          ""))
        total_h  = float(row.get("total_hours",      0) or 0)
        comp     = float(row.get("completion_added", 0) or 0)
        billing  = float(row.get("billing_amount",   0) or 0)

        if total_h > 0 and comp > total_h * 0.5:
            issues.append({
                "employee_id":   emp_id,
                "employee_name": emp_name,
                "site":          site,
                "issue_type":    "השלמה חריגה",
                "description": (
                    f"השלמה {comp:.1f}h = {comp / total_h * 100:.0f}% "
                    f"מהשעות ({total_h:.1f}h) — בדוק הסכם"
                ),
            })

        if billing < 0 and total_h > 0:
            issues.append({
                "employee_id":   emp_id,
                "employee_name": emp_name,
                "site":          site,
                "issue_type":    "חיוב שלילי",
                "description":   f"חיוב ₪{billing:.2f} עם {total_h:.1f}h עבודה",
            })

    return issues


def results_to_dicts(results: list[ValidationResult]) -> list[dict]:
    return [
        {
            "עמוד":        r.page,
            "מס' עובד":    r.employee_id,
            "שם עובד":     r.employee_name,
            "שורות שנקראו": r.rows_parsed,
            "שעות שנקראו": r.parsed_sum,
            "סה\"כ ב-PDF": r.pdf_total,
            "הפרש":        r.diff,
            "סטטוס":       r.status,
            "הערה":        r.note,
        }
        for r in results
    ]
