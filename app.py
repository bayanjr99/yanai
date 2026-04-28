"""
Internal BI Billing System — unified analytics dashboard.
Flow: Upload data → Calculate → BI Dashboard (from master_full.parquet)
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

try:
    import plotly.express as px
    import plotly.graph_objects as go
    _PLOTLY = True
except ImportError:
    _PLOTLY = False

from pipeline import (
    run_month_pipeline, build_master_full, list_available_months,
    month_file_mtime, update_master, get_all_data, filter_by_client,
    get_profit_trend, get_top_clients, get_top_employees, build_summary_tables,
    validate_master, DATA_ROOT, OUTPUT_ROOT, MASTER_DIR,
    MASTER_PATH, MASTER_XLSX,
)
from core.validation import ValidationError

# ===========================================================================
# PAGE CONFIG + GLOBAL CSS
# ===========================================================================

st.set_page_config(layout="wide", page_title="BI Billing System", page_icon="📊")

st.markdown("""
<style>
body, .stApp { direction: rtl; font-family: 'Segoe UI', Arial, sans-serif; }
.block-container { padding-top: 0 !important; padding-bottom: 2rem; }
section[data-testid="stSidebar"] { background: #f9fafb; }

.bi-hdr {
    background: linear-gradient(135deg,#1a3a5c,#2563eb);
    color:#fff; padding:14px 28px; margin:-1rem -4rem 0;
    display:flex; align-items:center; justify-content:space-between;
    box-shadow:0 2px 10px rgba(0,0,0,.18);
}
.bi-hdr-title { font-size:20px; font-weight:900; letter-spacing:-.3px; }
.bi-hdr-sub   { font-size:12px; opacity:.7; margin-top:2px; }

.kpi-card {
    background:#fff; border-radius:12px; padding:16px 12px;
    text-align:center; border:1px solid #e5e7eb;
    box-shadow:0 1px 6px rgba(0,0,0,.06); min-height:90px;
    display:flex; flex-direction:column; justify-content:center;
}
.kpi-label { font-size:11px; color:#9ca3af; font-weight:700;
    text-transform:uppercase; letter-spacing:.5px; margin-bottom:5px; }
.kpi-value { font-size:26px; font-weight:900; color:#111827; line-height:1.15; }
.kpi-value.green { color:#16a34a; }
.kpi-value.red   { color:#dc2626; }
.kpi-delta { font-size:12px; font-weight:600; margin-top:3px; }

.sec-hdr {
    font-size:14px; font-weight:800; color:#1F497D;
    padding:6px 0 8px; border-bottom:2px solid #e5e7eb;
    margin:18px 0 10px;
}

.ob-card {
    background:#fff; border-radius:14px; padding:22px 16px;
    border:1px solid #e5e7eb; box-shadow:0 2px 8px rgba(0,0,0,.05);
    text-align:center;
}
.ob-icon  { font-size:36px; margin-bottom:10px; }
.ob-step  { font-size:11px; font-weight:700; color:#6366f1;
    text-transform:uppercase; letter-spacing:.5px; margin-bottom:4px; }
.ob-title { font-size:15px; font-weight:800; color:#111827; margin-bottom:6px; }
.ob-body  { font-size:13px; color:#6b7280; line-height:1.6; }

.comp-bar {
    background:#f0f9ff; border:1px solid #bae6fd; border-radius:8px;
    padding:8px 14px; font-size:13px; color:#374151; margin-bottom:6px;
}
</style>
""", unsafe_allow_html=True)

# ===========================================================================
# CHART HELPERS
# ===========================================================================

_PL = dict(margin=dict(l=0, r=0, t=8, b=0), paper_bgcolor="white",
           plot_bgcolor="white", font=dict(family="Segoe UI, Arial", size=12))


def _line_trend(df: pd.DataFrame):
    """Trend chart — works on get_profit_trend() output (clean column names)."""
    if not _PLOTLY or df.empty or "month" not in df.columns:
        return None
    fig = go.Figure()
    # billing / profit / cost are the clean column names from get_profit_trend()
    color_map = {
        "billing": ("#1F497D", "חיוב", True),
        "profit":  ("#16a34a", "רווח", False),
        "cost":    ("#dc2626", "עלות", False),
    }
    for col, (color, name, fill) in color_map.items():
        if col not in df.columns:
            continue
        kwargs: dict = dict(x=df["month"], y=df[col], name=name,
                            line=dict(color=color, width=2.2))
        if fill:
            kwargs.update(fill="tozeroy", fillcolor="rgba(31,73,125,.07)")
        fig.add_trace(go.Scatter(**kwargs))
    fig.update_layout(**{**_PL, "showlegend": True}, height=270,
                      legend=dict(orientation="h", y=1.1),
                      xaxis=dict(showgrid=False, tickangle=-30),
                      yaxis=dict(showgrid=True, gridcolor="#f3f4f6", tickformat="₪,.0f"))
    return fig


def _bar_h(df: pd.DataFrame, x_col: str, y_col: str, color: str = "#1F497D"):
    if not _PLOTLY or df.empty or x_col not in df.columns or y_col not in df.columns:
        return None
    d = df.sort_values(x_col, ascending=True).tail(10)
    fig = px.bar(d, x=x_col, y=y_col, orientation="h",
                 color_discrete_sequence=[color], labels={x_col: "₪", y_col: ""})
    fig.update_layout(**_PL, height=max(180, len(d) * 32))
    fig.update_traces(texttemplate="₪%{x:,.0f}", textposition="outside")
    fig.update_xaxes(visible=False); fig.update_yaxes(showgrid=False)
    return fig


def _bar_clients(df: pd.DataFrame):
    """Top clients bar — expects 'billing' column (clean master schema)."""
    if not _PLOTLY or df.empty or "client" not in df.columns:
        return None
    col = "billing" if "billing" in df.columns else "billing_amount"
    return _bar_h(df, col, "client", "#1F497D")


def _bar_clients_profit(df: pd.DataFrame):
    if not _PLOTLY or df.empty or "profit" not in df.columns or "client" not in df.columns:
        return None
    d = df.sort_values("profit").copy()
    d["_c"] = d["profit"].apply(lambda x: "#16a34a" if x >= 0 else "#dc2626")
    fig = px.bar(d, x="profit", y="client", orientation="h",
                 color="_c", color_discrete_map={c: c for c in d["_c"].unique()},
                 labels={"profit": "₪", "client": ""})
    fig.update_layout(**_PL, height=max(180, len(d) * 32), showlegend=False)
    fig.update_traces(texttemplate="₪%{x:,.0f}", textposition="outside")
    fig.update_xaxes(visible=False); fig.update_yaxes(showgrid=False)
    return fig


def _bar_employees(df: pd.DataFrame):
    if not _PLOTLY or df.empty or "employee_name" not in df.columns:
        return None
    col = "cost" if "cost" in df.columns else (list(df.columns)[-1])
    return _bar_h(df, col, "employee_name", "#7c3aed")


def _pie(df: pd.DataFrame):
    """Revenue pie — uses 'billing' (clean schema) or 'billing_amount' as fallback."""
    if not _PLOTLY or df.empty or "client" not in df.columns:
        return None
    col = "billing" if "billing" in df.columns else ("billing_amount" if "billing_amount" in df.columns else None)
    if col is None:
        return None
    d = df.groupby("client")[col].sum().nlargest(8).reset_index()
    fig = px.pie(d, values=col, names="client", hole=0.4,
                 color_discrete_sequence=px.colors.qualitative.Set2)
    fig.update_layout(**_PL, height=260, showlegend=True,
                      legend=dict(orientation="v", font_size=10))
    fig.update_traces(textposition="inside", textinfo="percent+label")
    return fig


# ===========================================================================
# HELPERS
# ===========================================================================

_CAT_CSS = {
    "HIGH":   "background:#dcfce7;color:#166534",
    "MEDIUM": "background:#dbeafe;color:#1e3a8a",
    "LOW":    "background:#fef9c3;color:#854d0e",
    "LOSS":   "background:#fee2e2;color:#991b1b",
}


def _scat(v):
    return _CAT_CSS.get(str(v), "")


def _pct_badge(cur: float, prev: float) -> str:
    if not prev:
        return ""
    p = (cur - prev) / abs(prev) * 100
    c = "#16a34a" if p > 0 else "#dc2626"
    s = "+" if p > 0 else ""
    return f'<span style="color:{c};font-weight:700;font-size:12px">{s}{p:.1f}%</span>'


def _prev_month(month: str) -> str:
    try:
        mm, yy = int(month[:2]), int(month[3:])
        return f"12-{yy-1}" if mm == 1 else f"{mm-1:02d}-{yy}"
    except Exception:
        return ""


def _save_month_files(month: str, hours_file, costs_file):
    d = os.path.join(DATA_ROOT, month)
    os.makedirs(d, exist_ok=True)
    ext = "pdf" if hours_file.name.lower().endswith(".pdf") else "xlsx"
    open(os.path.join(d, f"hours.{ext}"), "wb").write(hours_file.read())
    open(os.path.join(d, "costs.xlsx"), "wb").write(costs_file.read())


@st.cache_data(show_spinner=False)
def _run_month_cached(month: str, _mtime: float) -> dict:
    try:
        result = run_month_pipeline(month)
    except ValidationError as e:
        return {"success": False, "error": f"שגיאת אימות PDF: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {
        "success":   True,
        "detail_df": result.detail_df,
        "issues_df": result.issues_df,
        "month_str": result.month_str,
    }


@st.cache_data(show_spinner=False, ttl=120)
def _load_master_cached() -> pd.DataFrame:
    return get_all_data()


def _answers_with_pandas(df: pd.DataFrame, question: str) -> str | None:
    """Answer simple questions using pandas (master uses clean column names)."""
    q = question.strip().lower()
    if df.empty or "client" not in df.columns:
        return None
    if "billing" in df.columns:
        try:
            top_client = df.groupby("client")["billing"].sum().idxmax()
            if any(w in q for w in ["רווחי", "הכי רווחי", "top client", "best client"]):
                b = float(df.groupby("client")["billing"].sum()[top_client])
                p = float(df.groupby("client")["profit"].sum()[top_client]) if "profit" in df.columns else 0
                return f"הלקוח הכי רווחי: **{top_client}** — חיוב ₪{b:,.0f}, רווח ₪{p:,.0f}"
        except Exception:
            pass
    if any(w in q for w in ["כמה חודשים", "חודשים"]) and "month" in df.columns:
        return f"יש {df['month'].nunique()} חודשים: {', '.join(sorted(df['month'].unique()))}"
    if any(w in q for w in ["עובד", "עלות עובד"]) and "employee_name" in df.columns and "cost" in df.columns:
        try:
            top_emp = df.groupby("employee_name")["cost"].sum().idxmax()
            c = float(df.groupby("employee_name")["cost"].sum()[top_emp])
            return f"העובד היקר ביותר: **{top_emp}** — עלות ₪{c:,.0f}"
        except Exception:
            pass
    return None


# ===========================================================================
# SIDEBAR
# ===========================================================================

_agr_path = os.path.join(DATA_ROOT, "agreements.xlsx")
_agr_ok   = os.path.exists(_agr_path)

with st.sidebar:
    st.markdown(
        '<div style="text-align:center;padding:10px 0 4px">'
        '<div style="font-size:28px">📊</div>'
        '<div style="font-weight:900;color:#1F497D;font-size:15px">BI Billing</div>'
        '<div style="font-size:11px;color:#9ca3af">ינאי פרסונל</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    # ── Agreements ────────────────────────────────────────────────────────────
    if not _agr_ok:
        st.warning("⚠️ חסר קובץ הסכמים")
    else:
        st.caption("✅ הסכמים נטענו")
    with st.expander("📋 עדכן הסכמים"):
        up_agr = st.file_uploader("agreements.xlsx", type=["xlsx"], key="up_agr")
        if up_agr:
            os.makedirs(DATA_ROOT, exist_ok=True)
            open(_agr_path, "wb").write(up_agr.read())
            st.success("✅ נשמר"); st.rerun()

    st.divider()

    # ── Upload monthly data ───────────────────────────────────────────────────
    with st.expander("➕ העלה חודש חדש"):
        nm = st.text_input("חודש (MM-YYYY)", placeholder="04-2026", key="nm_in")
        hu = st.file_uploader("שעות (PDF / xlsx)", type=["pdf", "xlsx"], key="up_h")
        cu = st.file_uploader("עלויות (xlsx)", type=["xlsx"], key="up_c")

        # Optional shared costs for all months
        sc = st.file_uploader("עלויות משותפות (אופציונלי)", type=["xlsx"], key="up_sc")
        if sc:
            open(os.path.join(DATA_ROOT, "costs_shared.xlsx"), "wb").write(sc.read())
            st.success("✅ עלויות משותפות נשמרו")

        if st.button("💾 שמור", disabled=not (nm and hu and cu), key="save_month"):
            _save_month_files(nm, hu, cu)
            st.success(f"✅ {nm} נשמר"); st.rerun()

    st.divider()

    # ── Calculate one month ───────────────────────────────────────────────────
    available_months = list_available_months(DATA_ROOT)
    if available_months:
        sel_month = st.selectbox(
            "📅 חודש לחישוב", available_months,
            index=len(available_months) - 1, key="sel_month",
        )
        if st.button("🚀 חשב חיוב", type="primary",
                     use_container_width=True, disabled=not _agr_ok,
                     help="" if _agr_ok else "העלה agreements.xlsx תחילה"):
            mtime = month_file_mtime(sel_month, DATA_ROOT)
            with st.spinner(f"מחשב {sel_month}..."):
                res = _run_month_cached(sel_month, mtime)
            if res["success"]:
                try:
                    update_master(res["detail_df"], sel_month)
                    _load_master_cached.clear()
                except Exception:
                    pass
                st.session_state["last_month"] = sel_month
                st.session_state["last_issues"] = res["issues_df"]
                st.session_state.pop("pipeline_error", None)
                st.rerun()
            else:
                st.session_state["pipeline_error"] = res["error"]
                st.rerun()
    else:
        st.warning("אין חודשים — העלה נתונים")

    # ── Batch calculate all ───────────────────────────────────────────────────
    st.divider()
    master_now = _load_master_cached()
    done_months = set(master_now["month"].unique()) if not master_now.empty and "month" in master_now.columns else set()
    pending = [m for m in available_months if m not in done_months]

    if pending:
        st.caption(f"{len(pending)} חודשים ממתינים")
        if st.button(f"⚡ חשב {len(pending)} חודשים",
                     disabled=not _agr_ok, key="batch_calc"):
            _ok, _fail = [], []
            prog = st.progress(0)
            for i, bm in enumerate(pending):
                mdir  = os.path.join(DATA_ROOT, bm)
                cpath = os.path.join(mdir, "costs.xlsx")
                spath = os.path.join(DATA_ROOT, "costs_shared.xlsx")
                if not os.path.exists(cpath) and os.path.exists(spath):
                    import shutil as _sh
                    _sh.copy(spath, cpath)
                br = _run_month_cached(bm, month_file_mtime(bm, DATA_ROOT))
                if br["success"]:
                    try:
                        update_master(br["detail_df"], bm)
                    except Exception:
                        pass
                    _ok.append(bm)
                else:
                    _fail.append(f"{bm}: {br['error']}")
                prog.progress((i + 1) / len(pending))
            if _ok:
                _load_master_cached.clear()
                st.success(f"✅ הושלמו: {', '.join(_ok)}")
            for ef in _fail:
                st.warning(f"⚠️ {ef}")
            st.rerun()
    else:
        st.caption("✅ כל החודשים עובדו")

    # ── Build / rebuild master ────────────────────────────────────────────────
    st.divider()
    if st.button("🔄 בנה Master מחדש", use_container_width=True, key="build_master"):
        with st.spinner("מחשב את כל החודשים..."):
            master_built, errs = build_master_full(DATA_ROOT)
        _load_master_cached.clear()
        if not master_built.empty:
            st.success(
                f"✅ {len(master_built)} שורות · "
                f"{master_built['month'].nunique()} חודשים · "
                f"נשמר parquet + xlsx"
            )
        for e in errs:
            st.warning(f"⚠️ {e}")
        st.rerun()

    # Power BI export link
    if os.path.exists(MASTER_XLSX):
        with open(MASTER_XLSX, "rb") as _f:
            st.download_button(
                "📥 הורד master_full.xlsx (Power BI)",
                data=_f.read(),
                file_name="master_full.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

# ===========================================================================
# HEADER
# ===========================================================================

_cur_m = st.session_state.get("last_month", "")
st.markdown(
    f'<div class="bi-hdr">'
    f'<div><div class="bi-hdr-title">📊 BI Billing System</div>'
    f'<div class="bi-hdr-sub">ניהול חיוב ורווחיות פנימי · ינאי פרסונל</div></div>'
    f'<div style="font-size:13px;opacity:.85">{"📅 " + _cur_m if _cur_m else ""}</div>'
    f'</div>',
    unsafe_allow_html=True,
)
st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

if st.session_state.get("pipeline_error"):
    st.error(f"❌ {st.session_state['pipeline_error']}")

# ===========================================================================
# LOAD MASTER
# ===========================================================================

master_raw = _load_master_cached()

# ===========================================================================
# ONBOARDING (empty master)
# ===========================================================================

if master_raw.empty:
    st.markdown(
        '<div style="text-align:center;padding:2rem 0 1rem">'
        '<h3 style="font-weight:800;font-size:21px">ברוך הבא 👋</h3>'
        '<p style="color:#6b7280;font-size:14px">שלושה שלבים לדשבורד BI פעיל</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    for col, icon, step, title, body in [
        (c1, "📂", "שלב 1 — העלה", "קבצי נתונים",
         "<code>data/MM-YYYY/</code><br>• <b>hours.pdf</b> / hours.xlsx<br>"
         "• <b>costs.xlsx</b><br><br><code>data/agreements.xlsx</code>"),
        (c2, "🚀", "שלב 2 — חשב", "חישוב חיוב",
         "בחר חודש בסרגל הצד<br>לחץ <b>🚀 חשב חיוב</b><br><br>"
         "או לחץ <b>🔄 בנה Master</b><br>לחשב כל החודשים"),
        (c3, "📊", "שלב 3 — נתח", "דשבורד BI",
         "• KPIs: חיוב, עלות, רווח, מרג'ין<br>"
         "• גרפי מגמה<br>• לקוחות מובילים<br>"
         "• ייצוא ל-Power BI"),
    ]:
        with col:
            st.markdown(
                f'<div class="ob-card"><div class="ob-icon">{icon}</div>'
                f'<div class="ob-step">{step}</div><div class="ob-title">{title}</div>'
                f'<div class="ob-body">{body}</div></div>',
                unsafe_allow_html=True,
            )
    if available_months:
        st.info(
            f"✅ {len(available_months)} חודשים זמינים — לחץ **🔄 בנה Master מחדש** "
            f"בסרגל הצד לאפיק את כל הנתונים"
        )
    st.stop()

# ===========================================================================
# SECTION B — FILTERS
# ===========================================================================

st.markdown('<div class="sec-hdr">🔍 סינון</div>', unsafe_allow_html=True)

_months_all = sorted(master_raw["month"].unique().tolist()) if "month" in master_raw.columns else []
_clients_all = sorted(master_raw["client"].dropna().unique()) if "client" in master_raw.columns else []
_emp_all = (sorted(master_raw["employee_name"].dropna().unique())
            if "employee_name" in master_raw.columns else [])

f1, f2, f3 = st.columns([2, 2, 2])

with f1:
    if len(_months_all) > 1:
        date_range = st.select_slider(
            "📅 טווח חודשים", options=_months_all,
            value=(_months_all[0], _months_all[-1]), key="bi_range",
        )
    elif _months_all:
        date_range = (_months_all[0], _months_all[0])
        st.caption(f"📅 {_months_all[0]}")
    else:
        date_range = None

with f2:
    f_clients = st.multiselect("👥 לקוח", _clients_all, key="bi_cl")

with f3:
    f_emp = st.multiselect("👤 עובד", _emp_all, key="bi_em")

# Apply filters
master = master_raw.copy()
if date_range and "month" in master.columns:
    master = master[(master["month"] >= date_range[0]) & (master["month"] <= date_range[1])]
if f_clients:
    master = filter_by_client(master, f_clients)
if f_emp and "employee_name" in master.columns:
    master = master[master["employee_name"].isin(f_emp)]

# ===========================================================================
# SECTION A — KPI BAR  (uses clean master column names)
# ===========================================================================

st.markdown('<div class="sec-hdr">📈 KPIs</div>', unsafe_allow_html=True)

if master.empty:
    st.warning("אין נתונים לפי הסינון שנבחר")
    st.stop()

_total_billing  = float(master["billing"].sum()) if "billing" in master.columns else 0.0
_total_cost     = float(master["cost"].sum())    if "cost"    in master.columns else 0.0
_total_profit   = float(master["profit"].sum())  if "profit"  in master.columns else 0.0
_total_hours    = float(master["hours"].sum())   if "hours"   in master.columns else 0.0
_margin_overall = (_total_profit / _total_billing * 100) if _total_billing > 0 else 0.0
_pcls = "green" if _total_profit >= 0 else "red"
_mcls = "green" if _margin_overall >= 10 else "red"

# Trend for delta badges
_trend_df = get_profit_trend(master)
_prev_b = _prev_p = 0.0
_cur_b  = _total_billing
_cur_p  = _total_profit
if len(_trend_df) >= 2:
    _prev_b = float(_trend_df.iloc[-2].get("billing", 0))
    _prev_p = float(_trend_df.iloc[-2].get("profit",  0))
    _cur_b  = float(_trend_df.iloc[-1].get("billing", _total_billing))
    _cur_p  = float(_trend_df.iloc[-1].get("profit",  _total_profit))

k1, k2, k3, k4 = st.columns(4)
for _col, lbl, val, cls, dc, dp in [
    (k1, "💰 חיוב כולל",  f"₪{_total_billing:,.0f}",  "",    _cur_b, _prev_b),
    (k2, "📤 עלות כוללת", f"₪{_total_cost:,.0f}",      "",    0, 0),
    (k3, "📈 רווח כולל",  f"₪{_total_profit:,.0f}",    _pcls, _cur_p, _prev_p),
    (k4, "% מרג'ין",      f"{_margin_overall:.1f}%",   _mcls, 0, 0),
]:
    badge = _pct_badge(dc, dp) if dp else ""
    with _col:
        st.markdown(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">{lbl}</div>'
            f'<div class="kpi-value {cls}">{val}</div>'
            f'<div class="kpi-delta">{badge}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

# Last-calculated month bar
_last_m = st.session_state.get("last_month", "")
if _last_m and "month" in master.columns and _last_m in master["month"].values:
    _lm_df = master[master["month"] == _last_m]
    _lm_b  = float(_lm_df["billing"].sum()) if "billing" in _lm_df.columns else 0
    _lm_p  = float(_lm_df["profit"].sum())  if "profit"  in _lm_df.columns else 0
    _prev  = _prev_month(_last_m)
    _prev_row_m = master[master["month"] == _prev] if _prev and _prev in master["month"].values else pd.DataFrame()
    _pb_m = float(_prev_row_m["billing"].sum()) if not _prev_row_m.empty and "billing" in _prev_row_m.columns else 0
    _pp_m = float(_prev_row_m["profit"].sum())  if not _prev_row_m.empty and "profit"  in _prev_row_m.columns else 0
    _issues_last = st.session_state.get("last_issues", pd.DataFrame())
    _ni = len(_issues_last) if isinstance(_issues_last, pd.DataFrame) and not _issues_last.empty else 0
    prev_txt = (f' &nbsp;|&nbsp; vs {_prev}: חיוב {_pct_badge(_lm_b,_pb_m)} רווח {_pct_badge(_lm_p,_pp_m)}'
                if _pb_m else "")
    st.markdown(
        f'<div class="comp-bar">📅 <b>{_last_m}</b>: '
        f'חיוב ₪{_lm_b:,.0f} · רווח ₪{_lm_p:,.0f}'
        f'{" · ⚠️ " + str(_ni) + " חריגים" if _ni else " · ✅ ללא חריגות"}'
        f'{prev_txt}</div>',
        unsafe_allow_html=True,
    )

# ===========================================================================
# SECTION C — CHARTS
# ===========================================================================

st.markdown('<div class="sec-hdr">📊 גרפים</div>', unsafe_allow_html=True)

if _PLOTLY:
    top_cl  = get_top_clients(master)
    top_emp = get_top_employees(master)

    r1a, r1b = st.columns([3, 2])
    with r1a:
        st.caption("מגמת הכנסות, רווח ועלות לפי חודש")
        fig = _line_trend(_trend_df)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("נדרש לפחות חודש אחד")
    with r1b:
        st.caption("חלוקת הכנסות — לקוחות")
        fig = _pie(master)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    r2a, r2b = st.columns(2)
    with r2a:
        st.caption("לקוחות מובילים — חיוב")
        fig = _bar_clients(top_cl)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with r2b:
        st.caption("עובדים — עלות מעביד")
        fig = _bar_employees(top_emp)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    r3a, _ = st.columns(2)
    with r3a:
        st.caption("רווח לפי לקוח")
        fig = _bar_clients_profit(top_cl)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
else:
    st.info("💡 התקן plotly לגרפים: `pip install plotly`")

# ===========================================================================
# SECTION D — TABLE  (client | hours | billing | cost | profit | margin)
# ===========================================================================

st.markdown('<div class="sec-hdr">📋 רווחיות לפי לקוח</div>', unsafe_allow_html=True)

# Aggregate from master (clean column names)
if "client" in master.columns and "billing" in master.columns:
    _client_tbl = master.groupby("client", as_index=False).agg(
        hours  =("hours",   "sum"),
        billing=("billing", "sum"),
        cost   =("cost",    "sum"),
        profit =("profit",  "sum"),
    ).sort_values("billing", ascending=False)
    _client_tbl["margin"] = (
        _client_tbl["profit"] / _client_tbl["billing"].replace(0, float("nan")) * 100
    ).round(1).fillna(0.0)
    _client_tbl["קטגוריה"] = _client_tbl["margin"].apply(
        lambda m: "HIGH" if m > 30 else "MEDIUM" if m >= 10 else "LOW" if m >= 0 else "LOSS"
    )
    _ct_disp = _client_tbl.rename(columns={
        "client": "לקוח", "hours": "שעות", "billing": "חיוב ₪",
        "cost": "עלות ₪", "profit": "רווח ₪", "margin": "% מרג'ין",
    })
    _show = [c for c in ["לקוח", "שעות", "חיוב ₪", "עלות ₪", "רווח ₪", "% מרג'ין", "קטגוריה"]
             if c in _ct_disp.columns]
    st.dataframe(
        _ct_disp[_show].style
        .format({"שעות": "{:.1f}", "חיוב ₪": "₪{:,.0f}",
                 "עלות ₪": "₪{:,.0f}", "רווח ₪": "₪{:,.0f}", "% מרג'ין": "{:.1f}%"})
        .applymap(_scat, subset=["קטגוריה"]),
        use_container_width=True, hide_index=True,
    )

# Employee drill-down
with st.expander("🔍 פירוט לפי עובד"):
    if "employee_name" in master.columns and "billing" in master.columns:
        _emp_tbl = master.groupby(["employee_name", "client"], as_index=False).agg(
            hours  =("hours",   "sum"),
            billing=("billing", "sum"),
            cost   =("cost",    "sum"),
            profit =("profit",  "sum"),
        )
        _emp_tbl["margin"] = (
            _emp_tbl["profit"] / _emp_tbl["billing"].replace(0, float("nan")) * 100
        ).round(1).fillna(0.0)
        _emp_tbl = _emp_tbl.sort_values("billing", ascending=False).reset_index(drop=True)
        _emp_tbl = _emp_tbl.rename(columns={
            "employee_name": "עובד", "client": "לקוח", "hours": "שעות",
            "billing": "חיוב ₪", "cost": "עלות ₪", "profit": "רווח ₪", "margin": "% מרג'ין",
        })
        st.dataframe(
            _emp_tbl.style.format({
                "שעות": "{:.1f}", "חיוב ₪": "₪{:,.0f}",
                "עלות ₪": "₪{:,.0f}", "רווח ₪": "₪{:,.0f}", "% מרג'ין": "{:.1f}%",
            }),
            use_container_width=True, hide_index=True,
        )

# ===========================================================================
# SECTION E — ALERTS
# ===========================================================================

st.markdown('<div class="sec-hdr">⚠️ התראות</div>', unsafe_allow_html=True)

_alerts: list[tuple[str, str]] = []

# 1. Negative profit clients
if "client" in master.columns and "profit" in master.columns:
    _by_client = master.groupby("client")[["billing", "profit", "margin"]].sum()
    _loss = _by_client[_by_client["profit"] < 0]
    if not _loss.empty:
        _tot = float(_loss["profit"].sum())
        _alerts.append(("error",
            f"❌ לקוחות בהפסד ({len(_loss)}): {', '.join(_loss.index.tolist())} — "
            f'סה"כ ₪{_tot:,.0f}'))

# 2. Low margin clients (<10%, but profitable)
if "client" in master.columns and "profit" in master.columns:
    _by_client_m = master.groupby("client").agg(
        billing=("billing", "sum"), profit=("profit", "sum")
    )
    _by_client_m["margin"] = (
        _by_client_m["profit"] / _by_client_m["billing"].replace(0, float("nan")) * 100
    ).fillna(0)
    _low = _by_client_m[(_by_client_m["profit"] >= 0) & (_by_client_m["margin"] < 10)]
    if not _low.empty:
        _alerts.append(("warning", f"⚠️ מרג'ין נמוך (<10%): {', '.join(_low.index.tolist())}"))

# 3. MoM billing drop >15%
if len(_trend_df) >= 2 and "billing" in _trend_df.columns:
    _t_last = float(_trend_df.iloc[-1]["billing"])
    _t_prev = float(_trend_df.iloc[-2]["billing"])
    if _t_prev > 0:
        _drop = (_t_last - _t_prev) / _t_prev * 100
        if _drop < -15:
            _alerts.append(("warning",
                f"⚠️ ירידה בחיוב: {_drop:.1f}% (₪{_t_prev:,.0f} → ₪{_t_last:,.0f})"))

# 4. Issues from last calculation run
_issues_last = st.session_state.get("last_issues", pd.DataFrame())
if isinstance(_issues_last, pd.DataFrame) and not _issues_last.empty and "issue_type" in _issues_last.columns:
    _na = _issues_last["issue_type"].str.contains("הסכם חסר", na=False).sum()
    if _na:
        _alerts.append(("warning", f"⚠️ הסכם חסר — {_na} שורות בחישוב האחרון"))

# 5. Validation warnings from master
_val_warnings = validate_master(master)
for _w in _val_warnings:
    _alerts.append(("warning", f"⚠️ {_w}"))

if _alerts:
    for kind, msg in _alerts:
        if kind == "error":
            st.error(msg)
        else:
            st.warning(msg)
else:
    st.success("✅ אין חריגות בנתונים המסוננים")

# Recommendations
with st.expander("💬 המלצות לפי לקוח"):
    if "client" in master.columns and "billing" in master.columns:
        _rec_tbl = master.groupby("client", as_index=False).agg(
            billing=("billing", "sum"), cost=("cost", "sum"), profit=("profit", "sum")
        )
        _rec_tbl["margin"] = (
            _rec_tbl["profit"] / _rec_tbl["billing"].replace(0, float("nan")) * 100
        ).round(1).fillna(0.0)
        def _make_insight(row) -> str:
            tips = []
            if row["profit"] < 0:     tips.append("⚠️ לקוח מפסיד — בדיקה דחופה")
            if 0 <= row["profit"] and row["margin"] < 10: tips.append("שקול העלאת תעריף")
            if row["cost"] > 0 and row["billing"] > 0 and row["cost"] / row["billing"] > 0.85:
                tips.append("בדוק עלויות עובדים")
            return " | ".join(tips) if tips else "✓ מצב תקין"
        _rec_tbl["category"] = _rec_tbl["margin"].apply(
            lambda m: "HIGH" if m > 30 else "MEDIUM" if m >= 10 else "LOW" if m >= 0 else "LOSS"
        )
        _rec_tbl["insight"] = _rec_tbl.apply(_make_insight, axis=1)
        _rec_tbl = _rec_tbl.sort_values("profit").rename(columns={
            "client": "לקוח", "billing": "חיוב ₪", "profit": "רווח ₪",
            "margin": "% מרג'ין", "category": "קטגוריה", "insight": "המלצה",
        })
        _rs = [c for c in ["לקוח", "חיוב ₪", "רווח ₪", "% מרג'ין", "קטגוריה", "המלצה"] if c in _rec_tbl.columns]
        st.dataframe(
            _rec_tbl[_rs].style
            .format({"חיוב ₪": "₪{:,.0f}", "רווח ₪": "₪{:,.0f}", "% מרג'ין": "{:.1f}%"})
            .applymap(_scat, subset=["קטגוריה"]),
            use_container_width=True, hide_index=True,
        )

# ===========================================================================
# OPTIONAL AI CHAT
# ===========================================================================

_api_key = os.getenv("ANTHROPIC_API_KEY", "")
if _api_key:
    st.divider()
    st.markdown('<div class="sec-hdr">🤖 שאל על הנתונים</div>', unsafe_allow_html=True)
    _q = st.text_input("שאלה:", placeholder="איזה לקוח הכי רווחי?", key="ai_q",
                        label_visibility="collapsed")
    if _q and st.button("שאל", key="ai_btn"):
        # Try pandas first
        _pandas_ans = _answers_with_pandas(master, _q)
        if _pandas_ans:
            st.info(_pandas_ans)
        else:
            try:
                from ai_tools import ask_ai_about_report
                with st.spinner("חושב..."):
                    _ans = ask_ai_about_report(master.head(300), _q)
                st.info(_ans)
            except Exception as _e:
                st.error(f"שגיאת AI: {_e}")
