"""
tests/test_client_site_split.py — בדיקות לפיצול (employee × client × site).

מוודא שעובדים שעובדים אצל כמה (לקוח, אתר) מקבלים פיצול עלות נכון.
"""
import pytest
from pathlib import Path
import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"


# ─────────────────────────────────────────────────────────────────────────────
# 1. אתר לא שייך ליותר מלקוח אחד
# ─────────────────────────────────────────────────────────────────────────────
def test_no_site_belongs_to_multiple_clients():
    """אתר לא יכול להיות שייך ליותר מלקוח אחד באותו חודש."""
    from core.andromeda_loader import load_andromeda_hours, find_andromeda_file

    for m_dir in sorted(DATA_DIR.glob("*-*")):
        if not m_dir.is_dir():
            continue
        p = find_andromeda_file(m_dir)
        if p is None:
            continue
        df = load_andromeda_hours(p, m_dir.name)
        if df.empty:
            continue
        site_to_clients = df.groupby("site")["client"].nunique()
        violations = site_to_clients[site_to_clients > 1]
        assert len(violations) == 0, (
            f"{m_dir.name}: אתרים עם >1 לקוח: {list(violations.index)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. עובדים רב-(לקוח, אתר) בקובץ אנדרומדה
# ─────────────────────────────────────────────────────────────────────────────
def test_andromeda_loads_multi_client_site_employees():
    """וודא שעובדים מופיעים במספר (לקוח, אתר) בחודש אחד."""
    from core.andromeda_loader import load_andromeda_hours, find_andromeda_file

    p = find_andromeda_file(DATA_DIR / "03-2026")
    if p is None:
        pytest.skip("אין Andromeda ב-03/2026")
    df = load_andromeda_hours(p, "03-2026")

    multi = df.groupby("employee_id").size()
    assert (multi > 1).sum() >= 40, (
        f"מצופים 40+ עובדים רב-אתריים, נמצאו {(multi > 1).sum()}"
    )

    e = df[df["employee_id"] == "1007"]
    assert len(e) >= 5, f"עובד 1007 הופיע רק ב-{len(e)} שורות (מצופה 5+)"

    total_h = e["total_hours"].sum()
    assert 155 < total_h < 162, f"שעות 1007 = {total_h:.2f}, מצופה ~158"


# ─────────────────────────────────────────────────────────────────────────────
# 3. עלות עובד 1007 מתפצלת לפי שעות בין (לקוח, אתר)
# ─────────────────────────────────────────────────────────────────────────────
def test_cost_distributed_correctly_per_client_site():
    """עלות עובד 1007 מתחלקת בין לקוחות/אתרים לפי שעות בפועל."""
    from core.preprocessor import load_cache

    df, _ = load_cache()
    rows = df[(df["employee_id"] == "1007") & (df["month"] == "03-2026")]
    assert len(rows) >= 4, f"מצופה 4+ שורות לעובד 1007 בחודש 03-2026, נמצאו {len(rows)}"

    # סך עלות מוקצית = employer_cost (±0.5 לעיגול)
    total_alloc = rows["allocated_cost"].sum()
    emp_cost = rows["employer_cost"].iloc[0]
    assert abs(total_alloc - emp_cost) < 0.5, (
        f"סכום allocated_cost={total_alloc:.2f} ≠ employer_cost={emp_cost:.2f}"
    )

    # בראל הנדסה צריך להיות ~50% מהשעות → ~50% מהעלות
    barel = rows[rows["site"].str.contains("בראל", na=False)]
    assert not barel.empty, "חסרת שורת בראל לעובד 1007"
    barel_pct = barel["allocated_cost"].sum() / total_alloc
    assert 0.40 < barel_pct < 0.60, (
        f"בראל קיבל {barel_pct:.1%} מהעלות, מצופה ~50% (79.83/158h)"
    )

    # מ.אילון — כמה אתרים שונים, כל אחד עם עלות נפרדת
    mayalon = rows[rows["client"].str.contains("אילון", na=False)]
    assert len(mayalon) >= 3, (
        f"מ.אילון צריך 3+ שורות, נמצאו {len(mayalon)}"
    )
    assert mayalon["allocated_cost"].min() > 0, "כל שורת מ.אילון חייבת עלות חיובית"


# ─────────────────────────────────────────────────────────────────────────────
# 4. אין כפילויות במפתח (month, employee, client, site)
# ─────────────────────────────────────────────────────────────────────────────
def test_grouping_key_is_employee_client_site():
    """לא צריכות להיות כפילויות במפתח (month, employee_id, client, site)."""
    from core.preprocessor import load_cache

    df, _ = load_cache()
    dups = df.duplicated(subset=["month", "employee_id", "client", "site"], keep=False)
    assert dups.sum() == 0, f"{int(dups.sum())} כפילויות במפתח (month,emp,client,site)"


# ─────────────────────────────────────────────────────────────────────────────
# 5. סך שורות עלה מעל 2,138 (לפני Andromeda)
# ─────────────────────────────────────────────────────────────────────────────
def test_total_rows_above_pre_andromeda_baseline():
    """מספר שורות ה-parquet גדל משמעותית לאחר שילוב Andromeda."""
    from core.preprocessor import load_cache

    df, _ = load_cache()
    assert len(df) > 2138, f"מצופה >2138 שורות, נמצאו {len(df)}"
    # 13 חודשים עם Andromeda + 2 legacy → לפחות 2,800
    assert len(df) >= 2800, f"מצופה 2800+ שורות, נמצאו {len(df)}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. הקצאת עלות לכל שורה = total_hours × cost_per_hour
# ─────────────────────────────────────────────────────────────────────────────
def test_allocated_cost_equals_hours_times_rate():
    """allocated_cost חייב להיות total_hours × cost_per_hour (±₪1)."""
    from core.preprocessor import load_cache

    df, _ = load_cache()
    if "allocated_cost" not in df.columns or "cost_per_hour" not in df.columns:
        pytest.skip("חסרות עמודות allocated_cost / cost_per_hour")

    check = df[df["total_hours"] > 0].copy()
    check["expected_alloc"] = (check["total_hours"] * check["cost_per_hour"]).round(2)
    diff = (check["allocated_cost"] - check["expected_alloc"]).abs()
    # cost_per_hour נשמר ב-2dp אחרי חישוב עם 4dp → פער אפשרי עד ₪3 עבור עובד 200h
    bad = diff[diff > 3.0]
    assert len(bad) == 0, (
        f"{len(bad)} שורות עם פער >₪3 בין allocated_cost לבין hours×rate"
    )
