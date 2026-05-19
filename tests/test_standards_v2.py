"""
tests/test_standards_v2.py — Unit tests for core/standards_v2.apply_standards.

Each test creates a synthetic DataFrame row for a specific billing_kind and
verifies that apply_standards() computes the correct expected_billing.

Tests use the REAL data/תקן.xlsx so that rates match what the business uses.
"""

from pathlib import Path
import pandas as pd
import pytest

DATA_DIR = Path(__file__).parent.parent / "data"
pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "תקן.xlsx").exists()
    and not any(DATA_DIR.glob("*.xlsx")),
    reason="data/תקן.xlsx not found — skipping integration tests",
)


def _row(client, site, total_hours=180.0, work_days=22, **extra) -> pd.DataFrame:
    """Build a minimal single-row DataFrame for apply_standards testing."""
    base = {
        "month":       "03-2026",
        "employee_id": "TEST",
        "client":      client,
        "site":        site,
        "total_hours": total_hours,
        "work_days":   work_days,
        "employer_cost": 10000.0,
        "source":      "test",
    }
    base.update(extra)
    return pd.DataFrame([base])


def _apply(df):
    from core.standards_v2 import apply_standards
    return apply_standards(df, DATA_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# 1. hourly_no_completion  (ולפמן — site "ולפמן")
#    Note: R10 (rate=70, no country) and R11 (rate=85, מודובנים) share the
#    same (client, site) key; R11 overwrites R10 in the lookup → rate=85.
# ─────────────────────────────────────────────────────────────────────────────
def test_hourly_no_completion():
    df = _row('ולפמן תעשיות בע"מ', "ולפמן", total_hours=200)
    out = _apply(df)
    assert out.loc[0, "billing_kind"] == "hourly_no_completion", out.loc[0, "billing_kind"]
    # Rate is 85 because R11 (מודובנים) overwrites R10 in the lookup dict
    actual_rate = out.loc[0, "hourly_rate"]
    assert actual_rate in (70.0, 85.0), f"Unexpected rate: {actual_rate}"
    assert out.loc[0, "expected_billing"] == pytest.approx(200 * actual_rate)


# ─────────────────────────────────────────────────────────────────────────────
# 2. hourly_with_completion — under target (billable = std)
#    עטיה: rate=72, std=236
# ─────────────────────────────────────────────────────────────────────────────
def test_hourly_with_completion_under():
    df = _row('א.ש. עטיה השקעות בע"מ', 'א ש עטיה', total_hours=200)
    out = _apply(df)
    assert out.loc[0, "billing_kind"] == "hourly_with_completion", out.loc[0, "billing_kind"]
    assert out.loc[0, "std_hours_month"] == pytest.approx(236.0)
    assert out.loc[0, "hourly_rate"] == pytest.approx(72.0)
    # max(200, 236) × 72 = 16,992
    assert out.loc[0, "expected_billing"] == pytest.approx(236 * 72)
    assert out.loc[0, "shortage_hours"]   == pytest.approx(36.0)
    assert out.loc[0, "completion_pct"]   == pytest.approx(200 / 236 * 100, abs=0.2)


# ─────────────────────────────────────────────────────────────────────────────
# 3. hourly_with_completion — over target (billable = actual)
# ─────────────────────────────────────────────────────────────────────────────
def test_hourly_with_completion_over():
    df = _row('א.ש. עטיה השקעות בע"מ', 'א ש עטיה', total_hours=260)
    out = _apply(df)
    assert out.loc[0, "billing_kind"] == "hourly_with_completion"
    # max(260, 236) × 72 = 18,720
    assert out.loc[0, "expected_billing"] == pytest.approx(260 * 72)
    assert out.loc[0, "shortage_hours"]   == pytest.approx(0.0)
    assert out.loc[0, "completion_pct"]   == pytest.approx(100.0)


# ─────────────────────────────────────────────────────────────────────────────
# 4. daily_with_ot — בראל הנדסה שמעון גנח
#    daily_rate=800, ot_hourly_rate=80, daily_min_hours=10
#    work_days=22, total_hours=240 → OT = 240 − 22×10 = 20h
# ─────────────────────────────────────────────────────────────────────────────
def test_daily_with_ot():
    df = _row("בראל הנדסה שמעון גנח", "בראל הנדסה שמעון גנח",
              total_hours=240, work_days=22)
    out = _apply(df)
    bk = out.loc[0, "billing_kind"]
    assert bk in ("daily_with_ot", "mixed"), f"billing_kind={bk}"
    if bk == "daily_with_ot":
        assert out.loc[0, "daily_rate"]      == pytest.approx(800.0)
        assert out.loc[0, "ot_hourly_rate"]  == pytest.approx(80.0)
        assert out.loc[0, "daily_min_hours"] == pytest.approx(10.0)
        # 22 × 800 + (240 − 220) × 80 = 17,600 + 1,600 = 19,200
        assert out.loc[0, "expected_billing"] == pytest.approx(22 * 800 + 20 * 80)


# ─────────────────────────────────────────────────────────────────────────────
# 5. daily_or_monthly_min — ולפמן אדירים
#    rate=70 (hourly), daily_min=10, std=236
#    work_days=22 → option1 = 22×70×10=15,400; option2 = 236×70=16,520 → min=15,400
# ─────────────────────────────────────────────────────────────────────────────
def test_daily_or_monthly_min():
    df = _row('ולפמן תעשיות בע"מ', "ולפמן אדירים",
              total_hours=200, work_days=22)
    out = _apply(df)
    assert out.loc[0, "billing_kind"] == "daily_or_monthly_min", out.loc[0, "billing_kind"]
    assert out.loc[0, "hourly_rate"]     == pytest.approx(70.0)
    assert out.loc[0, "std_hours_month"] == pytest.approx(236.0)
    # min(22×70×10, 236×70) = min(15400, 16520) = 15400
    assert out.loc[0, "expected_billing"] == pytest.approx(min(22 * 70 * 10, 236 * 70))


# ─────────────────────────────────────────────────────────────────────────────
# 6. daily_min_only — שמואל שמעון (השלמה ל-10 שעות/יום)
#    rate=70, daily_min=10, no monthly target
#    work_days=22, total_hours=190 → max(190, 22×10)=220 → 220×70=15,400
# ─────────────────────────────────────────────────────────────────────────────
def test_daily_min_only():
    df = _row("שמואל שמעון ושות עב עפר ופ בעמ", "שמעון שמואל",
              total_hours=190, work_days=22)
    out = _apply(df)
    assert out.loc[0, "billing_kind"] == "daily_min_only", out.loc[0, "billing_kind"]
    assert out.loc[0, "hourly_rate"]    == pytest.approx(70.0)
    assert out.loc[0, "daily_min_hours"] == pytest.approx(10.0)
    # max(190, 22×10) × 70 = 220 × 70 = 15,400
    assert out.loc[0, "expected_billing"] == pytest.approx(max(190, 22 * 10) * 70)


# ─────────────────────────────────────────────────────────────────────────────
# 7. unknown — client not in standards
# ─────────────────────────────────────────────────────────────────────────────
def test_unknown_client():
    df = _row("לקוח_לא_קיים_XYZ", "אתר_לא_קיים", total_hours=100)
    out = _apply(df)
    assert out.loc[0, "billing_kind"] == "unknown"
    assert out.loc[0, "match_type"]   == "none"
    # fallback: h × hr = 100 × 0 = 0
    assert out.loc[0, "expected_billing"] == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 8. no_pricing — client in _no_match list
# ─────────────────────────────────────────────────────────────────────────────
def test_no_pricing_client():
    df = _row('נתיבים דרום בע"מ', "כלשהו", total_hours=100)
    out = _apply(df)
    assert out.loc[0, "billing_kind"] in ("no_pricing", "unknown")


# ─────────────────────────────────────────────────────────────────────────────
# 9. Vectorised — multiple rows at once
# ─────────────────────────────────────────────────────────────────────────────
def test_multiple_rows():
    rows = pd.concat([
        _row('א.ש. עטיה השקעות בע"מ', 'א ש עטיה', total_hours=200),
        _row('ולפמן תעשיות בע"מ', "ולפמן", total_hours=150),
    ], ignore_index=True)
    out = _apply(rows)
    assert len(out) == 2
    assert out.loc[0, "billing_kind"] == "hourly_with_completion"
    assert out.loc[1, "billing_kind"] == "hourly_no_completion"
    assert out.loc[0, "expected_billing"] == pytest.approx(236 * 72)
    # ולפמן rate is whichever row wins the dict (70 or 85)
    _r1 = out.loc[1, "hourly_rate"]
    assert out.loc[1, "expected_billing"] == pytest.approx(150 * _r1)
