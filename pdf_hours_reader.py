"""
קורא שעות עובדים מ-PDF של מערכת דנזר/ACL.
כל עמוד = עובד אחד לחודש אחד.

פירסום שעות:
  - 100%, 125%, 150%, 175%, 200% — מהשורה החודשית (שורת "שעות חודשיות")
  - סה"כ שעות לתשלום — מחולש כ-100%+125%+150%+175%+200%
    (לא "שעות לדיווח" שיכולה לכלול שעות לא בתשלום)
  - שם אתר — משורות הימים

מה שחסר ב-PDF → נלקח מ-employees_cost.xlsx (ב-main.py):
  - שם לקוח (CustomerName)
"""

import re
from collections import Counter

import pandas as pd
from pypdf import PdfReader


# ──────────────────────────────────────────────────────────
# פירסום שורת "שעות חודשיות"
# ──────────────────────────────────────────────────────────
def _parse_monthly_row(monthly_raw: str, h100: float, h_pay: float) -> tuple:
    """
    מחלץ h125, h150, h175, h200 משורת "שעות חודשיות".

    הפורמט: [break][h100][h125][h150][h175][h200][h_pay]  (ללא רווחים)
    אנחנו יודעים h100 ו-h_pay מהסיכום, ולכן נשתמש בהם כעוגנים.

    החזרה: (h125, h150, h175, h200)
    """
    if not monthly_raw or h100 is None or h_pay is None:
        return 0.0, 0.0, 0.0, 0.0

    h100_s = f"{h100:.2f}"
    h_pay_s = f"{h_pay:.2f}"

    # מצא h100 בשרשרת
    idx = monthly_raw.find(h100_s)
    if idx == -1:
        return 0.0, 0.0, 0.0, 0.0

    after_h100 = monthly_raw[idx + len(h100_s):]

    # הסר את h_pay מהסוף
    pay_idx = after_h100.rfind(h_pay_s)
    if pay_idx == -1:
        return 0.0, 0.0, 0.0, 0.0

    overtime_s = after_h100[:pay_idx]

    # פרוס מספרים: X.XX (עשרוני) או 0 (אפס בודד)
    parts = re.findall(r'\d+\.\d+|0', overtime_s)
    h125 = float(parts[0]) if len(parts) > 0 else 0.0
    h150 = float(parts[1]) if len(parts) > 1 else 0.0
    h175 = float(parts[2]) if len(parts) > 2 else 0.0
    h200 = float(parts[3]) if len(parts) > 3 else 0.0

    return h125, h150, h175, h200


# ──────────────────────────────────────────────────────────
# חילוץ שם אתר משורות הימים
# ──────────────────────────────────────────────────────────
def _extract_site(text: str) -> str:
    """מחזיר את שם האתר הכי נפוץ בעמוד"""
    candidates = []
    lines = text.split("\n")

    skip_kw = {
        "ינאי", "חברה", "יום", "סוג", "שעות", 'סה"כ', "ימי", "זכאו",
        "חופשה", "מחלה", "חג", "http", "מערכת", "דנזר", "תלושי",
    }

    for i, line in enumerate(lines):
        line = line.strip()
        if not line or any(kw in line for kw in skip_kw):
            continue

        is_day = bool(re.match(r"^[א-ת][׳׳]?\s*-\s*\d{2}", line)) or line.startswith("שבת")

        if is_day:
            m = re.search(r"[\d.]+\s*([א-ת][א-ת\s\-\"\'./()]{1,50}?)\s*$", line)
            if m:
                c = m.group(1).strip()
                if c and "״" not in c and "׳" not in c:
                    candidates.append(c)
            elif i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt and re.match(r"^[א-ת][א-ת\s\-\"\'./()]{2,50}$", nxt):
                    candidates.append(nxt)
        elif re.match(r"^[א-ת][א-ת\s\-\"\'./()]{2,50}$", line):
            candidates.append(line)

    return Counter(candidates).most_common(1)[0][0] if candidates else ""


# ──────────────────────────────────────────────────────────
# פירסום עמוד אחד (עובד אחד)
# ──────────────────────────────────────────────────────────
def _parse_page(text: str) -> dict | None:
    # מספר עובד
    emp_m = re.search(r"\(#(\d+)\)", text)
    if not emp_m:
        return None
    emp_id = int(emp_m.group(1))

    # ── סיכום חודשי ──
    def get(pattern):
        m = re.search(pattern, text)
        return float(m.group(1)) if m else 0.0

    days_m = re.search(r"ימי עבודה\s*(\d+)", text)
    pay_m  = re.search(r'סה"כ שעות לתשלום\s*([\d.]+)', text)
    if not days_m or not pay_m:
        return None

    h100    = get(r"שעות רגילות\s*([\d.]+)")
    h_pay   = float(pay_m.group(1))
    brk     = get(r'סה"כ שעות הפסקה\s*([\d.]+)')
    h_total = get(r'סה"כ שעות עבודה\s*([\d.]+)')

    # שעות להשלמה — מה שאנדרומדה מחשבת כחסר מהתקן
    # -1 = שדה לא קיים (עובד שעבד מעל התקן, לא צריך השלמה כלל)
    comp_m  = re.search(r'שעות להשלמה\s*([\d.]+)', text)
    h_comp  = float(comp_m.group(1)) if comp_m else -1.0

    # ── פירסום 125% / 150% / 175% / 200% מהשורה החודשית ──
    monthly_m = re.search(r"שעות חודשיות:([\d.]+)", text)
    monthly_raw = monthly_m.group(1) if monthly_m else ""

    h125, h150, h175, h200 = _parse_monthly_row(monthly_raw, h100, h_pay)

    # בדיקת עקביות: הסכום צריך להיות ≈ h_pay
    computed = round(h100 + h125 + h150 + h175 + h200, 2)
    if abs(computed - h_pay) > 0.1:
        # פירסום נכשל — השתמש רק ב-100% ו-נוספות כסכום
        h_overtime = get(r"שעות נוספות\s*([\d.]+)")
        h125, h150, h175, h200 = h_overtime, 0.0, 0.0, 0.0

    return {
        "_emp_id":     emp_id,
        "_days":       float(days_m.group(1)),
        "_break":      brk,
        "_h100":       h100,
        "_h125":       round(h125, 2),
        "_h150":       round(h150, 2),
        "_h175":       round(h175, 2),
        "_h200":       round(h200, 2),
        "_h_total":    h_total,
        "_h_report":   h_pay,    # שעות לתשלום (לא לדיווח)
        "_h_comp_pdf": h_comp,   # שעות להשלמה כפי שאנדרומדה חישבה (None = אין)
        "_site_pdf":   _extract_site(text),
    }


# ──────────────────────────────────────────────────────────
# ממשק ציבורי
# ──────────────────────────────────────────────────────────
def parse_pdf_hours(path: str) -> pd.DataFrame:
    reader = PdfReader(path)
    rows = []

    for page in reader.pages:
        text = page.extract_text()
        if text:
            result = _parse_page(text)
            if result:
                rows.append(result)

    if not rows:
        raise ValueError("לא נמצאו נתוני עובדים ב-PDF")

    df = pd.DataFrame(rows)
    print(f"[OK] עובדים מה-PDF: {len(df)}")
    print(f"[OK] סה\"כ שעות לתשלום: {df['_h_report'].sum():,.2f}")
    print(f"[OK] סה\"כ ימי עבודה:    {df['_days'].sum():,.0f}")
    return df
