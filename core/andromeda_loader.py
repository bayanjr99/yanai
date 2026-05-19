"""
core/andromeda_loader.py — טעינת קובץ שעות מאנדרומדה (Excel).

זהו המקור המדויק ביותר לשעות עבודה. כל שורה = (עובד × לקוח × אתר)
בחודש נתון. עובדים שעבדו בכמה אתרים יופיעו במספר שורות עם פיצול
מדויק של השעות.

מבנה הקובץ:
  שורה 1: כותרת ("ינאי פרסונל בע"מ - (MM/YYYY)")
  שורה 2: ריקה
  שורה 3: כותרות (שם לקוח, שם פרויקט, מספר עובד, ...)
  שורה 4: ריקה
  שורה 5+: נתונים
"""
from __future__ import annotations

import re
import warnings as _warnings
from pathlib import Path

import pandas as pd


# ── Column name patterns ───────────────────────────────────────────────────────

_COL_MAP = {
    "work_days":              ["ימי עבודה", "ימי"],
    "break_hours":            ["הפסקה"],
    "h100":                   ["100%"],
    "h125":                   ["125%"],
    "h150":                   ["150%"],
    "h175":                   ["175%"],
    "h200":                   ["200%"],
    "total_hours":            ['סה"כ שעות', "סהכ שעות", "total"],
    "sick_paid":              ["מחלה בתשלום"],
    "sick_unpaid":            ["מחלה לא בתשלום"],
    "vacation":               ["חופשה"],
    "visa_intern":            ["אינטר ויזה", "ויזה"],
    "work_injury":            ["תאונת עבודה", "תאונה"],
    "holiday":                ["חג"],
    "rain_paid":              ["יום גשם בתשלום"],
    "rain_unpaid":            ["יום גשם לא בתשלום"],
    "total_reportable_hours": ['סה"כ שעות לדיווח', "סהכ שעות לדיווח"],
}


def _find_col(df: pd.DataFrame, patterns: list[str]) -> str | None:
    """מוצא עמודה לפי רשימת שמות אפשריים."""
    for col in df.columns:
        col_clean = str(col).strip().replace('"', '"').replace('"', '"')
        for pat in patterns:
            if pat in col_clean or col_clean in pat:
                return col
    return None


def load_andromeda_hours(path: str | Path, month: str) -> pd.DataFrame:
    """
    טוען קובץ Excel של אנדרומדה ומחזיר DataFrame תקני.

    Returns DataFrame עם עמודות:
      month, employee_id, employee_name, client, site, work_days,
      total_hours, h100, h125, h150, h175, h200,
      sick_paid, sick_unpaid, vacation, holiday, work_injury,
      visa_intern, rain_paid, rain_unpaid, break_hours,
      total_reportable_hours, source
    """
    path = Path(path)

    # מצא את שורת הכותרות
    raw = pd.read_excel(str(path), sheet_name=0, header=None)

    if raw.shape[1] < 5:
        raise ValueError(
            f"קובץ Andromeda נראה כ-pivot table לא מורחב "
            f"({raw.shape[1]} עמודות במקום ~21). "
            f"יצא מחדש מאנדרומדה כטבלה רגילה (לא pivot)."
        )

    header_row: int | None = None
    for i in range(min(10, len(raw))):
        vals = [str(v) for v in raw.iloc[i].tolist() if pd.notna(v)]
        if any("שם לקוח" in v or "מספר עובד" in v for v in vals):
            header_row = i
            break

    if header_row is None:
        raise ValueError(f"לא מצאתי שורת כותרות ב-{path}")

    # אזהרה אם כותרת הקובץ לא תואמת לשם התיקייה
    title = str(raw.iloc[0, 0]) if pd.notna(raw.iloc[0, 0]) else ""
    m_match = re.search(r'(\d{2})/(\d{4})', title)
    if m_match:
        title_month = f"{m_match.group(1)}-{m_match.group(2)}"
        if title_month != month:
            _warnings.warn(
                f"[andromeda] {path.name}: file title says {title_month} but folder is {month}",
                stacklevel=2,
            )

    # קרא מחדש עם header במקום הנכון
    df = pd.read_excel(str(path), sheet_name=0, header=header_row)

    # הסר שורות ריקות לחלוטין
    df = df.dropna(how="all").reset_index(drop=True)

    # זיהוי עמודות קבועות
    client_col = _find_col(df, ["שם לקוח"])
    site_col   = _find_col(df, ["שם פרויקט"])
    empid_col  = _find_col(df, ["מספר עובד"])
    name_col   = _find_col(df, ["שם עובד"])

    if empid_col is None:
        raise ValueError(f"לא מצאתי עמודת 'מספר עובד' ב-{path}")

    # סנן שורות ללא employee_id תקין (מספר בלבד)
    df = df[df[empid_col].notna()].copy()
    df = df[df[empid_col].astype(str).str.strip().str.match(r"^\d+\.?\d*$")].copy()
    df = df.reset_index(drop=True)

    if df.empty:
        return pd.DataFrame()

    # בנה DataFrame פלט
    out = pd.DataFrame()
    out["employee_id"] = df[empid_col].apply(
        lambda x: str(int(float(x))) if pd.notna(x) else ""
    )
    out["employee_name"] = (
        df[name_col].astype(str).str.strip() if name_col else ""
    )
    out["client"] = (
        df[client_col].astype(str).str.strip() if client_col else ""
    )
    out["site"] = (
        df[site_col].astype(str).str.strip() if site_col else ""
    )

    # עמודות מספריות
    for new_col, patterns in _COL_MAP.items():
        src = _find_col(df, patterns)
        out[new_col] = pd.to_numeric(df[src], errors="coerce").fillna(0.0) if src else 0.0

    out["month"]  = month
    out["source"] = "AndromedaExcel"

    # שמור שורה אם יש בה כל פעילות כלשהי: שעות עבודה, ימי עבודה,
    # או היעדרות מתועדת (מחלה / חופשה / חג / גשם / תאונה / דיווח).
    # שורות "ריקות" אמיתיות (כגון "לא לדיווח" עם 0 בכל השדות) — נזרקות.
    _ACTIVITY_COLS = [
        "total_hours", "work_days",
        "sick_paid", "sick_unpaid", "vacation", "holiday",
        "rain_paid", "rain_unpaid", "work_injury",
        "total_reportable_hours",
    ]
    _activity = sum(
        out[c] for c in _ACTIVITY_COLS if c in out.columns
    )
    out = out[_activity > 0].reset_index(drop=True)

    # בדיקת תקינות: אין כפילויות (emp × client × site)
    dup_mask = out.duplicated(subset=["employee_id", "client", "site"], keep=False)
    if dup_mask.any():
        n_dup = int(dup_mask.sum())
        _warnings.warn(
            f"[andromeda] {month}: {n_dup} duplicate (emp,client,site) rows — aggregating",
            stacklevel=2,
        )
        num_cols = list(_COL_MAP.keys())
        agg_dict = {c: "sum" for c in num_cols if c in out.columns}
        agg_dict["employee_name"] = "first"
        out = (
            out.groupby(
                ["employee_id", "client", "site", "month", "source"],
                as_index=False,
            ).agg(agg_dict)
        )

    return out


def find_andromeda_file(month_dir: Path) -> Path | None:
    """
    מחזיר את קובץ אנדרומדה בתיקיית חודש, או None.

    תבניות שמות:
      employeeHoursAndromeda_*.xlsx
      hoursAndromeda.xlsx
      andromeda*.xlsx
      *אנדרומדה*.xlsx
    """
    if not month_dir.exists() or not month_dir.is_dir():
        return None
    for f in month_dir.iterdir():
        if not f.is_file() or f.suffix.lower() != ".xlsx":
            continue
        n = f.name.lower()
        if (
            "andromeda" in n
            or "אנדרומדה" in f.name
            or "employeehours" in n
        ):
            return f
    return None
