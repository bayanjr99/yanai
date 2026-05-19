"""
cloud_app.py — Manpower Profitability Dashboard
Cloud-ready entry point for Streamlit Cloud deployment.

Run locally : streamlit run cloud_app.py
Deploy      : set this file as the main app in Streamlit Cloud

Data sources (priority order):
  1. User-uploaded file  (.parquet / .xlsx / .csv)
  2. Built-in demo data  (synthetic, ~1 000 rows, 12 clients, 12 months)

No local filesystem writes. All analysis is in-memory.
Login via .streamlit/secrets.toml  [users]  section.
"""

from __future__ import annotations

import io
import os

import numpy as np
import pandas as pd
import streamlit as st

try:
    import plotly.express as px
    import plotly.graph_objects as go
    _PLOTLY = True
except ImportError:
    _PLOTLY = False

# ===========================================================================
# PAGE CONFIG
# ===========================================================================

st.set_page_config(
    page_title="Manpower Profitability Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ===========================================================================
# DESIGN TOKENS
# ===========================================================================

_BLUE   = "#2563eb"
_GREEN  = "#16a34a"
_RED    = "#dc2626"
_AMBER  = "#d97706"
_PURPLE = "#7c3aed"
_PL     = dict(margin=dict(l=0, r=0, t=10, b=0),
               paper_bgcolor="white", plot_bgcolor="white",
               font=dict(family="Inter, Segoe UI, Arial", size=12,
                         color="#0f172a"))

# ===========================================================================
# CSS
# ===========================================================================

st.markdown("""
<style>
html,body,.stApp{direction:rtl;font-family:'Inter','Segoe UI',Arial,sans-serif;
  background:#F8FAFC}
.block-container{padding:1rem 2rem 3rem!important;max-width:1440px}
section[data-testid="stSidebar"]{background:#fff;border-left:1px solid #e2e8f0}

/* brand header */
.brand-header{background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 50%,#2563eb 100%);
  color:#fff;padding:16px 24px;border-radius:14px;
  display:flex;align-items:center;justify-content:space-between;
  box-shadow:0 4px 24px rgba(15,23,42,.32);margin-bottom:6px}
.brand-logo-wrap{display:flex;align-items:center;gap:14px}
.brand-logo-box{width:46px;height:46px;border-radius:10px;
  background:rgba(255,255,255,.15);display:flex;align-items:center;
  justify-content:center;font-size:26px}
.brand-title{font-size:20px;font-weight:800;letter-spacing:-.4px}
.brand-sub{font-size:11px;opacity:.72;margin-top:2px}
.brand-badge{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);
  border-radius:8px;padding:6px 14px;font-size:12px;font-weight:600}
.status-dot{width:8px;height:8px;border-radius:50%;background:#22c55e;
  box-shadow:0 0 8px #22c55e;display:inline-block;margin-left:6px}

/* super strip */
.super-strip{display:grid;grid-template-columns:1fr 1px 1fr 1px 1fr;gap:0;
  border-radius:14px;overflow:hidden;margin:4px 0 18px;
  box-shadow:0 6px 28px rgba(0,0,0,.14)}
.super-cell{padding:18px 20px;display:flex;align-items:center;gap:14px}
.super-cell.danger {background:linear-gradient(140deg,#7f1d1d,#dc2626);color:#fff}
.super-cell.warning{background:linear-gradient(140deg,#431407,#ea580c);color:#fff}
.super-cell.success{background:linear-gradient(140deg,#14532d,#16a34a);color:#fff}
.super-div{background:rgba(255,255,255,.2)}
.super-icon{font-size:34px;flex-shrink:0}
.super-content{flex:1;min-width:0}
.super-tag{font-size:9px;font-weight:800;text-transform:uppercase;
  letter-spacing:1px;opacity:.78;margin-bottom:3px}
.super-name{font-size:13px;font-weight:700;margin-bottom:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:220px}
.super-value{font-size:24px;font-weight:900;line-height:1.1}
.super-sub{font-size:11px;opacity:.82;margin-top:4px}

/* KPI cards */
.kpi-card{background:#fff;border-radius:12px;padding:16px 12px;
  text-align:center;border:1px solid #e5e7eb;
  box-shadow:0 1px 6px rgba(0,0,0,.06);min-height:90px;
  display:flex;flex-direction:column;justify-content:center}
.kpi-card.border-blue  {border-top:4px solid #2563eb}
.kpi-card.border-green {border-top:4px solid #16a34a}
.kpi-card.border-red   {border-top:4px solid #dc2626}
.kpi-card.border-amber {border-top:4px solid #d97706}
.kpi-label{font-size:11px;color:#9ca3af;font-weight:700;
  text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px}
.kpi-value{font-size:26px;font-weight:900;color:#111827;line-height:1.15}
.kpi-value.green{color:#16a34a} .kpi-value.red{color:#dc2626}
.kpi-value.blue {color:#2563eb} .kpi-value.amber{color:#d97706}
.kpi-icon{font-size:20px;margin-bottom:4px}
.kpi-delta{font-size:12px;font-weight:600;margin-top:3px}

/* section headers */
.sec-hdr{font-size:14px;font-weight:800;color:#1F497D;
  padding:6px 0 8px;border-bottom:2px solid #e5e7eb;margin:18px 0 10px}

/* alert rows */
.alert-critical{display:flex;align-items:flex-start;gap:10px;
  background:#fef2f2;border:1px solid #fecaca;border-right:4px solid #dc2626;
  padding:11px 16px;border-radius:8px;margin:5px 0;font-size:13px;
  color:#7f1d1d;line-height:1.6}
.alert-warning{display:flex;align-items:flex-start;gap:10px;
  background:#fff7ed;border:1px solid #fed7aa;border-right:4px solid #f97316;
  padding:11px 16px;border-radius:8px;margin:5px 0;font-size:13px;
  color:#7c2d12;line-height:1.6}
.alert-notice{display:flex;align-items:flex-start;gap:10px;
  background:#fefce8;border:1px solid #fde68a;border-right:4px solid #eab308;
  padding:11px 16px;border-radius:8px;margin:5px 0;font-size:13px;
  color:#713f12;line-height:1.6}
.alert-ok{display:flex;align-items:center;gap:10px;
  background:#f0fdf4;border:1px solid #bbf7d0;border-right:4px solid #16a34a;
  padding:11px 16px;border-radius:8px;margin:5px 0;font-size:13px;
  color:#14532d}
.alert-icon{font-size:18px;flex-shrink:0;margin-top:1px}
.alert-body{flex:1}
.alert-title{font-weight:700}
.alert-detail{font-size:12px;margin-top:2px;opacity:.85}

/* segmentation */
.seg-card{border-radius:12px;padding:16px 14px;text-align:center;
  border:1px solid;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.seg-card.profit{background:#f0fdf4;border-color:#86efac}
.seg-card.break {background:#fefce8;border-color:#fde68a}
.seg-card.loss  {background:#fef2f2;border-color:#fca5a5}
.seg-label{font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.6px;color:#6b7280;margin-bottom:4px}
.seg-count{font-size:28px;font-weight:900}
.seg-count.g{color:#16a34a} .seg-count.y{color:#ca8a04} .seg-count.r{color:#dc2626}
.seg-total{font-size:14px;font-weight:700;margin-top:2px}
.seg-total.g{color:#16a34a} .seg-total.y{color:#ca8a04} .seg-total.r{color:#dc2626}

/* insight cards */
.insight-card{border-radius:10px;padding:14px 16px;margin:6px 0;
  display:flex;align-items:flex-start;gap:12px}
.insight-card.red  {background:#fef2f2;border-right:4px solid #dc2626}
.insight-card.green{background:#f0fdf4;border-right:4px solid #16a34a}
.ic-title{font-weight:800;font-size:15px;margin-bottom:2px}
.ic-sub  {font-size:13px;color:#4b5563}

/* story view */
.story-header{background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;
  padding:16px 20px;margin:10px 0;display:flex;
  align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
.story-name{font-size:18px;font-weight:800;color:#0f172a}
.story-badge{padding:4px 14px;border-radius:20px;font-size:13px;font-weight:700}
.story-badge.profit{background:#dcfce7;color:#14532d}
.story-badge.loss  {background:#fee2e2;color:#7f1d1d}
.story-badge.break {background:#fef9c3;color:#713f12}
.story-narrative{background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;
  padding:14px 18px;font-size:13px;color:#1e3a8a;line-height:1.7;margin:10px 0}

/* impact tags */
.impact-tag{display:inline-block;font-size:12px;font-weight:700;
  padding:2px 9px;border-radius:20px;margin:2px 3px 2px 0}
.impact-tag.loss{background:#fee2e2;color:#7f1d1d}
.impact-tag.gain{background:#dcfce7;color:#14532d}
.impact-tag.warn{background:#ffedd5;color:#7c2d12}

/* priority items */
.priority-item{display:flex;align-items:flex-start;gap:14px;
  background:#fff;border:1px solid #e5e7eb;border-radius:12px;
  padding:14px 18px;margin:6px 0;box-shadow:0 1px 6px rgba(0,0,0,.06)}
.priority-rank{font-size:22px;font-weight:900;color:#9ca3af;
  min-width:30px;text-align:center;margin-top:4px}
.priority-impact{font-size:17px;font-weight:800;min-width:160px;margin-top:4px}
.priority-impact.crit{color:#dc2626}
.priority-impact.warn{color:#f97316}
.priority-impact.note{color:#ca8a04}
.priority-info{flex:1}
.priority-title{font-size:14px;font-weight:700;color:#111827;margin-bottom:4px}
.driver-pill{display:inline-block;font-size:11px;font-weight:600;
  padding:2px 8px;border-radius:20px;background:#f1f5f9;
  color:#475569;margin:2px 3px 2px 0}

/* txt insight */
.txt-insight{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
  padding:10px 16px;font-size:14px;color:#1e293b;margin:5px 0}

/* login */
.login-wrap{max-width:400px;margin:80px auto 0;padding:0 16px}
.login-card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;
  padding:36px 32px;box-shadow:0 8px 32px rgba(0,0,0,.08)}
</style>
""", unsafe_allow_html=True)

# ===========================================================================
# LOGIN
# ===========================================================================

import bcrypt as _bcrypt
import time as _time


def _load_creds() -> dict[str, str]:
    """Load username→bcrypt_hash map from secrets.toml [users] section.

    Raises RuntimeError when no users are configured — fails loudly so the
    operator knows to populate .streamlit/secrets.toml before deployment.
    Secrets must store bcrypt hashes, NOT plaintext passwords.
    Generate a hash with: python -c "import bcrypt; print(bcrypt.hashpw(b'mypass', bcrypt.gensalt()).decode())"
    """
    try:
        creds = dict(st.secrets.get("users", {}))
    except Exception:
        creds = {}
    if not creds:
        raise RuntimeError(
            "No users configured. "
            "Add a [users] section to .streamlit/secrets.toml with "
            "username = '<bcrypt_hash>' entries."
        )
    return creds


def _verify_password(stored_hash: str, candidate: str) -> bool:
    try:
        return _bcrypt.checkpw(candidate.encode(), stored_hash.encode())
    except Exception:
        return False


_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_DELAY_SEC    = 1.5


def _check_login() -> bool:
    if st.session_state.get("_auth"):
        return True

    _creds = _load_creds()

    # Centre the login card
    _l, _c, _r = st.columns([1, 2, 1])
    with _c:
        st.markdown("""
        <div style="text-align:center;padding:40px 0 20px">
          <div style="width:72px;height:72px;border-radius:16px;
            background:linear-gradient(135deg,#1e3a8a,#2563eb);
            display:inline-flex;align-items:center;justify-content:center;
            font-size:36px;margin-bottom:14px">📊</div>
          <div style="font-size:22px;font-weight:800;color:#0f172a">
            BI Billing System</div>
          <div style="font-size:13px;color:#64748b;margin-top:4px">
            Manpower Profitability Dashboard</div>
        </div>
        """, unsafe_allow_html=True)

        attempts = st.session_state.get("_login_attempts", 0)

        with st.form("login_form", clear_on_submit=False):
            _user = st.text_input("שם משתמש", placeholder="admin")
            _pass = st.text_input("סיסמא", type="password", placeholder="••••••")
            _ok   = st.form_submit_button("כניסה →", use_container_width=True,
                                           type="primary")
            if _ok:
                if attempts >= _MAX_LOGIN_ATTEMPTS:
                    st.error("חשבון נעול זמנית עקב ניסיונות כניסה חוזרים. נסה שוב מאוחר יותר.")
                elif _creds.get(_user) and _verify_password(_creds[_user], _pass):
                    st.session_state.update({
                        "_auth": True,
                        "_user": _user,
                        "_login_attempts": 0,
                    })
                    st.rerun()
                else:
                    _time.sleep(_LOGIN_DELAY_SEC)
                    st.session_state["_login_attempts"] = attempts + 1
                    st.error("שם משתמש או סיסמא שגויים")

    return False

if not _check_login():
    st.stop()

# ===========================================================================
# DATA LOADING
# ===========================================================================

@st.cache_data(show_spinner=False)
def _load_demo() -> pd.DataFrame:
    from demo_data import generate_demo
    return generate_demo()


@st.cache_data(show_spinner=False)
def _compute_billing(df: pd.DataFrame) -> pd.DataFrame:
    """Add billing / profit / margin / hours if not already present."""
    df = df.copy()
    if "allocated_cost" in df.columns:
        df["cost"] = pd.to_numeric(df["allocated_cost"], errors="coerce").fillna(0.0)
    elif "employer_cost" in df.columns:
        df["cost"] = pd.to_numeric(df["employer_cost"], errors="coerce").fillna(0.0)
    else:
        df["cost"] = 0.0

    if "billing" not in df.columns or df["billing"].sum() == 0:
        rate = pd.to_numeric(df.get("hourly_rate", 0), errors="coerce").fillna(0.0)
        def _col(c): return pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0.0)
        billing = (
            _col("h100") * rate
            + _col("h125") * rate * 1.25
            + _col("h150") * rate * 1.50
            + _col("h175") * rate * 1.75
            + _col("h200") * rate * 2.00
        )
        if "daily_rate" in df.columns and "work_days" in df.columns:
            dr = pd.to_numeric(df["daily_rate"], errors="coerce").fillna(0.0)
            dd = pd.to_numeric(df["work_days"],  errors="coerce").fillna(0.0)
            billing = billing.where((dr == 0) | (billing > 0), dr * dd)
        df["billing"] = billing.round(2)

    df["profit"] = (df["billing"] - df["cost"]).round(2)
    _b = df["billing"].replace(0, float("nan"))
    df["margin"] = (df["profit"] / _b * 100).round(2).fillna(0.0)
    if "total_hours" in df.columns and "hours" not in df.columns:
        df["hours"] = pd.to_numeric(df["total_hours"], errors="coerce").fillna(0.0)
    elif "hours" not in df.columns:
        df["hours"] = 0.0
    return df


def _read_uploaded(file) -> pd.DataFrame | None:
    name = file.name.lower()
    try:
        if name.endswith(".parquet"):
            return pd.read_parquet(file)
        if name.endswith(".csv"):
            return pd.read_csv(file, dtype=str)
        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(file, dtype=str)
    except Exception as exc:
        st.sidebar.error(f"שגיאה בטעינת הקובץ: {exc}")
    return None


# ===========================================================================
# SIDEBAR
# ===========================================================================

with st.sidebar:
    st.markdown(
        '<div style="padding:16px 0 12px;text-align:center">'
        '<div style="width:50px;height:50px;border-radius:12px;'
        'background:linear-gradient(135deg,#1e3a8a,#2563eb);'
        'display:inline-flex;align-items:center;justify-content:center;'
        'font-size:26px;margin-bottom:6px">📊</div>'
        '<div style="font-size:15px;font-weight:800;color:#0f172a">BI Billing</div>'
        '<div style="font-size:11px;color:#64748b;margin-top:1px">ינאי פרסונל</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    # ── Data source ───────────────────────────────────────────────────────────
    st.markdown("**📂 מקור נתונים**")
    _src = st.radio("", ["📊 נתוני הדגמה", "📁 העלה קובץ"], key="data_src",
                    label_visibility="collapsed")

    _uploaded_df: pd.DataFrame | None = None
    if _src == "📁 העלה קובץ":
        _up = st.file_uploader(
            "קובץ נתונים (.parquet / .xlsx / .csv)",
            type=["parquet", "xlsx", "xls", "csv"],
            key="upload",
        )
        if _up:
            _uploaded_df = _read_uploaded(_up)
            if _uploaded_df is not None:
                st.success(f"✅ נטען: {len(_uploaded_df):,} שורות")
        st.caption(
            "הקובץ צריך להכיל עמודות:\n"
            "`client`, `month`, `total_hours` (או `h100`/`h125`/`h150`),\n"
            "`employer_cost`, `hourly_rate`"
        )

    st.divider()

    # ── Threshold controls ────────────────────────────────────────────────────
    with st.expander("⚙️ הגדרות סף"):
        _OT_THRESH   = st.slider("⏱️ סף שעות נוספות (%)", 10, 70,
                                  st.session_state.get("ot_t", 35), 5,
                                  key="ot_t") / 100
        _LOSS_THRESH = st.number_input("🔴 סף הפסד קריטי (₪)", 0, 500_000,
                                        value=int(st.session_state.get("lt", 50_000)),
                                        step=10_000, key="lt")
        _CPH_BENCH   = st.number_input("📐 בנצ'מרק עלות/שעה (₪)", 20, 150,
                                        value=int(st.session_state.get("cb", 53)),
                                        step=5, key="cb")

    st.divider()

    # ── Logout ────────────────────────────────────────────────────────────────
    _uname = st.session_state.get("_user", "")
    st.caption(f"מחובר: **{_uname}**")
    if st.button("🚪 התנתק", use_container_width=True):
        for k in ["_auth", "_user"]:
            st.session_state.pop(k, None)
        st.rerun()

# ===========================================================================
# LOAD & PREPARE DATA
# ===========================================================================

_OT_THRESH   = st.session_state.get("ot_t",   35) / 100
_LOSS_THRESH = st.session_state.get("lt",      50_000)
_CPH_BENCH   = st.session_state.get("cb",      53)

if _uploaded_df is not None:
    _raw = _compute_billing(_uploaded_df)
    _data_label = "נתונים מועלים"
else:
    _raw = _compute_billing(_load_demo())
    _data_label = "📊 נתוני הדגמה"

if _raw.empty:
    st.warning("אין נתונים — העלה קובץ או בחר נתוני הדגמה")
    st.stop()

# Normalise key columns
for _nc in ("client","site","employee_id","employee_name","month"):
    if _nc in _raw.columns:
        _raw[_nc] = _raw[_nc].astype(str).str.strip()

def _mkey(m: str) -> tuple:
    try: return (int(m[3:]), int(m[:2]))
    except: return (0, 0)

_months_all  = sorted((_raw["month"].unique().tolist() if "month" in _raw.columns else []), key=_mkey)
_clients_all = sorted(_raw["client"].dropna().unique().tolist()) if "client" in _raw.columns else []

def _filter(df: pd.DataFrame, rng, clients: list) -> pd.DataFrame:
    if rng and "month" in df.columns:
        lo, hi = _mkey(rng[0]), _mkey(rng[1])
        df = df[df["month"].map(_mkey).apply(lambda k: lo <= k <= hi)]
    if clients and "client" in df.columns:
        df = df[df["client"].isin(clients)]
    return df

def _pct_badge(cur: float, prev: float) -> str:
    if not prev: return ""
    p = (cur - prev) / abs(prev) * 100
    c = _GREEN if p > 0 else _RED
    s = "+" if p > 0 else ""
    return f'<span style="color:{c};font-weight:700;font-size:12px">{s}{p:.1f}%</span>'

# ===========================================================================
# BRANDED HEADER
# ===========================================================================

_now_str = pd.Timestamp.now().strftime("%d/%m/%Y %H:%M")
st.markdown(
    f'<div class="brand-header">'
    f'<div class="brand-logo-wrap">'
    f'<div class="brand-logo-box">📊</div>'
    f'<div><div class="brand-title">Manpower Profitability Dashboard</div>'
    f'<div class="brand-sub">BI Billing System · ינאי פרסונל · {_data_label}</div></div>'
    f'</div>'
    f'<div style="display:flex;align-items:center;gap:14px">'
    f'<div class="brand-badge">👤 {_uname}</div>'
    f'<div style="font-size:11px;opacity:.65">'
    f'<span class="status-dot"></span>{_now_str}</div>'
    f'</div></div>',
    unsafe_allow_html=True,
)

# ===========================================================================
# SUPER KPI STRIP
# ===========================================================================

if "client" in _raw.columns and "profit" in _raw.columns:
    _gk: dict = {}
    for _c, _k in [("billing","sum"),("cost","sum"),("profit","sum"),("hours","sum")]:
        if _c in _raw.columns: _gk[_c] = (_c, _k)
    if "overtime_ratio" in _raw.columns: _gk["avg_ot"] = ("overtime_ratio","mean")
    _gs = _raw.groupby("client", as_index=False).agg(**_gk)
    _gs["margin"] = (_gs["profit"] / _gs["billing"].replace(0,float("nan"))*100).fillna(0)
    _sh = _gs.get("hours", pd.Series(float("nan"),index=_gs.index)).replace(0,float("nan"))
    _gs["pph"] = (_gs["profit"] / _sh).fillna(0)
    _worst = _gs.loc[_gs["profit"].idxmin()]
    _best  = _gs.loc[_gs["profit"].idxmax()]
    _risk  = (_gs.loc[_gs["avg_ot"].idxmax()] if "avg_ot" in _gs.columns else _gs.iloc[0])
    st.markdown(
        f'<div class="super-strip">'
        f'<div class="super-cell danger"><div class="super-icon">⚠️</div>'
        f'<div class="super-content"><div class="super-tag">הפסד הכי גדול</div>'
        f'<div class="super-name">{_worst["client"]}</div>'
        f'<div class="super-value">₪{float(_worst["profit"]):,.0f}</div>'
        f'<div class="super-sub">מרג\'ין {float(_worst.get("margin",0)):.1f}%</div>'
        f'</div></div>'
        f'<div class="super-div"></div>'
        f'<div class="super-cell warning"><div class="super-icon">🔥</div>'
        f'<div class="super-content"><div class="super-tag">סיכון עיקרי — שעות נוספות</div>'
        f'<div class="super-name">{_risk["client"]}</div>'
        f'<div class="super-value">{float(_risk.get("avg_ot",0))*100:.0f}% OT</div>'
        f'<div class="super-sub">רווח ₪{float(_risk.get("profit",0)):,.0f}</div>'
        f'</div></div>'
        f'<div class="super-div"></div>'
        f'<div class="super-cell success"><div class="super-icon">🏆</div>'
        f'<div class="super-content"><div class="super-tag">הביצועים הטובים ביותר</div>'
        f'<div class="super-name">{_best["client"]}</div>'
        f'<div class="super-value">₪{float(_best["profit"]):,.0f}</div>'
        f'<div class="super-sub">₪{float(_best.get("pph",0)):.1f}/ש\' · {float(_best.get("margin",0)):.1f}%</div>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )

# ===========================================================================
# TABS
# ===========================================================================

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 סקירה", "👥 לקוחות", "👤 עובדים", "💡 תובנות", "🔄 השוואה"
])

# ── helpers reused across tabs ────────────────────────────────────────────────
_OT_COLORS = {"h100":_BLUE,"h125":"#60a5fa","h150":_AMBER,"h175":"#f97316","h200":_RED}
_OT_LABELS = {"h100":"100%","h125":"125%","h150":"150%","h175":"175%","h200":"200%"}

def _trend_from(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "month" not in df.columns: return pd.DataFrame()
    kw: dict = {}
    for c, k in [("billing","sum"),("cost","sum"),("profit","sum"),("hours","sum")]:
        if c in df.columns: kw[c] = (c, k)
    if not kw: return pd.DataFrame()
    return df.groupby("month", as_index=False).agg(**kw).sort_values("month", key=lambda s: s.map(_mkey))

# =============================================================================
# TAB 1: OVERVIEW
# =============================================================================
with tab1:
    fa, fb = st.columns([3, 2])
    with fa:
        t1r = st.select_slider("📅 טווח", options=_months_all,
                                value=(_months_all[0], _months_all[-1]),
                                key="t1r") if len(_months_all) > 1 else \
              (_months_all[0], _months_all[0]) if _months_all else None
    with fb:
        t1c = st.multiselect("👥 לקוח", _clients_all, key="t1c",
                              placeholder="כל הלקוחות")
    df1 = _filter(_raw.copy(), t1r, t1c)
    if df1.empty:
        st.warning("אין נתונים"); st.stop()

    _B  = float(df1["billing"].sum()) if "billing" in df1.columns else 0
    _C  = float(df1["cost"].sum())    if "cost"    in df1.columns else 0
    _P  = float(df1["profit"].sum())  if "profit"  in df1.columns else 0
    _H  = float(df1["hours"].sum())   if "hours"   in df1.columns else 0
    _NE = int(df1["employee_id"].nunique()) if "employee_id" in df1.columns else 0
    _M  = _P / _B * 100 if _B > 0 else 0
    _PPH = _P / _H if _H > 0 else 0

    _tdf = _trend_from(df1)
    _pb = _pp = 0.0
    if len(_tdf) >= 2:
        _pb = float(_tdf.iloc[-2].get("billing", 0) or 0)
        _pp = float(_tdf.iloc[-2].get("profit",  0) or 0)
        _cb2 = float(_tdf.iloc[-1].get("billing", _B) or _B)
        _cp2 = float(_tdf.iloc[-1].get("profit",  _P) or _P)
    else:
        _cb2, _cp2 = _B, _P

    # 7 KPI cards
    kpi_defs = [
        ("💰","הכנסה",     f"₪{_B:,.0f}",  "blue",  "border-blue",  _cb2,_pb),
        ("📤","עלות",      f"₪{_C:,.0f}",  "amber", "border-amber", 0,0),
        ("📈","רווח",      f"₪{_P:,.0f}",
         "green" if _P>=0 else "red",
         "border-green" if _P>=0 else "border-red", _cp2,_pp),
        ("%" ,"מרג'ין",    f"{_M:.1f}%",
         "green" if _M>=10 else ("amber" if _M>=0 else "red"),
         "border-green" if _M>=10 else "border-amber", 0,0),
        ("⚡","רווח/שעה",  f"₪{_PPH:.1f}",
         "green" if _PPH>=0 else "red",
         "border-green" if _PPH>=0 else "border-red", 0,0),
        ("⏱️","שעות",      f"{_H:,.0f}",   "blue",  "border-blue",  0,0),
        ("👤","עובדים",    str(_NE),        "blue",  "border-blue",  0,0),
    ]
    kcols = st.columns(7)
    for _kc, (icon,lbl,val,vc,bc,dc,dp) in zip(kcols, kpi_defs):
        badge = _pct_badge(dc, dp) if dp else ""
        with _kc:
            st.markdown(
                f'<div class="kpi-card {bc}">'
                f'<div class="kpi-icon">{icon}</div>'
                f'<div class="kpi-label">{lbl}</div>'
                f'<div class="kpi-value {vc}">{val}</div>'
                f'<div class="kpi-delta">{badge}</div></div>',
                unsafe_allow_html=True,
            )

    if _PLOTLY:
        r1a, r1b = st.columns([3, 2])
        with r1a:
            st.markdown('<div class="sec-hdr">📈 מגמה חודשית</div>',
                        unsafe_allow_html=True)
            if not _tdf.empty:
                fig = go.Figure()
                for col,color,name,fill in [
                    ("billing",_BLUE,"הכנסה",True),
                    ("cost",_RED,"עלות",False),
                    ("profit",_GREEN,"רווח",False),
                ]:
                    if col not in _tdf.columns: continue
                    kw2: dict = dict(x=_tdf["month"],y=_tdf[col],name=name,
                                    line=dict(color=color,width=2.2),mode="lines+markers",
                                    marker=dict(size=5))
                    if fill: kw2.update(fill="tozeroy",fillcolor="rgba(37,99,235,.07)")
                    fig.add_trace(go.Scatter(**kw2))
                fig.update_layout(**{**_PL,"height":280,"showlegend":True},
                                  legend=dict(orientation="h",y=1.12),
                                  xaxis=dict(showgrid=False,tickangle=-30),
                                  yaxis=dict(tickprefix="₪",tickformat=",.0f",
                                             showgrid=True,gridcolor="#f3f4f6"))
                st.plotly_chart(fig, use_container_width=True,
                                config={"displayModeBar":False})
        with r1b:
            st.markdown('<div class="sec-hdr">🥧 חלוקת הכנסות</div>',
                        unsafe_allow_html=True)
            if "client" in df1.columns and "billing" in df1.columns:
                _pd2 = df1.groupby("client")["billing"].sum().nlargest(8).reset_index()
                fp = go.Figure(go.Pie(
                    values=_pd2["billing"], labels=_pd2["client"], hole=0.44,
                    marker_colors=px.colors.qualitative.Set2 if _PLOTLY else None,
                    hovertemplate="<b>%{label}</b><br>₪%{value:,.0f}<extra></extra>",
                ))
                fp.update_layout(**{**_PL,"height":280,"showlegend":True},
                                  legend=dict(orientation="v",font_size=10))
                fp.update_traces(textinfo="percent",textposition="inside")
                st.plotly_chart(fp, use_container_width=True,
                                config={"displayModeBar":False})

        # Overtime stacked bar
        _otc = [c for c in ("h100","h125","h150","h175","h200") if c in df1.columns]
        if _otc and "month" in df1.columns:
            st.markdown('<div class="sec-hdr">📊 פירוט שעות לפי אחוז</div>',
                        unsafe_allow_html=True)
            _otm = df1.groupby("month")[_otc].sum().reset_index()
            fig_ot = go.Figure()
            for col in _otc:
                fig_ot.add_trace(go.Bar(
                    name=_OT_LABELS.get(col,col),
                    x=_otm["month"], y=_otm[col],
                    marker_color=_OT_COLORS.get(col,"#888"),
                ))
            fig_ot.update_layout(**{**_PL,"height":240,"barmode":"stack",
                                    "showlegend":True},
                                 legend=dict(orientation="h",y=1.12),
                                 xaxis=dict(showgrid=False,tickangle=-30),
                                 yaxis=dict(showgrid=True,gridcolor="#f3f4f6"))
            st.plotly_chart(fig_ot, use_container_width=True,
                            config={"displayModeBar":False})

    # Smart alerts
    st.markdown('<div class="sec-hdr">🚨 התראות</div>', unsafe_allow_html=True)
    _sm: list[tuple] = []
    if "client" in df1.columns and "profit" in df1.columns:
        _bcp = df1.groupby("client")["profit"].sum().sort_values()
        for _cl, _pr in _bcp[_bcp < 0].items():
            _la = abs(float(_pr))
            cls = "alert-critical" if _la >= _LOSS_THRESH else "alert-warning"
            ico = "🔴" if _la >= _LOSS_THRESH else "🟠"
            _sm.append((0 if _la>=_LOSS_THRESH else 1, cls, ico,
                f"{'הפסד קריטי' if _la>=_LOSS_THRESH else 'הפסד'} — <b>{_cl}</b>",
                f"₪{_la:,.0f}"))
    if "overtime_ratio" in df1.columns and "client" in df1.columns:
        for _cl, _ot in df1.groupby("client")["overtime_ratio"].mean().items():
            if _ot > _OT_THRESH:
                _sm.append((1,"alert-warning","🟠",
                    f"שעות נוספות גבוהות — <b>{_cl}</b>",
                    f"{_ot*100:.0f}% (סף {_OT_THRESH*100:.0f}%)"))
    if "shortage_hours" in df1.columns and "client" in df1.columns:
        for _cl, _sh in df1.groupby("client")["shortage_hours"].sum().items():
            if _sh > 100:
                _sm.append((2,"alert-notice","🟡",
                    f"חסר שעות לתקן — <b>{_cl}</b>",f"{_sh:.0f} שעות"))
    if _sm:
        for _, cls, ico, title, det in sorted(_sm, key=lambda x: x[0]):
            st.markdown(
                f'<div class="{cls}"><div class="alert-icon">{ico}</div>'
                f'<div class="alert-body"><div class="alert-title">{title}</div>'
                f'<div class="alert-detail">{det}</div></div></div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown('<div class="alert-ok"><div class="alert-icon">✅</div>'
                    '<div class="alert-body"><div class="alert-title">הכל תקין</div>'
                    '</div></div>', unsafe_allow_html=True)

    # Segmentation
    if "billing" in df1.columns and "profit" in df1.columns and "client" in df1.columns:
        st.markdown('<div class="sec-hdr">🗂️ סגמנטציה</div>', unsafe_allow_html=True)
        _sg = df1.groupby("client",as_index=False).agg(billing=("billing","sum"),profit=("profit","sum"))
        _sg["m"] = (_sg["profit"]/_sg["billing"].replace(0,float("nan"))*100).fillna(0)
        _sg["_s"] = _sg.apply(lambda r:"profit" if r["profit"]>0 and r["m"]>=10
                               else("break" if r["profit"]>=0 else "loss"),axis=1)
        _sgg = _sg.groupby("_s",as_index=False).agg(clients=("client","count"),
               tp=("profit","sum"),tb=("billing","sum"))
        def _si(code):
            rr = _sgg[_sgg["_s"]==code]
            if rr.empty: return 0,0.0,0.0
            return int(rr.iloc[0]["clients"]),float(rr.iloc[0]["tp"]),float(rr.iloc[0]["tb"])
        sc1,sc2,sc3 = st.columns(3)
        for _sc,code,lbl,cc,tc,em in [(sc1,"profit","לקוחות רווחיים","g","g","🟢"),
                                       (sc2,"break","נקודת איזון","y","y","🟡"),
                                       (sc3,"loss","לקוחות מפסידים","r","r","🔴")]:
            cn,tp,tb = _si(code)
            with _sc:
                st.markdown(
                    f'<div class="seg-card {code}">'
                    f'<div class="seg-label">{em} {lbl}</div>'
                    f'<div class="seg-count {cc}">{cn}</div>'
                    f'<div style="font-size:12px;color:#6b7280">₪{tb:,.0f} הכנסות</div>'
                    f'<div class="seg-total {tc}">{"+" if tp>=0 else ""}₪{tp:,.0f}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

# =============================================================================
# TAB 2: CLIENTS + STORY VIEW
# =============================================================================
with tab2:
    f2a, f2b = st.columns([3, 2])
    with f2a:
        t2r = st.select_slider("📅 טווח", options=_months_all,
                                value=(_months_all[0],_months_all[-1]),
                                key="t2r") if len(_months_all)>1 else \
              (_months_all[0],_months_all[0]) if _months_all else None
    with f2b:
        t2c = st.multiselect("👥 סינון", _clients_all, key="t2c",
                              placeholder="כל הלקוחות")
    df2 = _filter(_raw.copy(), t2r, t2c)

    if df2.empty or not all(c in df2.columns for c in ["client","billing","profit"]):
        st.info("אין נתוני billing / profit")
    else:
        _ca2 = df2.groupby("client",as_index=False).agg(
            billing=("billing","sum"), cost=("cost","sum"),
            profit=("profit","sum"),  hours=("hours","sum") if "hours" in df2.columns else ("billing","count"),
        )
        _ca2["margin"] = (_ca2["profit"]/_ca2["billing"].replace(0,float("nan"))*100).fillna(0)
        _ca2["cph"]    = (_ca2["cost"]/_ca2["hours"].replace(0,float("nan"))).fillna(0).round(0)
        if "overtime_ratio" in df2.columns:
            _ca2 = _ca2.merge(
                df2.groupby("client")["overtime_ratio"].mean().reset_index().rename(columns={"overtime_ratio":"avg_ot"}),
                on="client", how="left"
            )
        _ca2["#"] = range(1, len(_ca2)+1)
        _ca2["st"] = _ca2["profit"].apply(lambda v:"🔴" if v<0 else("🟡" if v<_ca2["profit"].max()*0.1 else "🟢"))

        # Profit bar
        if _PLOTLY:
            st.markdown('<div class="sec-hdr">📊 רווח לפי לקוח</div>',
                        unsafe_allow_html=True)
            _bd = _ca2.sort_values("profit")
            fig_cl = go.Figure(go.Bar(
                x=_bd["profit"], y=_bd["client"], orientation="h",
                marker_color=[_GREEN if v>=0 else _RED for v in _bd["profit"]],
                text=[f"₪{v:,.0f}" for v in _bd["profit"]],
                textposition="outside",
                hovertemplate="<b>%{y}</b><br>₪%{x:,.0f}<extra></extra>",
            ))
            fig_cl.add_vline(x=0, line_color="#6b7280", line_width=1)
            fig_cl.update_layout(**{**_PL,"height":max(200,len(_bd)*30)},
                                 showlegend=False,
                                 xaxis=dict(visible=False),
                                 yaxis=dict(showgrid=False))
            st.plotly_chart(fig_cl, use_container_width=True, config={"displayModeBar":False})

        # Table
        st.markdown('<div class="sec-hdr">📋 טבלת לקוחות</div>', unsafe_allow_html=True)
        _tbl_cols = {c:v for c,v in {
            "#":"#","client":"לקוח","hours":"שעות","billing":"הכנסה ₪",
            "cost":"עלות ₪","profit":"רווח ₪","margin":"מרג'ין %",
            "cph":"עלות/שעה ₪","st":"סטטוס",
        }.items() if c in _ca2.columns}
        _td = _ca2[[k for k in _tbl_cols]].rename(columns=_tbl_cols)
        def _rs(row):
            if "רווח ₪" in row.index and row["רווח ₪"]<0:
                return ["background-color:#fff0f0"]*len(row)
            return [""]*len(row)
        _tf2: dict = {}
        for _fc,_ff in [("הכנסה ₪","₪{:,.0f}"),("עלות ₪","₪{:,.0f}"),
                        ("רווח ₪","₪{:,.0f}"),("מרג'ין %","{:.1f}%"),
                        ("עלות/שעה ₪","₪{:,.0f}"),("שעות","{:,.1f}")]:
            if _fc in _td.columns: _tf2[_fc] = _ff
        st.dataframe(_td.style.format(_tf2).apply(_rs,axis=1),
                     use_container_width=True, hide_index=True)

        # Client story drill-down
        st.markdown('<div class="sec-hdr">📖 סיפור הלקוח</div>', unsafe_allow_html=True)
        _drill = st.selectbox("בחר לקוח לניתוח מעמיק",
                               ["— בחר —"]+sorted(_ca2["client"].tolist()),
                               key="drill")
        if _drill != "— בחר —":
            _cd = df2[df2["client"]==_drill]
            _cb3 = float(_cd["billing"].sum()); _cc3 = float(_cd["cost"].sum())
            _cp3 = float(_cd["profit"].sum());  _ch3 = float(_cd["hours"].sum()) if "hours" in _cd.columns else 0
            _cm3 = _cp3/_cb3*100 if _cb3>0 else 0
            _cpph= _cp3/_ch3 if _ch3>0 else 0
            _ccph= _cc3/_ch3 if _ch3>0 else 0
            _cot = float(_cd["overtime_ratio"].mean()) if "overtime_ratio" in _cd.columns else 0
            _cmo = int(_cd["month"].nunique()) if "month" in _cd.columns else 1
            _cemp= int(_cd["employee_id"].nunique()) if "employee_id" in _cd.columns else 0
            _css3 = "profit" if _cp3>0 and _cm3>=10 else("break" if _cp3>=0 else "loss")
            _clbl = "🟢 רווחי" if _css3=="profit" else("🟡 נקודת איזון" if _css3=="break" else "🔴 מפסיד")

            st.markdown(f'<div class="story-header">'
                        f'<div><div class="story-name">{_drill}</div>'
                        f'<div style="font-size:12px;color:#6b7280">{_cmo} חודשים · {_cemp} עובדים</div></div>'
                        f'<span class="story-badge {_css3}">{_clbl}</span></div>',
                        unsafe_allow_html=True)

            # Narrative
            _nar = [f"<b>{_drill}</b> הכניס ₪{_cb3:,.0f} ({_ch3:,.0f} שעות, {_cemp} עובדים)."]
            if _cp3 >= 0:
                _nar.append(f"רווח ₪{_cp3:,.0f} · מרג'ין <b>{_cm3:.1f}%</b> · ₪{_cpph:.1f}/שעה.")
            else:
                _nar.append(f"⚠️ הפסד ₪{abs(_cp3):,.0f} — עלות {_ccph:.0f}₪/ש' עולה על הכנסה.")
            if _cot > _OT_THRESH:
                _nar.append(f"שעות נוספות <b>{_cot*100:.0f}%</b> — מעל הסף ({_OT_THRESH*100:.0f}%).")
            st.markdown(f'<div class="story-narrative">💬 {" ".join(_nar)}</div>',
                        unsafe_allow_html=True)

            # Timeline chart
            if _PLOTLY and "month" in _cd.columns:
                _cl_tl = _cd.groupby("month",as_index=False).agg(
                    billing=("billing","sum"), profit=("profit","sum"),
                    avg_ot=("overtime_ratio","mean") if "overtime_ratio" in _cd.columns else ("billing","count"),
                ).sort_values("month", key=lambda s: s.map(_mkey))
                fig_tl = go.Figure()
                fig_tl.add_trace(go.Bar(
                    x=_cl_tl["month"], y=_cl_tl["profit"], name="רווח",
                    marker_color=[_GREEN if v>=0 else _RED for v in _cl_tl["profit"]],
                    opacity=0.75,
                ))
                fig_tl.add_trace(go.Scatter(
                    x=_cl_tl["month"], y=_cl_tl["billing"], name="הכנסה",
                    mode="lines+markers", line=dict(color=_BLUE,width=2,dash="dot"),
                    marker=dict(size=5),
                ))
                if "avg_ot" in _cl_tl.columns:
                    fig_tl.add_trace(go.Scatter(
                        x=_cl_tl["month"], y=_cl_tl["avg_ot"]*100, name="שעות נוספות %",
                        mode="lines+markers", line=dict(color=_AMBER,width=2),
                        marker=dict(size=5), yaxis="y2",
                    ))
                fig_tl.add_hline(y=0,line_color="#9ca3af",line_dash="dash",line_width=1)
                fig_tl.update_layout(
                    **{**_PL,"height":300,"showlegend":True,"barmode":"relative"},
                    legend=dict(orientation="h",y=1.12),
                    xaxis=dict(showgrid=False,tickangle=-30),
                    yaxis=dict(tickprefix="₪",tickformat=",.0f",showgrid=True,gridcolor="#f3f4f6"),
                    yaxis2=dict(overlaying="y",side="left",ticksuffix="%",
                                range=[0,100],showgrid=False,position=0.0),
                )
                st.caption("עמודות = רווח · קו כחול = הכנסה · קו כתום = שעות נוספות %")
                st.plotly_chart(fig_tl,use_container_width=True,config={"displayModeBar":False})

            # Employees
            if "employee_name" in _cd.columns:
                with st.expander(f"👤 עובדים תחת {_drill}"):
                    _ea: dict = dict(cost=("cost","sum"),billing=("billing","sum"),profit=("profit","sum"))
                    if "hours" in _cd.columns: _ea["hours"] = ("hours","sum")
                    if "overtime_ratio" in _cd.columns: _ea["ot"] = ("overtime_ratio","mean")
                    _ec = _cd.groupby("employee_name",as_index=False).agg(**_ea).sort_values("cost",ascending=False)
                    _ec.rename(columns={"employee_name":"עובד","hours":"שעות","cost":"עלות ₪",
                                        "billing":"חיוב ₪","profit":"רווח ₪","ot":"% OT"},inplace=True)
                    _ef2: dict = {}
                    for l,f in [("שעות","{:.1f}"),("עלות ₪","₪{:,.0f}"),("חיוב ₪","₪{:,.0f}"),("רווח ₪","₪{:,.0f}"),("% OT","{:.1%}")]:
                        if l in _ec.columns: _ef2[l]=f
                    st.dataframe(_ec.style.format(_ef2),use_container_width=True,hide_index=True)

# =============================================================================
# TAB 3: EMPLOYEES
# =============================================================================
with tab3:
    f3a, f3b = st.columns([3, 2])
    with f3a:
        t3m = st.selectbox("📅 חודש",["כל החודשים"]+_months_all,key="t3m")
    with f3b:
        t3c = st.multiselect("👥 לקוח",_clients_all,key="t3c",placeholder="כל הלקוחות")
    df3 = _raw.copy()
    if t3m != "כל החודשים" and "month" in df3.columns:
        df3 = df3[df3["month"]==t3m]
    if t3c and "client" in df3.columns:
        df3 = df3[df3["client"].isin(t3c)]

    if df3.empty or "employee_name" not in df3.columns:
        st.info("אין נתוני עובדים")
    else:
        _eak: dict = dict(cost=("cost","sum"))
        if "hours" in df3.columns:    _eak["hours"]   = ("hours","sum")
        if "billing" in df3.columns:  _eak["billing"] = ("billing","sum")
        if "client" in df3.columns:   _eak["client"]  = ("client","first")
        if "overtime_ratio" in df3.columns: _eak["ot"] = ("overtime_ratio","mean")
        _et = df3.groupby("employee_name",as_index=False).agg(**_eak).sort_values("cost",ascending=False).reset_index(drop=True)
        if "cost" in _et.columns and "hours" in _et.columns:
            _et["cph"] = (_et["cost"]/_et["hours"].replace(0,float("nan"))).round(0).fillna(0)
        _et["#"] = range(1,len(_et)+1)

        if _PLOTLY:
            st.markdown('<div class="sec-hdr">💰 עובדים — עלות מעביד (טופ 15)</div>',
                        unsafe_allow_html=True)
            _top15 = _et.head(15).sort_values("cost")
            fig_e = go.Figure(go.Bar(
                x=_top15["cost"],y=_top15["employee_name"],orientation="h",
                marker_color=_PURPLE,
                text=[f"₪{v:,.0f}" for v in _top15["cost"]],textposition="outside",
            ))
            fig_e.update_layout(**{**_PL,"height":max(200,len(_top15)*30)},
                                showlegend=False,
                                xaxis=dict(visible=False),yaxis=dict(showgrid=False))
            st.plotly_chart(fig_e,use_container_width=True,config={"displayModeBar":False})

        st.markdown('<div class="sec-hdr">📋 טבלת עובדים</div>',unsafe_allow_html=True)
        _em2 = {"#":"#","employee_name":"עובד","client":"לקוח","hours":"שעות",
                "ot":"% שעות נוספות","cph":"עלות/שעה ₪","cost":"עלות מעביד ₪"}
        _eds = [k for k in _em2 if k in _et.columns]
        _ed = _et[_eds].rename(columns={k:v for k,v in _em2.items() if k in _eds})
        _ef3: dict = {}
        for _l,_f in [("שעות","{:.1f}"),("עלות מעביד ₪","₪{:,.0f}"),
                      ("עלות/שעה ₪","₪{:,.0f}"),("% שעות נוספות","{:.1%}")]:
            if _l in _ed.columns: _ef3[_l]=_f
        def _ot_style(val):
            try:
                v = float(str(val).rstrip("%"))/100
                if v>_OT_THRESH: return "background-color:#fef3c7;color:#92400e;font-weight:700"
            except Exception: pass
            return ""
        _es = _ed.style.format(_ef3)
        if "% שעות נוספות" in _ed.columns:
            _es = _es.applymap(_ot_style,subset=["% שעות נוספות"])
        st.dataframe(_es,use_container_width=True,hide_index=True)

# =============================================================================
# TAB 4: INSIGHTS
# =============================================================================
with tab4:
    f4a, f4b = st.columns([3,2])
    with f4a:
        t4r = st.select_slider("📅 טווח",options=_months_all,
                                value=(_months_all[0],_months_all[-1]),
                                key="t4r") if len(_months_all)>1 else \
              (_months_all[0],_months_all[0]) if _months_all else None
    with f4b:
        t4c = st.multiselect("👥 לקוח",_clients_all,key="t4c",placeholder="כל")
    df4 = _filter(_raw.copy(), t4r, t4c)

    if df4.empty or not all(c in df4.columns for c in ["client","billing","profit"]):
        st.info("אין נתונים")
    else:
        _iak: dict = dict(billing=("billing","sum"),cost=("cost","sum"),profit=("profit","sum"))
        if "hours" in df4.columns:         _iak["hours"]         = ("hours","sum")
        if "cost_per_hour" in df4.columns: _iak["avg_cph"]       = ("cost_per_hour","mean")
        if "overtime_ratio" in df4.columns:_iak["avg_ot"]        = ("overtime_ratio","mean")
        if "shortage_hours" in df4.columns:_iak["total_shortage"]= ("shortage_hours","sum")
        if "hourly_rate" in df4.columns:   _iak["hourly_rate"]   = ("hourly_rate","mean")
        _ic = df4.groupby("client",as_index=False).agg(**_iak)
        _ic["margin"] = (_ic["profit"]/_ic["billing"].replace(0,float("nan"))*100).round(1).fillna(0)
        _sh4 = _ic.get("hours",pd.Series(float("nan"),index=_ic.index)).replace(0,float("nan"))
        _ic["pph"] = (_ic["profit"]/_sh4).fillna(0)

        # Benchmark
        _bp = float(_ic.loc[_ic["profit"]>0,"avg_cph"].replace(0,float("nan")).median()
                    if "avg_cph" in _ic.columns and (_ic["profit"]>0).any()
                    else _CPH_BENCH)
        if "hours" in _ic.columns:
            _ic["excess_cost"] = (((_ic.get("avg_cph",0)-_bp).clip(lower=0)
                                  *_ic["hours"].replace(0,float("nan")))).fillna(0)
        if "total_shortage" in _ic.columns and "hourly_rate" in _ic.columns:
            _ic["sh_lost"] = (_ic["total_shortage"]*_ic["hourly_rate"]).fillna(0)
        else:
            _ic["sh_lost"] = 0

        # Hero cards
        if len(_ic) >= 2:
            _bi = _ic.loc[_ic["profit"].idxmin()]; _bI = _ic.loc[_ic["profit"].idxmax()]
            h1, h2 = st.columns(2)
            for hc, row, cls, badge, lbl in [
                (h1,_bi,"worst","⚠️","לקוח הכי מפסיד"),
                (h2,_bI,"best","🏆","לקוח הכי רווחי"),
            ]:
                with hc:
                    _pv = float(row["profit"]); _mv = float(row.get("margin",0))
                    _hv = float(row.get("hours",0) or 0)
                    _ppv = _pv/_hv if _hv>0 else 0
                    _col2 = "red" if _pv<0 else "green"
                    st.markdown(
                        f'<div style="border-radius:14px;padding:20px;text-align:center;'
                        f'border:2px solid {"#dc2626" if cls=="worst" else "#16a34a"};'
                        f'background:{"linear-gradient(135deg,#fef2f2,#fee2e2)" if cls=="worst" else "linear-gradient(135deg,#f0fdf4,#dcfce7)"};'
                        f'box-shadow:0 4px 18px rgba(0,0,0,.08)">'
                        f'<div style="font-size:28px;margin-bottom:6px">{badge}</div>'
                        f'<div style="font-size:10px;font-weight:700;text-transform:uppercase;'
                        f'letter-spacing:.7px;color:#6b7280">{lbl}</div>'
                        f'<div style="font-size:16px;font-weight:800;margin:4px 0">{row["client"]}</div>'
                        f'<div style="font-size:28px;font-weight:900;color:{"#dc2626" if _pv<0 else "#16a34a"}">'
                        f'₪{_pv:,.0f}</div>'
                        f'<div style="font-size:12px;color:#6b7280;margin-top:6px">'
                        f'מרג\'ין {_mv:.1f}% · ₪{_ppv:.1f}/שעה</div></div>',
                        unsafe_allow_html=True,
                    )

        # Top 5 Issues
        st.markdown('<div class="sec-hdr">🎯 5 הבעיות הגדולות ביותר</div>',
                    unsafe_allow_html=True)
        _iss: list[dict] = []
        for _, row in _ic.iterrows():
            _pv = float(row.get("profit",0) or 0)
            _ov = float(row.get("avg_ot",0) or 0)
            _sv = float(row.get("total_shortage",0) or 0)
            _cv = float(row.get("avg_cph",0) or 0)
            _ev = float(row.get("excess_cost",0) or 0)
            _lv = float(row.get("sh_lost",0) or 0)
            _hv2= float(row.get("hours",0) or 0)
            if _pv < 0:
                _ds = []
                if _ov > 0.25: _ds.append(f"שעות נוספות {_ov*100:.0f}%")
                if _sv > 30:   _ds.append(f"חסר {_sv:.0f}ש' → ₪{_lv:,.0f}")
                if _cv > _bp*1.15 and _ev > 3000:
                    _ds.append(f"עלות/שעה ₪{_cv:.0f} (תקן ₪{_bp:.0f}) → ₪{_ev:,.0f}")
                if not _ds: _ds.append("עלות > הכנסה")
                _iss.append(dict(l="crit",b="🔴",cl=row["client"],
                    imp=abs(_pv),is_=f"₪{abs(_pv):,.0f} הפסד",
                    t=f"הפסד — {row['client']}",d=_ds))
            elif _ov > _OT_THRESH and _hv2 > 0:
                _xc = _hv2 * _ov * _cv * 0.25
                _iss.append(dict(l="warn",b="🟠",cl=row["client"],
                    imp=_xc,is_=f"₪{_xc:,.0f} עלות עודפת",
                    t=f"שעות נוספות — {row['client']}",d=[f"OT {_ov*100:.0f}%"]))
            elif _sv > 80 and _lv > 5000:
                _iss.append(dict(l="note",b="🟡",cl=row["client"],
                    imp=_lv,is_=f"₪{_lv:,.0f} אבוד",
                    t=f"מחסור שעות — {row['client']}",d=[f"{_sv:.0f}ש' חסרות"]))
        _iss.sort(key=lambda x:x["imp"],reverse=True)
        for rk,iss in enumerate(_iss[:5],1):
            _pills = "".join(
                f'<span class="driver-pill">{d}</span>' for d in iss["d"]
            )
            st.markdown(
                f'<div class="priority-item">'
                f'<div class="priority-rank">#{rk}</div>'
                f'<div class="priority-impact {iss["l"]}">{iss["b"]} {iss["is_"]}</div>'
                f'<div class="priority-info">'
                f'<div class="priority-title">{iss["t"]}</div>'
                f'<div>{_pills}</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        if not _iss:
            st.markdown('<div class="alert-ok"><div class="alert-icon">✅</div>'
                        '<div class="alert-body"><div class="alert-title">אין בעיות מהותיות</div></div>'
                        '</div>',unsafe_allow_html=True)

        # Losing / profitable cards
        c4a, c4b = st.columns(2)
        with c4a:
            _loss4 = _ic[_ic["profit"]<0].sort_values("profit").head(5)
            if not _loss4.empty:
                st.markdown('<div class="sec-hdr">🔴 לקוחות מפסידים</div>',unsafe_allow_html=True)
                for _,r in _loss4.iterrows():
                    _lv2 = abs(float(r["profit"]))
                    _tags = []
                    if float(r.get("avg_ot",0)or 0)>0.25:
                        _tags.append(f'<span class="impact-tag warn">OT {float(r.get("avg_ot",0))*100:.0f}%</span>')
                    if float(r.get("sh_lost",0)or 0)>0:
                        _tags.append(f'<span class="impact-tag warn">₪{float(r.get("sh_lost",0)):,.0f} אבוד</span>')
                    if not _tags: _tags.append('<span class="impact-tag loss">עלות > הכנסה</span>')
                    st.markdown(
                        f'<div class="insight-card red"><div class="ic-badge">🔴</div>'
                        f'<div><div class="ic-title">{r["client"]} — ₪{_lv2:,.0f}</div>'
                        f'<div style="margin-top:6px">{"".join(_tags)}</div></div></div>',
                        unsafe_allow_html=True,
                    )
        with c4b:
            _prof4 = _ic[_ic["profit"]>0].sort_values("profit",ascending=False).head(5)
            if not _prof4.empty:
                st.markdown('<div class="sec-hdr">🟢 לקוחות רווחיים</div>',unsafe_allow_html=True)
                for _,r in _prof4.iterrows():
                    _pv2 = float(r["profit"]); _mv2 = float(r.get("margin",0))
                    _hv3 = float(r.get("hours",0)or 0); _ppv2 = _pv2/_hv3 if _hv3>0 else 0
                    _tgs = [f'<span class="impact-tag gain">מרג\'ין {_mv2:.0f}%</span>']
                    if _ppv2 > 5: _tgs.append(f'<span class="impact-tag gain">₪{_ppv2:.1f}/שעה</span>')
                    st.markdown(
                        f'<div class="insight-card green"><div class="ic-badge">🟢</div>'
                        f'<div><div class="ic-title">{r["client"]} — ₪{_pv2:,.0f}</div>'
                        f'<div style="margin-top:6px">{"".join(_tgs)}</div></div></div>',
                        unsafe_allow_html=True,
                    )

        # Profit Drivers Bubble Chart
        if _PLOTLY and "hours" in _ic.columns:
            st.markdown('<div class="sec-hdr">🫧 מפת מניעי הרווח</div>',unsafe_allow_html=True)
            _bdf = _ic.copy()
            if "avg_cph" not in _bdf.columns:
                _bdf["avg_cph"] = (_bdf["cost"]/_bdf["hours"].replace(0,float("nan"))).fillna(0)
            if "avg_ot" not in _bdf.columns: _bdf["avg_ot"] = 0.0
            _bdf = _bdf[(_bdf["hours"]>0)&(_bdf["avg_cph"]>0)].copy()
            if not _bdf.empty:
                def _bh(r):
                    return (f"<b>{r['client']}</b><br>"
                            f"רווח: ₪{float(r['profit']):,.0f}<br>"
                            f"עלות/שעה: ₪{float(r['avg_cph']):.0f} (תקן ₪{_bp:.0f})<br>"
                            f"שעות נוספות: {float(r.get('avg_ot',0))*100:.0f}%")
                _bdf["_h"] = _bdf.apply(_bh,axis=1)
                fig_b = go.Figure(go.Scatter(
                    x=_bdf["avg_cph"], y=_bdf["profit"],
                    mode="markers+text",
                    marker=dict(
                        size=_bdf["hours"].clip(lower=50).apply(lambda v:max(12,min(65,v/60))),
                        color=_bdf["avg_ot"], colorscale="RdBu_r",
                        colorbar=dict(title="% OT",tickformat=".0%",len=0.6),
                        showscale=True, opacity=0.82,
                        line=dict(width=1,color="#475569"),
                    ),
                    text=_bdf["client"],textposition="top center",textfont=dict(size=10),
                    hovertemplate=_bdf["_h"]+"<extra></extra>",
                ))
                fig_b.add_hline(y=0,line_color="#dc2626",line_dash="dash",line_width=1.5,
                               annotation_text="קו אפס",annotation_position="left")
                fig_b.add_vline(x=_bp,line_color="#6b7280",line_dash="dot",line_width=1.5,
                               annotation_text=f"תקן ₪{_bp:.0f}",annotation_position="top right")
                fig_b.update_layout(**{**_PL,"height":480},
                    xaxis=dict(title="עלות לשעה (₪)",showgrid=True,gridcolor="#f3f4f6",tickprefix="₪"),
                    yaxis=dict(title="רווח (₪)",showgrid=True,gridcolor="#f3f4f6",tickprefix="₪",tickformat=",.0f"),
                )
                st.caption("גודל = שעות · צבע = % OT · קו אדום = נקודת איזון · קו אפור = בנצ'מרק עלות")
                st.plotly_chart(fig_b,use_container_width=True,config={"displayModeBar":False})

        # KPI insights text
        st.markdown('<div class="sec-hdr">💬 תובנות מפתח</div>',unsafe_allow_html=True)
        _ki: list[str] = []
        _bst = _ic.loc[_ic["profit"].idxmax()]
        _ki.append(f"הלקוח הכי רווחי: <b>{_bst['client']}</b> · מרג'ין {float(_bst.get('margin',0)):.1f}% · ₪{float(_bst['profit']):,.0f}")
        _ot4 = [c for c in ("h125","h150","h175","h200") if c in df4.columns]
        if _ot4 and "hours" in df4.columns:
            _o4 = float(df4[_ot4].sum().sum()); _t4 = float(df4["hours"].sum())
            if _t4 > 0: _ki.append(f"{_o4/_t4*100:.1f}% משעות הן שעות נוספות ({_o4:,.0f} שעות)")
        if "shortage_hours" in df4.columns and "employee_id" in df4.columns:
            _sh5 = df4[df4["shortage_hours"]>0]
            if not _sh5.empty:
                _ki.append(f"{_sh5['employee_id'].nunique()} עובדים מתחת לתקן · {float(_sh5['shortage_hours'].sum()):.0f} שעות חסרות")
        if "cost_per_hour" in df4.columns:
            _cph5 = df4["cost_per_hour"].replace(0,float("nan")).dropna()
            if not _cph5.empty:
                _ki.append(f"עלות ממוצעת לשעה: ₪{float(_cph5.mean()):.0f} (טווח ₪{float(_cph5.min()):.0f}–₪{float(_cph5.max()):.0f})")
        if "hours" in df4.columns and "profit" in df4.columns:
            _th5 = float(df4["hours"].sum()); _tp5 = float(df4["profit"].sum())
            if _th5>0: _ki.append(f"רווח ממוצע לשעה: ₪{_tp5/_th5:.1f} · {_th5:,.0f} שעות · {df4['month'].nunique() if 'month' in df4.columns else '?'} חודשים")
        for kt in _ki:
            st.markdown(f'<div class="txt-insight">💡 {kt}</div>',unsafe_allow_html=True)

        # Excel export
        st.markdown('<div class="sec-hdr">📥 ייצוא</div>',unsafe_allow_html=True)
        ec1, ec2 = st.columns(2)
        with ec1:
            def _mk_excel():
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as xw:
                    _s1 = _ic[[c for c in ("client","billing","cost","profit","margin","hours","avg_cph","avg_ot") if c in _ic.columns]].copy()
                    _s1.rename(columns={"client":"לקוח","billing":"הכנסות","cost":"עלות","profit":"רווח","margin":"מרג'ין%","hours":"שעות","avg_cph":"עלות/שעה","avg_ot":"% OT"},inplace=True)
                    _s1.to_excel(xw,sheet_name="סיכום לקוחות",index=False)
                    if _iss:
                        pd.DataFrame([{"דירוג":i+1,"לקוח":x["cl"],"השפעה":x["imp"],"תיאור":x["t"],"גורמים":" | ".join(x["d"])} for i,x in enumerate(_iss[:5])]).to_excel(xw,sheet_name="בעיות עיקריות",index=False)
                    pd.DataFrame({"תובנות":_ki}).to_excel(xw,sheet_name="תובנות",index=False)
                buf.seek(0); return buf.getvalue()
            st.download_button("📊 ייצוא Excel",data=_mk_excel(),file_name="bi_report.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True,type="primary")
        with ec2:
            st.download_button("📄 ייצוא נתונים (CSV)",
                               data=_ic.to_csv(index=False).encode("utf-8-sig"),
                               file_name="clients_summary.csv",mime="text/csv",
                               use_container_width=True)

# =============================================================================
# TAB 5: COMPARISON
# =============================================================================
with tab5:
    st.markdown('<div class="sec-hdr">🔄 השוואת לקוחות</div>',unsafe_allow_html=True)
    c5a, c5b = st.columns([3,2])
    with c5a:
        cmp_cl = st.multiselect("בחר 2–4 לקוחות",_clients_all,max_selections=4,key="cmp5",placeholder="בחר לקוחות...")
    with c5b:
        cmp_r = st.select_slider("📅 טווח",options=_months_all,value=(_months_all[0],_months_all[-1]),key="cmpr5") if len(_months_all)>1 else (_months_all[0],_months_all[0]) if _months_all else None

    if len(cmp_cl) < 2:
        st.markdown('<div style="text-align:center;padding:40px;color:#6b7280">'
                    '<div style="font-size:48px;margin-bottom:12px">🔄</div>'
                    '<div style="font-size:16px;font-weight:700">בחר 2–4 לקוחות להשוואה</div>'
                    '</div>',unsafe_allow_html=True)
    else:
        _cd5 = _filter(_raw.copy(), cmp_r, cmp_cl)
        if _cd5.empty:
            st.warning("אין נתונים")
        else:
            _cak: dict = {}
            for c,k in [("billing","sum"),("cost","sum"),("profit","sum"),("hours","sum")]:
                if c in _cd5.columns: _cak[c]=(c,k)
            if "overtime_ratio" in _cd5.columns: _cak["avg_ot"]=("overtime_ratio","mean")
            if "cost_per_hour"  in _cd5.columns: _cak["avg_cph"]=("cost_per_hour","mean")
            _ca5 = _cd5.groupby("client",as_index=False).agg(**_cak)
            _ca5["margin"] = (_ca5["profit"]/_ca5["billing"].replace(0,float("nan"))*100).fillna(0)
            _sh5b = _ca5.get("hours",pd.Series(float("nan"),index=_ca5.index)).replace(0,float("nan"))
            _ca5["pph"]     = (_ca5["profit"]/_sh5b).fillna(0)
            _ca5["cph_c"]   = (_ca5.get("cost",0)/_sh5b).fillna(0)

            # Summary table
            _ct5 = {c:v for c,v in {"client":"לקוח","billing":"הכנסות ₪","cost":"עלות ₪","profit":"רווח ₪","margin":"מרג'ין %","pph":"רווח/שעה ₪","cph_c":"עלות/שעה ₪","avg_ot":"% OT"}.items() if c in _ca5.columns}
            _ctd = _ca5[[k for k in _ct5]].rename(columns=_ct5)
            def _cr5(row):
                if "רווח ₪" in row.index and row["רווח ₪"]<0: return ["background-color:#fef2f2"]*len(row)
                return [""]*len(row)
            _cf5: dict = {}
            for l,f in [("הכנסות ₪","₪{:,.0f}"),("עלות ₪","₪{:,.0f}"),("רווח ₪","₪{:,.0f}"),
                        ("מרג'ין %","{:.1f}%"),("רווח/שעה ₪","₪{:.1f}"),("עלות/שעה ₪","₪{:.0f}"),("% OT","{:.1%}")]:
                if l in _ctd.columns: _cf5[l]=f
            st.dataframe(_ctd.style.format(_cf5).apply(_cr5,axis=1),use_container_width=True,hide_index=True)

            if _PLOTLY:
                _pal5 = [_BLUE,_GREEN,_RED,_AMBER,_PURPLE]
                _bm5 = [(c,l,inv) for c,l,inv in [("profit","רווח (₪)",False),("pph","רווח/שעה",False),
                        ("cph_c","עלות/שעה",True),("avg_ot","% OT",True)] if c in _ca5.columns]
                for row_p in [_bm5[i:i+2] for i in range(0,len(_bm5),2)]:
                    _rcols = st.columns(len(row_p))
                    for _rc,(met,lbl,inv) in zip(_rcols,row_p):
                        with _rc:
                            _vs = _ca5[met].tolist()
                            if met=="profit": _clrs=[_GREEN if v>=0 else _RED for v in _vs]
                            elif inv:
                                _rk=[pd.Series(_vs).rank(ascending=False).astype(int).tolist()[i]-1 for i in range(len(_vs))]
                                _clrs=[([_RED,_AMBER,_GREEN,_BLUE])[min(r,3)] for r in _rk]
                            else:
                                _rk=[pd.Series(_vs).rank(ascending=True).astype(int).tolist()[i]-1 for i in range(len(_vs))]
                                _clrs=[([_RED,_AMBER,_GREEN,_BLUE])[min(r,3)] for r in _rk]
                            _txt5 = [f"₪{v:,.0f}" if "₪" in lbl else f"{v:.1%}" if "%" in lbl else f"{v:.1f}" for v in _vs]
                            fb5 = go.Figure(go.Bar(x=_ca5["client"],y=_vs,marker_color=_clrs,
                                                   text=_txt5,textposition="outside"))
                            fb5.add_hline(y=0,line_color="#9ca3af",line_dash="dash",line_width=1)
                            fb5.update_layout(**{**_PL,"height":210,"showlegend":False,"title":lbl},
                                             xaxis=dict(showgrid=False,tickangle=-15),
                                             yaxis=dict(showgrid=True,gridcolor="#f3f4f6",visible=False))
                            st.plotly_chart(fb5,use_container_width=True,config={"displayModeBar":False})

                # Trend
                if "month" in _cd5.columns:
                    st.markdown('<div class="sec-hdr">📈 מגמת רווח</div>',unsafe_allow_html=True)
                    _ct5t = _cd5.groupby(["client","month"],as_index=False).agg(profit=("profit","sum"),billing=("billing","sum")).sort_values("month")
                    _cm5 = st.radio("הצג:",["רווח","הכנסות"],horizontal=True,key="cm5")
                    _cy = "profit" if _cm5=="רווח" else "billing"
                    fig_ct = go.Figure()
                    for i,cl in enumerate(cmp_cl):
                        _s5 = _ct5t[_ct5t["client"]==cl]
                        if _s5.empty: continue
                        fig_ct.add_trace(go.Scatter(x=_s5["month"],y=_s5[_cy],name=cl,
                            mode="lines+markers",line=dict(color=_pal5[i%len(_pal5)],width=2.5),marker=dict(size=6),
                            hovertemplate=f"<b>{cl}</b><br>%{{x}}: ₪%{{y:,.0f}}<extra></extra>"))
                    fig_ct.add_hline(y=0,line_color="#9ca3af",line_dash="dash",line_width=1)
                    fig_ct.update_layout(**{**_PL,"height":300,"showlegend":True},
                                         legend=dict(orientation="h",y=1.1),
                                         xaxis=dict(showgrid=False,tickangle=-30),
                                         yaxis=dict(tickprefix="₪",tickformat=",.0f",showgrid=True,gridcolor="#f3f4f6"))
                    st.plotly_chart(fig_ct,use_container_width=True,config={"displayModeBar":False})
