"""
tests/test_andromeda_split.py — בדיקות לטעינת קבצי Andromeda וחלוקת עלויות.
"""
import pytest
from pathlib import Path
import pandas as pd

from core.andromeda_loader import load_andromeda_hours, find_andromeda_file

DATA_DIR = Path(__file__).parent.parent / "data"
FOLDER_03_2026 = DATA_DIR / "03-2026"


def _get_andro_path():
    p = find_andromeda_file(FOLDER_03_2026)
    if p is None:
        pytest.skip("אין קובץ Andromeda ב-03-2026")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# 1. בדיקת מבנה הפלט
# ─────────────────────────────────────────────────────────────────────────────
def test_andromeda_output_schema():
    df = load_andromeda_hours(_get_andro_path(), "03-2026")
    required = {"employee_id", "employee_name", "client", "site",
                "work_days", "total_hours", "h100", "h125", "h150",
                "h175", "h200", "month", "source"}
    missing = required - set(df.columns)
    assert not missing, f"חסרות עמודות: {missing}"
    assert (df["source"] == "AndromedaExcel").all()
    assert (df["month"] == "03-2026").all()
    assert df["employee_id"].str.match(r"^\d+$").all()


# ─────────────────────────────────────────────────────────────────────────────
# 2. עובדים רב-אתריים
# ─────────────────────────────────────────────────────────────────────────────
def test_andromeda_loads_multi_site():
    df = load_andromeda_hours(_get_andro_path(), "03-2026")
    multi = df.groupby("employee_id").size()
    assert (multi > 1).sum() >= 40, (
        f"מצופים 40+ עובדים רב-אתריים, נמצאו {(multi>1).sum()}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. עובד 1007 — 6 שורות פיצול, סה"כ ~158h
# ─────────────────────────────────────────────────────────────────────────────
def test_employee_1007_split():
    df = load_andromeda_hours(_get_andro_path(), "03-2026")
    emp = df[df["employee_id"] == "1007"]
    assert len(emp) >= 5, f"עובד 1007 ב-{len(emp)} שורות, מצופה 5+"
    total = emp["total_hours"].sum()
    assert 155 < total < 162, f"שעות 1007 = {total:.2f}, מצופה ~158.31"
    # בראל הנדסה צריך להיות ~79.83h
    barel = emp[emp["site"].str.contains("בראל", na=False)]
    assert not barel.empty, "חסרת שורת בראל לעובד 1007"
    assert 75 < barel["total_hours"].sum() < 85


# ─────────────────────────────────────────────────────────────────────────────
# 4. אחרי build_and_save: שורות גדלו, עלות מחולקת נכון
# ─────────────────────────────────────────────────────────────────────────────
def test_dashboard_assigns_cost_correctly():
    from core.preprocessor import load_cache
    df, _ = load_cache()

    rows_03 = df[df["month"] == "03-2026"]
    assert len(rows_03) >= 280, (
        f"03/2026: {len(rows_03)} שורות, מצופה 280+"
    )

    emp_1007 = df[(df["employee_id"] == "1007") & (df["month"] == "03-2026")]
    assert len(emp_1007) >= 4, (
        f"עובד 1007 ב-{len(emp_1007)} שורות, מצופה 4+"
    )

    total_alloc = emp_1007["allocated_cost"].sum()
    assert 9200 < total_alloc < 9800, (
        f"סך עלות מוקצית 1007 = ₪{total_alloc:,.0f}, מצופה ~₪9,551"
    )

    barel = emp_1007[emp_1007["site"].str.contains("בראל", na=False)]
    if not barel.empty:
        barel_pct = barel["allocated_cost"].sum() / total_alloc
        # בראל = 79.83/158.31 ≈ 50.4% מהעלות
        assert 0.40 < barel_pct < 0.60, (
            f"בראל קיבל {barel_pct:.1%} מהעלות, מצופה ~50%"
        )


def test_total_rows_increased():
    from core.preprocessor import load_cache
    df, _ = load_cache()
    # לפני Andromeda היו 2138 שורות; אחרי — יותר
    assert len(df) > 2138, (
        f"מצופה יותר מ-2138 שורות, נמצאו {len(df)}"
    )
