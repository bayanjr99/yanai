"""
Smart Billing System — Premium SaaS Dashboard  (Phase 5)
Single-page dashboard: Upload → Calculate → Results
"""

from __future__ import annotations

import json
import os
import tempfile

import pandas as pd
import streamlit as st

try:
    import plotly.express as px
    import plotly.graph_objects as go
    _PLOTLY = True
except ImportError:
    _PLOTLY = False

from db   import init_db, log_run
from auth import login_user, register_user
from pipeline import (
    run_month_pipeline, list_available_months,
    month_file_mtime, DATA_ROOT, OUTPUT_ROOT,
    get_available_months, save_month_history,
    load_month_kpis, load_month_clients, load_trend_df,
    HISTORY_ROOT,
)
from core.validation import ValidationError
from core.analytics import (
    kpi_summary, dashboard_table,
    profitability_by_employee, insights_engine, top_insights,
)

init_db()

# ===========================================================================
# AUTH GATE
# ===========================================================================

def _auth_screen() -> None:
    st.set_page_config(page_title="Smart Billing | Login", page_icon="💰", layout="centered")
    st.markdown(
        "<style>body,.stApp{direction:rtl}.block-container{max-width:420px;margin:auto;padding-top:5rem}</style>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="text-align:center;margin-bottom:2rem">'
        '<div style="font-size:52px">💰</div>'
        '<h2 style="color:#1F497D;margin:.4rem 0 0">Smart Billing System</h2>'
        '<p style="color:#6b7280;font-size:14px">חשב חיוב ורווחיות בלחיצה אחת</p>'
        "</div>",
        unsafe_allow_html=True,
    )
    t1, t2 = st.tabs(["🔑 כניסה", "✏️ הרשמה"])
    with t1:
        with st.form("lf"):
            em = st.text_input("אימייל", placeholder="your@email.com")
            pw = st.text_input("סיסמה", type="password")
            if st.form_submit_button("→ כניסה למערכת", type="primary", use_container_width=True):
                ok, user = login_user(em, pw)
                if ok:
                    st.session_state["user"] = user
                    st.rerun()
                else:
                    st.error("אימייל או סיסמה שגויים")
    with t2:
        with st.form("rf"):
            nm = st.text_input("שם מלא")
            re_ = st.text_input("אימייל")
            rp  = st.text_input("סיסמה", type="password")
            if st.form_submit_button("→ צור חשבון", use_container_width=True):
                ok, msg = register_user(nm, re_, rp)
                st.success("✅ נרשמת — עבור לכניסה") if ok else st.error(msg)
    st.stop()


if "user" not in st.session_state:
    _auth_screen()

_user: dict = st.session_state["user"]

# ===========================================================================
# PAGE CONFIG + CSS
# ===========================================================================

st.set_page_config(layout="wide", page_title="Smart Billing System", page_icon="💰")

st.markdown("""
<style>
/* ── Global ── */
body, .stApp { direction: rtl; font-family: 'Segoe UI', Arial, sans-serif; }
.block-container { padding-top: 0 !important; padding-bottom: 2rem; }
section[data-testid="stSidebar"] { background: #f9fafb; }

/* ── Top header bar ── */
.sbs-hdr {
    background: linear-gradient(135deg,#1a3a5c,#1F497D);
    color:#fff; padding:16px 28px; margin:-1rem -4rem 0;
    display:flex; align-items:center; justify-content:space-between;
    box-shadow:0 2px 8px rgba(0,0,0,.15);
}
.sbs-hdr-title { font-size:20px; font-weight:900; letter-spacing:-.3px; }
.sbs-hdr-sub   { font-size:12px; opacity:.7; margin-top:2px; }
.sbs-hdr-right { font-size:13px; opacity:.85; text-align:left; }

/* ── KPI cards ── */
.kpi-card {
    background:#fff; border-radius:14px; padding:20px 14px;
    text-align:center; border:1px solid #edf0f7;
    box-shadow:0 2px 10px rgba(0,0,0,.05); height:108px;
    display:flex; flex-direction:column; justify-content:center;
    transition:box-shadow .2s;
}
.kpi-card:hover { box-shadow:0 4px 18px rgba(31,73,125,.12); }
.kpi-label { font-size:11px; color:#9ca3af; font-weight:700;
    text-transform:uppercase; letter-spacing:.4px; margin-bottom:8px; }
.kpi-value { font-size:32px; font-weight:900; color:#111827; line-height:1.1; }
.kpi-value.green { color:#16a34a; }
.kpi-value.red   { color:#dc2626; }

/* ── Glance panel ── */
.glance {
    background:linear-gradient(135deg,#1F497D,#2563eb);
    color:#fff; border-radius:14px; padding:20px 26px;
    box-shadow:0 4px 16px rgba(31,73,125,.2); margin-bottom:4px;
}
.glance-title { font-size:11px; opacity:.65; font-weight:700;
    text-transform:uppercase; letter-spacing:.5px; margin-bottom:6px; }
.glance-text  { font-size:16px; font-weight:600; line-height:1.7; }
.glance-badge { display:inline-block; background:rgba(255,255,255,.18);
    border-radius:20px; padding:3px 14px; font-size:12px; font-weight:700; margin-top:8px; }

/* ── Comparison bar ── */
.comp-bar {
    background:#f8faff; border:1px solid #dde3f0; border-radius:12px;
    padding:10px 18px; font-size:14px; color:#374151; margin-top:8px; margin-bottom:4px;
}

/* ── Section header ── */
.sec-hdr {
    font-size:15px; font-weight:800; color:#1F497D;
    padding-bottom:8px; border-bottom:2px solid #edf0f7;
    margin:20px 0 12px; display:flex; align-items:center; gap:6px;
}

/* ── Alert box ── */
.alert-box { background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:8px 16px; }
.alert-row { display:flex; align-items:center; gap:10px;
    padding:9px 4px; border-bottom:1px solid #f3f4f6; font-size:14px; }
.alert-row:last-child { border-bottom:none; }
.alert-icon { font-size:17px; flex-shrink:0; }
.a-crit { color:#991b1b; font-weight:700; }
.a-warn { color:#92400e; }

/* ── Onboarding card ── */
.ob-card {
    background:#fff; border-radius:16px; padding:28px 22px;
    border:1px solid #edf0f7; box-shadow:0 2px 10px rgba(0,0,0,.05);
    text-align:center; height:100%;
}
.ob-icon  { font-size:40px; margin-bottom:14px; }
.ob-step  { font-size:11px; font-weight:700; color:#6366f1;
    text-transform:uppercase; letter-spacing:.5px; margin-bottom:6px; }
.ob-title { font-size:16px; font-weight:800; color:#111827; margin-bottom:8px; }
.ob-body  { font-size:13px; color:#6b7280; line-height:1.65; }

/* ── Filter tag ── */
.ftag { display:inline-block; background:#1F497D; color:#fff;
    border-radius:20px; padding:3px 12px; margin:2px;
    font-size:12px; font-weight:600; }

/* ── Sidebar steps ── */
.step-row { display:flex; align-items:flex-start; gap:10px; padding:8px 0; }
.step-num { min-width:24px; height:24px; border-radius:50%; background:#1F497D;
    color:#fff; font-size:12px; font-weight:800;
    display:flex; align-items:center; justify-content:center; flex-shrink:0; }
.step-num.done { background:#16a34a; }
.step-lbl { font-size:13px; font-weight:700; color:#111827; }
.step-sub { font-size:11px; color:#9ca3af; }
</style>
""", unsafe_allow_html=True)

# ===========================================================================
# PLOTLY CHART HELPERS
# ===========================================================================

_PL = dict(margin=dict(l=0, r=0, t=8, b=0), paper_bgcolor="white",
           plot_bgcolor="white", font=dict(family="Segoe UI, Arial", size=12))


def _bar_billing(df: pd.DataFrame):
    if not _PLOTLY or df.empty: return None
    d = df.groupby("client")["billing_amount"].sum().reset_index().sort_values("billing_amount")
    fig = px.bar(d, x="billing_amount", y="client", orientation="h",
                 color_discrete_sequence=["#1F497D"],
                 labels={"billing_amount": "₪", "client": ""})
    fig.update_layout(**_PL, height=max(200, len(d)*34))
    fig.update_traces(texttemplate="₪%{x:,.0f}", textposition="outside")
    fig.update_xaxes(visible=False); fig.update_yaxes(showgrid=False)
    return fig


def _bar_profit(df: pd.DataFrame):
    if not _PLOTLY or df.empty: return None
    d = df.groupby("client")["profit"].sum().reset_index().sort_values("profit")
    d["c"] = d["profit"].apply(lambda x: "#16a34a" if x >= 0 else "#dc2626")
    fig = px.bar(d, x="profit", y="client", orientation="h",
                 color="c", color_discrete_map={c: c for c in d["c"].unique()},
                 labels={"profit": "₪", "client": ""})
    fig.update_layout(**_PL, height=max(200, len(d)*34), showlegend=False)
    fig.update_traces(texttemplate="₪%{x:,.0f}", textposition="outside")
    fig.update_xaxes(visible=False); fig.update_yaxes(showgrid=False)
    return fig


def _pie_revenue(df: pd.DataFrame):
    if not _PLOTLY or df.empty: return None
    d = df.groupby("client")["billing_amount"].sum().reset_index()
    fig = px.pie(d, values="billing_amount", names="client", hole=0.42,
                 color_discrete_sequence=px.colors.qualitative.Set2)
    fig.update_layout(**_PL, height=270, showlegend=True,
                      legend=dict(orientation="v", font_size=11))
    fig.update_traces(textposition="inside", textinfo="percent")
    return fig


@st.cache_data(show_spinner=False, ttl=300)
def _load_trend() -> pd.DataFrame:
    rows = []
    if not os.path.isdir(OUTPUT_ROOT): return pd.DataFrame()
    for m in sorted(os.listdir(OUTPUT_ROOT)):
        p = os.path.join(OUTPUT_ROOT, m, "kpis.json")
        if os.path.exists(p):
            try:
                with open(p) as f: d = json.load(f)
                d["month"] = m; rows.append(d)
            except Exception: pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _line_trend(tdf: pd.DataFrame):
    if not _PLOTLY or tdf.empty or "month" not in tdf.columns: return None
    fig = go.Figure()
    if "total_billing" in tdf.columns:
        fig.add_trace(go.Scatter(x=tdf["month"], y=tdf["total_billing"], name="חיוב",
            line=dict(color="#1F497D", width=2.5), fill="tozeroy", fillcolor="rgba(31,73,125,.07)"))
    if "total_profit" in tdf.columns:
        fig.add_trace(go.Scatter(x=tdf["month"], y=tdf["total_profit"], name="רווח",
            line=dict(color="#16a34a", width=2)))
    fig.update_layout(**{**_PL, "showlegend": True}, height=240,
                      legend=dict(orientation="h", y=1.08),
                      xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#f3f4f6"))
    return fig

# ===========================================================================
# DATA HELPERS
# ===========================================================================

def _prev_month(month: str) -> str:
    try:
        mm, yy = int(month[:2]), int(month[3:])
        return f"12-{yy-1}" if mm == 1 else f"{mm-1:02d}-{yy}"
    except Exception: return ""


def _load_prev_kpis(month: str) -> dict | None:
    prev = _prev_month(month)
    if not prev: return None
    p = os.path.join(OUTPUT_ROOT, prev, "kpis.json")
    if os.path.exists(p):
        try:
            with open(p) as f: return json.load(f)
        except Exception: pass
    return None


def _save_kpis(month: str, kpis: dict) -> None:
    out = os.path.join(OUTPUT_ROOT, month)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "kpis.json"), "w") as f:
        json.dump(kpis, f)


@st.cache_data(show_spinner=False)
def _run_month(month: str, _mtime: float) -> dict:
    try:
        result = run_month_pipeline(month)
    except ValidationError as e:
        return {"success": False, "error": f"שגיאת אימות PDF: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    mo = os.path.join(OUTPUT_ROOT, month)
    def _rb(n): p = os.path.join(mo, n); return open(p,"rb").read() if os.path.exists(p) else b""
    return {
        "success": True,
        "detail_df": result.detail_df, "daily_df": result.daily_df,
        "issues_df": result.issues_df, "validation": result.validation,
        "month_str": result.month_str,
        "billing_bytes": _rb("final.xlsx"), "issues_bytes": _rb("issues.xlsx"),
        "profitability_bytes": _rb("profitability.xlsx"),
    }


def _apply_filters(df, clients, sites, employees):
    if clients and "client" in df.columns:      df = df[df["client"].isin(clients)]
    if sites and "site" in df.columns:          df = df[df["site"].isin(sites)]
    if employees and "employee_name" in df.columns: df = df[df["employee_name"].isin(employees)]
    return df


def _save_month_files(month, hours_file, costs_file, ovr_file=None):
    d = os.path.join(DATA_ROOT, month); os.makedirs(d, exist_ok=True)
    ext = "pdf" if hours_file.name.lower().endswith(".pdf") else "xlsx"
    open(os.path.join(d, f"hours.{ext}"), "wb").write(hours_file.read())
    open(os.path.join(d, "costs.xlsx"),   "wb").write(costs_file.read())
    if ovr_file:
        open(os.path.join(DATA_ROOT, "overrides.xlsx"), "wb").write(ovr_file.read())


_CAT_CSS = {
    "HIGH":   "background-color:#dcfce7;color:#166534",
    "MEDIUM": "background-color:#dbeafe;color:#1e3a8a",
    "LOW":    "background-color:#fef9c3;color:#854d0e",
    "LOSS":   "background-color:#fee2e2;color:#991b1b",
}

def _scat(v): return _CAT_CSS.get(str(v), "")

def _srow(row):
    try:
        m = float(row.get("מרג'ין %") or row.get("% רווח") or 0)
        c = float(row.get("השלמה") or 0)
    except Exception: return [""] * len(row)
    if m < 0:  return ["background-color:#fee2e2;font-weight:bold"] * len(row)
    if m < 10: return ["background-color:#fef9c3"] * len(row)
    if c > 0:  return ["background-color:#fffde7"] * len(row)
    return [""] * len(row)

# ===========================================================================
# SIDEBAR
# ===========================================================================

sel_clients: list[str] = []
sel_sites:   list[str] = []
sel_emp:     list[str] = []

with st.sidebar:
    # Brand
    st.markdown(
        '<div style="text-align:center;padding:12px 0 8px">'
        '<div style="font-size:32px">💰</div>'
        '<div style="font-weight:900;color:#1F497D;font-size:15px">Smart Billing</div>'
        '<div style="font-size:11px;color:#9ca3af;margin-top:2px">ינאי פרסונל</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    # Step guide
    _has = bool(st.session_state.get("result"))
    for n, lbl, sub, done in [
        ("1", "בחר חודש",    "מהרשימה למטה",  True),
        ("2", "חשב חיוב",   "לחץ כפתור ירוק", _has),
        ("3", "ראה תוצאות", "דשבורד מלא",     _has),
    ]:
        st.markdown(
            f'<div class="step-row">'
            f'<div class="step-num {"done" if done else ""}">{n}</div>'
            f'<div><div class="step-lbl">{lbl}</div>'
            f'<div class="step-sub">{sub}</div></div></div>',
            unsafe_allow_html=True,
        )
    st.divider()

    # ── Agreements file (required, shared across all months) ─────────────────
    _agr_path = os.path.join(DATA_ROOT, "agreements.xlsx")
    _agr_ok   = os.path.exists(_agr_path)

    if not _agr_ok:
        st.warning("⚠️ חסר קובץ הסכמים")
        agr_up = st.file_uploader(
            "📋 הסכמים (agreements.xlsx)", type=["xlsx"], key="up_agr_main"
        )
        if agr_up:
            os.makedirs(DATA_ROOT, exist_ok=True)
            with open(_agr_path, "wb") as _f:
                _f.write(agr_up.read())
            st.success("✅ הסכמים נשמרו")
            st.rerun()
    else:
        st.caption("✅ הסכמים נטענו")
        if st.button("🔄 עדכן הסכמים", use_container_width=True, key="replace_agr"):
            agr_up2 = st.file_uploader(
                "📋 הסכמים חדשים", type=["xlsx"], key="up_agr_replace"
            )
            if agr_up2:
                with open(_agr_path, "wb") as _f:
                    _f.write(agr_up2.read())
                st.success("✅ עודכן"); st.rerun()

    st.divider()

    # Month selector + calculate
    available_months = list_available_months(DATA_ROOT)
    if available_months:
        selected_month = st.selectbox(
            "📅 חודש לחישוב", available_months,
            index=len(available_months) - 1, key="sel_month",
        )
        calc_btn = st.button(
            "🚀 חשב חיוב",
            type="primary",
            use_container_width=True,
            disabled=not _agr_ok,
            help="" if _agr_ok else "העלה קובץ הסכמים תחילה",
        )
        if calc_btn and _agr_ok:
            mtime = month_file_mtime(selected_month, DATA_ROOT)
            with st.spinner(f"מחשב {selected_month}..."):
                cached = _run_month(selected_month, mtime)
            if cached["success"]:
                st.session_state.update({
                    "result": cached, "sel_month_key": selected_month, "pipeline_error": None,
                })
                kp = kpi_summary(cached["detail_df"])
                kp["n_issues"] = len(cached["issues_df"])
                _save_kpis(selected_month, kp)
                # Persist to /data/months/ for history (survives Render restarts)
                try:
                    from pipeline import PipelineResult
                    import types
                    _r = types.SimpleNamespace(
                        detail_df=cached["detail_df"],
                        daily_df=cached["daily_df"],
                        issues_df=cached["issues_df"],
                        validation=cached["validation"],
                        month_str=cached["month_str"],
                    )
                    save_month_history(selected_month, _r, kp)
                except Exception:
                    pass
                try: log_run(selected_month, kp, _user.get("id"))
                except Exception: pass
                st.rerun()
            else:
                st.session_state["pipeline_error"] = cached["error"]
    else:
        selected_month = None
        st.warning("לא נמצאו חודשים")

    # Add month (secondary)
    with st.expander("➕ הוסף / עדכן נתונים"):
        # Agreements (shared)
        agr_exp = st.file_uploader(
            "📋 הסכמים (agreements.xlsx)", type=["xlsx"], key="up_agr_exp"
        )
        if agr_exp:
            os.makedirs(DATA_ROOT, exist_ok=True)
            with open(os.path.join(DATA_ROOT, "agreements.xlsx"), "wb") as _f:
                _f.write(agr_exp.read())
            st.success("✅ הסכמים נשמרו"); st.rerun()

        st.divider()
        # Shared costs file (optional — used when month folder has no costs.xlsx)
        shared_costs = st.file_uploader(
            "💼 עלויות משותפות (אופציונלי — לכל החודשים)",
            type=["xlsx"], key="up_shared_costs",
        )
        if shared_costs:
            _sc_path = os.path.join(DATA_ROOT, "costs_shared.xlsx")
            with open(_sc_path, "wb") as _f:
                _f.write(shared_costs.read())
            st.success("✅ עלויות משותפות נשמרו")

        # Monthly data
        nm = st.text_input("חודש (MM-YYYY)", placeholder="02-2026", key="nm_in")
        hu = st.file_uploader("שעות (PDF/xlsx)", type=["pdf","xlsx"], key="up_h")
        cu = st.file_uploader("עלויות חודש (xlsx)", type=["xlsx"], key="up_c")
        if st.button("💾 שמור חודש", disabled=not(nm and hu and cu), key="save_month"):
            _save_month_files(nm, hu, cu)
            st.success(f"✅ {nm} נשמר"); st.rerun()

        st.divider()
        # ── Batch: calculate all available months ──────────────────────────
        st.markdown("**⚡ חשב כל החודשים בבת אחת**")
        _all_available = list_available_months(DATA_ROOT)
        _already_done  = set(get_available_months())
        _pending = [m for m in _all_available if m not in _already_done]
        if _pending:
            st.caption(f"{len(_pending)} חודשים ממתינים לחישוב: {', '.join(_pending)}")
        else:
            st.caption("✅ כל החודשים חושבו")
        if st.button(
            f"🚀 חשב {len(_pending)} חודשים",
            disabled=(not _pending or not _agr_ok),
            key="batch_calc",
        ):
            _batch_ok, _batch_fail = [], []
            _progress = st.progress(0)
            _status   = st.empty()
            for _i, _bm in enumerate(_pending):
                _status.text(f"מחשב {_bm} ({_i+1}/{len(_pending)})...")
                try:
                    # Use shared costs fallback if month has no costs.xlsx
                    _month_dir   = os.path.join(DATA_ROOT, _bm)
                    _costs_path  = os.path.join(_month_dir, "costs.xlsx")
                    _shared_path = os.path.join(DATA_ROOT, "costs_shared.xlsx")
                    if not os.path.exists(_costs_path) and os.path.exists(_shared_path):
                        import shutil as _sh
                        _sh.copy(_shared_path, _costs_path)

                    _br = _run_month(_bm, month_file_mtime(_bm, DATA_ROOT))
                    if _br["success"]:
                        _bkp = kpi_summary(_br["detail_df"])
                        _bkp["n_issues"] = len(_br["issues_df"])
                        import types as _tp
                        _robj = _tp.SimpleNamespace(
                            detail_df=_br["detail_df"], daily_df=_br["daily_df"],
                            issues_df=_br["issues_df"], validation=_br["validation"],
                            month_str=_br["month_str"],
                        )
                        save_month_history(_bm, _robj, _bkp)
                        _batch_ok.append(_bm)
                    else:
                        _batch_fail.append(f"{_bm}: {_br['error']}")
                except Exception as _e:
                    _batch_fail.append(f"{_bm}: {_e}")
                _progress.progress((_i + 1) / len(_pending))
            _status.empty()
            if _batch_ok:
                st.success(f"✅ הושלמו: {', '.join(_batch_ok)}")
            if _batch_fail:
                for _ef in _batch_fail:
                    st.warning(f"⚠️ {_ef}")
            st.rerun()

    # Filters (post-calculate)
    if st.session_state.get("result"):
        _df = st.session_state["result"]["detail_df"]
        st.divider()
        st.markdown("**🔍 סינון**")
        sel_clients = st.multiselect("לקוח", sorted(_df["client"].dropna().unique()))
        sel_sites   = st.multiselect("אתר",  sorted(_df["site"].dropna().unique()))
        sel_emp     = st.multiselect("עובד", sorted(_df["employee_name"].dropna().unique()))
        if (sel_clients or sel_sites or sel_emp):
            if st.button("✕ נקה סינון", use_container_width=True):
                sel_clients = sel_sites = sel_emp = []
                st.rerun()

    # User
    st.divider()
    st.caption(f"👤 {_user.get('full_name') or _user.get('email','')}")
    if st.button("🚪 התנתק", use_container_width=True):
        del st.session_state["user"]; st.session_state.pop("result", None); st.rerun()

# ===========================================================================
# HEADER BAR (always visible)
# ===========================================================================

_cur_m = st.session_state.get("sel_month_key", "")
st.markdown(
    f'<div class="sbs-hdr">'
    f'<div><div class="sbs-hdr-title">📊 Smart Billing System</div>'
    f'<div class="sbs-hdr-sub">חשב חיוב ורווחיות בלחיצה אחת · ינאי פרסונל</div></div>'
    f'<div class="sbs-hdr-right">{"📅 " + _cur_m if _cur_m else ""}</div>'
    f'</div>',
    unsafe_allow_html=True,
)
st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

# Error banner
if st.session_state.get("pipeline_error"):
    st.error(f"❌ {st.session_state['pipeline_error']}")

# ===========================================================================
# ONBOARDING (no results yet)
# ===========================================================================

if not st.session_state.get("result"):
    st.markdown(
        '<div style="text-align:center;padding:1.5rem 0 1rem">'
        '<h3 style="color:#111827;font-weight:800;font-size:22px">ברוך הבא! 👋</h3>'
        '<p style="color:#6b7280;font-size:14px">שלושה צעדים פשוטים להפיק דוח חיוב מקצועי</p>'
        '</div>', unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    for col, icon, step, title, body in [
        (c1, "📂", "שלב 1 — הכן",    "קבצי נתונים",
         "<code>data/MM-YYYY/</code><br>• <b>hours.pdf</b> (Andromeda)<br>• <b>costs.xlsx</b><br><br><code>data/agreements.xlsx</code>"),
        (c2, "🚀", "שלב 2 — חשב",    "בחר חודש וחשב",
         "בחר חודש מהרשימה בסרגל הצד<br><br>לחץ <b>🚀 חשב חיוב</b><br><br>תוצאות מוכנות תוך שניות"),
        (c3, "📊", "שלב 3 — נתח",    "ראה תוצאות",
         "• KPIs וגרפים אינטראקטיביים<br>• טבלאות חיוב ורווחיות<br>• התראות חריגות<br>• פירוט לפי לקוח"),
    ]:
        with col:
            st.markdown(
                f'<div class="ob-card"><div class="ob-icon">{icon}</div>'
                f'<div class="ob-step">{step}</div><div class="ob-title">{title}</div>'
                f'<div class="ob-body">{body}</div></div>', unsafe_allow_html=True,
            )
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    if available_months:
        st.success(f"✅  {len(available_months)} חודשים זמינים — בחר חודש בסרגל הצד ולחץ **חשב חיוב**")
    else:
        st.info("אין חודשים עדיין — צור תיקיה בפורמט `MM-YYYY` תחת `data/` עם קבצי שעות ועלויות")
    st.stop()

# ===========================================================================
# LOAD RESULTS
# ===========================================================================

res       = st.session_state["result"]
detail_df = res["detail_df"]
daily_df  = res["daily_df"]
issues_df = res["issues_df"]
sel_month = st.session_state.get("sel_month_key", "")

filtered = _apply_filters(detail_df, sel_clients, sel_sites, sel_emp)

# Active filter pills
if sel_clients or sel_sites or sel_emp:
    parts = []
    if sel_clients: parts.append(f"לקוח: {', '.join(sel_clients)}")
    if sel_sites:   parts.append(f"אתר: {', '.join(sel_sites)}")
    if sel_emp:     parts.append(f"עובד: {', '.join(sel_emp)}")
    st.markdown(
        "🔍 " + " ".join(f'<span class="ftag">{p}</span>' for p in parts),
        unsafe_allow_html=True,
    )
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

# ===========================================================================
# KPI BAR
# ===========================================================================

with st.container():
    kpis     = kpi_summary(filtered)
    n_issues = len(issues_df)
    margin   = kpis["total_profit"] / kpis["total_billing"] * 100 if kpis["total_billing"] > 0 else 0.0
    pcls     = "green" if kpis["total_profit"] >= 0 else "red"
    icls     = "red" if n_issues > 0 else "green"

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    for col, lbl, val, cls in [
        (k1, "💰 חיוב",   f'₪{kpis["total_billing"]:,.0f}', ""),
        (k2, "📤 עלות",   f'₪{kpis["total_cost"]:,.0f}',    ""),
        (k3, "📈 רווח",   f'₪{kpis["total_profit"]:,.0f}',  pcls),
        (k4, "% מרג'ין",  f'{margin:.1f}%',                  pcls),
        (k5, "👥 לקוחות", str(kpis["active_clients"]),        ""),
        (k6, "⚠️ חריגים", str(n_issues),                      icls),
    ]:
        with col:
            st.markdown(
                f'<div class="kpi-card"><div class="kpi-label">{lbl}</div>'
                f'<div class="kpi-value {cls}">{val}</div></div>',
                unsafe_allow_html=True,
            )

    # Comparison bar
    prev_kpis = _load_prev_kpis(sel_month) if sel_month else None
    if prev_kpis:
        def _pct(cur, prev):
            if not prev: return "—"
            p = (cur - prev) / abs(prev) * 100
            c = "#16a34a" if p > 0 else "#dc2626"
            return f'<span style="color:{c};font-weight:700">{"+" if p>0 else ""}{p:.1f}%</span>'
        pb = prev_kpis.get("total_billing", 0); pp = prev_kpis.get("total_profit", 0)
        st.markdown(
            f'<div class="comp-bar">📅 vs {_prev_month(sel_month)}: &nbsp;'
            f'חיוב ₪{pb:,.0f} → ₪{kpis["total_billing"]:,.0f} {_pct(kpis["total_billing"],pb)}'
            f' &nbsp;|&nbsp; רווח ₪{pp:,.0f} → ₪{kpis["total_profit"]:,.0f} {_pct(kpis["total_profit"],pp)}'
            f'</div>', unsafe_allow_html=True,
        )

    # Narrative
    _badge = f"⚠️ {n_issues} חריגים" if n_issues > 0 else "✅ ללא חריגות"
    _pw    = "הפסד" if kpis["total_profit"] < 0 else "רווח"
    st.markdown(
        f'<div class="glance">'
        f'<div class="glance-title">סיכום {sel_month}</div>'
        f'<div class="glance-text">חיוב <b>₪{kpis["total_billing"]:,.0f}</b>'
        f' ל-{kpis["active_clients"]} לקוחות, {kpis["active_employees"]} עובדים. '
        f'{_pw}: <b>₪{abs(kpis["total_profit"]):,.0f}</b> ({margin:.1f}%).</div>'
        f'<span class="glance-badge">{_badge}</span></div>',
        unsafe_allow_html=True,
    )

# ===========================================================================
# CHARTS
# ===========================================================================

st.markdown('<div class="sec-hdr">📊 גרפים</div>', unsafe_allow_html=True)

if _PLOTLY and not filtered.empty:
    ch1, ch2 = st.columns(2)
    with ch1:
        st.caption("חיוב לפי לקוח")
        fig = _bar_billing(filtered)
        if fig: st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with ch2:
        st.caption("רווח לפי לקוח")
        fig = _bar_profit(filtered)
        if fig: st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    ch3, ch4 = st.columns(2)
    with ch3:
        tdf = _load_trend()
        if not tdf.empty:
            st.caption("מגמה חודשית")
            fig = _line_trend(tdf)
            if fig: st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("מגמה חודשית")
            st.info("נתונים יצטברו לאחר הרצות נוספות")
    with ch4:
        st.caption("חלוקת הכנסות")
        fig = _pie_revenue(filtered)
        if fig: st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
elif not _PLOTLY:
    st.info("💡 התקן plotly לגרפים: `pip install plotly`")

# ===========================================================================
# TABLES
# ===========================================================================

st.markdown('<div class="sec-hdr">📋 טבלאות</div>', unsafe_allow_html=True)
tl, tr = st.columns([3, 2])

with tl:
    st.caption("פירוט חיוב — עובד × אתר")
    if not filtered.empty:
        cols_b = [c for c in ["employee_name","client","site","days","total_hours",
                               "billable_hours","completion_added","billing_amount",
                               "cost","profit","margin_pct"] if c in filtered.columns]
        disp_b = filtered[cols_b].rename(columns={
            "employee_name":"עובד","client":"לקוח","site":"אתר","days":"ימים",
            "total_hours":"שעות","billable_hours":"לחיוב","completion_added":"השלמה",
            "billing_amount":"חיוב ₪","cost":"עלות ₪","profit":"רווח ₪","margin_pct":"מרג'ין %",
        }).reset_index(drop=True)
        num_c = ["שעות","לחיוב","השלמה","חיוב ₪","עלות ₪","רווח ₪"]
        tot   = {c: disp_b[c].sum() if c in num_c else ("" if c != "עובד" else 'סה"כ') for c in disp_b.columns}
        tot["עובד"] = 'סה"כ'
        disp_b = pd.concat([disp_b, pd.DataFrame([tot])], ignore_index=True)
        def _fc(v, col):
            if v == "": return ""
            try: fv = float(v)
            except: return str(v)
            if col in ("חיוב ₪","עלות ₪","רווח ₪"): return f"₪{fv:,.0f}"
            if col == "מרג'ין %": return f"{fv:.1f}%"
            if col in ("שעות","לחיוב","השלמה"): return f"{fv:.2f}"
            if col == "ימים": return str(int(fv)) if fv == int(fv) else f"{fv:.1f}"
            return str(v)
        st.dataframe(
            disp_b.style.apply(_srow, axis=1).format({c: (lambda v, c=c: _fc(v,c)) for c in disp_b.columns}),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("אין נתונים לפי הסינון")

with tr:
    st.caption("רווחיות לפי לקוח")
    dash = dashboard_table(filtered)
    if not dash.empty:
        drop = [c for c in ["billing_change_pct","profit_change_pct"] if c in dash.columns]
        dd = dash.drop(columns=drop).rename(columns={
            "client":"לקוח","billing_amount":"חיוב ₪","cost":"עלות ₪",
            "profit":"רווח ₪","margin_pct":"% רווח","category":"קטגוריה",
        })
        st.dataframe(
            dd.style.format({"חיוב ₪":"₪{:,.0f}","עלות ₪":"₪{:,.0f}","רווח ₪":"₪{:,.0f}","% רווח":"{:.1f}%"})
            .applymap(_scat, subset=["קטגוריה"]),
            use_container_width=True, hide_index=True,
        )

# ===========================================================================
# ALERTS PANEL
# ===========================================================================

st.markdown('<div class="sec-hdr">⚠️ התראות</div>', unsafe_allow_html=True)

ins_df = insights_engine(filtered)
alerts: list[tuple[str, str, str]] = []

if not issues_df.empty and "issue_type" in issues_df.columns:
    r0 = issues_df[issues_df["issue_type"].str.contains("תעריף|חיוב אפס", na=False)]
    if not r0.empty: alerts.append(("crit","❌",f"תעריף 0 ₪ — {len(r0)} שורות"))
    na = issues_df[issues_df["issue_type"].str.contains("הסכם חסר", na=False)]
    if not na.empty: alerts.append(("crit","❌",f"הסכם חסר — {na['employee_name'].dropna().nunique()} עובדים"))

if not ins_df.empty:
    crit = ins_df[ins_df["profit"] < 0]
    if not crit.empty:
        alerts.append(("crit","❌",f"לקוחות בהפסד: {', '.join(crit['client'].tolist())}"))
    low = ins_df[(ins_df["profit"] >= 0) & (ins_df["margin_pct"].fillna(100) < 10)]
    if not low.empty:
        alerts.append(("warn","⚠️",f"מרג'ין נמוך (<10%): {', '.join(low['client'].tolist())}"))

if not filtered.empty and "completion_added" in filtered.columns and "total_hours" in filtered.columns:
    hc = filtered[
        (filtered["total_hours"] > 0) &
        (filtered["completion_added"] / filtered["total_hours"].replace(0,float("nan")) > 0.3)
    ]
    if not hc.empty:
        alerts.append(("warn","⚠️",f"השלמה גבוהה (>30%): {hc['employee_name'].dropna().nunique()} עובדים"))

if alerts:
    rows_html = "".join(
        f'<div class="alert-row"><span class="alert-icon">{ic}</span>'
        f'<span class="{"a-crit" if lv=="crit" else "a-warn"}">{txt}</span></div>'
        for lv, ic, txt in alerts
    )
    st.markdown(f'<div class="alert-box">{rows_html}</div>', unsafe_allow_html=True)
else:
    st.success("✅ לא נמצאו חריגות בנתונים שנבחרו")

if not ins_df.empty:
    with st.expander("💬 המלצות לפי לקוח"):
        rec = ins_df.rename(columns={
            "client":"לקוח","billing_amount":"חיוב ₪","profit":"רווח ₪",
            "margin_pct":"% רווח","category":"קטגוריה","insight":"המלצה",
        })[["לקוח","חיוב ₪","רווח ₪","% רווח","קטגוריה","המלצה"]]
        st.dataframe(
            rec.style.format({"חיוב ₪":"₪{:,.0f}","רווח ₪":"₪{:,.0f}","% רווח":"{:.1f}%"})
            .applymap(_scat, subset=["קטגוריה"]),
            use_container_width=True, hide_index=True,
        )

# ===========================================================================
# DRILL-DOWN — Client Details
# ===========================================================================

st.markdown('<div class="sec-hdr">🔍 פירוט לקוח</div>', unsafe_allow_html=True)

all_cl = sorted(filtered["client"].dropna().unique().tolist()) if not filtered.empty else []
sel_cl = st.selectbox("בחר לקוח:", ["— בחר —"] + all_cl, key="drill_cl")

if sel_cl and sel_cl != "— בחר —":
    cdf = filtered[filtered["client"] == sel_cl]
    cb = cdf["billing_amount"].sum(); cc = cdf["cost"].sum()
    cp = cdf["profit"].sum(); cm = cp / cb * 100 if cb > 0 else 0

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("חיוב",   f"₪{cb:,.0f}")
    d2.metric("עלות",   f"₪{cc:,.0f}")
    d3.metric("רווח",   f"₪{cp:,.0f}")
    d4.metric("מרג'ין", f"{cm:.1f}%")

    ec = [c for c in ["employee_name","site","days","total_hours","billable_hours",
                       "completion_added","billing_amount","cost","profit","margin_pct"] if c in cdf.columns]
    dd = cdf[ec].rename(columns={
        "employee_name":"עובד","site":"אתר","days":"ימים","total_hours":"שעות",
        "billable_hours":"לחיוב","completion_added":"השלמה",
        "billing_amount":"חיוב ₪","cost":"עלות ₪","profit":"רווח ₪","margin_pct":"מרג'ין %",
    }).reset_index(drop=True)
    st.dataframe(
        dd.style.apply(_srow, axis=1)
        .format({"חיוב ₪":"₪{:,.0f}","עלות ₪":"₪{:,.0f}","רווח ₪":"₪{:,.0f}",
                 "שעות":"{:.2f}","לחיוב":"{:.2f}","השלמה":"{:.2f}","מרג'ין %":"{:.1f}%"}),
        use_container_width=True, hide_index=True,
    )

# ===========================================================================
# DEBUG PANEL — Daily Row Breakdown
# ===========================================================================

st.markdown('<div class="sec-hdr">🔧 פירוט יומי — Debug</div>', unsafe_allow_html=True)

if not filtered.empty and not daily_df.empty:
    row_labels = list(dict.fromkeys(
        f"{r['employee_name']} — {r['site']}"
        for _, r in filtered.iterrows()
    ))
    sel_dbg = st.selectbox("בחר עובד / אתר:", ["— בחר —"] + row_labels, key="dbg_sel")

    if sel_dbg and sel_dbg != "— בחר —":
        parts = sel_dbg.split(" — ")
        en = parts[0].strip(); st_ = parts[1].strip() if len(parts) > 1 else ""
        dsub = (
            daily_df[
                (daily_df.get("employee_name", pd.Series(dtype=str)) == en) &
                (daily_df.get("site",          pd.Series(dtype=str)) == st_)
            ] if "employee_name" in daily_df.columns else pd.DataFrame()
        )

        if not dsub.empty:
            dsub = dsub.copy()
            if "hours_to_pay" in dsub.columns and "break_hours" in dsub.columns:
                dsub["worked_hours"] = dsub["hours_to_pay"] + dsub["break_hours"]

            # Agreement card
            if "agreement_used" in dsub.columns:
                ag = dsub["agreement_used"].iloc[0]
                mr = dsub["match_reason"].iloc[0] if "match_reason" in dsub.columns else ""
                rt = float(dsub["rate"].iloc[0]) if "rate" in dsub.columns else 0
                bt = dsub["billing_type"].iloc[0] if "billing_type" in dsub.columns else ""
                st.info(f"**הסכם:** {ag}  |  **התאמה:** {mr}  |  **תעריף:** ₪{rt:,.2f}  |  **סוג:** {bt}")

            col_map = {
                "date":"תאריך","worked_hours":"שעות עבודה","break_hours":"הפסקה",
                "hours_to_pay":"לתשלום","billable_hours_day":"לחיוב",
                "completion_day":"השלמה","rate":"תעריף","billing_day":"חיוב ₪",
                "ot_hours_day":"שעות נוספות","blocked":"חסום","block_reason":"סיבה",
            }
            avail = [c for c in col_map if c in dsub.columns]
            ex = dsub[avail].rename(columns=col_map).reset_index(drop=True)
            fmt_d = {"חיוב ₪":"₪{:,.2f}","תעריף":"₪{:,.2f}"}
            for c in ("שעות עבודה","הפסקה","לתשלום","לחיוב","השלמה","שעות נוספות"):
                if c in ex.columns: fmt_d[c] = "{:.2f}"
            st.dataframe(ex.style.format(fmt_d), use_container_width=True, hide_index=True)

            sh = float(dsub["hours_to_pay"].sum())  if "hours_to_pay"   in dsub.columns else 0
            sc = float(dsub["completion_day"].sum()) if "completion_day" in dsub.columns else 0
            sb = float(dsub["billing_day"].sum())    if "billing_day"    in dsub.columns else 0
            s1, s2, s3 = st.columns(3)
            s1.metric('סה"כ שעות', f"{sh:.2f}")
            s2.metric('סה"כ השלמה', f"{sc:.2f}")
            s3.metric('סה"כ חיוב', f"₪{sb:,.2f}")
else:
    st.caption("הרץ חישוב כדי לאפשר פירוט יומי")

# ===========================================================================
# ===========================================================================
# HISTORY & BI DASHBOARD
# ===========================================================================

st.divider()
st.markdown('<div class="sec-hdr">📅 היסטוריה ואנליטיקה</div>', unsafe_allow_html=True)

_hist_months = get_available_months()

if not _hist_months:
    st.info("📂 אין נתונים היסטוריים עדיין — נתונים יצטברו לאחר כל חישוב.")
else:
    # ── Tabs: Dashboard | Monthly Details | Clients ──────────────────────────
    _tab_dash, _tab_monthly, _tab_clients = st.tabs(
        ["📊 דשבורד", "📋 פירוט חודשי", "👥 לקוחות"]
    )

    @st.cache_data(show_spinner=False, ttl=120)
    def _cached_trend() -> pd.DataFrame:
        return load_trend_df()

    @st.cache_data(show_spinner=False, ttl=120)
    def _cached_clients_all() -> pd.DataFrame:
        frames = []
        for _m in get_available_months():
            _df = load_month_clients(_m)
            if not _df.empty:
                frames.append(_df)
        if frames:
            return pd.concat(frames, ignore_index=True)
        return pd.DataFrame()

    _trend_df    = _cached_trend()
    _clients_all = _cached_clients_all()

    # ── TAB 1: Dashboard ──────────────────────────────────────────────────────
    with _tab_dash:
        if not _trend_df.empty:
            # KPI bar — latest month
            _latest = _trend_df.iloc[-1]
            _prev   = _trend_df.iloc[-2] if len(_trend_df) >= 2 else None

            def _chg(cur, prev, col):
                if prev is None or prev.get(col, 0) == 0: return ""
                p = (cur.get(col, 0) - prev[col]) / abs(prev[col]) * 100
                c = "#16a34a" if p >= 0 else "#dc2626"
                return f'<span style="color:{c};font-size:13px;font-weight:700">{"+" if p>=0 else ""}{p:.1f}%</span>'

            hk1, hk2, hk3, hk4 = st.columns(4)
            for _col, _lbl, _fmt, _clr in [
                (hk1, "💰 חיוב (חודש אחרון)",  f'₪{_latest.get("total_billing",0):,.0f}',  ""),
                (hk2, "📈 רווח",               f'₪{_latest.get("total_profit",0):,.0f}',   "green" if _latest.get("total_profit",0)>=0 else "red"),
                (hk3, "% שינוי חיוב",          _chg(_latest, _prev, "total_billing"),        ""),
                (hk4, "% שינוי רווח",          _chg(_latest, _prev, "total_profit"),         ""),
            ]:
                with _col:
                    st.markdown(
                        f'<div class="kpi-card"><div class="kpi-label">{_lbl}</div>'
                        f'<div class="kpi-value {_clr}">{_fmt}</div></div>',
                        unsafe_allow_html=True,
                    )

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

            # Charts: trend + pie
            if _PLOTLY:
                _hc1, _hc2 = st.columns([2, 1])
                with _hc1:
                    st.caption("מגמת הכנסות ורווח")
                    _fig_t = _line_trend(_trend_df)
                    if _fig_t:
                        st.plotly_chart(_fig_t, use_container_width=True, config={"displayModeBar": False})

                with _hc2:
                    if not _clients_all.empty:
                        st.caption("חלוקת הכנסות (כל הזמנים)")
                        _cl_sum = _clients_all.groupby("client")["billing_amount"].sum().nlargest(5).reset_index()
                        _fig_pie = _pie_revenue(_cl_sum.rename(columns={"billing_amount":"billing_amount"}))
                        if _fig_pie:
                            st.plotly_chart(_fig_pie, use_container_width=True, config={"displayModeBar": False})

    # ── TAB 2: Monthly Details ────────────────────────────────────────────────
    with _tab_monthly:
        _sel_hist = st.selectbox(
            "בחר חודש:", _hist_months, index=len(_hist_months)-1, key="hist_sel"
        )
        _hkpis = load_month_kpis(_sel_hist)

        if _hkpis:
            _prev_m  = _prev_month(_sel_hist)
            _pkpis   = load_month_kpis(_prev_m) if _prev_m else None

            mk1, mk2, mk3, mk4 = st.columns(4)
            _mmargin = (_hkpis.get("total_profit",0) / _hkpis.get("total_billing",1) * 100) if _hkpis.get("total_billing") else 0
            for _col, _lbl, _val in [
                (mk1, "💰 חיוב",   f'₪{_hkpis.get("total_billing",0):,.0f}'),
                (mk2, "📤 עלות",   f'₪{_hkpis.get("total_cost",0):,.0f}'),
                (mk3, "📈 רווח",   f'₪{_hkpis.get("total_profit",0):,.0f}'),
                (mk4, "% מרג'ין",  f'{_mmargin:.1f}%'),
            ]:
                with _col:
                    st.markdown(
                        f'<div class="kpi-card"><div class="kpi-label">{_lbl}</div>'
                        f'<div class="kpi-value">{_val}</div></div>',
                        unsafe_allow_html=True,
                    )

            # Month comparison
            if _pkpis:
                st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
                _cb = _hkpis.get("total_billing",0); _pb = _pkpis.get("total_billing",0)
                _cp = _hkpis.get("total_profit",0);  _pp = _pkpis.get("total_profit",0)
                def _mpct(c, p):
                    if not p: return "—"
                    v = (c-p)/abs(p)*100
                    clr = "#16a34a" if v>=0 else "#dc2626"
                    return f'<span style="color:{clr};font-weight:700">{"+" if v>=0 else ""}{v:.1f}%</span>'
                st.markdown(
                    f'<div class="comp-bar">📅 vs {_prev_m}: '
                    f'חיוב ₪{_pb:,.0f} → ₪{_cb:,.0f} {_mpct(_cb,_pb)}'
                    f' &nbsp;|&nbsp; רווח ₪{_pp:,.0f} → ₪{_cp:,.0f} {_mpct(_cp,_pp)}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Client breakdown for selected month
            _hcl = load_month_clients(_sel_hist)
            if not _hcl.empty and _PLOTLY:
                _hcc1, _hcc2 = st.columns(2)
                with _hcc1:
                    st.caption(f"חיוב לפי לקוח — {_sel_hist}")
                    _f = _bar_billing(_hcl.rename(columns={"billing_amount":"billing_amount"}))
                    if _f: st.plotly_chart(_f, use_container_width=True, config={"displayModeBar": False})
                with _hcc2:
                    st.caption(f"רווח לפי לקוח — {_sel_hist}")
                    _f = _bar_profit(_hcl)
                    if _f: st.plotly_chart(_f, use_container_width=True, config={"displayModeBar": False})

    # ── TAB 3: Clients ────────────────────────────────────────────────────────
    with _tab_clients:
        if not _clients_all.empty:
            _all_cl = sorted(_clients_all["client"].dropna().unique().tolist())
            _sel_cl_h = st.selectbox("בחר לקוח:", _all_cl, key="hist_cl_sel")

            _cl_hist = _clients_all[_clients_all["client"] == _sel_cl_h].sort_values("month")

            if not _cl_hist.empty:
                # Summary metrics
                _tot_b = _cl_hist["billing_amount"].sum()
                _tot_p = _cl_hist["profit"].sum()
                _avg_m = _tot_p / _tot_b * 100 if _tot_b else 0
                cc1, cc2, cc3 = st.columns(3)
                cc1.metric("סה\"כ חיוב (כל הזמנים)", f"₪{_tot_b:,.0f}")
                cc2.metric("סה\"כ רווח",              f"₪{_tot_p:,.0f}")
                cc3.metric("מרג'ין ממוצע",            f"{_avg_m:.1f}%")

                # Trend for this client
                if _PLOTLY:
                    _fig_cl = go.Figure()
                    _fig_cl.add_trace(go.Scatter(x=_cl_hist["month"], y=_cl_hist["billing_amount"],
                        name="חיוב", line=dict(color="#1F497D", width=2.5)))
                    _fig_cl.add_trace(go.Scatter(x=_cl_hist["month"], y=_cl_hist["profit"],
                        name="רווח", line=dict(color="#16a34a", width=2)))
                    _fig_cl.update_layout(**{**_PL, "showlegend":True, "height":240},
                        legend=dict(orientation="h",y=1.08),
                        xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#f3f4f6"))
                    st.plotly_chart(_fig_cl, use_container_width=True, config={"displayModeBar": False})

            # Alerts — negative profit clients
            _alert_clients = (
                _clients_all.groupby("client")[["billing_amount","profit"]].sum()
                .query("profit < 0").sort_values("profit")
            )
            if not _alert_clients.empty:
                st.warning(f"⚠️ {len(_alert_clients)} לקוחות עם רווח שלילי לאורך כל הזמנים:")
                st.dataframe(
                    _alert_clients.style.format({"billing_amount":"₪{:,.0f}","profit":"₪{:,.0f}"}),
                    use_container_width=True,
                )

# ===========================================================================
# DOWNLOADS
# ===========================================================================

st.divider()
st.markdown('<div class="sec-hdr">⬇️ הורדות</div>', unsafe_allow_html=True)
d1, d2, d3, _ = st.columns([1,1,1,2])
_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_mn   = sel_month or "report"
with d1:
    if res.get("billing_bytes"):
        st.download_button("📊 דוח חיוב",    res["billing_bytes"],       f"billing_{_mn}.xlsx",       _mime)
with d2:
    if res.get("issues_bytes"):
        st.download_button("⚠️ דוח חריגים",  res["issues_bytes"],        f"issues_{_mn}.xlsx",        _mime)
with d3:
    if res.get("profitability_bytes"):
        st.download_button("📈 רווחיות",      res["profitability_bytes"], f"profitability_{_mn}.xlsx", _mime)

# ===========================================================================
# CLAUDE CHAT (optional)
# ===========================================================================

_api_key = os.getenv("ANTHROPIC_API_KEY")
if _api_key:
    st.divider()
    st.markdown('<div class="sec-hdr">💬 שאל את Claude</div>', unsafe_allow_html=True)
    if "chat_history" not in st.session_state: st.session_state.chat_history = []

    if st.session_state.chat_history:
        html_c = '<div style="max-height:320px;overflow-y:auto;padding:10px;border:1px solid #e5e7eb;border-radius:12px;background:#fff;margin-bottom:10px">'
        import html as _hl
        for m in st.session_state.chat_history:
            txt = _hl.escape(str(m["content"]))
            if m["role"] == "user":
                html_c += f'<div style="float:right;clear:both;background:#1F497D;color:#fff;padding:8px 14px;border-radius:16px 16px 4px 16px;margin:4px 0;max-width:75%;direction:rtl">{txt}</div>'
            else:
                html_c += f'<div style="float:left;clear:both;background:#f3f4f6;color:#111;padding:8px 14px;border-radius:16px 16px 16px 4px;margin:4px 0;max-width:75%;direction:rtl">🤖 {txt}</div>'
        html_c += '<div style="clear:both"></div></div>'
        st.markdown(html_c, unsafe_allow_html=True)

    with st.form("chat_form", clear_on_submit=True):
        ci, cb_ = st.columns([6,1])
        with ci: ui = st.text_input("שאלה", placeholder="איזה לקוח הכי רווחי?", label_visibility="collapsed")
        with cb_: sub = st.form_submit_button("שלח")

    if sub and ui.strip():
        import anthropic
        ctx = f"\n\nנתוני חיוב:\n{filtered.to_string(index=False)}"
        st.session_state.chat_history.append({"role":"user","content":ui})
        with st.spinner("Claude חושב..."):
            try:
                c = anthropic.Anthropic(api_key=_api_key)
                r = c.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1500,
                    system="אתה עוזר פיננסי. ענה בעברית, קצר וברור." + ctx,
                    messages=[{"role":m["role"],"content":m["content"]} for m in st.session_state.chat_history[-8:]])
                ans = r.content[0].text.strip()
            except Exception as e: ans = f"שגיאה: {e}"
        st.session_state.chat_history.append({"role":"assistant","content":ans})
        st.rerun()

    if st.session_state.get("chat_history"):
        if st.button("🗑️ נקה שיחה"): st.session_state.chat_history = []; st.rerun()
