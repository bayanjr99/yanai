"""
Parse Andromeda payroll PDF.

PDF format (per page = one employee, one month):
  Header line:  "NAME - (#EMPID) - ..."
  Month line:   "ינאי פרסונל ... 02/2026 ..."
  Work row:     "[א-ז]׳ - DD  רגיל  HH:MM - HH:MM  <cols>  HOURS_TO_PAY  SITE"
                All numeric columns are concatenated without spaces.
  Total line:   'סה"כ שעות לתשלום264.51'

Output
------
DataFrame columns: employee_id, employee_name, date, site, hours_to_pay

Validation
----------
Each page sum(hours_to_pay) is compared against the PDF 'סה"כ שעות לתשלום' value.
A warning is logged when they differ by >0.05.
"""

import logging
import re
from datetime import date
from typing import Optional

import pandas as pd
from pypdf import PdfReader

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output column contract
# ---------------------------------------------------------------------------
DAILY_COLS = ["employee_id", "employee_name", "date", "site", "hours_to_pay", "break_hours", "break_source"]

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_RE_EMP_ID     = re.compile(r"#\s*(\d{4,6})")
_RE_MONTH_YEAR = re.compile(r"\b(\d{1,2})/(\d{4})\b")

# Hebrew day letter (alef–zayin) + geresh + " - DD"  OR  "שבת - DD"
_RE_DAY_NUM    = re.compile(r"(?:[א-ז][׳']|שבת)\s*-\s*(\d{1,2})", re.UNICODE)

# Time range e.g. "21:00 - 07:00" (spaces around dash optional)
_RE_TIME_PAIR  = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")

# Exactly N.NN or NN.NN — hours column values (1–2 digits, dot, 2 digits)
_RE_HOUR_VAL   = re.compile(r"\d{1,2}\.\d{2}")

# 'סה"כ שעות לתשלום' followed immediately by the total value
_RE_TOTAL_PAY  = re.compile(r'סה"כ\s*שעות\s*לתשלום\s*([\d.,]+)', re.UNICODE)

# Holiday date patterns like ט״ו בשבט, י״ד, ל׳ — indicates site is on next line
_RE_HOLIDAY_NOTE = re.compile(r"[א-ת]{1,3}[״׳][א-ת]", re.UNICODE)

# Rows to skip entirely (summaries, non-work days, closures, web URLs)
_SKIP_ROW_RE = re.compile(
    r"אתר\s+סגור"
    r"|ללא\s+דיווח"
    r"|לא\s+לדיווח"
    r"|לא\s+רלוונטי"
    r"|סה[\"״]כ"
    r"|שעות\s+[שחנ]"
    r"|שעות\s+לתשלום"
    r"|שעות\s+לדיווח"
    r"|ימי\s+עבודה"
    r"|זכאויות"
    r"|מחלה|חופשה|חגים"
    r"|סיכום\s+חודשי"
    r"|http"
    r"|פרויקט.*מועד",
    re.UNICODE,
)

_STOP_WORDS = frozenset({
    "סה", "כ", "ימי", "עבודה", "שעות", "הפסקה", "שלמות", "דיווח",
    "זכויות", "חופשה", "מחלה", "חגים", "נסיעות", "תאריך", "פרויקט",
    "לקוח", "עובד", "מספר", "ינאי", "פרסונל", "סיכום",
    "חודש", "שם", "כולל", "ללא", "חברה", "מחברה",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(s: str) -> float:
    try:
        return float(str(s).replace(",", ".").strip())
    except (ValueError, AttributeError, TypeError):
        return 0.0


def _extract_emp_id_name(lines: list[str]) -> tuple[str, str]:
    """Return (employee_id, employee_name) from the first ~30 lines of a page."""
    emp_id = emp_name = ""
    for line in lines[:30]:
        m = _RE_EMP_ID.search(line)
        if m:
            emp_id = m.group(1)
            candidate = _RE_EMP_ID.sub("", line)
            candidate = re.sub(r"[:#\-\d./()AC]", " ", candidate).strip()
            # Try Hebrew name first
            heb_words = [w for w in re.findall(r"[א-ת]{2,}", candidate, re.UNICODE)
                         if w not in _STOP_WORDS]
            if heb_words:
                emp_name = " ".join(heb_words)
            else:
                # Latin name (e.g. PORNSAK THONGPRADAB)
                lat_words = [w for w in re.findall(r"[A-Za-z]{2,}", candidate)
                             if len(w) > 1]
                if lat_words:
                    emp_name = " ".join(lat_words)
            break
    return emp_id, emp_name


def _extract_page_total(lines: list[str]) -> Optional[float]:
    """Return שעות לתשלום total from 'סה"כ שעות לתשלום' line, or None."""
    for line in reversed(lines):
        m = _RE_TOTAL_PAY.search(line)
        if m:
            return _to_float(m.group(1))
    return None


def _has_hebrew(text: str) -> bool:
    """True if text contains at least one Hebrew word of length > 1."""
    return bool(re.search(r"[א-ת]{2,}", text, re.UNICODE))


# ---------------------------------------------------------------------------
# Row parser  (returns partial result with site=None when holiday-note found)
# ---------------------------------------------------------------------------

def _parse_daily_row(line: str, month: int, year: int) -> Optional[dict]:
    """
    Parse one text line as a daily work entry.

    Returns None when the line is not a parseable work row.
    Returns a dict with site=None when the site appears on the next line
    (holiday note present at end of line).
    """
    if _SKIP_ROW_RE.search(line):
        return None

    day_m = _RE_DAY_NUM.search(line)
    if not day_m:
        return None

    time_m = _RE_TIME_PAIR.search(line)
    if not time_m:
        return None

    # All N.NN matches on the full line
    hour_matches = list(_RE_HOUR_VAL.finditer(line))
    if not hour_matches:
        return None

    last_match   = hour_matches[-1]
    hours_to_pay = _to_float(last_match.group())

    # Break hours = first decimal IF it is < 1.0 h
    # (Andromeda layout: break | 100% | 125% | ... | hours_to_pay)
    # A value < 1.0 means it's a partial-hour break (e.g. 0.50, 0.75).
    # Values ≥ 1.0 at the start mean no break was recorded and the first
    # decimal is already the 100% work hours column.
    first_val   = _to_float(hour_matches[0].group())
    # TODO: this heuristic fails when an employee works < 1h on a day (first_val
    # would be misclassified as a break). A column-position approach (pdfplumber)
    # would be more reliable. break_source marks these rows for future review.
    if first_val < 1.0 and len(hour_matches) > 1:
        break_hours  = first_val
        break_source = "heuristic"
    else:
        break_hours  = 0.0
        break_source = "none"

    # Site = everything after the last N.NN value
    site_raw = line[last_match.end():].strip()

    day = int(day_m.group(1))
    try:
        parsed_date = date(year, month, day)
    except ValueError:
        return None

    # Detect holiday note (e.g. "ט״ו בשבט") — real site is on the next line
    if _RE_HOLIDAY_NOTE.search(site_raw) or not _has_hebrew(site_raw):
        site = None
    else:
        site = site_raw if _has_hebrew(site_raw) else None

    return {
        "date":         parsed_date,
        "site":         site,
        "hours_to_pay": hours_to_pay,
        "break_hours":  break_hours,
        "break_source": break_source,
        "start_time":   time_m.group(1),
        "end_time":     time_m.group(2),
    }


# ---------------------------------------------------------------------------
# Page-level parsing
# ---------------------------------------------------------------------------

def _parse_page(text: str) -> tuple[list[dict], Optional[float]]:
    """Return (daily_rows, page_total) for one PDF page."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    emp_id, emp_name = _extract_emp_id_name(lines)
    if not emp_id:
        return [], None

    # Extract month/year from header (first 10 lines)
    month, year = 1, 2000
    for line in lines[:10]:
        m = _RE_MONTH_YEAR.search(line)
        if m:
            month, year = int(m.group(1)), int(m.group(2))
            break

    page_total = _extract_page_total(lines)

    rows = []
    for i, line in enumerate(lines):
        parsed = _parse_daily_row(line, month, year)
        if parsed is None:
            continue

        site = parsed["site"]

        if site is None:
            # Site is on the next line (holiday note pushed it there)
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                # Use next line if it looks like a site name
                if (_has_hebrew(next_line)
                        and not _RE_DAY_NUM.search(next_line)
                        and not _RE_TIME_PAIR.search(next_line)
                        and not _SKIP_ROW_RE.search(next_line)):
                    site = next_line
            if not site:
                continue

        rows.append({
            "employee_id":   emp_id,
            "employee_name": emp_name,
            "date":          parsed["date"],
            "site":          site,
            "hours_to_pay":  parsed["hours_to_pay"],
            "break_hours":   parsed["break_hours"],
            "break_source":  parsed.get("break_source", "none"),
        })

    return rows, page_total


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_pdf(path: str, report_month=None) -> pd.DataFrame:
    """
    Parse an Andromeda payroll PDF and return one row per worked day.

    Columns: employee_id, employee_name, date, site, hours_to_pay.

    Logs a warning for each page where sum(hours_to_pay) differs from
    the PDF 'סה"כ שעות לתשלום' value by more than 0.05 hours.
    """
    reader = PdfReader(path)
    all_rows: list[dict] = []

    for page_num, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        rows, page_total = _parse_page(text)

        if rows:
            computed = sum(r["hours_to_pay"] for r in rows)
            if page_total is not None:
                diff = abs(computed - page_total)
                if diff > 0.05:
                    emp = rows[0]["employee_id"]
                    log.warning(
                        "Page %d (emp %s): parsed %.2f h ≠ PDF total %.2f h (Δ %.2f)",
                        page_num, emp, computed, page_total, diff,
                    )
        elif page_total is not None:
            log.warning(
                "Page %d: no daily rows parsed (PDF total = %.2f h)",
                page_num, page_total,
            )

        all_rows.extend(rows)

    if not all_rows:
        return pd.DataFrame(columns=DAILY_COLS)

    return pd.DataFrame(all_rows)[DAILY_COLS].reset_index(drop=True)
