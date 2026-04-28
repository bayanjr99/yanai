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
    month_file_mtime, update_master, get_all_data,
    filter_by_client, get_profit_trend, get_top_clients, get_top_employees,
    DATA_ROOT, MASTER_PATH, MASTER_XLSX,
)
from core.validation import ValidationError
from core.analytics import kpi_summary, dashboard_table, insights_engine

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
    if not _PLOTLY or df.empty or "month" not in df.columns:
        return None
    fig = go.Figure()
    color_map = {
        "billing_amount": ("#1F497D", "חיוב", True),
        "profit":         ("#16a34a", "רווח", False),
        "cost":           ("#dc2626", "עלות", False),
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
                      yaxis=dict(showgrid=True, gridcolor="#f3f4f6",
                                 tickformat="₪,.0f"))
    return fig


def _bar_h(df: pd.DataFrame, x_col: str, y_col: str, color: str = "#1F497D", title_fmt: str = "₪{:,.0f}"):
    if not _PLOTLY or df.empty:
        return None
    d = df.sort_values(x_col, ascending=True).tail(10)
    fig = px.bar(d, x=x_col, y=y_col, orientation="h",
                 color_discrete_sequence=[color], labels={x_col: "₪", y_col: ""})
    fig.update_layout(**_PL, height=max(180, len(d) * 32))
    fig.update_traces(texttemplate=f"₪%{{x:,.0f}}", textposition="outside")
    fig.update_xaxes(visible=False)
    fig.update_yaxes(showgrid=False)
    return fig


def _bar_clients(df: pd.DataFrame):
    if not _PLOTLY or df.empty or "client" not in df.columns or "billing_amount" not in df.columns:
        return None
    return _bar_h(df, "billing_amount", "client", "#1F497D")


def _bar_clients_profit(df: pd.DataFrame):
    if not _PLOTLY or df.empty:
        return None
    d = df.sort_values("billing_amount").copy()
    if "profit" not in d.columns:
        return None
    d = d.sort_values("profit")
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


def _pie(df: pd.DataFrame, col: str = "billing_amount"):
    if not _PLOTLY or df.empty or "client" not in df.columns or col not in df.columns:
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
    """Try to answer simple questions using pandas before calling AI."""
    q = question.strip().lower()
    if not df.empty and "client" in df.columns and "billing_amount" in df.columns:
        top_client = df.groupby("client")["billing_amount"].sum().idxmax()
        if any(w in q for w in ["רווחי", "הכי רווחי", "top client", "best client"]):
            b = df.groupby("client")["billing_amount"].sum()[top_client]
            p = df.groupby("client")["profit"].sum()[top_client] if "profit" in df.columns else 0
            return f"הלקוח הכי רווחי: **{top_client}** — חיוב ₪{b:,.0f}, רווח ₪{p:,.0f}"
        if any(w in q for w in ["כמה חודשים", "חודשים"]) and "month" in df.columns:
            return f"יש {df['month'].nunique()} חודשים בנתונים: {', '.join(sorted(df['month'].unique()))}"
        if any(w in q for w in ["עובד", "עלות עובד"]) and "employee_name" in df.columns:
            top_emp = df.groupby("employee_name")["cost"].sum().idxmax() if "cost" in df.columns else "—"
            c = df.groupby("employee_name")["cost"].sum()[top_emp] if "cost" in df.columns else 0
            return f"העובד היקר ביותר: **{top_emp}** — עלות ₪{c:,.0f}"
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
# SECTION A — KPI BAR
# ===========================================================================

st.markdown('<div class="sec-hdr">📈 KPIs</div>', unsafe_allow_html=True)

if master.empty:
    st.warning("אין נתונים לפי הסינון שנבחר")
    st.stop()

_kpis   = kpi_summary(master)
_margin = (_kpis["total_profit"] / _kpis["total_billing"] * 100
           if _kpis["total_billing"] > 0 else 0.0)
_pcls   = "green" if _kpis["total_profit"] >= 0 else "red"
_mcls   = "green" if _margin >= 10 else "red"

# vs previous period comparison (last month in filtered range)
_trend_df = get_profit_trend(master)
_prev_b = _prev_p = 0.0
if len(_trend_df) >= 2:
    _prev_row = _trend_df.iloc[-2]
    _prev_b   = float(_prev_row.get("billing_amount", 0))
    _prev_p   = float(_prev_row.get("profit", 0))
_cur_b = float(_trend_df.iloc[-1].get("billing_amount", 0)) if not _trend_df.empty else _kpis["total_billing"]
_cur_p = float(_trend_df.iloc[-1].get("profit", 0)) if not _trend_df.empty else _kpis["total_profit"]

k1, k2, k3, k4 = st.columns(4)
for _col, lbl, val, cls, delta_cur, delta_prev in [
    (k1, "💰 חיוב כולל",  f'₪{_kpis["total_billing"]:,.0f}', "", _cur_b, _prev_b),
    (k2, "📤 עלות כוללת", f'₪{_kpis["total_cost"]:,.0f}',    "", 0, 0),
    (k3, "📈 רווח כולל",  f'₪{_kpis["total_profit"]:,.0f}',  _pcls, _cur_p, _prev_p),
    (k4, "% מרג'ין",      f'{_margin:.1f}%',                  _mcls, 0, 0),
]:
    badge = _pct_badge(delta_cur, delta_prev) if delta_prev else ""
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

# Last-calculated month quick stats
_last_m = st.session_state.get("last_month", "")
if _last_m and "month" in master.columns and _last_m in master["month"].values:
    _lm_df = master[master["month"] == _last_m]
    _lm_b  = float(_lm_df["billing_amount"].sum()) if "billing_amount" in _lm_df.columns else 0
    _lm_p  = float(_lm_df["profit"].sum()) if "profit" in _lm_df.columns else 0
    _prev  = _prev_month(_last_m)
    _prev_row_m = master[master["month"] == _prev] if _prev and _prev in master["month"].values else pd.DataFrame()
    _pb_m = float(_prev_row_m["billing_amount"].sum()) if not _prev_row_m.empty and "billing_amount" in _prev_row_m.columns else 0
    _pp_m = float(_prev_row_m["profit"].sum()) if not _prev_row_m.empty and "profit" in _prev_row_m.columns else 0
    _issues_last = st.session_state.get("last_issues", pd.DataFrame())
    _ni = len(_issues_last) if _issues_last is not None and not (isinstance(_issues_last, pd.DataFrame) and _issues_last.empty) else 0

    prev_txt = ""
    if _pb_m:
        prev_txt = (f' &nbsp;|&nbsp; vs {_prev}: חיוב {_pct_badge(_lm_b,_pb_m)} '
                    f'רווח {_pct_badge(_lm_p,_pp_m)}')
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

    # Row 1: trend + pie
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

    # Row 2: top clients bar + top employees cost
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

    # Row 3: profit by client
    r3a, r3b = st.columns(2)
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

_dash = dashboard_table(master)
if not _dash.empty:
    _dd = _dash.drop(columns=[c for c in ["billing_change_pct", "profit_change_pct"] if c in _dash.columns], errors="ignore")
    _hours_by_client = (
        master.groupby("client")["total_hours"].sum()
        if "total_hours" in master.columns else pd.Series(dtype=float)
    )
    if not _hours_by_client.empty:
        _dd["total_hours"] = _dd["client"].map(_hours_by_client).fillna(0)

    _dd = _dd.rename(columns={
        "client":         "לקוח",
        "total_hours":    "שעות",
        "billing_amount": "חיוב ₪",
        "cost":           "עלות ₪",
        "profit":         "רווח ₪",
        "margin_pct":     "% מרג'ין",
        "category":       "קטגוריה",
    })
    _show_cols = [c for c in ["לקוח", "שעות", "חיוב ₪", "עלות ₪", "רווח ₪", "% מרג'ין", "קטגוריה"]
                  if c in _dd.columns]
    st.dataframe(
        _dd[_show_cols].style
        .format({
            "שעות": "{:.1f}", "חיוב ₪": "₪{:,.0f}",
            "עלות ₪": "₪{:,.0f}", "רווח ₪": "₪{:,.0f}", "% מרג'ין": "{:.1f}%",
        })
        .applymap(_scat, subset=["קטגוריה"] if "קטגוריה" in _dd.columns else []),
        use_container_width=True, hide_index=True,
    )

# Drill-down by employee (expandable)
with st.expander("🔍 פירוט לפי עובד"):
    if "employee_name" in master.columns:
        _emp_tbl = (
            master.groupby(["employee_name", "client"], as_index=False)
            .agg(
                total_hours   =("total_hours",    "sum"),
                billing_amount=("billing_amount", "sum"),
                cost          =("cost",           "sum"),
                profit        =("profit",         "sum"),
            )
        )
        _emp_tbl["margin_pct"] = (
            _emp_tbl["profit"] / _emp_tbl["billing_amount"].replace(0, float("nan")) * 100
        ).round(1)
        _emp_tbl = _emp_tbl.sort_values("billing_amount", ascending=False).reset_index(drop=True)
        _emp_tbl = _emp_tbl.rename(columns={
            "employee_name": "עובד", "client": "לקוח",
            "total_hours": "שעות", "billing_amount": "חיוב ₪",
            "cost": "עלות ₪", "profit": "רווח ₪", "margin_pct": "% מרג'ין",
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

_ins_df  = insights_engine(master)
_alerts: list[tuple[str, str]] = []

# 1. Negative profit clients
if not _ins_df.empty and "profit" in _ins_df.columns:
    _loss = _ins_df[_ins_df["profit"] < 0]
    if not _loss.empty:
        _names = ", ".join(_loss["client"].tolist())
        _tot   = float(_loss["profit"].sum())
        _alerts.append(("error", f"❌ לקוחות בהפסד ({len(_loss)}): {_names} — סה\"כ ₪{_tot:,.0f}"))

# 2. Low margin clients (<10%)
if not _ins_df.empty and "margin_pct" in _ins_df.columns:
    _low = _ins_df[(_ins_df["profit"] >= 0) & (_ins_df["margin_pct"].fillna(100) < 10)]
    if not _low.empty:
        _alerts.append(("warning", f"⚠️ מרג'ין נמוך (<10%): {', '.join(_low['client'].tolist())}"))

# 3. MoM billing drop (last 2 months in trend)
if len(_trend_df) >= 2 and "billing_amount" in _trend_df.columns:
    _t_last   = float(_trend_df.iloc[-1]["billing_amount"])
    _t_prev   = float(_trend_df.iloc[-2]["billing_amount"])
    if _t_prev > 0:
        _drop_pct = (_t_last - _t_prev) / _t_prev * 100
        if _drop_pct < -15:
            _alerts.append(("warning",
                f"⚠️ ירידה בחיוב vs חודש קודם: {_drop_pct:.1f}% "
                f"(₪{_t_prev:,.0f} → ₪{_t_last:,.0f})"))

# 4. Issues from last run
_issues_last = st.session_state.get("last_issues", pd.DataFrame())
if isinstance(_issues_last, pd.DataFrame) and not _issues_last.empty:
    _n_issues = len(_issues_last)
    _na = len(_issues_last[_issues_last.get("issue_type", pd.Series(dtype=str)).str.contains("הסכם חסר", na=False)]) if "issue_type" in _issues_last.columns else 0
    if _na:
        _alerts.append(("warning", f"⚠️ הסכם חסר — {_na} שורות בחישוב האחרון"))

if _alerts:
    for kind, msg in _alerts:
        if kind == "error":
            st.error(msg)
        else:
            st.warning(msg)
else:
    st.success("✅ אין חריגות בנתונים המסוננים")

# Recommendations expander
if not _ins_df.empty:
    with st.expander("💬 המלצות לפי לקוח"):
        _rec = _ins_df.rename(columns={
            "client": "לקוח", "billing_amount": "חיוב ₪", "profit": "רווח ₪",
            "margin_pct": "% מרג'ין", "category": "קטגוריה", "insight": "המלצה",
        })
        _show = [c for c in ["לקוח", "חיוב ₪", "רווח ₪", "% מרג'ין", "קטגוריה", "המלצה"] if c in _rec.columns]
        st.dataframe(
            _rec[_show].style
            .format({"חיוב ₪": "₪{:,.0f}", "רווח ₪": "₪{:,.0f}", "% מרג'ין": "{:.1f}%"})
            .applymap(_scat, subset=["קטגוריה"] if "קטגוריה" in _rec.columns else []),
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
