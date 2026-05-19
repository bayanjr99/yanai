"""
מערכת ניתוח עלויות — ינאי פרסונל בע"מ
סקירה → גרפים → טבלאות → תובנות → סימולציה
"""
import logging
import os
import pandas as pd
import streamlit as st

# ── Persistent logging to disk ───────────────────────────────────────────────
# Every dashboard run appends to logs/dashboard.log, so failures can be
# diagnosed after the fact (previously: print() to stdout, lost on restart).
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(_log_dir, "dashboard.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dashboard")
logger.info("=== dashboard script started ===")

# ── Performance timing infrastructure ───────────────────────────────────
# Every "stage" of the render — load → filter → aggregate → render — is
# wrapped with `_stage()` which logs duration to BOTH the persistent log
# file AND to session_state so the Debug Panel can display "last run".
import time as _time_mod
import contextlib as _ctx

def _stage_init():
  """Reset the per-render timing list at the very top of the script."""
  import streamlit as _st
  _st.session_state["_perf"] = []
  _st.session_state["_perf_t0"] = _time_mod.perf_counter()

@_ctx.contextmanager
def _stage(name):
  """Context manager: time a block, log it, store in session_state.

  Usage:
    with _stage("load_data"):
      raw = load_data()
  """
  import streamlit as _st
  _t0 = _time_mod.perf_counter()
  try:
    yield
  finally:
    _dt = _time_mod.perf_counter() - _t0
    # console / log file
    logger.info("[PERF] %-30s %7.3f sec", name, _dt)
    print(f"[PERF] {name:<30s} {_dt:>7.3f} sec", flush=True)
    # session memory (cleared at next render)
    if "_perf" not in _st.session_state:
      _st.session_state["_perf"] = []
    _st.session_state["_perf"].append((name, _dt))

# Initialize timing for this render. MUST be called before the first _stage().
_stage_init()

try:
  import plotly.graph_objects as go
  HAS_PLOTLY = True
except ImportError:
  HAS_PLOTLY = False

# ═══ PAGE CONFIG ═════════════════════════════════════════════════════════════
# Favicon selection order:
#   1. static/icon.png  — simplified single-figure icon (preferred, cleaner at 16×16)
#   2. static/logo.png  — full logo (works but busier at favicon size)
#   3. 👷 emoji         — fallback when no image is present
_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "static", "icon.png")
_LOGO_PATH_FAV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "static", "logo.png")
_page_icon = "👷"
for _p in (_ICON_PATH, _LOGO_PATH_FAV):
  if os.path.exists(_p):
    try:
      from PIL import Image as _PILImage
      _page_icon = _PILImage.open(_p)
      break
    except Exception:
      continue

st.set_page_config(
  page_title="ינאי פרסונל בע\"מ — מערכת ניתוח עלויות",
  page_icon=_page_icon,
  layout="wide",
  initial_sidebar_state="collapsed",
)

# ═══ LOGIN GATE ══════════════════════════════════════════════════════════════
from app_auth import require_login, logout as _logout, current_user as _current_user
require_login()

# ═══ FIRST-PAINT LOADING VEIL ════════════════════════════════════════════════
# CRITICAL: Streamlit strips <script> tags from st.markdown — so the earlier
# JS-based "hide veil when content arrives" approach was broken (timer never
# advanced, veil never hid). This rewrite is PURE CSS:
#   1. The veil is in the DOM with opacity:1
#   2. CSS `:has()` watches for `.top-bar` (or other meaningful selectors)
#      anywhere in the document — when one of them mounts, opacity→0
#   3. As an unconditional safety belt, a CSS animation fades the veil out
#      after 4 seconds regardless of content state — user never gets stuck
#   4. After 4s without content, an "is something wrong?" pointer-events box
#      becomes clickable to reload the page (the <a href> works without JS)
st.markdown(
    """
    <style>
    @keyframes _yp_boot_spin { to { transform: rotate(360deg); } }
    @keyframes _yp_boot_pulse { 0%,100%{opacity:.4;} 50%{opacity:1;} }
    /* Unconditional fade-out after 4 seconds. Even if :has() doesn't fire
       (no content arrived), the veil disappears so the user is never stuck. */
    @keyframes _yp_boot_force_fade {
       0%, 80% { opacity:1; pointer-events:auto; }
       100%    { opacity:0; pointer-events:none; }
    }
    #yp-boot-veil {
        position:fixed;inset:0;z-index:99998;
        background:linear-gradient(180deg,#F1F5F9 0%,#E2E8F0 100%);
        display:flex;flex-direction:column;align-items:center;justify-content:center;
        font-family:'Inter','Segoe UI',Arial,sans-serif;direction:rtl;
        transition:opacity .35s ease;
        /* 5s total — 4s visible, 1s fade. After this the veil is invisible. */
        animation:_yp_boot_force_fade 5s ease forwards;
    }
    /* :has() — when ANY meaningful dashboard element mounts, hide the veil
       immediately. Supported in Chrome 105+ / Edge / Safari 15.4+ / Firefox
       121+. Pre-:has browsers fall back to the 5s timed fade above. */
    html:has(.top-bar) #yp-boot-veil,
    html:has(.filter-marker) #yp-boot-veil,
    html:has(.kpi-group-head) #yp-boot-veil,
    html:has(.empty-state) #yp-boot-veil,
    html:has(.exec-summary) #yp-boot-veil,
    body:has(.top-bar) #yp-boot-veil,
    body:has(.filter-marker) #yp-boot-veil,
    body:has(.kpi-group-head) #yp-boot-veil,
    body:has(.empty-state) #yp-boot-veil,
    body:has(.exec-summary) #yp-boot-veil {
        opacity:0 !important;
        pointer-events:none !important;
        animation:none !important;
    }
    #yp-boot-veil .boot-spinner {
        width:54px;height:54px;border:5px solid #BBF7D0;
        border-top-color:#16A34A;border-radius:50%;
        animation:_yp_boot_spin .8s linear infinite;margin-bottom:22px;
    }
    #yp-boot-veil .boot-title {
        font-size:16px;font-weight:800;color:#0E5A2E;letter-spacing:.2px;
        margin-bottom:4px;
    }
    #yp-boot-veil .boot-sub {
        font-size:12.5px;color:#64748B;
        animation:_yp_boot_pulse 1.4s ease-in-out infinite;
    }
    /* "Reload" link — appears after 3.5s via CSS-only animation; a plain
       <a href="javascript:..."> wouldn't work because Streamlit strips
       inline JS too, but clicking the page header / using F5 always works.
       This text just gives the user a hint. */
    @keyframes _yp_boot_show_slow {
       0%, 70% { opacity:0; transform:translateY(8px); }
       100%    { opacity:1; transform:translateY(0); }
    }
    #yp-boot-veil .boot-slow {
        margin-top:18px;padding:10px 16px;border-radius:10px;
        background:#FEF3C7;color:#78350F;border:1px solid #FDE68A;
        font-size:12px;max-width:360px;text-align:center;line-height:1.6;
        opacity:0;animation:_yp_boot_show_slow 5s ease forwards;
    }
    </style>
    <div id="yp-boot-veil">
      <div class="boot-spinner"></div>
      <div class="boot-title">טוען נתונים ומחשב מדדים…</div>
      <div class="boot-sub">ינאי פרסונל בע"מ · מערכת ניתוח עלויות</div>
      <div class="boot-slow">
        אם הטעינה ממשיכה זמן רב, רענן את הדף עם F5 או Ctrl+Shift+R.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ═══ CSS ═════════════════════════════════════════════════════════════════════
st.markdown('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">', unsafe_allow_html=True)
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
html,body,.stApp{direction:rtl;font-family:'Inter','Segoe UI',Arial,sans-serif;
  background:#F0F4F8;overflow-x:hidden!important;}
/* Hide Streamlit branding chrome — not relevant for the customer view.
   These selectors target the GitHub "Made with Streamlit" footer, the
   hamburger menu (⋮) in the upper-right, the running-man animation, and
   the deploy / share button. App still works; just no Streamlit branding. */
#MainMenu, footer, header[data-testid="stHeader"]{visibility:hidden!important;
  height:0!important;}
[data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], .stDeployButton,
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"]{
  display:none!important;}
/* footer link sometimes injected at bottom */
footer{display:none!important;}
*,*::before,*::after{box-sizing:border-box;}
.block-container{padding:3.8rem 1.5rem 4rem!important;max-width:100%!important;overflow-x:hidden!important;}
section[data-testid="stSidebar"],[data-testid="collapsedControl"]{display:none!important;}
.top-bar{background:linear-gradient(135deg,#052E16 0%,#0E5A2E 55%,#16A34A 100%);
  color:#fff;height:58px;width:100%;display:flex;align-items:center;
  justify-content:space-between;padding:0 1.5rem;margin-bottom:18px;
  box-shadow:0 2px 12px rgba(5,46,22,.35);}
.top-bar-brand{display:flex;align-items:center;gap:10px;}
.top-bar-logo{height:38px;width:38px;border-radius:50%;background:#fff;
  padding:2px;box-shadow:0 1px 4px rgba(0,0,0,.2),0 0 0 1.5px rgba(22,163,74,0.5);
  object-fit:contain;filter:saturate(1.35) contrast(1.05);}
.top-bar-title{font-size:15px;font-weight:800;letter-spacing:-.2px;}
.top-bar-title .ltd{font-weight:600;opacity:.85;}
.top-bar-title .sep{opacity:.5;margin:0 6px;}
.top-bar-title .sys{font-weight:600;opacity:.85;font-size:13px;}
.top-bar-meta{font-size:11px;opacity:.7;text-align:left;}
.top-bar-meta .tel{display:block;font-size:10px;opacity:.6;
  letter-spacing:.5px;direction:ltr;margin-top:2px;}
.dot{width:7px;height:7px;border-radius:50%;background:#22C55E;
  box-shadow:0 0 8px #22C55E;display:inline-block;margin-left:6px;}
/* Single-row KPI strip — column count is set dynamically right before render.
   Cards adapt width via minmax(0,1fr) so 5–11 cards all fit on one line. */
.kpi-strip{display:grid;gap:10px;margin-bottom:18px;align-items:stretch;}
.kpi-cell{background:#fff;border:0.5px solid #E8EAED;border-radius:12px;
  padding:12px 14px;position:relative;direction:rtl;
  display:flex;flex-direction:column;min-width:0;overflow:hidden;
  box-shadow:0 1px 3px rgba(0,0,0,.04),0 0 0 0.5px rgba(0,0,0,.015);
  transition:box-shadow .15s,transform .15s;}
.kpi-cell:hover{box-shadow:0 4px 14px rgba(22,163,74,.12);transform:translateY(-1px);}
.kpi-lbl{font-size:11px;font-weight:600;color:#64748B;margin-bottom:6px;
  display:flex;align-items:center;gap:5px;line-height:1.2;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.kpi-lbl i.ti{font-size:14px;color:#16A34A;}
.kpi-val{font-size:22px;font-weight:600;color:#0F172A;line-height:1.15;
  letter-spacing:-.4px;direction:ltr;text-align:right;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
/* delta line — vs-prev / target badges, sits above the chips */
.kpi-delta{font-size:10.5px;font-weight:600;margin-top:auto;padding-top:6px;
  color:#64748B;min-height:0;line-height:1.5;direction:rtl;text-align:right;
  word-break:break-word;}
.kpi-delta:not(:empty){padding-top:8px;border-top:1px dashed #E5E7EB;}
/* === Chip rows — each sub-data-point becomes its own small box ============ */
.kpi-chip-row{display:flex;flex-wrap:wrap;gap:4px;
  margin-top:5px;direction:rtl;justify-content:flex-start;}
/* Add a soft separator above the FIRST chip row only when the delta div
   above it is empty — otherwise we'd get two stacked dashed lines */
.kpi-delta:empty + .kpi-chip-row{margin-top:9px;padding-top:8px;
  border-top:1px dashed #E5E7EB;}
.kpi-chip{display:inline-flex;align-items:center;
  background:#F8FAFC;color:var(--ink-mid);
  padding:3px 7px;border-radius:6px;
  font-size:10px;font-weight:600;line-height:1.35;
  border:1px solid var(--line);white-space:nowrap;
  font-variant-numeric:tabular-nums;}
.kpi-chip:hover{background:var(--brand-green-soft);
  border-color:var(--status-good-border);color:var(--brand-green-dark);}
/* === Brand palette — single source of truth for status colors ============== */
:root {
  --brand-green:        #16A34A;
  --brand-green-dark:   #0E5A2E;
  --brand-green-soft:   #F0FDF4;
  --status-good:        #059669;
  --status-good-soft:   #F0FDF4;
  --status-good-border: #BBF7D0;
  --status-warn:        #D97706;
  --status-warn-soft:   #FFFBEB;
  --status-warn-border: #FDE68A;
  --status-bad:         #DC2626;
  --status-bad-soft:    #FEF2F2;
  --status-bad-border:  #FECACA;
  --status-info:        #2563EB;
  --status-info-soft:   #EFF6FF;
  --status-info-border: #BFDBFE;
  --ink-strong:         #0F172A;
  --ink-mid:            #475569;
  --ink-soft:           #64748B;
  --ink-faint:          #94A3B8;
  --line:               #E2E8F0;
  --line-faint:         #F1F5F9;
  --bg-card:            #FFFFFF;
  --bg-page:            #F8FAFC;
}
.up-bad{color:var(--status-bad);}.dn-good{color:var(--status-good);}.neutral{color:var(--ink-faint);}
.focus{padding:12px 18px;border-radius:10px;font-size:14px;font-weight:700;
  margin-bottom:16px;border:1px solid;border-right-width:4px;
  box-shadow:0 1px 4px rgba(0,0,0,.06);}
.focus.red{background:var(--status-bad-soft);border-color:var(--status-bad-border);border-right-color:var(--status-bad);color:#7F1D1D;}
.focus.amber{background:var(--status-warn-soft);border-color:var(--status-warn-border);border-right-color:var(--status-warn);color:#78350F;}
.focus.green{background:var(--status-good-soft);border-color:var(--status-good-border);border-right-color:var(--status-good);color:#14532D;}
.focus.blue{background:var(--status-info-soft);border-color:var(--status-info-border);border-right-color:var(--status-info);color:#1E3A8A;}
.blk{background:var(--bg-card);border:1px solid var(--line);border-radius:12px;
  padding:18px 20px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,.05);}
.blk-lbl{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;color:var(--ink-faint);margin-bottom:10px;}
.blk-body{font-size:13px;color:var(--ink-strong);line-height:1.75;}
.blk.green{background:var(--brand-green-soft);border-color:var(--status-good-border);}
.blk.dark{background:var(--ink-strong);border-color:var(--ink-strong);box-shadow:0 3px 14px rgba(15,23,42,.35);}
.blk.dark .blk-lbl{color:var(--ink-mid);font-size:10px;}
.blk.dark .blk-body{color:#F1F5F9;font-weight:800;font-size:16px;line-height:1.4;}
.ins{border-radius:10px;padding:13px 16px;margin-bottom:9px;
  display:flex;gap:12px;align-items:flex-start;border:1px solid;box-shadow:0 1px 3px rgba(0,0,0,.04);}
.ins.red{background:var(--status-bad-soft);border-color:var(--status-bad-border);border-right:3px solid var(--status-bad);}
.ins.amber{background:var(--status-warn-soft);border-color:var(--status-warn-border);border-right:3px solid var(--status-warn);}
.ins.green{background:var(--status-good-soft);border-color:var(--status-good-border);border-right:3px solid var(--status-good);}
.ins.blue{background:var(--status-info-soft);border-color:var(--status-info-border);border-right:3px solid var(--status-info);}
.ins-icon{font-size:18px;flex-shrink:0;margin-top:1px;}
.ins-title{font-size:12px;font-weight:700;color:var(--ink-strong);margin-bottom:3px;}
.ins-body{font-size:11px;color:#4B5563;line-height:1.55;}
/* === Section heading — stronger visual anchor than the old .sec ============ */
.sec{font-size:13px;font-weight:800;color:var(--ink-strong);
  letter-spacing:.2px;padding:18px 0 10px;border-bottom:2px solid var(--brand-green);
  margin:8px 0 16px;display:flex;align-items:center;gap:8px;}
.sec::before{content:"";display:inline-block;width:3px;height:18px;
  background:var(--brand-green);border-radius:2px;}
.sec .sec-meta{margin-right:auto;font-size:11px;font-weight:600;color:var(--ink-soft);
  letter-spacing:0;text-transform:none;}
/* === KPI group label — small caps between groups of KPI cards ============== */
.kpi-group-label{font-size:10px;font-weight:800;color:var(--ink-faint);
  text-transform:uppercase;letter-spacing:1.2px;margin:10px 0 6px;
  padding-right:6px;border-right:3px solid var(--brand-green);}
.sim-card{background:#fff;border:1px solid #E2E8F0;border-radius:12px;
  padding:18px 20px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.05);}
.sim-lbl{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.7px;
  color:#94A3B8;margin-bottom:4px;}
.sim-title{font-size:14px;font-weight:700;color:#0F172A;margin-bottom:3px;}
.sim-sub{font-size:11px;color:#64748B;margin-bottom:10px;}
/* === Plotly chart containers — uniform card-style frame ================== */
[data-testid="stPlotlyChart"]{
  background:var(--bg-card);border:1px solid var(--line);border-radius:10px;
  padding:8px 6px 4px;margin-bottom:6px;
  box-shadow:0 1px 3px rgba(0,0,0,.03);
  overflow:hidden;
}
[data-testid="stPlotlyChart"] .js-plotly-plot{max-width:100%;}
[data-testid="stPlotlyChart"] .modebar{display:none!important;}  /* hide plotly toolbar */
/* Caption right after a chart — softer than st.caption default */
[data-testid="stPlotlyChart"] + .stCaption,
[data-testid="stPlotlyChart"] + div .stCaption{
  font-size:11px!important;color:var(--ink-soft)!important;
  font-style:italic;margin-top:-2px!important;margin-bottom:10px!important;
  padding:0 4px;line-height:1.5;
}
[data-testid="stDataFrame"]{border-radius:10px!important;overflow:hidden;border:1px solid #E2E8F0!important;}
[data-testid="stDataFrame"] th{font-size:10px!important;font-weight:700!important;
  background:#F8FAFC!important;color:#475569!important;padding:10px 14px!important;
  text-transform:uppercase;letter-spacing:.4px;border-bottom:2px solid #E2E8F0!important;}
[data-testid="stDataFrame"] td{font-size:12px!important;padding:9px 14px!important;
  border-bottom:1px solid #F1F5F9!important;color:#0F172A!important;}
[data-testid="stDataFrame"] tr:nth-child(even) td{background:#FAFBFD!important;}
[data-testid="stDataFrame"] tr:hover td{background:#EFF6FF!important;cursor:default;}
[data-testid="stDataFrame"] td{padding:11px 14px!important;}
button[kind="primary"]{transition:all .2s;}
/* hover glow uses brand green (was Streamlit blue) */
button[kind="primary"]:hover{transform:translateY(-1px);
  box-shadow:0 4px 12px rgba(22,163,74,.35);}
/* === Expander headers — softer, more polished ============================== */
[data-testid="stExpander"] details summary {
  font-weight:700!important;color:var(--ink-strong)!important;
  background:#FAFBFD!important;border-radius:10px!important;
  padding:10px 14px!important;
}
[data-testid="stExpander"] details[open] summary {
  background:var(--brand-green-soft)!important;
  border-bottom:1px solid var(--status-good-border)!important;
  border-radius:10px 10px 0 0!important;
}
[data-testid="stExpander"] {
  border:1px solid var(--line)!important;border-radius:10px!important;
  margin-bottom:14px!important;box-shadow:0 1px 3px rgba(0,0,0,.03);
}
/* === Metric components — subtle but consistent ============================ */
[data-testid="stMetric"] {
  background:var(--bg-card);border:1px solid var(--line);
  border-radius:10px;padding:10px 14px;
}
[data-testid="stMetricLabel"] {font-size:11px!important;color:var(--ink-soft)!important;
  font-weight:600!important;letter-spacing:.2px!important;}
[data-testid="stMetricValue"] {font-size:20px!important;font-weight:700!important;
  color:var(--ink-strong)!important;letter-spacing:-.3px!important;}
[data-testid="stMetricDelta"] {font-size:11px!important;font-weight:600!important;}
[data-testid="stFormSubmitButton"]>button{
  background:linear-gradient(135deg,#059669 0%,#047857 100%)!important;
  border-color:#059669!important;color:#fff!important;font-weight:700!important;}
[data-testid="stFormSubmitButton"]>button:hover{
  background:linear-gradient(135deg,#047857 0%,#065f46 100%)!important;
  box-shadow:0 4px 14px rgba(5,150,105,.4)!important;transform:translateY(-1px);}
[data-testid="stSlider"]{direction:ltr!important;}
[data-testid="stSlider"] label{direction:rtl!important;text-align:right!important;display:block!important;}
[data-testid="stSlider"] [data-baseweb="slider"]{direction:ltr!important;}

/* ═══ POLISH LAYER ════════════════════════════════════════════════════════
   Additive refinements: do NOT remove rules above — these layer on top.
   ════════════════════════════════════════════════════════════════════════ */

/* --- Top bar: sticky on scroll + tighten alignment ------------------ */
.top-bar{border-radius:0 0 14px 14px;position:sticky;top:0;z-index:100;
  /* slightly stronger shadow when scrolled so it visually detaches */
  box-shadow:0 4px 14px rgba(5,46,22,.32);}
/* Reduce top padding because the sticky header now owns the top of the page */
.block-container{padding-top:0!important;}
/* Tabs strip: also sticky, sits just below the header                    */
[data-baseweb="tab-list"]{
  position:sticky!important;
  /* 58px = top-bar height (see .top-bar height in original CSS) */
  top:58px!important;
  z-index:90!important;
}
.top-bar-actions{display:flex;align-items:center;gap:8px;}
.sys-pill{display:inline-flex;align-items:center;gap:6px;
  background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.25);
  color:#fff;font-size:11px;font-weight:700;padding:4px 9px;border-radius:99px;
  backdrop-filter:blur(4px);}
.sys-pill .dot{width:6px;height:6px;background:#22C55E;
  box-shadow:0 0 6px #22C55E;margin:0;}
.sys-pill.warn{background:rgba(217,119,6,.25);border-color:rgba(253,230,138,.5);}
.sys-pill.warn .dot{background:#FBBF24;box-shadow:0 0 6px #FBBF24;}
.user-pill{display:inline-flex;align-items:center;gap:6px;
  background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);
  color:#F0FDF4;font-size:11px;font-weight:600;padding:4px 9px;border-radius:99px;}
.user-pill i.ti{font-size:13px;color:#BBF7D0;}

/* --- Filter bar: wrap the filter columns in a card -------------------
   Uses CSS :has() to detect any container that has a .filter-marker
   descendant. Looser selector (no >) survives Streamlit DOM updates.
   Modern browsers (Chrome 105+ / Firefox 121+ / Safari 15.4+) all
   support :has(). Pre-:has browsers gracefully fall back to no card.  */
[data-testid="stVerticalBlock"]:has(.filter-marker){
  background:#FFFFFF;
  border:1px solid var(--line);
  border-radius:14px;
  padding:14px 16px 6px;
  margin-bottom:16px;
  box-shadow:0 1px 4px rgba(15,23,42,.04);
  position:relative;
}
[data-testid="stVerticalBlock"]:has(.filter-marker)::before{
  content:"";display:block;position:absolute;top:-1px;right:-1px;left:-1px;
  height:3px;border-radius:14px 14px 0 0;
  background:linear-gradient(90deg,#0E5A2E 0%,#16A34A 50%,#22C55E 100%);
}
.filter-marker{font-size:11px;font-weight:800;color:#475569;
  text-transform:uppercase;letter-spacing:1.2px;margin-bottom:8px;
  display:flex;align-items:center;gap:6px;}
.filter-marker::before{content:"\f1c1";font-family:"tabler-icons";
  color:#16A34A;font-size:16px;font-weight:normal;letter-spacing:0;}
[data-testid="stVerticalBlock"]:has(.filter-marker) [data-testid="stWidgetLabel"],
[data-testid="stVerticalBlock"]:has(.filter-marker) label{
  font-size:11px!important;font-weight:600!important;color:var(--ink-soft)!important;}

/* --- Visual cleanup: less double borders, more breathing room ------- */
/* Plotly chart container — the surrounding card already provides a
   border; remove the inner plot border to avoid double-border look. */
[data-testid="stPlotlyChart"] .js-plotly-plot .main-svg{
  background:transparent!important;
}
/* st.dataframe inside an expander — the expander has a border already,
   drop the dataframe's outer border to prevent double-border look. */
[data-testid="stExpander"] [data-testid="stDataFrame"]{
  border:none!important;border-radius:8px!important;}
/* Larger gap between top-level sections so the eye can rest. */
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"]{
  gap:14px;
}
/* Section dividers — replace `st.markdown("---")` ugly hr with subtle ones */
hr{border:none!important;border-top:1px dashed var(--line)!important;
  margin:18px 0!important;opacity:.85;}
/* Subheaders / markdown headers */
.stMarkdown h5, .stMarkdown h4{
  font-size:14px!important;font-weight:800!important;color:#0F172A!important;
  margin:14px 0 6px!important;letter-spacing:-.1px;
}
/* Reduce metric component over-padding */
[data-testid="stMetric"]{padding:12px 14px;}

/* --- Billing triage grid (top of "חיוב ותקן" tab) ------------------- */
.billing-triage{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;
  margin-bottom:16px;}
.billing-triage-card{border-radius:12px;padding:14px 16px;
  box-shadow:0 1px 4px rgba(15,23,42,.04);display:flex;flex-direction:column;
  gap:5px;min-width:0;}
.billing-triage-head{display:flex;align-items:center;gap:8px;
  font-size:11.5px;font-weight:700;color:#475569;}
.billing-triage-label{flex:1;}
.billing-triage-val{font-size:24px;font-weight:800;line-height:1.1;
  font-variant-numeric:tabular-nums;letter-spacing:-.4px;}
.billing-triage-status{font-size:10px;font-weight:800;letter-spacing:.5px;
  text-transform:uppercase;}
.billing-triage-hint{font-size:11px;color:#64748B;line-height:1.4;margin-top:4px;}
@media (max-width:900px){.billing-triage{grid-template-columns:repeat(2,1fr);}}

/* --- CPH alert: structured card replacing the single-line focus ----- */
.cph-alert{display:flex;gap:14px;padding:16px 18px;border-radius:14px;
  margin:0 0 16px;box-shadow:0 2px 8px rgba(15,23,42,.06);}
.cph-alert-icon{width:42px;height:42px;border-radius:50%;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-size:22px;}
.cph-alert-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:10px;}
.cph-alert-title{font-size:15px;font-weight:800;letter-spacing:-.1px;}
.cph-alert-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;}
.cph-alert-cell{background:rgba(255,255,255,.7);border-radius:8px;padding:8px 10px;
  border:1px solid rgba(0,0,0,.04);}
.cph-alert-cell-lbl{font-size:10px;font-weight:700;color:#64748B;
  text-transform:uppercase;letter-spacing:.8px;margin-bottom:3px;}
.cph-alert-cell-val{font-size:17px;font-weight:800;color:#0F172A;
  letter-spacing:-.3px;font-variant-numeric:tabular-nums;}
.cph-alert-action{font-size:12px;color:#475569;line-height:1.55;
  background:rgba(255,255,255,.5);padding:8px 11px;border-radius:8px;}
.cph-alert-action b{color:#0F172A;}
@media (max-width:900px){.cph-alert-grid{grid-template-columns:repeat(2,1fr);}}

/* --- Executive summary card (Overview tab top) ---------------------- */
.exec-summary{background:#FFFFFF;border:1px solid var(--line);border-radius:16px;
  padding:0;margin:0 0 18px;box-shadow:0 4px 14px rgba(15,23,42,.06);
  overflow:hidden;}
.exec-summary-head{padding:14px 20px;display:flex;align-items:center;
  justify-content:space-between;flex-wrap:wrap;gap:12px;
  background:linear-gradient(90deg,#F8FAFC 0%,#FFFFFF 100%);
  border-bottom:1px solid var(--line);}
.exec-summary-title{font-size:15px;font-weight:800;color:#0F172A;
  letter-spacing:-.1px;display:flex;align-items:center;gap:9px;}
.exec-summary-title i.ti{font-size:22px;color:var(--brand-green);}
.exec-summary-status{display:inline-flex;align-items:center;gap:6px;
  padding:6px 14px;border-radius:99px;font-size:12px;font-weight:800;
  letter-spacing:.4px;white-space:nowrap;}
.exec-summary-status.good{background:var(--status-good-soft);
  color:var(--status-good);border:1px solid var(--status-good-border);}
.exec-summary-status.warn{background:var(--status-warn-soft);
  color:var(--status-warn);border:1px solid var(--status-warn-border);}
.exec-summary-status.bad {background:var(--status-bad-soft);
  color:var(--status-bad); border:1px solid var(--status-bad-border);}
.exec-summary-status::before{content:"";width:8px;height:8px;
  border-radius:50%;background:currentColor;}
.exec-summary-body{display:grid;grid-template-columns:repeat(3,1fr);
  padding:18px 20px;gap:18px;}
.exec-sum-q{display:flex;flex-direction:column;gap:5px;min-width:0;}
.exec-sum-q-label{font-size:10.5px;font-weight:800;color:#94A3B8;
  text-transform:uppercase;letter-spacing:1.2px;}
.exec-sum-q-value{font-size:20px;font-weight:800;color:#0F172A;
  letter-spacing:-.4px;display:flex;align-items:center;gap:8px;
  line-height:1.25;word-break:break-word;}
.exec-sum-bullet{display:inline-block;width:10px;height:10px;border-radius:50%;
  flex-shrink:0;}
.exec-sum-q-sub{font-size:11.5px;color:#64748B;line-height:1.4;}
.exec-sum-action{font-size:13px;font-weight:600;color:#334155;
  line-height:1.5;padding:8px 11px;background:#F8FAFC;border-radius:8px;
  border-right:3px solid var(--brand-green);}
@media (max-width:900px){.exec-summary-body{grid-template-columns:1fr;}}

/* --- Empty state: shown when filters return zero rows --------------- */
.empty-state{background:#FFFFFF;border:1px solid var(--line);
  border-radius:14px;padding:36px 28px;margin:16px 0;text-align:center;
  box-shadow:0 1px 4px rgba(15,23,42,.04);max-width:560px;
  margin-left:auto;margin-right:auto;}
.empty-state-icon{font-size:48px;color:#94A3B8;line-height:1;margin-bottom:10px;}
.empty-state-icon i.ti{font-size:48px;}
.empty-state-title{font-size:16px;font-weight:800;color:#0F172A;margin-bottom:8px;}
.empty-state-body{font-size:12.5px;color:#475569;line-height:1.6;}
.empty-state-body ul{list-style:disc;padding-right:20px!important;
  list-style-position:inside;}
.empty-state-body li{margin-bottom:3px;}
.empty-state-action{font-size:13px;color:var(--brand-green-dark);
  background:var(--brand-green-soft);border:1px solid var(--status-good-border);
  border-radius:10px;padding:10px 14px;margin-top:14px;font-weight:600;
  display:inline-block;}

/* --- KPI groups: labelled strips (פיננסי / תפעולי / סיכון) ----------- */
.kpi-group{margin-bottom:14px;}
.kpi-group-head{font-size:11px;font-weight:800;color:#475569;
  text-transform:uppercase;letter-spacing:1.2px;margin-bottom:6px;
  display:flex;align-items:center;gap:8px;padding:0 2px;}
.kpi-group-head i.ti{font-size:16px;color:var(--brand-green);}
.kpi-group-head .kpi-group-count{margin-right:auto;font-size:10px;
  font-weight:600;color:#94A3B8;letter-spacing:.3px;text-transform:none;}

/* --- KPI cells: stronger accent left-edge + better hover ------------- */
.kpi-cell{border-top:3px solid transparent;
  /* slightly more breathing room; primary value should dominate */
  padding:14px 14px 10px;}
.kpi-cell[data-accent="green"] {border-top-color:#16A34A;}
.kpi-cell[data-accent="red"]   {border-top-color:#DC2626;}
.kpi-cell[data-accent="amber"] {border-top-color:#D97706;}
.kpi-cell[data-accent="blue"]  {border-top-color:#2563EB;}
.kpi-cell[data-accent="slate"] {border-top-color:#64748B;}
.kpi-cell:hover{box-shadow:0 6px 18px rgba(15,23,42,.10);
  transform:translateY(-2px);}
/* Primary value: make it the unambiguous hero of the card */
.kpi-cell .kpi-val{font-size:24px;font-weight:700;letter-spacing:-.5px;
  margin-top:2px;margin-bottom:2px;}
/* Chips: tighter, lower visual weight; first row stays, rest hide
   until hover so cards aren't crowded. Keeps the data discoverable
   (CEO can hover to drill in) without making the default state busy. */
.kpi-cell .kpi-chip{font-size:9.5px;padding:2px 6px;border-radius:5px;
  color:#64748B;background:#F8FAFC;}
.kpi-cell .kpi-chip-row{margin-top:4px;gap:3px;}
.kpi-cell .kpi-chip-row + .kpi-chip-row{
  max-height:0;opacity:0;overflow:hidden;margin-top:0;padding:0;
  transition:max-height .25s ease,opacity .2s ease,margin-top .25s,padding .25s;
}
.kpi-cell:hover .kpi-chip-row + .kpi-chip-row{
  max-height:60px;opacity:1;margin-top:4px;
}
/* "more" cue: tiny dotted underline indicating "hover to see more" */
.kpi-cell:has(.kpi-chip-row + .kpi-chip-row) .kpi-chip-row:first-of-type::after{
  content:"…";color:#94A3B8;font-weight:700;font-size:11px;
  margin-right:auto;padding:0 4px;align-self:center;}

/* --- Tabs: turn Streamlit's default tabs into a clean pill nav ------ */
[data-baseweb="tab-list"]{
  background:#FFFFFF!important;border:1px solid var(--line)!important;
  border-radius:14px!important;padding:6px!important;gap:4px!important;
  box-shadow:0 1px 4px rgba(15,23,42,.04);margin-bottom:14px!important;
  overflow:visible!important;
}
[data-baseweb="tab-list"] button[data-baseweb="tab"]{
  background:transparent!important;border-radius:10px!important;
  font-weight:700!important;font-size:13px!important;
  color:var(--ink-mid)!important;
  padding:9px 16px!important;height:auto!important;
  transition:background .15s,color .15s,box-shadow .15s!important;
  border:none!important;
}
[data-baseweb="tab-list"] button[data-baseweb="tab"]:hover{
  background:var(--brand-green-soft)!important;color:var(--brand-green-dark)!important;}
[data-baseweb="tab-list"] button[data-baseweb="tab"][aria-selected="true"]{
  background:linear-gradient(135deg,#0E5A2E 0%,#16A34A 100%)!important;
  color:#FFFFFF!important;
  box-shadow:0 3px 10px rgba(22,163,74,.32)!important;
}
[data-baseweb="tab-list"] [data-baseweb="tab-highlight"],
[data-baseweb="tab-list"] [data-baseweb="tab-border"]{display:none!important;}

/* nested tabs (sub-tabs inside Tables) — slightly smaller pills */
[data-baseweb="tab-panel"] [data-baseweb="tab-list"]{
  padding:5px!important;background:#FAFBFD!important;
}
[data-baseweb="tab-panel"] [data-baseweb="tab-list"] button[data-baseweb="tab"]{
  font-size:12px!important;padding:7px 14px!important;
}

/* --- Tables: sticky header + frozen first column + better hover ----- */
[data-testid="stDataFrame"] [data-testid="StyledDataFrameDataCell"]{
  font-variant-numeric:tabular-nums;
}
/* Cap dataframe height so the sticky header has something to stick to;
   200px header + tabs leave a comfortable 60vh of body in viewport. */
[data-testid="stDataFrame"]{max-height:60vh!important;}
[data-testid="stDataFrame"] thead th{
  position:sticky!important;top:0!important;z-index:5!important;
  background:#F1F5F9!important;
  box-shadow:inset 0 -2px 0 #CBD5E1;
}
/* Freeze the FIRST visible column (usually # / id) so context stays
   while scrolling horizontally. In RTL, "first" is the rightmost
   visible — Streamlit's grid uses logical columns so :first-child
   targets the right side in our direction:rtl layout. */
[data-testid="stDataFrame"] tbody td:first-child,
[data-testid="stDataFrame"] thead th:first-child {
  position:sticky!important;right:0!important;z-index:4!important;
  background:#F8FAFC!important;
  box-shadow:-2px 0 0 #E2E8F0;
}
[data-testid="stDataFrame"] thead th:first-child {z-index:6!important;}
/* zebra is already set above — add right-edge brand line for row hover */
[data-testid="stDataFrame"] tr:hover td:last-child{
  box-shadow:inset -3px 0 0 var(--brand-green);
}
/* CSV download buttons under tables: clearer affordance */
.stDownloadButton button{
  border:1px solid var(--brand-green)!important;
  color:var(--brand-green-dark)!important;background:#fff!important;
  font-weight:700!important;
}
.stDownloadButton button:hover{
  background:var(--brand-green-soft)!important;
  box-shadow:0 3px 10px rgba(22,163,74,.18)!important;
}

/* --- Section card: a clean wrapper for in-tab content blocks ------ */
.section-card{background:#FFFFFF;border:1px solid var(--line);
  border-radius:14px;padding:18px 20px;margin-bottom:14px;
  box-shadow:0 1px 4px rgba(15,23,42,.04);}
.section-card-title{font-size:13px;font-weight:800;color:var(--ink-strong);
  margin-bottom:12px;display:flex;align-items:center;gap:8px;
  padding-bottom:10px;border-bottom:1px solid var(--line-faint);}
.section-card-title i.ti{font-size:18px;color:var(--brand-green);}
.section-card-title .badge{margin-right:auto;font-size:11px;font-weight:700;
  background:var(--brand-green-soft);color:var(--brand-green-dark);
  border:1px solid var(--status-good-border);padding:2px 9px;border-radius:99px;}

/* --- Executive insight card: problem · impact · who · action · priority */
.exec-insight{background:#fff;border:1px solid var(--line);border-radius:12px;
  padding:14px 16px;margin-bottom:10px;display:grid;
  grid-template-columns:auto 1fr auto;gap:10px 16px;align-items:start;
  box-shadow:0 1px 3px rgba(15,23,42,.04);
  border-right:4px solid var(--ink-faint);transition:box-shadow .15s,transform .15s;}
.exec-insight:hover{box-shadow:0 6px 16px rgba(15,23,42,.10);transform:translateY(-1px);}
.exec-insight[data-priority="high"]  {border-right-color:var(--status-bad);}
.exec-insight[data-priority="med"]   {border-right-color:var(--status-warn);}
.exec-insight[data-priority="low"]   {border-right-color:var(--status-good);}
.exec-insight-icon{font-size:22px;line-height:1;padding-top:2px;}
.exec-insight-body{display:flex;flex-direction:column;gap:6px;min-width:0;}
.exec-insight-problem{font-size:13.5px;font-weight:800;color:var(--ink-strong);
  line-height:1.4;}
.exec-insight-meta{display:flex;flex-wrap:wrap;gap:6px 10px;font-size:11px;
  color:var(--ink-mid);align-items:center;}
.exec-insight-tag{display:inline-flex;align-items:center;gap:4px;
  padding:3px 9px;border-radius:99px;background:#F8FAFC;
  border:1px solid var(--line);font-weight:600;font-size:11px;color:var(--ink-mid);}
.exec-insight-tag.impact{color:var(--status-bad-soft);background:var(--status-bad);
  border-color:var(--status-bad);color:#fff;font-variant-numeric:tabular-nums;}
.exec-insight-tag.impact.positive{background:var(--status-good);
  border-color:var(--status-good);color:#fff;}
.exec-insight-tag.who{background:var(--brand-green-soft);
  border-color:var(--status-good-border);color:var(--brand-green-dark);}
.exec-insight-action{font-size:12px;color:var(--ink-mid);
  background:var(--bg-page);border-radius:8px;padding:7px 10px;
  border-right:2px solid var(--brand-green);line-height:1.5;}
.exec-insight-action b{color:var(--brand-green-dark);}
.exec-priority-pill{display:inline-flex;align-items:center;gap:5px;
  padding:5px 11px;border-radius:99px;font-size:10.5px;font-weight:800;
  text-transform:uppercase;letter-spacing:.8px;white-space:nowrap;
  align-self:start;margin-top:2px;}
.exec-priority-pill[data-priority="high"]{background:var(--status-bad-soft);
  color:var(--status-bad);border:1px solid var(--status-bad-border);}
.exec-priority-pill[data-priority="med"]{background:var(--status-warn-soft);
  color:var(--status-warn);border:1px solid var(--status-warn-border);}
.exec-priority-pill[data-priority="low"]{background:var(--status-good-soft);
  color:var(--status-good);border:1px solid var(--status-good-border);}

/* --- Executive report look (used in conclusions tab) -------------- */
.exec-banner{background:linear-gradient(135deg,#0E5A2E 0%,#16A34A 50%,#22C55E 100%);
  color:#fff;border-radius:14px;padding:18px 22px;margin-bottom:16px;
  box-shadow:0 8px 24px rgba(22,163,74,.22);display:flex;
  align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;}
.exec-banner-title{font-size:15px;font-weight:800;letter-spacing:.2px;
  display:flex;align-items:center;gap:9px;}
.exec-banner-title i.ti{font-size:22px;opacity:.95;}
.exec-banner-sub{font-size:11px;opacity:.85;font-weight:500;
  margin-top:3px;letter-spacing:.3px;}
.exec-banner-stats{display:flex;gap:18px;align-items:center;}
.exec-stat{text-align:right;}
.exec-stat-val{font-size:18px;font-weight:800;line-height:1.1;
  font-variant-numeric:tabular-nums;}
.exec-stat-lbl{font-size:10px;opacity:.8;letter-spacing:.6px;
  text-transform:uppercase;margin-top:2px;}

/* --- Focus alert: a bit more present -------------------------- */
.focus{font-size:13.5px;padding:13px 18px;}
.focus::before{content:"";display:inline-block;width:4px;height:18px;
  vertical-align:-3px;margin-left:8px;border-radius:2px;}
.focus.green::before{background:var(--status-good);}
.focus.amber::before{background:var(--status-warn);}
.focus.red::before  {background:var(--status-bad);}
.focus.blue::before {background:var(--status-info);}

/* --- Buttons: subtle base hover for non-primary buttons -------- */
.stButton > button{transition:all .15s;border-radius:10px!important;
  font-weight:700!important;}
.stButton > button:hover{box-shadow:0 3px 10px rgba(22,163,74,.18);
  border-color:var(--brand-green)!important;}

/* --- Caption: tighter, gentler ------------------------- */
.stCaption{font-size:11.5px!important;color:var(--ink-soft)!important;}

/* --- Multiselect / select: brand green focus ring ------------- */
[data-baseweb="select"]:focus-within > div{
  border-color:var(--brand-green)!important;
  box-shadow:0 0 0 2px rgba(22,163,74,.18)!important;
}

</style>
""", unsafe_allow_html=True)

_ALERT_HEB = {
  "missing_client":  "חסר לקוח",
  "missing_site":    "חסר אתר",
  "missing_country": "מדינה לא ידועה",
  "excel_only":      "עובד רק באקסל",
  "duplicate":       "שורה כפולה",
  "zero_hours":      "שעות אפס עם עלות",
  "abnormal_hours":  "שעות חריגות",
  "no_standard":     "אין תקן/הסכם",
  "high_shortage":   "חסר רב מהתקן",
}

BLUE,GREEN,RED,AMBER,SLATE,NAVY,PURP = (
  "#2563EB","#059669","#DC2626","#D97706","#64748B","#0F172A","#7C3AED")
OT_LEVELS = ["h125","h150","h175","h200"]
OT_LABELS = {"h125":"125%","h150":"150%","h175":"175%","h200":"200%"}
OT_COLORS = {"h125":"#60a5fa","h150":AMBER,"h175":"#f97316","h200":RED}
# Default plotly layout — applied via {**_PL, ...} in every chart so all
# charts get consistent margins, fonts, and a sane height baseline.
# Per-chart code can still override "height" when a specific size is needed.
_PL = dict(
    height=290,                            # baseline height — overridable
    # Generous baseline margins so Hebrew tick labels never clip. Plotly
    # automargin (set in _polish) will further expand when needed.
    margin=dict(l=20,r=20,t=50,b=40),
    paper_bgcolor="white", plot_bgcolor="white",
    font=dict(family="Inter,Segoe UI,Arial",size=12, color="#0F172A"),
    hoverlabel=dict(bgcolor="#0F172A", font_color="#FFFFFF",
                     bordercolor="#0F172A", font_size=12,
                     font_family="Inter,Segoe UI"),
)
# Helper applied AFTER update_layout — adds polished title/axis/legend styling
# WITHOUT clashing with per-chart kwargs (those are passed directly to
# update_layout, so they'd otherwise raise "multiple values for kwarg").
#
# CRITICAL: title_font / title_x / title_y must NOT be set when the chart has
# no title text — Plotly renders an "undefined" placeholder in that case.
def _polish(fig, title=None):
    """Apply consistent typography/grid/title styling to a plotly figure.

    Universal post-processing:
      • title styling (only when a title exists — no "undefined" leakage)
      • axis tick fonts + zeroline off
      • automargin on both axes (so long Hebrew tick labels never clip)
      • smart x-axis tick angle when categorical labels are likely long
      • soft hover styling (matches the dark hoverlabel set in _PL)
    """
    try:
      # Always-safe layout tweaks (no title required).
      _u = dict(
        xaxis_tickfont=dict(size=11, color="#64748B"),
        yaxis_tickfont=dict(size=11, color="#64748B"),
        xaxis_title_font=dict(size=11, color="#475569"),
        yaxis_title_font=dict(size=11, color="#475569"),
        xaxis_zeroline=False, yaxis_zeroline=False,
        legend_font=dict(size=11, color="#475569"),
        # automargin keeps the plot area away from long axis labels,
        # so Hebrew client / month strings never get clipped.
        xaxis_automargin=True, yaxis_automargin=True,
      )
      # Smart tick angle: if x has more than ~6 categories and they're
      # strings, slant the labels. Heuristic only — doesn't touch numeric.
      try:
        _xd = []
        for _tr in (fig.data or []):
          _xs = getattr(_tr, "x", None)
          if _xs is not None and len(_xd) < 12:
            _xd.extend(list(_xs)[:12])
        _is_str = any(isinstance(v, str) for v in _xd)
        if _is_str and len(_xd) > 6:
          _u["xaxis_tickangle"] = -30
      except Exception:
        pass
      # Only apply title-specific styling if there IS a title — either
      # passed in here or already set on the figure layout. Otherwise
      # Plotly may render an "undefined" placeholder.
      _existing_title = None
      try:
        _existing_title = (fig.layout.title.text
                            if fig.layout and fig.layout.title else None)
      except Exception:
        pass
      _final_title = title if title is not None else _existing_title
      if _final_title:
        _u["title_text"]    = _final_title
        _u["title_font"]    = dict(size=13, color="#0F172A",
                                    family="Inter,Segoe UI")
        _u["title_x"]       = 0.02
        _u["title_xanchor"] = "left"
        _u["title_y"]       = 0.97
        _u["title_yanchor"] = "top"
      fig.update_layout(**_u)
    except Exception:
      pass
    return fig
CPH_TARGET = 50.0

# ═══ נתונים ══════════════════════════════════════════════════════════════════
_PARQUET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
            "output","cache","processed_data.parquet")
_MASTER  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
            "output","master","master_full.parquet")

_INCOME = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "output","cache","income.parquet")

@st.cache_data(show_spinner=False, ttl=300)
def load_data():
  if not os.path.exists(_PARQUET): return pd.DataFrame()
  df = pd.read_parquet(_PARQUET)
  for c in ("employer_cost","cost_per_hour","total_hours","overtime_ratio",
       "h100","h125","h150","h175","h200","billable_hours","shortage_revenue",
       "cost","employee_cost","allocated_cost"):
    if c in df.columns:
      df[c] = pd.to_numeric(df[c],errors="coerce").fillna(0.0)
  # cost = allocated_cost (correctly split per client×site); fallback for old parquet
  if "cost" not in df.columns:
    df["cost"] = df["allocated_cost"] if "allocated_cost" in df.columns else df["employer_cost"]
  # Merge billing_amount/profit/margin_pct from master_full if available
  if os.path.exists(_MASTER):
    try:
      _mdf = pd.read_parquet(_MASTER)
      _mcols = [c for c in ("month","employee_id","site","billing_amount","profit","margin_pct")
                if c in _mdf.columns]
      _on    = [c for c in ("month","employee_id","site") if c in _mdf.columns and c in df.columns]
      if "billing_amount" in _mcols and _on:
        df = df.merge(_mdf[_mcols], on=_on, how="left", suffixes=("","_m"))
        for _extra in ("billing_amount_m","profit_m","margin_pct_m"):
          if _extra in df.columns: df = df.drop(columns=[_extra])
    except Exception:
      pass
  # Merge income.parquet (real billing from accounting system)
  if os.path.exists(_INCOME):
    try:
      _inc = pd.read_parquet(_INCOME)
      # אם billing_amount כבר קיים (מ-master_full או מ-preprocessor), אל תדרוס
      if "billing_amount" not in df.columns:
        _inc_cols = [c for c in ("month","client","billing_amount","profit",
                                  "margin_pct","billed_hours") if c in _inc.columns]
        _inc_on   = [c for c in ("month","client") if c in _inc.columns and c in df.columns]
        if "billing_amount" in _inc_cols and _inc_on:
          df = df.merge(_inc[_inc_cols], on=_inc_on, how="left")
          print(f"💰 Income merged: {int((_inc['billing_amount']>0).sum())} rows with billing data")
    except Exception as e:
      print(f"⚠️ Could not load income.parquet: {e}")
  return df

def _mkey(m):
  # מחזיר מספר שלם YYYYMM — בטוח למיון ב-sorted() וב-sort_values()
  try: return int(m[3:]) * 100 + int(m[:2])
  except: return 0

with _stage("load_data (parquet)"):
  _raw_with_internal = load_data()
if _raw_with_internal.empty:
  st.error("❌ אין נתונים. הרץ את ה-preprocessor תחילה."); st.stop()

# ─── Internal-entity filter ────────────────────────────────────────────────
# Rows where client OR site names ינאי פרסונל are the company's own admin /
# overhead employees, NOT a billable client. They are EXCLUDED from every
# total in this dashboard (cost, revenue, profit, hours, employee count).
# The standalone overhead summary is shown separately in the Conclusions tab.
from core.internal_entities import internal_mask
_internal_summary = _raw_with_internal[internal_mask(_raw_with_internal)].copy()
raw = _raw_with_internal[~internal_mask(_raw_with_internal)].copy()
logger.info(
    "Filtered %d internal-entity rows (ינאי פרסונל) — cost ₪%.0f. "
    "Remaining for dashboard: %d rows.",
    len(_internal_summary),
    float(_internal_summary["cost"].sum()) if not _internal_summary.empty and "cost" in _internal_summary.columns else 0,
    len(raw),
)

# ── Parquet freshness guard ───────────────────────────────────────────────────
if "allocated_cost" not in raw.columns:
  st.warning(
    "⚠️ **Parquet ישן** — עמודת `allocated_cost` חסרה. "
    "עלויות מוצגות ב-**×1.5+ ניפוח** (employer_cost במקום allocated_cost). "
    "הרץ `python -c \"from core.preprocessor import build_and_save; build_and_save()\"` לתוצאות מדויקות.",
    icon="⚠️",
  )

# ── Internal-entity exclusion notice (only when there's data to show) ──────
# Moved to a collapsed "מידע טכני" expander at the BOTTOM of the page — the
# blue info banner used to sit between Header and KPIs, which the user
# rightly flagged as noise for the CEO view. It's still discoverable when
# needed (full breakdown is in Conclusions tab → section 8).
_internal_summary_for_footer = None
if not _internal_summary.empty:
    _internal_summary_for_footer = {
      "cost":   float(_internal_summary["cost"].sum()),
      "hours":  float(_internal_summary["total_hours"].sum()),
      "emps":   int(_internal_summary["employee_id"].nunique()),
      "months": int(_internal_summary["month"].nunique()),
    }

# ── Standards file presence check ─────────────────────────────────────────────
_std_xlsx_present = any(
    f.endswith(".xlsx") and "תקן" in f
    for f in os.listdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    if os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f))
) if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")) else False

if not _std_xlsx_present:
    st.error(
        "❌ **`data/תקן.xlsx` חסר!** כל החיובים יוצגו כ-`unknown` ויכולת הבקרה "
        "מושבתת. השב את הקובץ למקום ולחץ ▻ ושר את הדף.",
        icon="❌",
    )
    logger.error("STANDARDS FILE MISSING — data/תקן.xlsx not found")

# ── Data freshness check — warn if source files are newer than the cache ─────
# Skipped entirely on Streamlit Cloud: every git checkout rewrites file mtimes
# with timing that depends on git's checkout order, so the "source newer than
# cache" comparison produces false positives. The check is useful only when
# the user is editing local files between runs of run_build.py.
import glob as _glob
import time as _time
_here = os.path.dirname(os.path.abspath(__file__))
_IS_STREAMLIT_CLOUD = (
    "/mount/src/" in os.path.abspath(__file__)
    or os.environ.get("HOSTNAME", "").startswith("streamlit-")
)
_cache_pq = os.path.join(_here, "output", "cache", "processed_data.parquet")
if not _IS_STREAMLIT_CLOUD and os.path.exists(_cache_pq):
  _cache_mt = os.path.getmtime(_cache_pq)
  _sources = (
    _glob.glob(os.path.join(_here, "data", "*", "*.xlsx")) +
    _glob.glob(os.path.join(_here, "data", "*", "*.xls"))  +
    _glob.glob(os.path.join(_here, "data", "*", "*.pdf"))  +
    _glob.glob(os.path.join(_here, "data", "*.xlsx"))
  )
  # Slack widened from 60s to 1 hour — git checkouts can spread mtimes across
  # several seconds and we don't want spurious warnings.
  _newer = [p for p in _sources if os.path.getmtime(p) > _cache_mt + 3600]
  if _newer:
    _rel = [os.path.relpath(p, _here) for p in _newer[:5]]
    _more = f" +{len(_newer)-5} נוספים" if len(_newer) > 5 else ""
    _hrs_old = int((_time.time() - _cache_mt) / 3600)
    st.warning(
      f"📅 **נתונים עדכניים יותר זמינים** — {len(_newer)} קבצי מקור עודכנו אחרי הבנייה האחרונה "
      f"({_hrs_old} שעות). דוגמאות: {' · '.join(_rel)}{_more}. "
      "הרץ `python run_build.py` כדי לרענן.",
      icon="📅",
    )

# ── Source quality guard: warn when non-Andromeda data affects cost reports ────
if "source" in raw.columns or "source_file_type" in raw.columns:
  _src_col = "source_file_type" if "source_file_type" in raw.columns else "source"
  _bad_src = raw[~raw[_src_col].isin(["AndromedaExcel"])].copy()
  # Only flag months that HAVE an Andromeda file but used fallback
  # (months without Andromeda files — 01-2025, 02-2025 — are expected Legacy)
  _andro_months = raw[raw[_src_col] == "AndromedaExcel"]["month"].unique()
  _pdf_months = (
      raw[raw[_src_col].isin(["PDF", "Fallback"])]
      ["month"].unique()
  )
  _bad_andro = [m for m in _pdf_months if m in _andro_months or
                # also flag if Andromeda file exists on disk for this month
                any(True for _ in [1])]
  # Simpler: flag any month that mixes Andromeda + non-Andromeda rows
  _mixed_months = []
  for _m in raw["month"].unique():
    _m_src = raw[raw["month"]==_m][_src_col].unique()
    if len(_m_src) > 1 or (len(_m_src) == 1 and _m_src[0] not in ("AndromedaExcel","Legacy","Excel")):
      _mixed_months.append((_m, list(_m_src)))
  # Flag months where ALL rows are non-Andromeda but Andromeda should exist
  _pdf_only_months = [
      m for m in raw["month"].unique()
      if set(raw[raw["month"]==m][_src_col].unique()).issubset({"PDF","Fallback"})
      and m not in ("01-2025","02-2025")  # known legacy months
  ]
  if _pdf_only_months:
    st.error(
      f"🚨 **עלות לקוח/אתר עלולה להיות שגויה** — "
      f"{len(_pdf_only_months)} חודשים נטענו מ-PDF fallback במקום Andromeda Excel: "
      f"**{', '.join(_pdf_only_months)}**\n\n"
      f"ב-PDF אין פיצול מלא לפי לקוח × אתר — עובדים מרובי-אתרים מביאים עלות שגויה לאתר בודד. "
      f"סגור קבצי Excel פתוחים והרץ: "
      f"`python -c \"from core.preprocessor import build_and_save; build_and_save()\"`"
    )

all_months    = sorted(raw["month"].dropna().unique().tolist(), key=_mkey)
all_clients   = sorted(raw["client"].dropna().unique().tolist()) if "client" in raw.columns else []
all_countries = sorted(raw["country"].dropna().unique().tolist()) if "country" in raw.columns else []

# ═══ עזרים ═══════════════════════════════════════════════════════════════════
def _filt(df,rng,clients):
  if rng and "month" in df.columns:
    lo,hi = _mkey(rng[0]),_mkey(rng[1])
    df = df[df["month"].map(_mkey).apply(lambda k: lo<=k<=hi)]
  if clients and "client" in df.columns:
    df = df[df["client"].isin(clients)]
  return df

def _ot_prem_s(df):
  r = df["cost_per_hour"].replace(0,float("nan"))
  return (df.get("h125",pd.Series(0,index=df.index)).fillna(0)*0.25 +
      df.get("h150",pd.Series(0,index=df.index)).fillna(0)*0.50 +
      df.get("h175",pd.Series(0,index=df.index)).fillna(0)*0.75 +
      df.get("h200",pd.Series(0,index=df.index)).fillna(0)*1.00
      ).mul(r.fillna(0))

def _prev_m(m):
  try: mm,yy=int(m[:2]),int(m[3:]); return f"12-{yy-1}" if mm==1 else f"{mm-1:02d}-{yy}"
  except: return ""

def _dh(cur,prev,inv=False):
  if not prev: return '<span class="neutral">—</span>'
  p=(cur-prev)/abs(prev)*100
  good = (p>0) if inv else (p<0)   # inv=True: increase is good (e.g. hours)
  cls="dn-good" if good else "up-bad"; arrow="▼" if p<0 else "▲"
  return f'<span class="kpi-delta {cls}">{arrow}{abs(p):.1f}%</span>'

def _ot_st(hi,md):
  if hi>5: return "crit","🔴 קריטי"
  if md>15: return "warn","🟠 אזהרה"
  return "ok","✅ תקין"

def _sec(t): st.markdown(f'<div class="sec">{t}</div>',unsafe_allow_html=True)

def _calc_ot_savings(df, hcol, mult, pct_reduction):
  """
  מחשב (gross_cost_save, revenue_loss, net_save) להפחתת OT לפי billing_kind.
  משתמש בתעריפי ההסכמים האמיתיים מ-תקן.xlsx (hourly_rate, ot_hourly_rate).
  """
  if hcol not in df.columns or pct_reduction == 0:
    return (0.0, 0.0, 0.0)
  _HAS_KIND = "billing_kind" in df.columns
  gross = 0.0; rev_loss = 0.0
  _kinds = df["billing_kind"].dropna().unique() if _HAS_KIND else ["unknown"]
  for kind in _kinds:
    sub = df[df["billing_kind"] == kind] if _HAS_KIND else df
    h_lvl = float(sub[hcol].sum())
    if h_lvl == 0: continue
    h_reduced = h_lvl * pct_reduction
    th = float(sub["total_hours"].sum()) or 1
    gross += h_reduced * (float(sub["cost"].sum()) / th) * mult

    if kind == "hourly_no_completion":
      avg_hr = float(sub["hourly_rate"].replace(0, float("nan")).mean()) if "hourly_rate" in sub.columns else 0
      rev_loss += h_reduced * avg_hr * mult

    elif kind == "hourly_with_completion":
      # הפסד הכנסה רק על שעות שמעל יעד התקן
      if "std_hours_month" in sub.columns:
        ot_h = float((sub["total_hours"] - sub["std_hours_month"].fillna(0)).clip(lower=0).sum())
      else:
        ot_h = 0.0
      eff = min(h_reduced, ot_h)
      avg_hr = float(sub["hourly_rate"].replace(0, float("nan")).mean()) if "hourly_rate" in sub.columns else 0
      rev_loss += eff * avg_hr * mult

    elif kind == "daily_no_ot":
      pass  # שעות נוספות לא חויבות — אין הפסד

    elif kind in ("daily_with_ot", "mixed"):
      # הפסד רק על שעות ה-OT הנוספות מעבר למינימום יומי
      if "daily_min_hours" in sub.columns and "work_days" in sub.columns:
        ot_b = float((sub["total_hours"] - sub["work_days"] * sub["daily_min_hours"].fillna(10)).clip(lower=0).sum())
      else:
        ot_b = h_lvl
      eff = min(h_reduced, ot_b)
      avg_ot = float(sub["ot_hourly_rate"].replace(0, float("nan")).mean()) if "ot_hourly_rate" in sub.columns else 0
      # mixed: 0.7 = ממוצע שמרני בין daily_with_ot (1.0) ל-daily_min_only (0.5) —
      # הסכמים מעורבים מחייבים OT חלקי, הנחה שמרנית עד ייחוס עמודה נפרדת בתקן.
      factor = 0.7 if kind == "mixed" else 1.0
      rev_loss += eff * avg_ot * mult * factor

    elif kind in ("daily_or_monthly_min", "daily_min_only"):
      eff_r = float(sub["expected_billing"].sum()) / th if "expected_billing" in sub.columns else 0
      rev_loss += h_reduced * eff_r * 0.5

    elif kind == "unknown":
      # לקוחות ללא תקן — ראה טאב "התראות" לרשימת "no_standard".
      # הפסד מוערך מנתוני חיוב בפועל אם זמינים; אחרת 0 (שמרני).
      if "billing_amount" in sub.columns and "billed_hours" in sub.columns:
        _dh = sub.drop_duplicates(["month", "client"])
        _bh = float(_dh["billed_hours"].sum()) or 1
        _ba = float(_dh["billing_amount"].sum())
        rev_loss += h_reduced * (_ba / _bh)
    # missing_data, no_pricing → rev_loss = 0

  return (gross, rev_loss, gross - rev_loss)

def _blk(lbl,body,cls=""):
  st.markdown(f'<div class="blk {cls}"><div class="blk-lbl">{lbl}</div>'
        f'<div class="blk-body">{body}</div></div>',unsafe_allow_html=True)

def _ins(color,icon,title,body):
  st.markdown(f'<div class="ins {color}"><div class="ins-icon">{icon}</div>'
        f'<div><div class="ins-title">{title}</div>'
        f'<div class="ins-body">{body}</div></div></div>',unsafe_allow_html=True)


# ── Global total-row helpers (also re-defined locally inside Tables tab —
# the local copies override these only inside that block; everywhere else
# in the script can call the globals.) ────────────────────────────────────
def _with_total_row(d, *, label='סה"כ', label_col=None,
                    empty_cols=(), recalc=None):
  """Append a 'סה"כ' row. Numeric cols are summed by default.

  - label:      text written in the label column ("סה\"כ" by default)
  - label_col:  column for the label (default: first non-numeric col)
  - empty_cols: columns to leave blank in the totals row
  - recalc:     dict {col: callable(df)→value} for weighted averages
                (margin %, cost/hour) where a sum is incorrect.

  Numeric "empty" cells use pd.NA (not "") so pandas formatters like
  '{:,.0f}' don't crash. The styler must pair this with na_rep="".
  """
  if d is None or getattr(d, "empty", True):
    return d
  if label_col is None:
    for c in d.columns:
      if not pd.api.types.is_numeric_dtype(d[c]):
        label_col = c
        break
  row = {}
  for c in d.columns:
    _is_num = pd.api.types.is_numeric_dtype(d[c])
    if c == label_col:
      row[c] = label
    elif c in empty_cols:
      row[c] = pd.NA if _is_num else ""
    elif recalc and c in recalc:
      try: row[c] = recalc[c](d)
      except Exception: row[c] = pd.NA if _is_num else ""
    elif _is_num:
      try: row[c] = float(d[c].sum())
      except Exception: row[c] = pd.NA
    else:
      row[c] = ""
  return pd.concat([d, pd.DataFrame([row])], ignore_index=True)


def _hl_total_row(styler):
  """Bold + gray background + top-border for the LAST row of the styler.

  Pair with _with_total_row(df) above — call _with_total_row first, then
  pass df.style.format(...) through this to highlight the appended row.
  """
  if styler.data is None or len(styler.data) == 0:
    return styler
  _last = len(styler.data) - 1
  def _hl(s):
    if s.name == _last:
      return ["background:#E2E8F0;font-weight:800;color:#0F172A;"
              "border-top:2px solid #64748B"] * len(s)
    return [""] * len(s)
  return styler.apply(_hl, axis=1)

# ── Column name → Hebrew display name ──────────────────────────────────────
# Centralised so EVERY table uses the same display labels. Add new mappings
# here when introducing a new technical column to a user-facing table.
_HEB_COL = {
  "client":           "לקוח",
  "client_name":      "לקוח",
  "site":             "אתר",
  "site_name":        "אתר",
  "country":          "מדינה",
  "month":            "חודש",
  "employee_id":      "מס' עובד",
  "employee_name":    "שם עובד",
  "work_days":        "ימים",
  "total_hours":      "שעות",
  "billable_hours":   "שעות לחיוב",
  "cost":             "עלות",
  "billing_amount":   "הכנסה",
  "profit":           "רווח",
  "margin_pct":       "מרג'ין %",
  "cost_per_hour":    "עלות לשעה",
  "avg_cph":          "עלות לשעה",
  "gross_salary":     "שכר ברוטו",
  "bituach":          "ביטוח לאומי",
  "pension":          "פנסיה",
  "adjusted_levy":    "אגרות",
  "fee_ratio":        "% אגרות",
  "vacation_fund":    "חופשה/מחלה",
  "severance":        "פיצויים",
  "medical_insurance":"ביטוח רפואי",
  "employment_levy":  "היטל תעסוקה",
  "incentive_fund":   "קרן עידוד",
  "savings_deposit":  "פיקדון",
  "other":            "אחר",
  "cost_driver":      "מצב",
  "overtime_ratio":   "% שעות נוספות",
  "utilization":      "% מילוי תקן",
  "h100":             "שעות 100%",
  "h125":             "שעות 125%",
  "h150":             "שעות 150%",
  "h175":             "שעות 175%",
  "h200":             "שעות 200%",
  "pct_h125":         "% 125%",
  "pct_h150":         "% 150%",
  "pct_h175":         "% 175%",
  "pct_h200":         "% 200%",
  "ot_status":        "מצב שעות נוספות",
  "cl_ot_prem":       "פרמיה ש.נ. (₪)",
  "loss":             "הפסד מצטבר (₪)",
  "מגמה":             "מגמה 12 ח'",
}
# `_alloc` suffix means "allocated by proportion across sites" — same display
# label as the base column. Add both for safety.
for _k in list(_HEB_COL.keys()):
  _HEB_COL.setdefault(f"{_k}_alloc", _HEB_COL[_k])

def _heb_cols(d):
  """Return a copy of `d` with technical column names renamed to Hebrew.

  Idempotent: already-Hebrew columns pass through unchanged. Does NOT
  modify the underlying DataFrame in place — pure display helper.
  """
  if d is None or getattr(d, "empty", True):
    return d
  return d.rename(columns={c: _HEB_COL[c] for c in d.columns if c in _HEB_COL})

def _clean_none(d):
  """Replace literal None/NaN with empty-string in OBJECT columns only.

  Numeric columns are left alone — pandas formatters expect NaN there,
  and replacing with "" would force the column to dtype=object and
  break `{:,.0f}` style formats. For numeric blanks use pd.NA + na_rep="".
  """
  if d is None or getattr(d, "empty", True):
    return d
  d = d.copy()
  for c in d.columns:
    if d[c].dtype == object:
      d[c] = d[c].where(d[c].notna(), "")
      d[c] = d[c].replace({"None": "", "nan": "", "NaN": ""})
  return d

def _exec_ins(priority, icon, problem, impact=None, who=None, action=None,
              impact_positive=False):
  """Executive-report insight: priority pill + problem + impact + who + action.

  priority: "high" | "med" | "low"  →  red / orange / green pill
  icon:     emoji or HTML
  problem:  one-line headline of the issue
  impact:   ₪-formatted financial impact (or hours, %, etc.) — optional
  who:      affected client/employee/site name(s) — optional
  action:   recommended action — optional
  """
  _prio_lbl = {"high":"עדיפות גבוהה","med":"עדיפות בינונית","low":"עדיפות נמוכה"}.get(priority,"")
  _meta_parts = []
  if impact:
    _imp_cls = "impact positive" if impact_positive else "impact"
    _meta_parts.append(f'<span class="exec-insight-tag {_imp_cls}">'
                        f'<i class="ti ti-currency-shekel"></i>{impact}</span>')
  if who:
    _meta_parts.append(f'<span class="exec-insight-tag who">'
                        f'<i class="ti ti-user"></i>{who}</span>')
  _meta_html = (f'<div class="exec-insight-meta">{"".join(_meta_parts)}</div>'
                if _meta_parts else "")
  _action_html = (f'<div class="exec-insight-action">'
                   f'<b>המלצה:</b> {action}</div>') if action else ""
  st.markdown(
    f'<div class="exec-insight" data-priority="{priority}">'
    f'<div class="exec-insight-icon">{icon}</div>'
    f'<div class="exec-insight-body">'
    f'<div class="exec-insight-problem">{problem}</div>'
    f'{_meta_html}{_action_html}'
    f'</div>'
    f'<div class="exec-priority-pill" data-priority="{priority}">{_prio_lbl}</div>'
    f'</div>',
    unsafe_allow_html=True,
  )

def _bar_h(x_vals,y_vals,colors,texts,title="",height=280,xvis=False):
  fig = go.Figure(go.Bar(x=x_vals,y=y_vals,orientation="h",
    marker_color=colors,opacity=0.88,text=texts,textposition="outside",
    hovertemplate="<b>%{y}</b><br>%{x}<extra></extra>"))
  fig.update_layout(**{**_PL,"height":max(height,len(y_vals)*28)},
    showlegend=False,title=dict(text=title,font=dict(size=12)),
    xaxis=dict(visible=xvis),yaxis=dict(showgrid=False,tickfont=dict(size=10)))
  return fig

_KPI_TARGETS = {
  "cph":         {"target": 50.0,  "good": "low",  "label": "עלות לשעה"},
  "ot_pct":      {"target": 8.0,   "good": "low",  "label": "שעות נוספות"},
  "utilization": {"target": None,   "good": "low",  "label": "מילוי תקן"},  # נמוך = טוב (יותר רווח תקן)
  "margin":      {"target": 15.0,  "good": "high", "label": "מרג'ין"},
}

def _kpi_status(value, target, good="low"):
  if good == "low":
    if value <= target:         return "good"
    if value <= target * 1.15:  return "warn"
    return "bad"
  else:
    if value >= target:         return "good"
    if value >= target * 0.85:  return "warn"
    return "bad"

def _kpi_block(label, value_str, prev_value=None, target=None, good="low",
               accent_color="blue", icon="", static_delta="", tooltip=""):
  # אייקון: תמיכה ב-ti-xxx (Tabler) ואמוג'י
  if icon and icon.startswith("ti-"):
    _accent_clr = {"blue":"#1D4ED8","red":"#A32D2D","amber":"#BA7517",
                   "green":"#0F6E56","slate":"#64748B"}.get(accent_color,"#9CA3AF")
    icon_html = f'<i class="ti {icon}" style="color:{_accent_clr}"></i>'
  elif icon:
    icon_html = f'<span style="font-size:13px;line-height:1">{icon}</span>'
  else:
    icon_html = ""

  # שינוי מול תקופה קודמת
  vs_prev = ""
  if prev_value is not None and prev_value != 0:
    try:
      v = float(str(value_str).replace("₪","").replace(",","")
                .replace("%","").replace("h","").strip())
      change = (v - prev_value) / abs(prev_value) * 100
      improving = (change < 0) if good == "low" else (change > 0)
      cls = "dn-good" if improving else "up-bad"
      arrow = "▼" if change < 0 else "▲"
      vs_prev = f'<span class="{cls}">{arrow}{abs(change):.1f}%</span>'
    except Exception:
      vs_prev = ""

  # השוואה ליעד
  tgt_badge = ""
  if target is not None:
    try:
      v = float(str(value_str).replace("₪","").replace(",","")
                .replace("%","").replace("h","").strip())
      st = _kpi_status(v, target, good)
      sym = {"good":"✓","warn":"●","bad":"⚠"}[st]
      clr = {"good":"#0F6E56","warn":"#BA7517","bad":"#A32D2D"}[st]
      tgt_badge = f'<span style="color:{clr}"> {sym} יעד&thinsp;{target}</span>'
    except Exception:
      pass

  # Build the "vs previous" + "vs target" pieces first (these are NOT chipped —
  # they have their own coloring/styling).
  badges_line = vs_prev + tgt_badge

  # Convert the static_delta string into a grid of mini-boxes ("chips").
  # Each ' · '-separated segment becomes one chip; '<br>' becomes a new row.
  chips_html = ""
  if static_delta:
    rows = static_delta.split("<br>")
    chip_rows = []
    for row in rows:
      pieces = [p.strip() for p in row.split(" · ") if p.strip()]
      if not pieces:
        continue
      row_html = "".join(f'<span class="kpi-chip">{p}</span>' for p in pieces)
      chip_rows.append(f'<div class="kpi-chip-row">{row_html}</div>')
    chips_html = "".join(chip_rows)

  # Native HTML `title` attribute = browser tooltip on hover. Cheap, no JS,
  # works on every browser including screen-readers. Used for explaining
  # metric terms (מרג'ין / עלות לשעה / שעות נוספות / אגרות וכו') so the
  # CEO doesn't need to ask what each KPI means.
  _tooltip_attr = f' title="{tooltip}"' if tooltip else ""
  _info_icon = ('<i class="ti ti-info-circle" '
                'style="font-size:11px;color:#94A3B8;margin-right:auto"></i>'
                ) if tooltip else ""
  return (
    f'<div class="kpi-cell" data-accent="{accent_color}"{_tooltip_attr}>'
    f'<div class="kpi-lbl">{icon_html}{label}{_info_icon}</div>'
    f'<div class="kpi-val">{value_str}</div>'
    f'<div class="kpi-delta">{badges_line}</div>'
    f'{chips_html}'
    f'</div>'
  )

def _sparkline_emoji(values):
  if not values or len(values) < 2: return ""
  bars = "▁▂▃▄▅▆▇█"
  mn,mx = min(values),max(values)
  if mx==mn: return bars[3]*len(values)
  return "".join(bars[min(7,int((v-mn)/(mx-mn)*7))] for v in values)

def _status_pill(status):
  pills = {
    "crit": ("🔴","קריטי","#FEE2E2","#7F1D1D"),
    "warn": ("🟠","לטיפול","#FEF3C7","#78350F"),
    "ok":   ("🟢","תקין",  "#D1FAE5","#14532D"),
  }
  icon,txt,bg,fg = pills.get(status,("⚪","?","#F1F5F9","#64748B"))
  return f"{icon} {txt}"

def _styled(df,fmt,hi_col=None,hi_thresh=None,hi_color="#FEF3C7",score_col=None):
  # na_rep="" → pd.NA / NaN rendered as empty (used by totals-row helper)
  s = df.style.format(fmt, na_rep="")
  if hi_col and hi_thresh and hi_col in df.columns:
    def _hf(v):
      try: return f"background:{hi_color};color:#78350F;font-weight:700" if float(v)>hi_thresh else ""
      except: return ""
    s = s.map(_hf,subset=[hi_col])
  if score_col and score_col in df.columns:
    def _sf(v):
      if not isinstance(v,(int,float)): return ""
      return f"color:{GREEN};font-weight:700" if v>=70 else (f"color:{AMBER};font-weight:700" if v>=45 else f"color:{RED};font-weight:700")
    s = s.map(_sf,subset=[score_col])
  return s

# ═══ מנגנון רענון ════════════════════════════════════════════════════════════
import json as _json_mod
from pathlib import Path as _PPath

def _file_sig(filepath):
  try:
    s = os.stat(filepath)
    return f"{s.st_mtime:.0f}:{s.st_size}"
  except Exception:
    return None

def _scan_data_dir():
  base = _PPath(os.path.dirname(os.path.abspath(__file__))) / "data"
  sigs = {}
  if not base.exists():
    return sigs
  for f in base.iterdir():
    if f.is_file() and f.suffix == ".xlsx" and "תקן" in f.name:
      sigs["_standards"] = _file_sig(f)
  for md in base.iterdir():
    if not md.is_dir(): continue
    for fn in ("income.xlsx","hours.xlsx","hours.xls","cost.pdf","costs.xlsx"):
      fp = md / fn
      if fp.exists():
        sigs[f"{md.name}/{fn}"] = _file_sig(fp)
  return sigs

def _sigs_path():
  return os.path.join(os.path.dirname(os.path.abspath(__file__)), "output","cache","_data_signatures.json")

@st.cache_data(ttl=15)
def _detect_changes():
  current = _scan_data_dir()
  saved = {}
  p = _sigs_path()
  if os.path.exists(p):
    try:
      with open(p,"r",encoding="utf-8") as _f: saved = _json_mod.load(_f)
    except Exception: pass
  new_f  = [k for k in current if k not in saved]
  chg_f  = [k for k in current if k in saved and saved[k] != current[k]]
  del_f  = [k for k in saved   if k not in current]
  has_ch = bool(new_f or chg_f or del_f)
  parts  = ([f"{len(new_f)} חדשים"] if new_f else []) + \
           ([f"{len(chg_f)} שונו"]  if chg_f else []) + \
           ([f"{len(del_f)} נמחקו"] if del_f else [])
  return {"has_changes":has_ch,"new_files":new_f,"changed_files":chg_f,"deleted_files":del_f,
          "summary":"צריך רענון: "+(", ".join(parts)) if has_ch else "הכל מעודכן"}

def _save_sigs():
  sigs = _scan_data_dir()
  p = _sigs_path()
  os.makedirs(os.path.dirname(p), exist_ok=True)
  with open(p,"w",encoding="utf-8") as _f: _json_mod.dump(sigs,_f,ensure_ascii=False)

def _run_refresh():
  """Refresh data button handler.

  Two paths:
  - **Local (full)**: re-runs the preprocessor over the raw PDFs/Excel in
    data/MM-YYYY/, rebuilds parquets, clears Streamlit's data cache, reloads.
  - **Streamlit Cloud (light)**: the raw source files are NOT in the cloud
    deploy (they're gitignored). The parquets are the source of truth and
    were updated by the last git push. So we only clear the Streamlit
    cache to force re-read of the parquet from disk.

  Crucially: this function NEVER touches session_state auth keys, so the
  user stays logged in across the refresh.
  """
  import time; _t0 = time.time()
  _is_cloud = (
      "/mount/src/" in os.path.abspath(__file__)
      or os.environ.get("HOSTNAME", "").startswith("streamlit-")
  )
  try:
    if _is_cloud:
      # Cloud: just clear the cache so the next render re-reads the
      # parquet from disk. The actual rebuild has to happen locally
      # and be pushed via git.
      st.cache_data.clear()
      _elapsed = time.time() - _t0
      logger.info("[REFRESH] cloud-mode cache clear, %.2fs", _elapsed)
      return {"success": True,
              "message": f"מטמון נוקה ב-{_elapsed:.1f}שנ' · "
                          "(ענן: לבנייה מחדש הריצי run_build.py מקומית ועשי git push)"}

    # Local: full rebuild from raw source files.
    from core.preprocessor import build_and_save
    build_and_save()
    _save_sigs()
    st.cache_data.clear()
    _elapsed = time.time() - _t0
    _meta_p = os.path.join(os.path.dirname(os.path.abspath(__file__)),"output","cache","build_meta.json")
    try:
      with open(_meta_p,"r",encoding="utf-8") as _f: _meta = _json_mod.load(_f)
      _rows = _meta.get("rows", "?"); _months_n = len(_meta.get("months",[]))
      _msg = f"רענון הושלם ב-{_elapsed:.1f}שנ' · {_months_n} חודשים · {_rows} שורות"
    except Exception:
      _msg = f"רענון הושלם ב-{_elapsed:.1f}שנ'"
    logger.info("[REFRESH] local rebuild done, %.2fs", _elapsed)
    return {"success":True,"message":_msg}
  except Exception as _ex:
    logger.exception("[REFRESH] failed: %s", _ex)
    return {"success":False,"error":str(_ex)}

# ═══ כותרת עליונה ═════════════════════════════════════════════════════════════
# Unified header: logo + title + system-status pill + user pill (left side),
# all in ONE bar. Action buttons (Refresh, Logout) sit in a slim row directly
# beneath so they remain Streamlit-functional (HTML can't trigger callbacks).
_now = pd.Timestamp.now().strftime("%d/%m/%Y %H:%M")
_ch_info = _detect_changes()

# Embed the company logo as base64 so it renders inline in the HTML banner.
# Falls back to a stylised emoji circle if static/logo.png isn't present yet.
import base64 as _b64
_logo_html = '<div class="top-bar-logo" style="background:#fff;display:inline-flex;align-items:center;justify-content:center;font-size:20px;color:#0E5A2E;">👷</div>'
_logo_fs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logo.png")
if os.path.exists(_logo_fs):
  try:
    with open(_logo_fs, "rb") as _f:
      _logo_b64 = _b64.b64encode(_f.read()).decode("ascii")
    _logo_html = f'<img class="top-bar-logo" src="data:image/png;base64,{_logo_b64}" alt="Yanai Personnel">'
  except Exception:
    pass

# Data freshness state — drives both header "system status" pill colour and the
# small "last updated" line below.
_meta_p2 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "output","cache","build_meta.json")
_age_min = None
_age_label = ""
_age_color = "#94A3B8"
if os.path.exists(_meta_p2):
  try:
    with open(_meta_p2,"r",encoding="utf-8") as _ff: _m2 = _json_mod.load(_ff)
    _ts = _m2.get("built_at","")
    if _ts:
      _age_min = (pd.Timestamp.now() - pd.Timestamp(_ts)).total_seconds() / 60
      if _age_min < 60:
        _age_label = f"לפני {int(_age_min)} דק'"
      elif _age_min < 1440:
        _age_label = f"לפני {int(_age_min/60)} שעות"
      else:
        _age_label = f"לפני {int(_age_min/1440)} ימים"
      _age_color = ("#059669" if _age_min<60
                    else "#D97706" if _age_min<1440 else "#DC2626")
  except Exception:
    pass

# system status: data-stale OR pending file changes → warn pill
_sys_warn = (_ch_info["has_changes"]
             or (_age_min is not None and _age_min >= 1440))
_sys_cls = "sys-pill warn" if _sys_warn else "sys-pill"
_sys_txt = "טעון לעדכון" if _sys_warn else "המערכת תקינה"

_cur_u = _current_user() or ""
_user_pill = (f'<span class="user-pill"><i class="ti ti-user-circle"></i>'
              f'{_cur_u}</span>') if _cur_u else ""

st.markdown(
  f'<div class="top-bar">'
  f'  <div class="top-bar-brand">'
  f'    {_logo_html}'
  f'    <div class="top-bar-title">'
  f'      <span>ינאי פרסונל</span><span class="ltd"> בע"מ</span>'
  f'      <span class="sep">·</span>'
  f'      <span class="sys">מערכת ניתוח עלויות</span>'
  f'    </div>'
  f'  </div>'
  f'  <div class="top-bar-actions">'
  f'    <span class="{_sys_cls}"><span class="dot"></span>{_sys_txt}</span>'
  f'    {_user_pill}'
  f'    <span class="user-pill" style="background:rgba(255,255,255,.06);'
  f'border-color:rgba(255,255,255,.12);font-weight:500">'
  f'<i class="ti ti-calendar"></i>{_now} · {len(all_months)} חודשים</span>'
  f'  </div>'
  f'</div>',
  unsafe_allow_html=True)

# ── Action row (functional buttons sit just under the green bar) ────────────
_act_meta, _act_warn, _act_refresh, _act_logout = st.columns([4, 4, 2, 2])
with _act_meta:
  if _age_label:
    st.markdown(
      f'<div style="font-size:11px;color:{_age_color};padding-top:6px;'
      f'font-weight:600">⏱️ נתונים עודכנו {_age_label}</div>',
      unsafe_allow_html=True)
with _act_warn:
  if _ch_info["has_changes"]:
    st.markdown(
      f'<div style="font-size:11px;color:#D97706;font-weight:700;'
      f'padding-top:6px">⚠️ {_ch_info["summary"]}</div>',
      unsafe_allow_html=True)
with _act_refresh:
  if st.button("🔄 רענן נתונים", key="btn_refresh_top",
                use_container_width=True, help="עדכן את כל הנתונים"):
    with st.spinner("מעבד נתונים..."):
      _res = _run_refresh()
    if _res["success"]:
      st.success(_res["message"])
      st.rerun()
    else:
      st.error(_res["error"])
with _act_logout:
  if st.button("יציאה ←", key="_logout_btn",
                help="התנתקות וחזרה למסך הכניסה",
                use_container_width=True):
    _logout()

# ═══ פילטר ════════════════════════════════════════════════════════════════════
# The .filter-marker element is detected by CSS :has() and styles the
# surrounding stVerticalBlock as a clean card. Using st.container() so all
# filter widgets are direct children of the same vertical block.
with st.container():
  st.markdown('<div class="filter-marker">סינון נתונים</div>',
               unsafe_allow_html=True)
  fa,fb,fc = st.columns([2,4,4])
  with fa:
    mode = st.radio("תצוגה",["חודש בודד","טווח"],horizontal=True,key="f_mode",
                     label_visibility="visible")
  with fb:
    if mode=="חודש בודד":
      sel_m = st.selectbox("חודש",all_months,index=len(all_months)-1,
                            key="f_single",label_visibility="visible")
      RNG = (sel_m,sel_m)
    else:
      c1,c2=st.columns(2)
      with c1: m_from=st.selectbox("מחודש",all_months,index=0,key="f_from")
      with c2:
        v=[m for m in all_months if _mkey(m)>=_mkey(m_from)]
        m_to=st.selectbox("עד חודש",v,index=len(v)-1,key="f_to")
      RNG=(m_from,m_to)
  with fc:
    sel_cl=st.multiselect("לקוחות",all_clients,key="f_cl",
                           placeholder="כל הלקוחות",label_visibility="visible")

  # שורת פילטרים שנייה — מדינה / בעיות / חיפוש / Reset
  fd,fe,ff,fg = st.columns([3,2,4,1.2])
  with fd:
    sel_ctr=st.multiselect("מדינות",all_countries,key="f_ctr",
                            placeholder="כל המדינות",label_visibility="visible")
  with fe:
    st.markdown(
      "<div style='font-size:11px;font-weight:600;color:#64748B;"
      "margin-bottom:6px'>סינון נוסף</div>", unsafe_allow_html=True)
    only_problems=st.toggle("🚨 רק שורות עם בעיה",key="f_probs")
  with ff:
    search_q=st.text_input("חיפוש",key="search_box",
                            placeholder="🔍 הקלד שם עובד / לקוח / אתר / מס' לסינון...",
                            label_visibility="visible")
  with fg:
    # Reset Filters — clears every f_* key + search and triggers a rerun.
    # Streamlit caveat: you can't pop a key whose widget renders on the same
    # run, so we clear them BEFORE st.rerun() and let widgets re-init.
    st.markdown(
      "<div style='font-size:11px;font-weight:600;color:#64748B;"
      "margin-bottom:6px'>&nbsp;</div>", unsafe_allow_html=True)
    if st.button("איפוס סינון", key="btn_reset_filters",
                 use_container_width=True,
                 help="חזרה לערכי ברירת מחדל: כל הלקוחות, כל המדינות, כל החודשים"):
      for _k in ("f_mode","f_single","f_from","f_to","f_cl",
                 "f_ctr","f_probs","search_box"):
        st.session_state.pop(_k, None)
      st.rerun()

# ═══ חישובים ══════════════════════════════════════════════════════════════════
with _stage("filter df (month + client)"):
  df = _filt(raw.copy(),RNG,sel_cl)

# פילטרים נוספים
if sel_ctr and "country" in df.columns:
  df = df[df["country"].isin(sel_ctr)]
if only_problems and "cost_driver" in df.columns:
  df = df[df["cost_driver"] != "תקין"]
if search_q.strip():
  _q = search_q.strip()
  _mask = pd.Series(False, index=df.index)
  for _col in ("employee_name","client","site","employee_id"):
    if _col in df.columns:
      _mask |= df[_col].astype(str).str.contains(_q, case=False, na=False)
  df = df[_mask]
  st.caption(f"🔍 נמצאו {len(df)} שורות עבור \"{_q}\"")

# ── Historical view ─────────────────────────────────────────────────────────
# Same row-level filters as `df` (client / country / problems-only) but WITHOUT
# the date-range restriction. Use this for trend charts, forecasts, and MoM
# comparisons so a single-month selection still has access to historical context.
_raw_hist = raw.copy()
if sel_cl:
  _raw_hist = _raw_hist[_raw_hist["client"].isin(sel_cl)]
if sel_ctr and "country" in _raw_hist.columns:
  _raw_hist = _raw_hist[_raw_hist["country"].isin(sel_ctr)]
if only_problems and "cost_driver" in _raw_hist.columns:
  _raw_hist = _raw_hist[_raw_hist["cost_driver"] != "תקין"]

# סנן "מתחת לתקן" מ-cost_driver (fallback אם הצינור הישן עוד קיים)
if "cost_driver" in df.columns:
  df["cost_driver"] = df["cost_driver"].apply(
    lambda v: " + ".join(p for p in str(v).split(" + ") if "מתחת לתקן" not in p) or "תקין"
  )

if df.empty:
  # Professional empty-state card — explains what to do instead of a small
  # warning banner. Shown when the user's filter combination yields zero
  # rows (e.g. "client X" not active in "month Y").
  st.markdown(
    '<div class="empty-state">'
    '<div class="empty-state-icon"><i class="ti ti-database-off"></i></div>'
    '<div class="empty-state-title">אין נתונים להצגה עבור הסינון הנוכחי</div>'
    '<div class="empty-state-body">'
    'יתכן שאחת המגבלות הבאות גרמה לזה:'
    '<ul style="text-align:right;margin:8px 16px 0;padding:0;font-size:12.5px">'
    '<li>לקוח שנבחר לא היה פעיל בחודש שנבחר</li>'
    '<li>מדינה שנבחרה ללא עובדים בטווח</li>'
    '<li>החיפוש לא תאם לאף שורה</li>'
    '<li>"רק שורות עם בעיה" — אין שורות עם בעיות בטווח זה</li>'
    '</ul></div>'
    '<div class="empty-state-action">'
    'לחץ על <b>איפוס סינון</b> למעלה כדי לחזור למצב ברירת מחדל'
    '</div></div>',
    unsafe_allow_html=True,
  )
  st.stop()

# Base components: allocate proportionally per site (use adjusted_levy, not raw levy)
with _stage("allocate cost components"):
  _alloc_r = (df["cost"] / df["employer_cost"].replace(0, float("nan"))).fillna(0)
  _BASE_COMPS = [c for c in ("gross_salary","bituach","pension","adjusted_levy") if c in df.columns]
  for _cc in _BASE_COMPS:
      df[f"{_cc}_alloc"] = (df[_cc] * _alloc_r).round(2)
# Extended components: pre-computed in parquet by merge_and_allocate (PDF months only)
_EXT_ALLOC = [c for c in ("vacation_fund_alloc","severance_alloc","medical_insurance_alloc",
                           "employment_levy_alloc","incentive_fund_alloc","savings_deposit_alloc")
              if c in df.columns]
# residual (should be ~0 for PDF months, captures unlabeled components for XLSX months)
_known_sum = (sum(df[f"{c}_alloc"] for c in _BASE_COMPS if f"{c}_alloc" in df.columns)
              + sum(df[c] for c in _EXT_ALLOC))
df["other_alloc"] = (df["cost"] - _known_sum).clip(lower=0).round(2)

_rl  = RNG[0] if RNG[0]==RNG[1] else f"{RNG[0]}–{RNG[1]}"

# ═══ CANONICAL METRICS FUNCTION ══════════════════════════════════════════════
# Single source of truth for every KPI on the page. EVERY tab, card, chart
# and table should read its summary numbers from here. If you find a divergence
# between two parts of the dashboard, the fix is to route both through this.
#
# Definitions (PINNED, do not change without updating Debug Panel + tooltips):
#   total_hours = h100 + h125 + h150 + h175 + h200    (verified — see audit)
#   cost        = sum(cost) over the filtered rows
#   revenue     = sum(billing_amount) over (month, client) dedup
#   cost_per_hour = cost / total_hours   (weighted, NOT a row-wise mean)
#
# `assert_no_cross_month` defaults to False because some callers intentionally
# pass multi-month dataframes (e.g. a 6-month range KPI). When the caller
# expects a single-month view, set it to True for a hard-fail assert.
def calculate_metrics(filtered_df, *, label="filtered"):
  """Return canonical KPI dict from a filtered DataFrame.

  All KPIs displayed anywhere in the dashboard must come from one of these
  fields. Tabs that need a different SCOPE (e.g. last-month-only inside a
  range) should call this function with their own filtered slice — not
  recompute from scratch.
  """
  d = filtered_df
  m_uniq = sorted(d["month"].dropna().unique().tolist(), key=_mkey) \
            if "month" in d.columns else []
  out = {
    "label":            label,
    "row_count":        int(len(d)),
    "months":           m_uniq,
    "month_count":      len(m_uniq),
    # Hours — explicit breakdown so the Debug Panel can show them all
    "h_regular":        float(d.get("h100", pd.Series([0])).fillna(0).sum()),
    "h_125":            float(d.get("h125", pd.Series([0])).fillna(0).sum()),
    "h_150":            float(d.get("h150", pd.Series([0])).fillna(0).sum()),
    "h_175":            float(d.get("h175", pd.Series([0])).fillna(0).sum()),
    "h_200":            float(d.get("h200", pd.Series([0])).fillna(0).sum()),
    "total_hours":      float(d["total_hours"].sum()) if "total_hours" in d.columns else 0.0,
    "reportable_hours": float(d["total_reportable_hours"].sum()) if "total_reportable_hours" in d.columns else 0.0,
    # Cost / revenue / profit
    "total_cost":       float(d["cost"].sum()) if "cost" in d.columns else 0.0,
    "employee_count":   int(d["employee_id"].nunique()) if "employee_id" in d.columns else 0,
    "client_count":     int(d["client"].nunique()) if "client" in d.columns else 0,
  }
  # Overtime totals (sum of all h125+...+h200)
  out["overtime_hours"] = out["h_125"] + out["h_150"] + out["h_175"] + out["h_200"]
  # Derived: cost-per-hour — weighted (sum cost / sum hours), NOT row mean.
  # Three explicit variants so any tab can show whichever it needs:
  out["cph_total"]      = (out["total_cost"]/out["total_hours"]) if out["total_hours"]>0 else 0.0
  out["cph_regular_only"] = (out["total_cost"]/out["h_regular"]) if out["h_regular"]>0 else 0.0
  out["cph_reportable"] = (out["total_cost"]/out["reportable_hours"]) if out["reportable_hours"]>0 else 0.0
  # Revenue / profit / margin — dedup by (month, client) since billing_amount
  # is replicated across employee rows in the parquet.
  if all(c in d.columns for c in ("billing_amount","client","month")):
    _dd = d.drop_duplicates(["month","client"])
    out["total_revenue"] = float(_dd["billing_amount"].sum())
  else:
    out["total_revenue"] = 0.0
  out["gross_profit"]  = out["total_revenue"] - out["total_cost"]
  out["margin_pct"]    = (out["gross_profit"]/out["total_revenue"]*100) if out["total_revenue"]>0 else 0.0
  # OT percentage of total hours — same definition the KPI strip uses
  out["ot_pct"]        = (out["overtime_hours"]/out["total_hours"]*100) if out["total_hours"]>0 else 0.0
  # OT premium ₪ (using same _ot_prem_s helper as anywhere else)
  try:
    out["ot_premium"] = float(_ot_prem_s(d).sum())
  except Exception:
    out["ot_premium"] = 0.0
  # Revenue/hour
  out["revenue_per_hour"] = (out["total_revenue"]/out["total_hours"]) if out["total_hours"]>0 else 0.0

  # ── Cost-component totals — dedup per (employee_id, month) because the
  # raw cost-file values (adjusted_levy, bituach, savings_deposit etc.) are
  # MONTHLY per-employee values replicated across all site rows for that
  # employee in that month. Without dedup we'd double-count multi-site
  # employees. Without the `month` key, a multi-month range would drop
  # whole months of the same employee (under-counting).
  if "employee_id" in d.columns and "month" in d.columns:
    _de = d.drop_duplicates(["employee_id","month"])
  elif "employee_id" in d.columns:
    _de = d.drop_duplicates("employee_id")
  else:
    _de = d
  def _emp_sum(col):
    return float(_de[col].sum()) if col in _de.columns else 0.0
  def _row_sum(col):
    return float(d[col].sum()) if col in d.columns else 0.0

  out["gross_salary"]      = _emp_sum("gross_salary")
  out["bituach"]           = _emp_sum("bituach")
  out["pension"]           = _emp_sum("pension")
  out["levy_raw"]          = _emp_sum("levy")             # raw אגרות from cost file
  out["levy_adjusted"]     = _emp_sum("adjusted_levy")    # after day-prorate
  out["employment_levy"]   = _emp_sum("employment_levy")
  out["medical_insurance"] = _emp_sum("medical_insurance")
  out["incentive_fund"]    = _emp_sum("incentive_fund")
  out["savings_deposit"]   = _emp_sum("savings_deposit")
  out["vacation_fund"]     = _emp_sum("vacation_fund")
  out["severance"]         = _emp_sum("severance")
  out["employer_cost"]     = _emp_sum("employer_cost")
  # Total fees & levies (אגרות + היטל) — what the user thinks of as "אגרות"
  out["fees_total"]        = out["levy_adjusted"] + out["employment_levy"]
  # ── Percentages — ALL weighted (sum/sum), NOT row-mean. Drives the
  # "אגרות מהעלות" KPI card and every fee-related display.
  out["fees_pct"]          = (out["fees_total"]/out["total_cost"]*100) if out["total_cost"]>0 else 0.0
  out["levy_adj_pct"]      = (out["levy_adjusted"]/out["total_cost"]*100) if out["total_cost"]>0 else 0.0
  out["gross_pct"]         = (out["gross_salary"]/out["total_cost"]*100) if out["total_cost"]>0 else 0.0
  out["bituach_pct"]       = (out["bituach"]/out["total_cost"]*100) if out["total_cost"]>0 else 0.0
  out["medical_pct"]       = (out["medical_insurance"]/out["total_cost"]*100) if out["total_cost"]>0 else 0.0
  out["deposit_pct"]       = (out["savings_deposit"]/out["total_cost"]*100) if out["total_cost"]>0 else 0.0
  out["incentive_pct"]     = (out["incentive_fund"]/out["total_cost"]*100) if out["total_cost"]>0 else 0.0
  return out

# ── Assertions ─────────────────────────────────────────────────────────
def _assert_no_cross_month(d, expected_month):
  """Fail loud if a 'single-month' view contains other months."""
  if "month" not in d.columns: return None
  found = set(d["month"].dropna().unique()) - {expected_month}
  return list(found) if found else None

# Build the canonical metrics for the CURRENT (filter-scope) dataframe.
with _stage("calculate_metrics"):
  _M = calculate_metrics(df, label=f"filtered ({_rl})")

# Legacy variable names — preserved so existing code keeps working without a
# 5000-line refactor. EVERY new code should read from `_M[...]` instead.
TC    = _M["total_cost"]
TH    = _M["total_hours"]
CPH_W = _M["cph_total"]
CPH   = CPH_W
_has_profit   = all(c in raw.columns for c in ("billing_amount","profit","margin_pct"))
# profit/billing_amount are per-client values replicated across all employee rows — must dedup
_dedup_mc      = df.drop_duplicates(["month","client"]) if _has_profit and "client" in df.columns and "month" in df.columns else df
_profit_total  = float(_dedup_mc["profit"].sum())        if _has_profit else 0.0
_billing_dedup = float(_dedup_mc["billing_amount"].sum()) if _has_profit else 0.0
# weighted margin (profit÷billing), not unweighted mean of per-client margin_pct
_margin_avg    = (_profit_total / _billing_dedup * 100) if _billing_dedup > 0 else 0.0
# Standard-fill / shortage metrics are intentionally disabled by business
# decision — they assume the תקן is correct, which we no longer want to
# assume. The "Conclusions" tab now generates RECOMMENDED rates by comparing
# real billing against the current standard instead. See section "8.
# המלצות תקן" below.
_has_shortage = False
_shortage_h   = 0.0
_has_shortage_rev = False
_shortage_rev = 0.0
_has_util     = False
_util_avg     = 0.0
_under_util   = 0.0
# ── אגרות (fees) % and total — read DIRECTLY from calculate_metrics (_M).
# No row-mean. No per-row replication. One canonical source.
#   fees_total = adjusted_levy + employment_levy   (per-employee deduped)
#   fees_pct   = fees_total / total_cost × 100      (weighted, NOT row-mean)
_has_fee   = _M.get("fees_total", 0) > 0
_fee_avg   = _M.get("fees_pct", 0.0)
_fee_total = _M.get("fees_total", 0.0)
NE  = int(df["employee_id"].nunique()) if "employee_id" in df.columns else 0
NC  = int(df["client"].nunique()) if "client" in df.columns else 0
OTP  = float(_ot_prem_s(df).sum())
OTR  = OTP/TC*100 if TC>0 else 0
NM  = max(1,df["month"].nunique()) if "month" in df.columns else 1

_avail_ot=[c for c in OT_LEVELS if c in df.columns]
_ot_h={c:float(df[c].sum()) for c in _avail_ot}
_ot_pc={c:_ot_h[c]/TH*100 if TH>0 else 0 for c in _avail_ot}
_phi=_ot_pc.get("h175",0)+_ot_pc.get("h200",0)
_p150=_ot_pc.get("h150",0)
_ot_sk,_ot_sl=_ot_st(_phi,_p150)
# Total OT hours as % of total hours — matches t8 "_ot_pct" so the global KPI
# and tab metrics are consistent (was: OTR = OT premium ₪ / total cost ₪).
OT_PCT_HOURS = (sum(_ot_h.values()) / TH * 100) if TH > 0 else 0

# ── KPI sub-breakdowns (compact mini-info under each card's value) ─────────
def _fmt_k(v): return f"{v/1000:.1f}K" if abs(v) >= 1000 else f"{v:,.0f}"

# 1. Hours breakdown by level — regular on line 1, OT levels on line 2
_h100_total = float(df.get("h100", pd.Series(0, index=df.index)).fillna(0).sum())
_h_line1 = f"100% {_fmt_k(_h100_total)}" if _h100_total > 0 else ""
_h_ot_parts = [f"{OT_LABELS[_c]} {_fmt_k(_ot_h.get(_c, 0))}"
               for _c in _avail_ot if _ot_h.get(_c, 0) > 0]
_h_line2 = " · ".join(_h_ot_parts)
_HOURS_BREAKDOWN = (f"{_h_line1}<br>{_h_line2}" if _h_line1 and _h_line2
                    else (_h_line1 or _h_line2))

# 2. OT % breakdown by level (only levels with >0.1% share)
_ot_pc_parts = [f"{OT_LABELS[_c]} {_ot_pc.get(_c, 0):.1f}%"
                for _c in _avail_ot if _ot_pc.get(_c, 0) > 0.1]
_OT_PCT_BREAKDOWN = " · ".join(_ot_pc_parts)

# 3. Top-3 cost components as % of total cost
_COST_COMP_LIST = [
  ("שכר ברוטו",   "gross_salary_alloc"),
  ("ביטוח לאומי", "bituach_alloc"),
  ("פנסיה",       "pension_alloc"),
  ("אגרות",       "adjusted_levy_alloc"),
  ("חופשה/מחלה", "vacation_fund_alloc"),
  ("פיצויים",     "severance_alloc"),
  ("ביטוח רפואי", "medical_insurance_alloc"),
  ("היטל תעסוקה", "employment_levy_alloc"),
  ("קרן עידוד",   "incentive_fund_alloc"),
  ("פיקדון",       "savings_deposit_alloc"),
  ("אחר",          "other_alloc"),
]
_cost_pairs = [(lbl, float(df[col].sum())) for lbl, col in _COST_COMP_LIST
               if col in df.columns and float(df[col].sum()) > 0]
_cost_pairs.sort(key=lambda x: -x[1])
_COST_TOP3 = (" · ".join(f"{lbl} {v/TC*100:.0f}%" for lbl, v in _cost_pairs[:3])
              if (TC > 0 and _cost_pairs) else "")

# 4. Cost-per-hour by OT level — effective rates (base × multiplier)
_OT_MULT_MAP = {"h125": 1.25, "h150": 1.50, "h175": 1.75, "h200": 2.00}
_cph_lvl_parts = []
if CPH_W > 0:
  _cph_lvl_parts.append(f"100% ₪{CPH_W:.0f}")
  for _c in _avail_ot:
    _m = _OT_MULT_MAP.get(_c, 1.0)
    _cph_lvl_parts.append(f"{OT_LABELS[_c]} ₪{CPH_W*_m:.0f}")
_CPH_LEVELS = " · ".join(_cph_lvl_parts)

# 5. Employees by country — top 3
_EMP_BY_COUNTRY = ""
if "country" in df.columns and "employee_id" in df.columns:
  _emp_country = (df.dropna(subset=["country"])
                  .assign(country=lambda d: d["country"].astype(str).str.strip())
                  .query("country != ''")
                  .groupby("country")["employee_id"].nunique()
                  .sort_values(ascending=False))
  if len(_emp_country) > 0:
    _ec_top = _emp_country.head(4)
    _ec_parts = [f"{c} {n}" for c, n in _ec_top.items()]
    if len(_emp_country) > 4:
      _ec_parts.append(f"+{int(_emp_country.iloc[4:].sum())} אחרים")
    _EMP_BY_COUNTRY = " · ".join(_ec_parts)

_TOP3_FEES = _TOP3_REVENUE = _TOP3_PROFIT = _TOP3_MARGIN = ""
# (Top 3 clients per metric is computed AFTER _ca is built — see below the
# _ca aggregation block. We only declare the placeholders here so KPI
# rendering further down doesn't NameError if _ca turns out empty.)
def _clip(s, n=14):
  s = str(s)
  return s if len(s) <= n else s[:n-1] + "…"

_pm=_prev_m(RNG[1])
_pd=raw[raw["month"]==_pm].copy() if _pm in all_months else pd.DataFrame()
if sel_cl and not _pd.empty: _pd=_pd[_pd["client"].isin(sel_cl)]
_pTC=float(_pd["cost"].sum()) if not _pd.empty else 0
# Weighted CPH for the previous-month KPI delta (not simple mean — consistent
# with the live KPI which is sum(cost)/sum(hours)).
_pCPH=(float(_pd["cost"].sum())/float(_pd["total_hours"].sum())
       if not _pd.empty and float(_pd["total_hours"].sum())>0 else 0)
_pTH=float(_pd["total_hours"].sum()) if not _pd.empty else 0
_pl=f"מול {_pm}" if _pm in all_months else ""

# per-client
_cl_agg=dict(עובדים=("employee_id","nunique"),שעות=("total_hours","sum"),
       ימים=("work_days","sum"),
       עלות=("cost","sum"),
       # avg_cph removed from agg — recomputed as weighted (cost/hours) below
       avg_ot=("overtime_ratio","mean"))
if "gross_salary_alloc"      in df.columns: _cl_agg["שכר ברוטו"]      =("gross_salary_alloc","sum")
if "bituach_alloc"           in df.columns: _cl_agg["ביטוח לאומי"]   =("bituach_alloc","sum")
if "pension_alloc"           in df.columns: _cl_agg["פנסיה"]          =("pension_alloc","sum")
if "adjusted_levy_alloc"     in df.columns: _cl_agg["אגרות"]          =("adjusted_levy_alloc","sum")
if "vacation_fund_alloc"     in df.columns: _cl_agg["חופשה/מחלה"]    =("vacation_fund_alloc","sum")
if "severance_alloc"         in df.columns: _cl_agg["פיצויים"]        =("severance_alloc","sum")
if "medical_insurance_alloc" in df.columns: _cl_agg["ביטוח רפואי"]   =("medical_insurance_alloc","sum")
if "employment_levy_alloc"   in df.columns: _cl_agg["היטל תעסוקה"]   =("employment_levy_alloc","sum")
if "incentive_fund_alloc"    in df.columns: _cl_agg["קרן עידוד"]      =("incentive_fund_alloc","sum")
if "savings_deposit_alloc"   in df.columns: _cl_agg["פיקדון"]         =("savings_deposit_alloc","sum")
if "other_alloc"             in df.columns: _cl_agg["אחר"]            =("other_alloc","sum")
for c in _avail_ot: _cl_agg[c]=(c,"sum")
with _stage("aggregate _ca (per-client)"):
  _ca=(df.groupby("client",as_index=False).agg(**_cl_agg)
       .sort_values("עלות",ascending=False).reset_index(drop=True))
_ca["% עלות"]=(_ca["עלות"]/_ca["עלות"].sum()*100).round(1)
# Weighted cost-per-hour per client: sum(cost) / sum(hours). Consistent with
# the KPI strip (CPH_W) — simple mean was biased by short-shift rows.
_ca["avg_cph"]=(_ca["עלות"]/_ca["שעות"].replace(0,float("nan"))).fillna(0).round(2)
for c in _avail_ot:
  _ca[f"pct_{c}"]=(_ca[c]/_ca["שעות"]*100).fillna(0).round(1)
def _clos(row):
  hi=row.get("pct_h175",0)+row.get("pct_h200",0); md=row.get("pct_h150",0)
  return _ot_st(hi,md)[0]
_ca["ot_status"]=_ca.apply(_clos,axis=1)
_dfc=df.copy(); _dfc["_p"]=_ot_prem_s(_dfc)
_ca["cl_ot_prem"]=_ca["client"].map(_dfc.groupby("client")["_p"].sum()).fillna(0)
_ca["loss"]=(_ca["avg_cph"]-CPH_W)*_ca["שעות"]; _ca["loss"]=_ca["loss"].clip(lower=0)
# add profit/margin per client — dedup by (month,client) then group by client
if "billing_amount" in df.columns and "profit" in df.columns and "month" in df.columns:
  _ca_inc = (df.drop_duplicates(["month","client"])
               .groupby("client", as_index=False)
               .agg(profit=("profit","sum"), billing_amount=("billing_amount","sum")))
  _ca_inc["margin_pct"] = (_ca_inc["profit"] / _ca_inc["billing_amount"].replace(0,float("nan")) * 100).round(1).fillna(0.0)
  _ca = _ca.merge(_ca_inc[["client","billing_amount","profit","margin_pct"]], on="client", how="left")
if "month" in df.columns:
  _spark_pivot = (df.groupby(["client","month"])["cost"].sum()
                  .unstack(fill_value=0)
                  .reindex(columns=sorted(df["month"].unique().tolist(),key=_mkey),fill_value=0))
  _ca["מגמה"] = _ca["client"].map(
    _spark_pivot.apply(lambda r:_sparkline_emoji(list(r.values)),axis=1).to_dict()
  ).fillna("")
_ca.insert(0,"#",range(1,len(_ca)+1))

# Top 3 clients per metric — feeds chips into the matching KPI card.
# Must run AFTER _ca is fully built (with billing_amount/profit/margin_pct
# columns merged in). Names are truncated so chips stay readable.
if not _ca.empty:
  # Top 3 by FEES (אגרות) — for the "אגרות מהעלות" KPI card.
  # Shown as % of total fees so the user instantly sees concentration
  # (e.g. "3 clients own 80% of all fees").
  if "אגרות" in _ca.columns:
    _total_fees = float(_ca["אגרות"].sum())
    if _total_fees > 0:
      _t3f = _ca.nlargest(3, "אגרות")
      _TOP3_FEES = " · ".join(
        f"{_clip(r['client'])} {r['אגרות']/_total_fees*100:.0f}%"
        for _, r in _t3f.iterrows() if r["אגרות"] > 0
      )
  if "billing_amount" in _ca.columns:
    _t3r = _ca.nlargest(3, "billing_amount")
    _TOP3_REVENUE = " · ".join(
      f"{_clip(r['client'])} ₪{r['billing_amount']/1000:.0f}K"
      for _, r in _t3r.iterrows() if r["billing_amount"] > 0
    )
  if "profit" in _ca.columns:
    _t3p = _ca.nlargest(3, "profit")
    _TOP3_PROFIT = " · ".join(
      f"{_clip(r['client'])} ₪{r['profit']/1000:.0f}K"
      for _, r in _t3p.iterrows() if r["profit"] > 0
    )
  if "margin_pct" in _ca.columns:
    _t3m = (_ca[_ca["margin_pct"].notna()]
            .nlargest(3, "margin_pct"))
    _TOP3_MARGIN = " · ".join(
      f"{_clip(r['client'])} {r['margin_pct']:.0f}%"
      for _, r in _t3m.iterrows() if r["margin_pct"] > 0
    )

_crit=_ca[_ca["ot_status"]=="crit"]
_warn=_ca[_ca["ot_status"]=="warn"]
_bad =int((_ca["ot_status"]!="ok").sum())
_hcph=_ca.loc[_ca["avg_cph"].idxmax()] if not _ca.empty else None
_lcph=_ca.loc[_ca["avg_cph"].idxmin()] if not _ca.empty else None
_loss_top=float(_hcph["loss"]) if _hcph is not None else 0

# per-employee (for analytics: top expensive, Pareto, KPIs)
_ea={}
if "employee_id" in df.columns:
  _e_cols=dict(עלות=("cost","sum"),שעות=("total_hours","sum"))
         # avg_cph removed — recomputed weighted (cost/hours) after groupby
  if "employee_name" in df.columns: _e_cols["employee_name"]=("employee_name","first")
  if "client" in df.columns: _e_cols["לקוח"]=("client","first")
  if "site"   in df.columns: _e_cols["אתר"] =("site","first")
  for c in _avail_ot: _e_cols[c]=(c,"sum")
  if "cost_driver" in df.columns:
    _e_cols["cost_driver"]=("cost_driver",lambda s: s.value_counts().idxmax() if len(s)>0 else "")
  with _stage("aggregate _ea (per-employee)"):
    _ea=(df.groupby("employee_id",as_index=False).agg(**_e_cols)
         .sort_values("עלות",ascending=False).reset_index(drop=True))
  # Weighted CPH per employee (was simple-mean — biased by part-time rows)
  _ea["avg_cph"]=(_ea["עלות"]/_ea["שעות"].replace(0,float("nan"))).fillna(0).round(2)
  _ea["% עלות"]=(_ea["עלות"]/_ea["עלות"].sum()*100).round(1)
  _ea.insert(0,"#",range(1,len(_ea)+1))

# per-employee × client × site (for the employee table tab)
_ea_detail={}
if "employee_id" in df.columns:
  _grp_keys=["employee_id"]
  if "client" in df.columns: _grp_keys.append("client")
  if "site"   in df.columns: _grp_keys.append("site")
  _ed_cols=dict(עלות=("cost","sum"),שעות=("total_hours","sum"),
          ימים=("work_days","sum"))
          # avg_cph removed — recomputed weighted (cost/hours) after groupby
  if "employee_name"       in df.columns: _ed_cols["employee_name"]  =("employee_name","first")
  if "gross_salary_alloc"      in df.columns: _ed_cols["שכר ברוטו"]    =("gross_salary_alloc","sum")
  if "bituach_alloc"           in df.columns: _ed_cols["ביטוח לאומי"]  =("bituach_alloc","sum")
  if "pension_alloc"           in df.columns: _ed_cols["פנסיה"]         =("pension_alloc","sum")
  if "adjusted_levy_alloc"     in df.columns: _ed_cols["אגרות"]         =("adjusted_levy_alloc","sum")
  if "vacation_fund_alloc"     in df.columns: _ed_cols["חופשה/מחלה"]   =("vacation_fund_alloc","sum")
  if "severance_alloc"         in df.columns: _ed_cols["פיצויים"]       =("severance_alloc","sum")
  if "medical_insurance_alloc" in df.columns: _ed_cols["ביטוח רפואי"]  =("medical_insurance_alloc","sum")
  if "employment_levy_alloc"   in df.columns: _ed_cols["היטל תעסוקה"]  =("employment_levy_alloc","sum")
  if "incentive_fund_alloc"    in df.columns: _ed_cols["קרן עידוד"]     =("incentive_fund_alloc","sum")
  if "savings_deposit_alloc"   in df.columns: _ed_cols["פיקדון"]        =("savings_deposit_alloc","sum")
  if "other_alloc"             in df.columns: _ed_cols["אחר"]           =("other_alloc","sum")
  for c in _avail_ot: _ed_cols[c]=(c,"sum")
  if "cost_driver" in df.columns:
    _ed_cols["cost_driver"]=("cost_driver",lambda s: s.value_counts().idxmax() if len(s)>0 else "")
  with _stage("aggregate _ea_detail"):
    _ea_detail=(df.groupby(_grp_keys,as_index=False).agg(**_ed_cols)
                .sort_values(["employee_id","עלות"],ascending=[True,False]).reset_index(drop=True))
  # Weighted CPH per (employee × client × site) — consistent with KPI strip
  _ea_detail["avg_cph"]=(
    _ea_detail["עלות"]/_ea_detail["שעות"].replace(0,float("nan"))
  ).fillna(0).round(2)
  if "client" in _ea_detail.columns: _ea_detail.rename(columns={"client":"לקוח"},inplace=True)
  if "site"   in _ea_detail.columns: _ea_detail.rename(columns={"site":"אתר"},  inplace=True)
  _ea_detail["% עלות"]=(_ea_detail["עלות"]/_ea_detail["עלות"].sum()*100).round(1)
  _ea_detail.insert(0,"#",range(1,len(_ea_detail)+1))

# ═══ KPI Strip — grouped into Financial / Operational / Risk ════════════════
# Tooltip definitions (browser-native `title` attribute on each card) — these
# explain the metric to a manager who doesn't know the terminology.
_TT = {
  "revenue":  "הכנסה בפועל מ-income.xlsx — מה שלקוחות חויבו בפועל לאחר כל "
              "תיקוני החיוב הידניים. בסיס לחישוב מרג'ין אמיתי.",
  "cost":     "סך עלות מעסיק לכל העובדים בתקופה — כולל שכר ברוטו, ביטוח לאומי, "
              "פנסיה, אגרות, פיצויים, חופשה ופיקדון.",
  "profit":   "הפרש בין הכנסה בפועל לעלות כוללת. רווח חיובי = הכנסה > עלות.",
  "margin":   "מרג'ין אמיתי = רווח ÷ הכנסה (באחוזים). מעל 15% נחשב בריא, "
              "מתחת ל-10% דורש התייחסות.",
  "emp":      "מספר עובדים ייחודיים בטווח התאריכים והפילטרים הנוכחיים.",
  "hours":    "סך שעות עבודה. 100% = שעות רגילות. 125%/150%/175%/200% = "
              "שעות נוספות לפי רמת תוספת.",
  "cph":      "עלות מעסיק ממוצעת לשעה — ממוצע משוקלל (סך עלות ÷ סך שעות). "
              "שונה ממוצע פשוט של ערכי cost_per_hour לכל שורה.",
  "ot":       "אחוז שעות נוספות מסך השעות. שעות מעל 100% — לפי רמות "
              "(125%, 150%, 175%, 200%). מעל 12% נחשב סיכון.",
  "fees":     "סכום: adjusted_levy (אגרות לאחר התאמה לימי עבודה) + employment_levy "
              "(היטל תעסוקה ע\"ז). אחוז: סכום זה ÷ סך עלות (משוקלל), לא ממוצע "
              "פשוט של אחוזים פר-שורה.",
  "shortfall":"רווח אקסטרה מתקן — חיוב לקוח על שעות תקן גם כשהעובד לא "
              "השלים את כל השעות בפועל.",
  "util":     "אחוז מילוי תקן ממוצע — כמה מהשעות בפועל יצרו רווח מעל התקן.",
}

# ── Group 1: financial ────────────────────────────────────────────────────
_kpis_fin = []
_has_billing = "billing_amount" in df.columns
_total_billing = float(df.drop_duplicates(["month","client"])["billing_amount"].sum()) if _has_billing and "client" in df.columns and "month" in df.columns else 0.0
_real_profit   = _total_billing - TC
_real_margin   = (_real_profit / _total_billing * 100) if _total_billing > 0 else 0.0
if _has_billing and _total_billing > 0:
  _kpis_fin.append(_kpi_block("הכנסה בפועל", f"₪{_total_billing:,.0f}",
                                accent_color="green", icon="ti-cash-banknote",
                                static_delta=(_TOP3_REVENUE or ""),
                                tooltip=_TT["revenue"]))
# עלות כוללת — components only (Top 3 cost categories).
_cost_lines = [s for s in (_pl, _COST_TOP3) if s]
_kpis_fin.append(_kpi_block("עלות כוללת", f"₪{TC:,.0f}",
                              prev_value=_pTC if _pTC else None, good="low",
                              accent_color="red", icon="ti-coin",
                              static_delta="<br>".join(_cost_lines),
                              tooltip=_TT["cost"]))
if _has_billing and _total_billing > 0:
  _kpis_fin.append(_kpi_block("רווח", f"₪{_real_profit:,.0f}",
                                accent_color="green", icon="ti-wallet",
                                static_delta=(_TOP3_PROFIT or ""),
                                tooltip=_TT["profit"]))
  _margin_lines = [f"מרג'ין יעד: 15%+"]
  if _TOP3_MARGIN: _margin_lines.append(_TOP3_MARGIN)
  _kpis_fin.append(_kpi_block("מרג'ין אמיתי", f"{_real_margin:.1f}%",
                                target=15.0, good="high",
                                accent_color="blue", icon="ti-chart-line",
                                static_delta="<br>".join(_margin_lines),
                                tooltip=_TT["margin"]))
elif _has_profit:
  # Fallback: standard-based profit (when income.xlsx unavailable).
  _kpis_fin.append(_kpi_block("רווח", f"₪{_profit_total:,.0f}",
                                target=_margin_avg, good="high",
                                accent_color="green", icon="ti-wallet",
                                static_delta=f"מרג'ין {_margin_avg:.1f}%",
                                tooltip=_TT["profit"]))

# ── Group 2: operational ─────────────────────────────────────────────────
_kpis_ops = []
_kpis_ops.append(_kpi_block("עובדים", str(NE),
                              accent_color="blue", icon="ti-users",
                              static_delta=(f"{NC} לקוחות<br>{_EMP_BY_COUNTRY}"
                                            if _EMP_BY_COUNTRY
                                            else f"{NC} לקוחות"),
                              tooltip=_TT["emp"]))
_kpis_ops.append(_kpi_block("שעות", f"{TH:,.0f}",
                              prev_value=_pTH, good="high",
                              accent_color="green", icon="ti-clock-hour-4",
                              static_delta=_HOURS_BREAKDOWN,
                              tooltip=_TT["hours"]))
if _has_shortage and _shortage_h > 0:
  _kpis_ops.append(_kpi_block("רווח אקסטרה מתקן", f"₪{_shortage_rev:,.0f}",
                                accent_color="green", icon="ti-trending-up",
                                static_delta=f"{_shortage_h:,.0f}h חויבו ללא עבודה",
                                tooltip=_TT["shortfall"]))
if _has_util and _util_avg > 0:
  _kpis_ops.append(_kpi_block("מילוי תקן ממוצע", f"{_util_avg:.0f}%",
                                accent_color="slate", icon="ti-target",
                                static_delta="ערך גבוה = יותר רווח תקין",
                                tooltip=_TT["util"]))

# ── Group 3: risk / variance ─────────────────────────────────────────────
_kpis_risk = []
_kpis_risk.append(_kpi_block("עלות לשעה", f"₪{CPH_W:.2f}",
                               prev_value=_pCPH, target=50.0, good="low",
                               accent_color="amber", icon="ti-clock",
                               static_delta=(f"ממוצע משוקלל<br>{_CPH_LEVELS}"
                                              if _CPH_LEVELS
                                              else "ממוצע משוקלל (עלות÷שעות)"),
                               tooltip=_TT["cph"]))
_kpis_risk.append(_kpi_block("שעות נוספות %", f"{OT_PCT_HOURS:.1f}%",
                               target=12.0, good="low",
                               accent_color="blue", icon="ti-alarm",
                               static_delta=(f"{_OT_PCT_BREAKDOWN}<br>"
                                              f"₪{OTP/1000:,.0f}K פרמיה ({OTR:.1f}% מעלות) · {_ot_sl}"
                                              if _OT_PCT_BREAKDOWN else
                                              f"₪{OTP:,.0f} פרמיה ({OTR:.1f}% מהעלות) · {_ot_sl}"),
                               tooltip=_TT["ot"]))
if _has_fee and _fee_avg > 0:
  # Show both the fees total AND the formula so the user can audit the %.
  _fees_lines = [
    f'₪{_fee_total:,.0f} ÷ ₪{_M["total_cost"]:,.0f}',
  ]
  if _TOP3_FEES: _fees_lines.append(_TOP3_FEES)
  _kpis_risk.append(_kpi_block(
      "אגרות מהעלות",
      f"{_fee_avg:.1f}%",            # ← 1 decimal so 14.55% no longer rounds to 15%
      accent_color="slate", icon="ti-receipt",
      static_delta="<br>".join(_fees_lines),
      tooltip=_TT["fees"]))

# ── Render — each group is a labelled strip ─────────────────────────────
def _render_kpi_group(kpis, group_label, group_icon):
  if not kpis: return
  n = len(kpis)
  st.markdown(
    f'<div class="kpi-group">'
    f'  <div class="kpi-group-head">'
    f'    <i class="ti {group_icon}"></i>{group_label}'
    f'    <span class="kpi-group-count">{n} מדדים</span>'
    f'  </div>'
    f'  <div class="kpi-strip" style="grid-template-columns:repeat({n},minmax(0,1fr));">'
    f'    {"".join(kpis)}'
    f'  </div>'
    f'</div>',
    unsafe_allow_html=True,
  )

# Two-row layout per user request: financial row + combined operations/risk
# row. The CEO sees the 4 "money" KPIs at the top, then the operational and
# risk context just below — no third strip, no scroll.
_kpis_ops_risk = _kpis_ops + _kpis_risk
with _stage("render KPI strip"):
  _render_kpi_group(_kpis_fin,      "פיננסי",        "ti-cash-banknote")
  _render_kpi_group(_kpis_ops_risk, "תפעולי וסיכון", "ti-activity")

# Note: the previous "billing breakdown" (עבודה / תוספות / זיכויים / הכנסה נטו)
# block and the "billing floor" caption were removed because the same numbers
# are already shown in the KPI strip above ("הכנסה בפועל", "רווח אקסטרה מתקן").

# ═══ Focus line — z-score anomaly detection ════════════════════════════════════
_anomalies = []
if "month" in raw.columns:
  _hist = raw.copy()
  if sel_cl: _hist = _hist[_hist["client"].isin(sel_cl)]

  # 1. שעות נוספות — נוטרלי, לא מסומן כחריגה.
  # OT מעלה את הרווח הכולל (יותר שעות = יותר הכנסה) אך לעיתים מצמצם רווח/שעה
  # אם הלקוח לא משלם פרמיה מלאה. אנו מציגים את המידע אבל לא מסמנים אותו
  # כבעיה אוטומטית — תלוי בהסכם הספציפי עם הלקוח.

  # 2. עלות לשעה מול היסטוריה — weighted mean per month (consistent with CPH_W).
  # Was using simple .mean() which over-weighted small-hour rows; switched to
  # cost_total / hours_total per month so the anomaly check matches the
  # weighted-average shown in the KPI strip and tabs.
  _cph_per_m_grp = _hist.groupby("month").agg(_c=("cost","sum"), _h=("total_hours","sum"))
  _cph_per_m = (_cph_per_m_grp["_c"] / _cph_per_m_grp["_h"].replace(0, float("nan"))).dropna()
  if len(_cph_per_m) >= 3:
    _mu,_sig = _cph_per_m.mean(),(_cph_per_m.std() or 1)
    _z = (CPH_W - _mu) / _sig
    if _z > 1.5:
      _anomalies.append((_z,"red" if _z>2.5 else "amber",
        f"עלות לשעה חריגה: ₪{CPH_W:.1f} (ממוצע {len(_cph_per_m)} חודשים: ₪{_mu:.1f})"))

  # 3. שעות חסר מול היסטוריה
  if _has_shortage and _shortage_h > 0 and "shortage_hours" in _hist.columns:
    _sh_per_m = _hist.groupby("month")["shortage_hours"].sum()
    if len(_sh_per_m) >= 3:
      _mu,_sig = _sh_per_m.mean(),(_sh_per_m.std() or 1)
      _z = (_shortage_h - _mu) / _sig
      if _z > 1.5:
        _anomalies.append((_z,"amber",
          f"שעות חסר חריגות: {_shortage_h:,.0f}h (ממוצע {len(_sh_per_m)} חודשים: {_mu:,.0f}h)"))

if _anomalies:
  _anomalies.sort(key=lambda x:-x[0])
  _az,_acolor,_amsg = _anomalies[0]
  # ── If the top anomaly is the CPH-anomaly, render it as a STRUCTURED
  # alert card (value / 6-mo avg / gap ₪ / impact / recommendation) rather
  # than a single long line. Falls back to the generic .focus line for
  # other anomaly types (e.g. shortage hours).
  if "עלות לשעה" in _amsg and len(_cph_per_m) >= 3:
    _gap_per_h    = CPH_W - _mu          # ₪/hour gap
    _impact_total = _gap_per_h * TH      # ₪ impact for the period
    _months_used  = len(_cph_per_m)
    _action_txt = (
      "בדוק עלייה חד-פעמית באגרות / היטלים, גידול בשעות נוספות, "
      "או עובדים חדשים עם שכר גבוה. שווה לבדוק את הלקוחות עם CPH "
      "הגבוה ביותר בטאב 'טבלאות' → 'לפי לקוח'."
    )
    _alert_color   = "#DC2626" if _acolor=="red" else "#D97706"
    _alert_bg      = "#FEF2F2" if _acolor=="red" else "#FFFBEB"
    _alert_border  = "#FECACA" if _acolor=="red" else "#FDE68A"
    _alert_iconbg  = "#FEE2E2" if _acolor=="red" else "#FEF3C7"
    _alert_title   = "עלות לשעה חריגה" if _acolor=="red" else "עלות לשעה מעל הממוצע"
    st.markdown(
      f'<div class="cph-alert" style="background:{_alert_bg};'
      f'border:1px solid {_alert_border};border-right:4px solid {_alert_color}">'
      f'  <div class="cph-alert-icon" style="background:{_alert_iconbg};'
      f'color:{_alert_color}">⚠️</div>'
      f'  <div class="cph-alert-body">'
      f'    <div class="cph-alert-title" style="color:{_alert_color}">'
      f'      {_alert_title}'
      f'    </div>'
      f'    <div class="cph-alert-grid">'
      f'      <div class="cph-alert-cell">'
      f'        <div class="cph-alert-cell-lbl">ערך נוכחי</div>'
      f'        <div class="cph-alert-cell-val" style="color:{_alert_color}">'
      f'        ₪{CPH_W:.2f}</div></div>'
      f'      <div class="cph-alert-cell">'
      f'        <div class="cph-alert-cell-lbl">ממוצע {_months_used} חודשים</div>'
      f'        <div class="cph-alert-cell-val">₪{_mu:.2f}</div></div>'
      f'      <div class="cph-alert-cell">'
      f'        <div class="cph-alert-cell-lbl">פער לשעה</div>'
      f'        <div class="cph-alert-cell-val" style="color:{_alert_color}">'
      f'        +₪{_gap_per_h:.2f}</div></div>'
      f'      <div class="cph-alert-cell">'
      f'        <div class="cph-alert-cell-lbl">השפעה כוללת בתקופה</div>'
      f'        <div class="cph-alert-cell-val" style="color:{_alert_color}">'
      f'        +₪{_impact_total/1000:,.0f}K</div></div>'
      f'    </div>'
      f'    <div class="cph-alert-action"><b>המלצה: </b>{_action_txt}</div>'
      f'  </div>'
      f'</div>',
      unsafe_allow_html=True,
    )
  else:
    _aicon = "🚨" if _acolor=="red" else "⚠️"
    st.markdown(f'<div class="focus {_acolor}">{_aicon} {_amsg}</div>',
                 unsafe_allow_html=True)
elif _has_profit and _profit_total>0:
  st.markdown(f'<div class="focus green">✅ ביצועים תקינים — '
    f'רווח ₪{_profit_total/1000:.0f}K, מרג\'ין {_margin_avg:.1f}%</div>',
    unsafe_allow_html=True)
else:
  _ftxt_ok=(f"✅ אין חריגות סטטיסטיות — {NC-_bad} מתוך {NC} לקוחות תקינים · "
            f"עלות כוללת ₪{TC/1000:.0f}K")
  st.markdown(f'<div class="focus green">{_ftxt_ok}</div>',unsafe_allow_html=True)

# ── נתוני רווח ─────────────────────────────────────────────────────────────────
if not _has_profit:
  st.caption("💡 נתוני רווח/מרג'ין אינם זמינים בנתוני עלויות בלבד. "
             "הם זמינים רק כאשר מריצים גם את מודול החיוב (pipeline.build_master_full).")

# ── ייצוא ──────────────────────────────────────────────────────────────────────
_master_xlsx = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "output","master","master_full.xlsx")

# Build a multi-sheet "management report" lazily — only on first click.
# Cached so re-clicks during the same session don't rebuild from scratch.
@st.cache_data(show_spinner=False, ttl=600)
def _build_mgmt_report(_df_records, _ca_records, _ea_records,
                       _period_label, _stamp):
  """Compose an in-memory xlsx with: סקירה / לקוחות / עובדים / נתונים גולמיים.

  Inputs are dict-records (hashable) so streamlit's cache can deduplicate.
  Returns the raw xlsx bytes. xlsxwriter is preferred (lighter than openpyxl)
  but we fall back to openpyxl if it's missing.
  """
  import io as _io
  _buf = _io.BytesIO()
  _engine = None
  for _eng in ("xlsxwriter", "openpyxl"):
    try:
      __import__(_eng); _engine = _eng; break
    except ImportError:
      continue
  if _engine is None:
    return None  # nothing to export with
  _df_o   = pd.DataFrame(_df_records)
  _ca_o   = pd.DataFrame(_ca_records)
  _ea_o   = pd.DataFrame(_ea_records)
  # Sheet 1: overview metrics (1 row)
  _ovw_row = {
    "תקופה":        _period_label,
    "הכנסה":        float(_total_billing) if _has_billing else float(_billing_dedup),
    "עלות":          float(TC),
    "רווח":          float(_real_profit) if _has_billing and _total_billing>0 else float(_profit_total),
    "מרג'ין %":     round(_real_margin if _has_billing and _total_billing>0 else _margin_avg, 2),
    "עלות לשעה":   round(CPH_W, 2),
    "שעות":          float(TH),
    "שעות נוספות %": round(OT_PCT_HOURS, 2),
    "עובדים":       int(NE),
    "לקוחות":       int(NC),
    "נוצר ב":      _stamp,
  }
  _ovw_df = pd.DataFrame([_ovw_row])
  with pd.ExcelWriter(_buf, engine=_engine) as _w:
    _ovw_df.to_excel(_w, sheet_name="סקירה",         index=False)
    _heb_cols(_ca_o).to_excel(_w, sheet_name="לקוחות",       index=False)
    _heb_cols(_ea_o).to_excel(_w, sheet_name="עובדים",       index=False)
    _heb_cols(_df_o).to_excel(_w, sheet_name="נתונים גולמיים", index=False)
  _buf.seek(0)
  return _buf.getvalue()

_export_col1, _export_col2, _ = st.columns([2, 2, 4])
with _export_col1:
  if os.path.exists(_master_xlsx):
    with open(_master_xlsx,"rb") as _fh:
      st.download_button("📥 ייצוא Excel / Power BI",_fh.read(),
                         f"master_full_{_rl}.xlsx",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
  else:
    _csv_bytes = df.to_csv(index=False,encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("📥 ייצוא CSV (נוכחי)",_csv_bytes,
                       f"data_export_{_rl}.csv","text/csv")
with _export_col2:
  # Single-click "full management report" — bundles overview KPIs +
  # clients + employees + raw data into one xlsx with Hebrew sheet names.
  if st.button("📊 ייצוא דוח ניהולי מלא", key="btn_mgmt_report",
                use_container_width=True,
                help="ייצא קובץ Excel אחד עם 4 גיליונות: "
                     "סקירה · לקוחות · עובדים · נתונים גולמיים"):
    with st.spinner("בונה דוח ניהולי..."):
      _stamp = pd.Timestamp.now().strftime("%d/%m/%Y %H:%M")
      _rep_bytes = _build_mgmt_report(
        df.to_dict(orient="records"),
        _ca.to_dict(orient="records"),
        _ea.to_dict(orient="records") if isinstance(_ea, pd.DataFrame) else [],
        _rl, _stamp,
      )
    if _rep_bytes is not None:
      _stamp_file = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
      st.download_button(
        "⬇️ הורד עכשיו (Excel)", _rep_bytes,
        file_name=f"yanai_mgmt_report_{_rl}_{_stamp_file}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_mgmt_report_now",
      )
    else:
      st.error("חסר xlsxwriter/openpyxl — התקן: pip install xlsxwriter")

# משתנים גלובליים משמשים בכמה טאבים
_hc = _hcph if _hcph is not None else (_ca.iloc[0] if not _ca.empty else None)
_lc = _lcph if _lcph is not None else (_ca.iloc[-1] if not _ca.empty else None)

# ═══ טאבים ════════════════════════════════════════════════════════════════════
# NEW CLEAN 5-TAB STRUCTURE (replaced 8 tabs that had too much duplication).
#
# Mapping of OLD content → NEW tab:
#   old t1 (Overview)        → new t1 ✓ (no change)
#   old t2 (Charts)          → split: client-charts→t2, employee-charts→t3, forecast→t5
#   old t3 (Tables)          → split: s1 (client)→t2, s2 (employee)→t3, s5 (month)→t5
#   old t4 (Insights)        → t5 (mostly summary content)
#   old t5 (Simulation)      → t4 (pricing simulation)
#   old t6 (Alerts)          → t4 (billing alerts)
#   old t7 (Billing)         → t4 ✓
#   old t8 (Conclusions)     → t5 ✓
#
# To avoid moving 3000+ lines of code, we use ALIASES: each old tab variable
# now points to its new tab. Two old tabs that conflict (e.g. client tables
# and employee tables both in old t3) require manual content reorganisation
# done inline.
t1, t_tables, t_billing, t_summary = st.tabs([
    "📊 סקירה",
    "📋 טבלאות",
    "🧾 חיוב ותקן",
    "📑 מסקנות",
])

# Initialize every tab-render timestamp BEFORE the `with` blocks, because
# Streamlit executes them in source order — and in this file `with t_tables:`
# physically appears before `with t1:`. If we did `_t_X = perf_counter()`
# inline right before each block, any block that's nested or reordered
# would NameError on the previous block's timer. Pre-initializing fixes that.
_t_overview = _t_tables = _t_billing = _t_summary = _time_mod.perf_counter()

# Create the 5 sub-tabs inside Tables EARLY, so other parts of the script
# (e.g. the old `with t2:` Charts block that now routes to the Clients
# sub-tab) can reference s1-s5 as already-defined globals.
#
# Inside each sub-tab we reserve TWO containers in order:
#   *_top    : tables go here   (appear at the top of the page)
#   *_bot    : charts go here   (appear below the tables)
# This way content order is determined by container, not by script order.
# Source-order timing: t_tables renders FIRST in this file (it has to —
# the sub-tabs s1..s5 are created here and referenced by t1's content).
_t_tables = _time_mod.perf_counter()
with t_tables:
    s1, s2, s3, s4, s5 = st.tabs([
        "🏢 לקוח", "👤 עובד", "🏗️ אתר", "🌍 מדינה", "📅 חודש"
    ])
    with s1:
        s1_top = st.container()
        s1_bot = st.container()
    with s2:
        s2_top = st.container()
        s2_bot = st.container()
    with s3:
        s3_top = st.container()
        s3_bot = st.container()
    with s4:
        s4_top = st.container()
        s4_bot = st.container()
    with s5:
        s5_top = st.container()
        s5_bot = st.container()

# Stable aliases used by the routed-content blocks below.
# Charts get _bot containers so they appear BELOW the tables in each sub-tab.
_TAB_OVERVIEW    = t1
_TAB_TABLES      = t_tables
_TAB_CLIENTS     = s1_bot        # Clients charts → below the Clients table
_TAB_EMPLOYEES   = s2_bot        # Employees charts → below the Employees table
_TAB_SITES       = s3_bot        # Sites charts → below the Sites table
_TAB_COUNTRIES   = s4_bot        # Countries charts → below the Countries table
_TAB_MONTHS      = s5_bot        # Months charts → below the Months table
_TAB_BILLING     = t_billing
_TAB_CONCLUSIONS = t_summary

# Route each legacy `with tN:` block to the matching destination:
#   t1 Overview     → t1 Overview
#   t2 Charts       → s1 Clients sub-tab (inside Tables)
#   t3 Tables       → t_tables (Tables tab itself — sub-tabs already created)
#   t4 Insights     → t_summary Conclusions
#   t5 Simulation   → t_billing
#   t6 Alerts       → t_billing
#   t7 Billing      → t_billing
#   t8 Conclusions  → t_summary
t2 = _TAB_CLIENTS       # old t2 (Charts) → Clients sub-tab
t3 = _TAB_TABLES        # old t3 (Tables) — outer block
t4 = _TAB_CONCLUSIONS   # old t4 (Insights) → Conclusions
t5 = _TAB_BILLING       # old t5 (Simulation) → Billing
t6 = _TAB_BILLING
t7 = _TAB_BILLING
t8 = _TAB_CONCLUSIONS

# ══════════════════════════════════════════════════════════════════════════════
# 1 — סקירה
# ══════════════════════════════════════════════════════════════════════════════
# t_tables block has now closed → record its duration before starting t1.
st.session_state.setdefault("_perf",[]).append(("render tab: טבלאות (Tables)",
   _time_mod.perf_counter()-_t_tables))
_t_overview = _time_mod.perf_counter()
with t1:
  if not _ca.empty:
    # ── Header: period & scope ────────────────────────────────────────────
    _hdr_period = RNG[0] if RNG[0]==RNG[1] else f"{RNG[0]} ← {RNG[1]}"
    st.markdown(
      f'<div style="background:linear-gradient(135deg,#F8FAFC,#F1F5F9);'
      f'border:1px solid #E2E8F0;border-radius:10px;padding:10px 16px;'
      f'margin:0 0 14px;display:flex;justify-content:space-between;'
      f'align-items:center;font-size:13px;color:#475569">'
      f'<span>📅 <b style="color:#0F172A">{_hdr_period}</b> '
      f'<span style="opacity:.5">·</span> {NM} חודשים '
      f'<span style="opacity:.5">·</span> {NC} לקוחות '
      f'<span style="opacity:.5">·</span> {NE} עובדים</span>'
      f'<span style="font-size:11px;opacity:.6;letter-spacing:.05em">'
      f'תמונת תקופה</span></div>',
      unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════
    # תקציר מנהלים — answers 3 questions in one card:
    #   1. האם החברה מרוויחה?     →  status pill (green/amber/red)
    #   2. איפה הבעיה הכי גדולה?  →  worst-margin client + impact
    #   3. מה הפעולה המומלצת?    →  derived from the above
    # Pure presentation — every number reads from existing aggregates.
    # ══════════════════════════════════════════════════════════════════════
    _exec_revenue  = _total_billing  if _has_billing and _total_billing>0 else _billing_dedup
    _exec_cost     = TC
    _exec_profit   = (_exec_revenue - _exec_cost) if _exec_revenue>0 else _profit_total
    _exec_margin   = (_exec_profit/_exec_revenue*100) if _exec_revenue>0 else _margin_avg

    if   _exec_margin >= 15: _exec_status_cls,_exec_status_lbl = "good","ביצועים בריאים"
    elif _exec_margin >= 8:  _exec_status_cls,_exec_status_lbl = "warn","דורש מעקב"
    else:                    _exec_status_cls,_exec_status_lbl = "bad", "דורש התייחסות"

    # Find worst client by margin — only if we have margin data per client
    _worst_client_html = ""
    _action_html = ""
    if "margin_pct" in _ca.columns and "billing_amount" in _ca.columns:
      _worst_pool = _ca[(_ca["billing_amount"] > 0) & (_ca["margin_pct"].notna())].copy()
      if not _worst_pool.empty:
        _worst_pool = _worst_pool.sort_values("margin_pct").head(1).iloc[0]
        _wc_name   = _clip(str(_worst_pool["client"]), 22)
        _wc_margin = float(_worst_pool["margin_pct"])
        _wc_rev    = float(_worst_pool["billing_amount"])
        _wc_color  = ("#DC2626" if _wc_margin<0
                      else "#D97706" if _wc_margin<10 else "#16A34A")
        _worst_client_html = (
          f'<div class="exec-sum-q">'
          f'<div class="exec-sum-q-label">איפה הבעיה הגדולה ביותר?</div>'
          f'<div class="exec-sum-q-value">'
          f'  <span class="exec-sum-bullet" style="background:{_wc_color}"></span>'
          f'  <b>{_wc_name}</b>'
          f'  <span style="color:{_wc_color};font-weight:700">'
          f'  {_wc_margin:+.1f}% מרג\'ין</span>'
          f'</div>'
          f'<div class="exec-sum-q-sub">'
          f'הכנסה ₪{_wc_rev/1000:,.0f}K · המרג\'ין הנמוך ביותר בלקוחות הפעילים'
          f'</div>'
          f'</div>'
        )
        # Recommendation derived from situation
        if _wc_margin < 0:
          _action_text = (f"🔴 לבדוק את {_wc_name} מיידית — מרג'ין שלילי. "
                          "אפשרויות: עדכון מחיר, צמצום עובדים, או יציאה מההסכם.")
        elif _wc_margin < 10:
          _action_text = (f"🟠 לזמן את {_wc_name} לפגישת תמחור. "
                          "המרג'ין נמוך — בדוק תוספות שעות נוספות או "
                          "ריבוי אגרות שמייקרות את העלות.")
        else:
          _action_text = (f"🟢 ביצועים תקינים. המשך לעקוב חודש-חודש; "
                          "הלקוח הכי חלש עדיין מעל סף הסיכון.")
        _action_html = (
          f'<div class="exec-sum-q">'
          f'<div class="exec-sum-q-label">מה הפעולה המומלצת?</div>'
          f'<div class="exec-sum-q-value exec-sum-action">{_action_text}</div>'
          f'</div>'
        )

    _profit_color = ("#0E5A2E" if _exec_profit > 0 else "#7F1D1D")
    st.markdown(
      f'<div class="exec-summary">'
      f'  <div class="exec-summary-head">'
      f'    <div class="exec-summary-title">'
      f'      <i class="ti ti-clipboard-check"></i>'
      f'      תקציר מנהלים · {_hdr_period}'
      f'    </div>'
      f'    <span class="exec-summary-status {_exec_status_cls}">'
      f'      {_exec_status_lbl}'
      f'    </span>'
      f'  </div>'
      f'  <div class="exec-summary-body">'
      f'    <div class="exec-sum-q">'
      f'      <div class="exec-sum-q-label">האם החברה מרוויחה?</div>'
      f'      <div class="exec-sum-q-value" style="color:{_profit_color}">'
      f'        ₪{_exec_profit/1000:,.0f}K רווח'
      f'        <span style="color:#64748B;font-weight:500;font-size:13px">'
      f'        · {_exec_margin:+.1f}% מרג\'ין</span>'
      f'      </div>'
      f'      <div class="exec-sum-q-sub">'
      f'        הכנסה ₪{_exec_revenue/1000:,.0f}K · עלות ₪{_exec_cost/1000:,.0f}K'
      f'      </div>'
      f'    </div>'
      f'    {_worst_client_html}'
      f'    {_action_html}'
      f'  </div>'
      f'</div>',
      unsafe_allow_html=True,
    )

    # ── שורה 1: גרף מגמה חודשית (Hero visual) ─────────────────────────────
    # Uses _raw_hist so trend covers full history even when the global date
    # filter is set to a single month.
    _NM_HIST = _raw_hist["month"].nunique() if "month" in _raw_hist.columns else 0
    if HAS_PLOTLY and _NM_HIST >= 2 and "month" in _raw_hist.columns:
      _sec("📈 מגמה חודשית — הכנסות · עלות · רווח")
      _trend_cost = _raw_hist.groupby("month", as_index=False)["cost"].sum()
      _trend_cost["_k"] = _trend_cost["month"].map(_mkey)
      if _has_profit and "billing_amount" in _raw_hist.columns and "profit" in _raw_hist.columns:
        _trend_rev = (_raw_hist.drop_duplicates(["month","client"])
                        .groupby("month", as_index=False)
                        .agg(billing=("billing_amount","sum"),
                             profit =("profit","sum")))
        _trend = _trend_cost.merge(_trend_rev, on="month", how="left").fillna(0)
      else:
        _trend = _trend_cost.copy()
        _trend["billing"] = 0.0
        _trend["profit"]  = 0.0
      _trend = _trend.sort_values("_k").drop(columns="_k").reset_index(drop=True)

      _fig_t = go.Figure()
      if _trend["billing"].sum() > 0:
        _fig_t.add_trace(go.Scatter(
          x=_trend["month"], y=_trend["billing"], name="הכנסות",
          mode="lines+markers",
          line=dict(color="#1D4ED8", width=3),
          marker=dict(size=7, color="#1D4ED8"),
          hovertemplate="%{x}<br>הכנסות: ₪%{y:,.0f}<extra></extra>"))
      _fig_t.add_trace(go.Scatter(
        x=_trend["month"], y=_trend["cost"], name="עלות",
        mode="lines+markers",
        line=dict(color="#A32D2D", width=3),
        marker=dict(size=7, color="#A32D2D"),
        hovertemplate="%{x}<br>עלות: ₪%{y:,.0f}<extra></extra>"))
      if _trend["profit"].sum() > 0:
        _fig_t.add_trace(go.Scatter(
          x=_trend["month"], y=_trend["profit"], name="רווח",
          mode="lines+markers",
          line=dict(color="#0F6E56", width=3, dash="dot"),
          marker=dict(size=7, color="#0F6E56"),
          hovertemplate="%{x}<br>רווח: ₪%{y:,.0f}<extra></extra>"))
      _fig_t.update_layout(
        height=300, margin=dict(l=10,r=10,t=10,b=40),
        legend=dict(orientation="h", y=-0.18, x=0.5, xanchor="center",
                    font=dict(size=12)),
        plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        xaxis=dict(showgrid=False, tickfont=dict(size=11),
                   linecolor="#E2E8F0"),
        yaxis=dict(gridcolor="#F1F5F9", tickfont=dict(size=11),
                   tickformat=",", title="", zeroline=False),
        hoverlabel=dict(bgcolor="#0F172A", font_color="#FFFFFF",
                        bordercolor="#0F172A"),
      )
      st.plotly_chart(_fig_t, use_container_width=True,
                      config={"displayModeBar":False})
      st.caption("מציג הכנסות מול עלויות לאורך זמן. הרווח (קו ירוק מקווקו) הוא הפער ביניהם.")
    elif _NM_HIST < 2:
      st.markdown(
        '<div style="background:#FEFCE8;border-left:3px solid #CA8A04;'
        'padding:10px 14px;border-radius:6px;margin:0 0 14px;'
        'font-size:12px;color:#713F12">'
        '📊 גרף המגמה זמין משני חודשים ומעלה (אין מספיק היסטוריה בנתונים).'
        '</div>',
        unsafe_allow_html=True)

    # ── שורה 2: נתונים מקצועיים — לקוחות מובילים + הרכב עלות ─────────────
    st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
    _d1,_d2 = st.columns([1.15, 1])
    with _d1:
      _sort_col = ("billing_amount"
                   if "billing_amount" in _ca.columns
                      and float(_ca["billing_amount"].sum())>0
                   else "עלות")
      _top_cl = _ca.nlargest(5, _sort_col)
      _rows_html = ""
      for _, _row in _top_cl.iterrows():
        _cl_name = str(_row.get("client","—"))
        if len(_cl_name) > 22: _cl_name = _cl_name[:21] + "…"
        _rev   = float(_row.get("billing_amount", 0))
        _cost  = float(_row.get("עלות", 0))
        _prof  = (float(_row.get("profit"))
                  if "profit" in _ca.columns else _rev - _cost)
        _marg  = (float(_row.get("margin_pct", 0))
                  if "margin_pct" in _ca.columns
                  else (_prof/_rev*100 if _rev>0 else 0))
        _m_clr  = "#0F6E56" if _marg>=15 else "#BA7517" if _marg>=5 else "#A32D2D"
        _rev_s  = f"₪{_rev/1000:,.0f}K" if _rev>0 else "—"
        _cost_s = f"₪{_cost/1000:,.0f}K" if _cost>0 else "—"
        _prof_s = (f"₪{_prof/1000:,.0f}K" if _prof>=0
                   else f"-₪{abs(_prof)/1000:,.0f}K")
        _prof_c = "#0F6E56" if _prof>=0 else "#A32D2D"
        _rows_html += (
          f'<tr style="border-top:1px solid #F1F5F9">'
          f'<td style="padding:7px 10px;font-weight:600;color:#0F172A;'
          f'font-size:13px;text-align:right">{_cl_name}</td>'
          f'<td style="padding:7px 10px;font-size:13px;color:#0F172A;'
          f'text-align:left;font-variant-numeric:tabular-nums">{_rev_s}</td>'
          f'<td style="padding:7px 10px;font-size:13px;color:#475569;'
          f'text-align:left;font-variant-numeric:tabular-nums">{_cost_s}</td>'
          f'<td style="padding:7px 10px;font-size:13px;color:{_prof_c};'
          f'font-weight:600;text-align:left;font-variant-numeric:tabular-nums">{_prof_s}</td>'
          f'<td style="padding:7px 10px;font-size:13px;color:{_m_clr};'
          f'font-weight:700;text-align:left;font-variant-numeric:tabular-nums">{_marg:.1f}%</td>'
          f'</tr>'
        )
      st.markdown(
        f'<div class="blk" style="padding:0;overflow:hidden">'
        f'<div class="blk-lbl" style="padding:14px 14px 6px">🏆 חמשת הלקוחות הגדולים</div>'
        f'<table style="width:100%;border-collapse:collapse">'
        f'<thead><tr style="background:#F8FAFC">'
        f'<th style="padding:6px 10px;text-align:right;font-size:10px;'
        f'color:#64748B;font-weight:700;letter-spacing:.05em">לקוח</th>'
        f'<th style="padding:6px 10px;text-align:left;font-size:10px;'
        f'color:#64748B;font-weight:700;letter-spacing:.05em">הכנסה</th>'
        f'<th style="padding:6px 10px;text-align:left;font-size:10px;'
        f'color:#64748B;font-weight:700;letter-spacing:.05em">עלות</th>'
        f'<th style="padding:6px 10px;text-align:left;font-size:10px;'
        f'color:#64748B;font-weight:700;letter-spacing:.05em">רווח</th>'
        f'<th style="padding:6px 10px;text-align:left;font-size:10px;'
        f'color:#64748B;font-weight:700;letter-spacing:.05em">מרג\'ין</th>'
        f'</tr></thead><tbody>{_rows_html}</tbody></table></div>',
        unsafe_allow_html=True)
    with _d2:
      _cost_cats = ["שכר ברוטו","ביטוח לאומי","פנסיה","אגרות","חופשה/מחלה",
                    "פיצויים","ביטוח רפואי","היטל תעסוקה","קרן עידוד",
                    "פיקדון","אחר"]
      _avail = [c for c in _cost_cats if c in _ca.columns]
      _totals = {c: float(_ca[c].sum()) for c in _avail
                 if c in _ca.columns and float(_ca[c].sum())>0}
      if _totals:
        _tsum = sum(_totals.values())
        _sorted = sorted(_totals.items(), key=lambda x:-x[1])[:6]
        _palette = ["#1D4ED8","#A32D2D","#0F6E56","#BA7517","#7C3AED","#64748B"]
        _bar_html = ""
        for _i,(_cat,_val) in enumerate(_sorted):
          _pct = _val/_tsum*100 if _tsum>0 else 0
          _clr = _palette[_i % len(_palette)]
          _bar_html += (
            f'<div style="margin-bottom:9px">'
            f'<div style="display:flex;justify-content:space-between;'
            f'font-size:11px;margin-bottom:3px">'
            f'<span style="font-weight:600;color:#475569">{_cat}</span>'
            f'<span style="color:#64748B;font-variant-numeric:tabular-nums">'
            f'₪{_val/1000:,.0f}K · {_pct:.1f}%</span></div>'
            f'<div style="height:7px;background:#F1F5F9;border-radius:4px;'
            f'overflow:hidden">'
            f'<div style="width:{_pct:.1f}%;height:100%;background:{_clr}"></div>'
            f'</div></div>'
          )
        st.markdown(
          f'<div class="blk">'
          f'<div class="blk-lbl">🧮 הרכב עלות מעביד</div>'
          f'<div style="margin-top:12px">{_bar_html}</div>'
          f'<div style="font-size:10px;color:#94A3B8;margin-top:8px;'
          f'text-align:left;font-variant-numeric:tabular-nums">'
          f'סה"כ מסווג: ₪{_tsum/1000:,.0f}K · מתוך עלות כוללת ₪{TC/1000:,.0f}K'
          f'</div></div>',
          unsafe_allow_html=True)
      else:
        # Fallback if cost components are not loaded
        _per_emp = TC/NE if NE>0 else 0
        _per_cl  = TC/NC if NC>0 else 0
        st.markdown(
          f'<div class="blk"><div class="blk-lbl">💼 פילוח עלות בסיסי</div>'
          f'<div class="blk-body" style="font-size:13px;line-height:1.9">'
          f'<div>עלות ממוצעת לעובד: <b style="color:#0F172A">₪{_per_emp:,.0f}</b></div>'
          f'<div>עלות ממוצעת ללקוח: <b style="color:#0F172A">₪{_per_cl:,.0f}</b></div>'
          f'<div>עלות לשעה משוקללת: <b style="color:#0F172A">₪{CPH_W:.1f}</b></div>'
          f'</div></div>',
          unsafe_allow_html=True)

    # ── שורה 3: 3 מדדי בריאות עסקיים ────────────────────────────────────
    st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
    _h1,_h2,_h3 = st.columns(3)
    with _h1:
      _top_share = float(_ca.iloc[0]["% עלות"])
      _cclr = "DC2626" if _top_share>40 else "D97706" if _top_share>25 else "059669"
      _clbl = "ריכוז גבוה — סיכון תלות" if _top_share>40 else "ריכוז סביר" if _top_share>25 else "מבוזר היטב"
      st.markdown(
        f'<div class="blk"><div class="blk-lbl">🎯 ריכוז עלות</div>'
        f'<div class="blk-body"><span style="font-size:22px;color:#{_cclr}">'
        f'{_top_share:.0f}%</span> ב-{_ca.iloc[0]["client"]}<br>'
        f'<span style="font-size:11px;color:#64748B">{_clbl}</span></div></div>',
        unsafe_allow_html=True)
    with _h2:
      _ot_clr = ("DC2626" if _ot_sk=="crit"
                 else "D97706" if _ot_sk=="warn"
                 else "059669")
      _ot_sub = f'{_phi:.1f}% גבוה (175/200) · {_p150:.1f}% בינוני (150)'
      st.markdown(
        f'<div class="blk"><div class="blk-lbl">⚠️ סטטוס שע"נ</div>'
        f'<div class="blk-body"><span style="font-size:22px;color:#{_ot_clr}">'
        f'{_ot_sl}</span><br>'
        f'<span style="font-size:11px;color:#64748B">{_ot_sub}</span></div></div>',
        unsafe_allow_html=True)
    with _h3:
      _n_crit = int(len(_crit))
      _n_warn = int(len(_warn)) if isinstance(_warn, pd.DataFrame) else 0
      _risk_total = _n_crit + _n_warn
      _risk_clr = ("DC2626" if _n_crit>0
                   else "D97706" if _n_warn>0
                   else "059669")
      _risk_lbl = (f'{_n_crit} קריטיים · {_n_warn} אזהרה'
                   if _risk_total>0 else "כל הלקוחות תקינים")
      st.markdown(
        f'<div class="blk"><div class="blk-lbl">🏢 לקוחות בסיכון</div>'
        f'<div class="blk-body"><span style="font-size:22px;color:#{_risk_clr}">'
        f'{_risk_total}</span> '
        f'<span style="font-size:11px;color:#64748B">מתוך {NC}</span><br>'
        f'<span style="font-size:11px;color:#64748B">{_risk_lbl}</span></div></div>',
        unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# 2 — גרפים
# ══════════════════════════════════════════════════════════════════════════════
with t2:
  if not HAS_PLOTLY:
    st.info("התקן plotly: pip install plotly")
  elif not _ca.empty:
    def _chart(fig):
      _polish(fig)
      st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})

    # שורה 1: עלות לפי לקוח + עלות לשעה לפי לקוח
    r1a,r1b = st.columns(2)
    with r1a:
      with st.expander("מי הלקוחות שעולים הכי הרבה?", expanded=True):
        _cb=_ca.sort_values("עלות")
        # אדום רק ללקוחות עם חריגת שעות נוספות אמיתית, לא לפי גודל עלות
        clrs=[RED if _cb.iloc[i].get("ot_status","ok")=="crit"
           else (AMBER if _cb.iloc[i].get("ot_status","ok")=="warn"
           else BLUE) for i in range(len(_cb))]
        _chart(_bar_h(_cb["עלות"],_cb["client"],clrs,
               [f"₪{v/1000:.0f} אלף" for v in _cb["עלות"]]))
        st.caption("עמודה לכל לקוח לפי סך עלות. אדום = שעות נוספות קריטיות · כתום = אזהרה · כחול = תקין.")
    with r1b:
      with st.expander("מי מעל הממוצע בעלות לשעה?", expanded=True):
        _cs=_ca.sort_values("avg_cph")
        cph_c=[RED if v>CPH else GREEN for v in _cs["avg_cph"]]
        fig=_bar_h(_cs["avg_cph"],_cs["client"],cph_c,
              [f"₪{v:.0f}" for v in _cs["avg_cph"]])
        fig.add_vline(x=CPH,line_dash="solid",line_color=NAVY,line_width=2,opacity=0.5,
               annotation_text=f" ממוצע עלות לשעה ₪{CPH:.0f}",annotation_font_size=10)
        _chart(fig)
        st.caption("כל לקוח מסומן באדום אם עלות/שעה שלו מעל הממוצע, ירוק אם מתחת. הקו האנכי = ממוצע משוקלל.")

      # שורה 2: פירוק שעות נוספות + הפסד לפי לקוח
    r2a,r2b = st.columns(2)
    with r2a:
      with st.expander("איפה נשרפות שעות יקרות?", expanded=True):
        if _avail_ot:
          tot=sum(_ot_h.values())
          fig=go.Figure(go.Pie(
            labels=[OT_LABELS[c] for c in _avail_ot],
            values=[_ot_h[c] for c in _avail_ot],
            marker_colors=[OT_COLORS[c] for c in _avail_ot],
            hole=0.58,textinfo="label+percent",textfont_size=11))
          fig.update_layout(**{**_PL,"height":280},showlegend=False,
            annotations=[dict(text=f"{tot:,.0f}<br>שעות",x=0.5,y=0.5,
                     font_size=13,showarrow=False)])
          _chart(fig)
          st.caption("התפלגות סוגי השעות הנוספות לפי תעריף (125%, 150%, 175%, 200%).")
    with r2b:
      with st.expander("איפה יש הפסד כספי?", expanded=True):
        _ld=_ca[_ca["loss"]>0].sort_values("loss",ascending=True)
        if not _ld.empty:
          _chart(_bar_h(_ld["loss"],_ld["client"],
                 [RED]*len(_ld),[f"₪{v/1000:.0f} אלף" for v in _ld["loss"]]))
          st.caption("ההפסד מוגדר כעלות/שעה מעל הממוצע × שעות עבודה. מסמן לקוחות לא רווחיים יחסית.")
        else:
          st.success("✅ אין הפסד — כל הלקוחות מתחת לממוצע עלות לשעה")

      # שורה 3: עובדים יקרים
    r3a,r3b = st.columns(2)
    with r3a:
      # Employee-related chart → routed to Employees tab
      pass
    with _TAB_EMPLOYEES:
      with st.expander("מי העובדים היקרים ביותר?", expanded=True):
        if isinstance(_ea,pd.DataFrame) and not _ea.empty:
          _t15=_ea.head(15).sort_values("עלות",ascending=True)
          _chart(_bar_h(_t15["עלות"],_t15["employee_id"],
                 [PURP]*len(_t15),[f"₪{v/1000:.0f} אלף" for v in _t15["עלות"]]))
          st.caption("15 העובדים עם העלות הגבוהה ביותר. מספר עובד מוצג מימין לעמודה.")

    # Trend chart → routed to Conclusions tab (matches section 9 there)
    with _TAB_CONCLUSIONS:
      # מגמה — תמיד מציגים 6 חודשים אחרונים מהנתונים הכלליים (raw)
      if HAS_PLOTLY and "month" in raw.columns:
        with st.expander("מגמה — 6 חודשים אחרונים", expanded=True):
          _last6 = all_months[-6:]
          _raw6  = raw[raw["month"].isin(_last6)]
          if sel_cl: _raw6 = _raw6[_raw6["client"].isin(sel_cl)]
          # Weighted CPH per month: sum(cost) / sum(hours).
          # Was: avg_cph=("cost_per_hour","mean") — simple mean is biased by
          # rows with few hours; the user-visible KPI strip uses weighted.
          _mt=(  _raw6.groupby("month",as_index=False)
               .agg(עלות=("cost","sum"),_h=("total_hours","sum"))
               .sort_values("month",key=lambda s:s.map(_mkey)))
          _mt["avg_cph"] = _mt["עלות"] / _mt["_h"].replace(0, float("nan"))
          _colors=[RED if m in [RNG[0],RNG[1]] else BLUE for m in _mt["month"]]
          fig=go.Figure()
          fig.add_trace(go.Bar(x=_mt["month"],y=_mt["עלות"],name="עלות",
            marker_color=_colors,opacity=0.85,
            hovertemplate="<b>%{x}</b><br>₪%{y:,.0f}<extra></extra>"))
          fig.add_trace(go.Scatter(x=_mt["month"],y=_mt["avg_cph"],name="עלות/שעה",
            mode="lines+markers",line=dict(color=AMBER,width=2),
            marker=dict(size=6),yaxis="y2",
            hovertemplate="<b>%{x}</b><br>₪%{y:.1f}/שעה<extra></extra>"))
          fig.update_layout(**{**_PL,"height":280,"showlegend":True},
            legend=dict(orientation="h",y=1.1,x=0),
            xaxis=dict(showgrid=False,tickangle=-30),
            yaxis=dict(tickprefix="₪",showgrid=True,gridcolor="#F1F5F9"),
            yaxis2=dict(overlaying="y",side="right",tickprefix="₪",showgrid=False))
          _chart(fig)
          st.caption("עמודות = עלות חודשית. קו כתום = עלות/שעה. עמודות אדומות = חודשים בטווח הנבחר.")

      # YoY — שנה מול שנה (routed to Months sub-tab)
    with _TAB_MONTHS:
      if HAS_PLOTLY and "month" in raw.columns:
        _HEB_MONTHS = {"01":"ינואר","02":"פברואר","03":"מרץ","04":"אפריל",
                       "05":"מאי","06":"יוני","07":"יולי","08":"אוגוסט",
                       "09":"ספטמבר","10":"אוקטובר","11":"נובמבר","12":"דצמבר"}
        _yoy_src = raw.copy()
        if sel_cl: _yoy_src = _yoy_src[_yoy_src["client"].isin(sel_cl)]
        _yoy = (_yoy_src.groupby("month",as_index=False)
                .agg(עלות=("cost","sum"))
                .sort_values("month",key=lambda s:s.map(_mkey)))
        _yoy["_mm"]  = _yoy["month"].str[:2]
        _yoy["_yyyy"]= _yoy["month"].str[3:]
        _yoy_both = _yoy[_yoy["_mm"].isin(
          _yoy.groupby("_mm")["_yyyy"].nunique().loc[lambda s:s>=2].index
        )]
        if not _yoy_both.empty:
          with st.expander("📅 השוואה שנה מול שנה", expanded=True):
            _y25 = _yoy_both[_yoy_both["_yyyy"]=="2025"].set_index("_mm")["עלות"]
            _y26 = _yoy_both[_yoy_both["_yyyy"]=="2026"].set_index("_mm")["עלות"]
            _mm_both = sorted(set(_y25.index) & set(_y26.index))
            _xlabels = [_HEB_MONTHS.get(m,m) for m in _mm_both]
            _chg_avg = ((_y26[_mm_both]/_y25[_mm_both]-1)*100).mean()

            # ── Per-month change list — used for headline summary AND for
            # the highest/lowest change badges below.
            _chg_per_m = {m: (_y26[m]/_y25[m]-1)*100 for m in _mm_both}
            # Pick the most-recent overlapping month for the "headline" line.
            _last_mm = _mm_both[-1] if _mm_both else None
            _last_chg = _chg_per_m.get(_last_mm, 0.0) if _last_mm else 0.0
            _last_lbl = _HEB_MONTHS.get(_last_mm, _last_mm) if _last_mm else ""
            _last_dir_he = "גבוה" if _last_chg > 0 else "נמוך"
            _last_color  = "#DC2626" if _last_chg > 0 else "#16A34A"

            # ── Headline summary card — the single sentence the user wanted
            st.markdown(
              f'<div style="background:#F8FAFC;border:1px solid #E2E8F0;'
              f'border-right:4px solid {_last_color};border-radius:10px;'
              f'padding:11px 16px;margin-bottom:12px;font-size:13.5px;'
              f'color:#0F172A;line-height:1.5">'
              f'<b>{_last_lbl} 2026</b> {_last_dir_he} מ-<b>{_last_lbl} 2025</b> '
              f'ב-<b style="color:{_last_color};font-variant-numeric:tabular-nums">'
              f'{abs(_last_chg):.1f}%</b> בעלויות '
              f'<span style="color:#64748B;font-size:12px">'
              f'(₪{_y25[_last_mm]/1000:,.0f}K → ₪{_y26[_last_mm]/1000:,.0f}K)'
              f'</span></div>',
              unsafe_allow_html=True,
            )

            fig_yoy=go.Figure()
            fig_yoy.add_trace(go.Bar(name="2025",x=_xlabels,y=_y25[_mm_both].tolist(),
              marker_color=SLATE,opacity=0.8,
              text=[f"₪{v/1000:.0f}K" for v in _y25[_mm_both]],textposition="outside"))
            fig_yoy.add_trace(go.Bar(name="2026",x=_xlabels,y=_y26[_mm_both].tolist(),
              marker_color=BLUE,opacity=0.85,
              text=[f"₪{v/1000:.0f}K" for v in _y26[_mm_both]],textposition="outside"))
            fig_yoy.update_layout(**{**_PL,"height":280,"showlegend":True},
              barmode="group",
              title=dict(text="השוואת עלויות לפי חודש: 2025 vs 2026",
                          font=dict(size=13,color="#0F172A"),
                          x=0.02,xanchor="left",y=0.97),
              legend=dict(orientation="h",y=1.1,x=0),
              xaxis=dict(showgrid=False),
              yaxis=dict(tickprefix="₪",showgrid=True,gridcolor="#F1F5F9"))
            _chart(fig_yoy)
            _chg_arrow = '▲' if _chg_avg > 0 else '▼'
            st.caption(
              f"השינוי הממוצע ב-{len(_mm_both)} חודשים חופפים: "
              f"{_chg_arrow}{abs(_chg_avg):.1f}%. "
              f"חודשים: {', '.join(_xlabels)}."
            )

      # Client ranking — most profitable vs most problematic (item #17).
      # Side-by-side bars, top 10 each. Reads from _ca (already includes
      # margin_pct, profit, billing_amount). Skipped if no profit data.
    if HAS_PLOTLY and "profit" in _ca.columns and "margin_pct" in _ca.columns:
      with st.expander("🏆 דירוג לקוחות — רווחיים ובעייתיים", expanded=True):
        # Active clients only (those with actual billing).
        _ca_active = _ca[_ca["billing_amount"] > 0].copy()
        if not _ca_active.empty:
          _topp = _ca_active.nlargest(10, "profit")
          _botp = _ca_active.nsmallest(10, "margin_pct")

          _ccol1, _ccol2 = st.columns(2)
          with _ccol1:
            st.markdown(
              "<div style='font-size:12.5px;font-weight:800;color:#0E5A2E;"
              "margin-bottom:6px'>🟢 הלקוחות הכי רווחיים (₪)</div>",
              unsafe_allow_html=True)
            _topp_sorted = _topp.sort_values("profit", ascending=True)
            _names_t = [_clip(str(n),22) for n in _topp_sorted["client"]]
            _fig_topp = go.Figure(go.Bar(
              x=_topp_sorted["profit"], y=_names_t,
              orientation="h", marker_color="#16A34A", opacity=0.88,
              text=[f"₪{v/1000:.0f}K · {m:.0f}%"
                     for v,m in zip(_topp_sorted["profit"],
                                     _topp_sorted["margin_pct"])],
              textposition="outside", textfont=dict(size=11),
              customdata=_topp_sorted["client"],
              hovertemplate="<b>%{customdata}</b><br>"
                             "רווח: ₪%{x:,.0f}<extra></extra>"))
            _fig_topp.update_layout(
              **{**_PL,"height":max(280, len(_topp_sorted)*30),
                 "margin":dict(l=10,r=80,t=20,b=20)},
              xaxis=dict(tickprefix="₪", showgrid=True, gridcolor="#F1F5F9"),
              yaxis=dict(automargin=True))
            _chart(_fig_topp)
          with _ccol2:
            st.markdown(
              "<div style='font-size:12.5px;font-weight:800;color:#A32D2D;"
              "margin-bottom:6px'>🔴 דורשים תשומת לב (לפי מרג'ין)</div>",
              unsafe_allow_html=True)
            _botp_sorted = _botp.sort_values("margin_pct", ascending=False)
            _names_b = [_clip(str(n),22) for n in _botp_sorted["client"]]
            _bar_clrs = ["#DC2626" if m<0
                          else "#D97706" if m<10
                          else "#16A34A" for m in _botp_sorted["margin_pct"]]
            _fig_botp = go.Figure(go.Bar(
              x=_botp_sorted["margin_pct"], y=_names_b,
              orientation="h", marker_color=_bar_clrs, opacity=0.88,
              text=[f"{m:+.1f}% · ₪{r/1000:.0f}K"
                     for m,r in zip(_botp_sorted["margin_pct"],
                                     _botp_sorted["billing_amount"])],
              textposition="outside", textfont=dict(size=11),
              customdata=_botp_sorted["client"],
              hovertemplate="<b>%{customdata}</b><br>"
                             "מרג'ין: %{x:.1f}%<extra></extra>"))
            _fig_botp.update_layout(
              **{**_PL,"height":max(280, len(_botp_sorted)*30),
                 "margin":dict(l=10,r=80,t=20,b=20)},
              xaxis=dict(ticksuffix="%", showgrid=True, gridcolor="#F1F5F9"),
              yaxis=dict(automargin=True))
            _chart(_fig_botp)
          st.markdown(
            "<div style='font-size:11.5px;color:#64748B;line-height:1.5;"
            "margin-top:8px'>"
            "💡 בצד שמאל: 10 הלקוחות עם הרווח הגבוה ביותר. "
            "בצד ימין: 10 הלקוחות עם המרג'ין הנמוך ביותר — "
            "אדום = הפסד, כתום = מרג'ין נמוך (&lt;10%), ירוק = תקין. "
            "התווית מציגה גם את גובה ההכנסה (₪K) — לקוח רווחי נמוך "
            "עם הכנסה גבוהה הוא הסיכון העסקי הגדול ביותר."
            "</div>", unsafe_allow_html=True)

      # 2.1 — Heatmap לקוח × חודש
    if HAS_PLOTLY and "month" in df.columns and "client" in df.columns:
      with st.expander("🗺️ מפת חום: עלות לפי לקוח וחודש", expanded=True):
        # Uses _raw_hist so the heatmap shows full month history regardless
        # of the global date filter (otherwise a 1-month selection = 1 column).
        _hm = (_raw_hist.groupby(["client","month"])["cost"].sum()
               .unstack(fill_value=0))
        _hm_months = sorted(_hm.columns, key=_mkey)
        _hm = _hm[_hm_months].loc[_hm.sum(axis=1).sort_values(ascending=False).index]
        fig_hm = go.Figure(go.Heatmap(
          z=_hm.values, x=_hm_months, y=_hm.index.tolist(),
          colorscale="YlOrRd",
          hovertemplate="<b>%{y}</b><br>%{x}<br>₪%{z:,.0f}<extra></extra>",
          colorbar=dict(title="₪",tickprefix="₪",
                         thickness=14,len=0.85,
                         tickfont=dict(size=10,color="#475569"))
        ))
        fig_hm.update_layout(
            **{**_PL,
               "height":max(320,len(_hm)*24),
               "margin":dict(l=10,r=14,t=50,b=40)},
            title=dict(text="מפת חום: עלות לפי לקוח וחודש",
                        font=dict(size=13,color="#0F172A"),
                        x=0.02,xanchor="left",y=0.97),
            xaxis=dict(tickangle=-30, title="חודש",
                        tickfont=dict(size=10)),
            yaxis=dict(automargin=True, title="לקוח",
                        tickfont=dict(size=10)))
        _chart(fig_hm)
        st.caption("תאים כהים = עלות גבוהה. מאפשר לזהות מגמות עונתיות ולקוחות עם שינויים חדים.")

      # 4.1 — Pareto Analysis (routed to Employees sub-tab)
    with _TAB_EMPLOYEES:
      if isinstance(_ea,pd.DataFrame) and not _ea.empty and HAS_PLOTLY:
        with st.expander("📊 ניתוח פארטו — איפה מרוכזת העלות?", expanded=True):
          # 80/20 stats are computed on the FULL employee universe so the
          # insight ("X עובדים אחראים ל-80%") always reflects reality.
          # The CHART, however, only plots Top N to stay readable when there
          # are 200+ employees (per user feedback).
          _par_all = _ea.sort_values("עלות",ascending=False).copy().reset_index(drop=True)
          _par_all["cumsum"] = _par_all["עלות"].cumsum()
          _par_all["cumpct"] = _par_all["cumsum"]/_par_all["עלות"].sum()*100
          _par_all["rank"]   = range(1,len(_par_all)+1)
          _idx80_mask = _par_all["cumpct"] >= 80
          _emps_80 = int(_par_all[_idx80_mask].index[0])+1 if _idx80_mask.any() else len(_par_all)
          _pct_emps_80 = _emps_80/max(len(_par_all),1)*100

          # Top-N selector — keeps the chart readable.
          _tn_col1, _tn_col2 = st.columns([1, 3])
          with _tn_col1:
            _topn_label = st.radio(
              "כמות עובדים בגרף",
              options=["Top 20", "Top 50", "Top 100", "הכל"],
              index=1, horizontal=True, key="pareto_topn",
              label_visibility="collapsed",
            )
          _topn_map = {"Top 20":20, "Top 50":50, "Top 100":100,
                        "הכל":len(_par_all)}
          _topn = _topn_map.get(_topn_label, 50)
          _par = _par_all.head(_topn).copy()
          with _tn_col2:
            st.markdown(
              f'<div style="font-size:11.5px;color:#64748B;padding-top:6px;'
              f'text-align:left">מציג <b>{len(_par)}</b> מתוך {len(_par_all)} '
              f'עובדים. הסטטיסטיקה למטה מחושבת על כל ה-{len(_par_all)} '
              f'עובדים.</div>',
              unsafe_allow_html=True)

          fig_par=go.Figure()
          fig_par.add_trace(go.Bar(x=_par["rank"],y=_par["עלות"],
            marker_color=BLUE,opacity=0.65,name="עלות",
            hovertemplate="עובד #%{x}<br>₪%{y:,.0f}<extra></extra>"))
          fig_par.add_trace(go.Scatter(x=_par["rank"],y=_par["cumpct"],
            mode="lines",
            line=dict(color=RED,width=2),name="% מצטבר",yaxis="y2",
            hovertemplate="#%{x}: %{y:.1f}%<extra></extra>"))
          fig_par.add_hline(y=80,line_dash="dash",line_color=AMBER,
                             annotation_text="80%",yref="y2")
          fig_par.update_layout(**{**_PL,"height":320,"showlegend":True},
            xaxis=dict(title=f"דירוג עובד (מהיקר ל-{len(_par)})"),
            yaxis=dict(title="עלות",tickprefix="₪"),
            yaxis2=dict(title="% מצטבר",overlaying="y",side="right",
                         range=[0,105],ticksuffix="%"),
            legend=dict(orientation="h",y=1.1))
          _chart(fig_par)
          st.markdown(
            "<div style='font-size:11.5px;color:#64748B;line-height:1.55'>"
            "📘 <b>חוק פארטו (80/20):</b> ברוב הארגונים, חלק קטן מהעובדים מייצר "
            "את רוב העלות. עמודות = עלות לעובד · קו אדום = אחוז מצטבר · "
            "הקו המקווקו ב-80% מציין את נקודת ה-80/20."
            "</div>",
            unsafe_allow_html=True)
          # Headline insight card
          _pareto_color = "red" if _pct_emps_80 < 20 else "green"
          _pareto_msg = (
            "ריכוז גבוה — סיכון על מספר עובדים קטן"
            if _pct_emps_80 < 20
            else "פיזור בריא — אין תלות בעובד אחד"
          )
          st.markdown(
            f'<div class="focus {_pareto_color}">'
            f'📊 <b>{_emps_80}</b> עובדים ({_pct_emps_80:.0f}% מהצוות) '
            f'אחראים ל-80% מהעלות. {_pareto_msg}.'
            f'</div>',
            unsafe_allow_html=True,
          )

      # 4.3 — Box plot לפי מדינה (routed to Countries sub-tab)
    with _TAB_COUNTRIES:
      if "country" in df.columns and HAS_PLOTLY:
        with st.expander("🌍 פיזור עלות לשעה לפי מדינה", expanded=True):
          _fig_box=go.Figure()
          for _cnt in sorted(df["country"].dropna().unique()):
            _vals=df[df["country"]==_cnt]["cost_per_hour"].dropna()
            _vals=_vals[_vals>0]
            if len(_vals)>0:
              _fig_box.add_trace(go.Box(y=_vals,name=_cnt,boxmean=True))
          _fig_box.update_layout(**{**_PL,"height":300,"showlegend":False},
            yaxis=dict(title="עלות לשעה",tickprefix="₪"))
          _chart(_fig_box)
          st.caption("מציג פיזור (חציון, רבעונים, חריגים) של עלות/שעה בכל מדינה.")

      # 4.4 — Sites charts (routed to Sites sub-tab)
    with _TAB_SITES:
      if "site" in df.columns and HAS_PLOTLY:
        with st.expander("🏗️ אתרים יקרים ביותר", expanded=True):
          _st_cost = (df.groupby("site",as_index=False)
                        .agg(עלות=("cost","sum"))
                        .nlargest(15,"עלות")
                        .sort_values("עלות",ascending=True))
          if not _st_cost.empty:
            _chart(_bar_h(_st_cost["עלות"], _st_cost["site"],
                          [BLUE]*len(_st_cost),
                          [f"₪{v/1000:.0f} אלף" for v in _st_cost["עלות"]]))
            st.caption("15 האתרים שצברו את העלות הגבוהה ביותר. מסומן בכחול — לזיהוי מוקדי הוצאה.")
          else:
            st.info("אין נתוני אתרים בטווח הנבחר")
        with st.expander("⏱️ פיזור שעות לפי אתר", expanded=True):
          _st_hrs = (df.groupby("site",as_index=False)
                       .agg(שעות=("total_hours","sum"))
                       .nlargest(15,"שעות")
                       .sort_values("שעות",ascending=True))
          if not _st_hrs.empty:
            _chart(_bar_h(_st_hrs["שעות"], _st_hrs["site"],
                          [PURP]*len(_st_hrs),
                          [f"{v:,.0f}h" for v in _st_hrs["שעות"]]))
            st.caption("15 האתרים העמוסים ביותר לפי סך שעות עבודה. עוזר לזהות איפה מרוכז כוח האדם.")
          else:
            st.info("אין נתוני שעות לפי אתר")

      # ── תחזית חודש הבא: הוסר ב-2026-05 — שכפול עם t8.6 (ממוצע משוקלל 6ח') ──

  # ══════════════════════════════════════════════════════════════════════════════
  # 3 — טבלאות
  # ══════════════════════════════════════════════════════════════════════════════
with t3:
  _FMT={"עלות":"₪{:,.0f}","שעות":"{:,.0f}","עלות/שעה":"₪{:.1f}","% עלות":"{:.1f}%","avg_cph":"₪{:.1f}"}

  def _drop_empty_cols(d):
    """Remove numeric cost columns that are all-zero. Never removes text/identifier columns."""
    keep = []
    for c in d.columns:
      col = d[c]
      if pd.api.types.is_numeric_dtype(col):
        if col.fillna(0).eq(0).all():
          continue
      keep.append(c)
    return d[keep]

  def _ot_cols_display(d):
    ot_rn={f"pct_{c}":f"שעות {OT_LABELS[c]}" for c in _avail_ot}
    ot_fm={f"שעות {OT_LABELS[c]}":"{:.1f}%" for c in _avail_ot}
    cols=[c for c in [f"pct_{c}" for c in _avail_ot] if c in d.columns]
    return d.rename(columns=ot_rn), {**_FMT, **ot_fm}

  def _column_picker(d, key):
    """Render a popover with checkbox-multiselect for column visibility.

    Pipeline:
      1. Translate technical English column names → Hebrew via _HEB_COL
         (idempotent — already-Hebrew columns pass through unchanged).
      2. Clean None/NaN in text columns (numeric kept as NaN so formatters
         can render via na_rep).
      3. Render the popover; user picks which of the translated columns
         to show. All visible by default. Empty selection → show all.
    """
    if d is None or d.empty:
      return d
    # Step 1+2: translate + clean
    d = _heb_cols(_clean_none(d))
    all_cols = list(d.columns)
    with st.popover(f"🎛️ עמודות ({len(all_cols)})", use_container_width=False):
      st.caption("בטל סימון להסתרת עמודות. ברירת המחדל: כל העמודות.")
      sel = st.multiselect(
        "עמודות פעילות",
        options=all_cols,
        default=all_cols,
        key=key,
        label_visibility="collapsed",
      )
    if not sel:
      return d
    return d[[c for c in all_cols if c in sel]]

  def _with_total_row(d, *, label='סה"כ', label_col=None,
                      empty_cols=(), recalc=None):
    """Append a 'סה\"כ' row. Numeric cols are summed by default.
    - label_col: where to write the 'סה\"כ' label (default: first non-numeric col)
    - empty_cols: columns to leave blank in the totals row
    - recalc: dict {col_name: callable(df) -> value} to override the sum
              (used for weighted averages like cost/hour, margin%, %-of-total)
    NOTE: numeric columns that should be blank in the totals row use pd.NA
          (not ""), so pandas formatters like '{:,.0f}' don't crash. The
          styler must use na_rep='' to render NA as empty (see _styled).
    """
    if d is None or d.empty:
      return d
    if label_col is None:
      for c in d.columns:
        if not pd.api.types.is_numeric_dtype(d[c]):
          label_col = c
          break
    row = {}
    for c in d.columns:
      _is_num = pd.api.types.is_numeric_dtype(d[c])
      if c == label_col:
        row[c] = label
      elif c in empty_cols:
        # Numeric columns need NA (so '{:,.0f}'.format() doesn't crash);
        # text columns can use empty string safely.
        row[c] = pd.NA if _is_num else ""
      elif recalc and c in recalc:
        try: row[c] = recalc[c](d)
        except Exception: row[c] = pd.NA if _is_num else ""
      elif _is_num:
        try: row[c] = float(d[c].sum())
        except Exception: row[c] = pd.NA
      else:
        row[c] = ""
    return pd.concat([d, pd.DataFrame([row])], ignore_index=True)

  def _hl_total_row(styler):
    """Bold + gray background + top-border for the LAST row of the styler."""
    if styler.data is None or len(styler.data) == 0:
      return styler
    _last = len(styler.data) - 1
    def _hl(s):
      if s.name == _last:
        return ["background:#E2E8F0;font-weight:800;color:#0F172A;"
                "border-top:2px solid #64748B"] * len(s)
      return [""] * len(s)
    return styler.apply(_hl, axis=1)

  # Sub-tabs s1-s5 are already created above (eagerly inside `with t_tables:`)
  # so this old `st.tabs([...])` line is REMOVED to avoid duplicates.
  # The `with s1:` / `with s2:` / etc. blocks below still work because s1-s5
  # are module-level variables.

  with s1_top:
    with st.expander("טבלת לקוחות", expanded=True):
      _tc1,_tc2=st.columns([6,1])
      with _tc2: _show_all_c=st.toggle("הצג הכל",key="tog_c")
      _base_cols=["#","client","עובדים","שעות","ימים","עלות","שכר ברוטו","ביטוח לאומי","פנסיה","אגרות",
                  "חופשה/מחלה","פיצויים","ביטוח רפואי","היטל תעסוקה","קרן עידוד","פיקדון","אחר",
                  "avg_cph","% עלות","מגמה","ot_status"]
      if _has_profit: _base_cols += ["billing_amount","profit","margin_pct"]
      _ot_extra=[f"pct_{c}" for c in _avail_ot]
      _cd_full=_ca[[c for c in _base_cols+_ot_extra if c in _ca.columns]].copy()
      if "ot_status" in _cd_full.columns:
        _cd_full["ot_status"] = _cd_full["ot_status"].map(_status_pill)
      _cd_full = _cd_full.rename(
        columns={"client":"לקוח","avg_cph":"עלות/שעה","ot_status":"סטטוס",
                 "billing_amount":"הכנסה","profit":"רווח","margin_pct":"מרג'ין %"})
      _cd_full = _drop_empty_cols(_cd_full)
      _cd = _cd_full if _show_all_c else _cd_full.head(10)
      _cd2,_fmt2=_ot_cols_display(_cd)
      _fmt2.update({"ימים":"{:,.0f}","שכר ברוטו":"₪{:,.0f}","ביטוח לאומי":"₪{:,.0f}",
                    "פנסיה":"₪{:,.0f}","אגרות":"₪{:,.0f}","חופשה/מחלה":"₪{:,.0f}",
                    "פיצויים":"₪{:,.0f}","ביטוח רפואי":"₪{:,.0f}","היטל תעסוקה":"₪{:,.0f}",
                    "קרן עידוד":"₪{:,.0f}","פיקדון":"₪{:,.0f}","אחר":"₪{:,.0f}"})
      if _has_profit:
        _fmt2.update({"הכנסה":"₪{:,.0f}","רווח":"₪{:,.0f}","מרג'ין %":"{:.1f}%"})
      # Column picker — affects display only; CSV export below uses _cd_full (all cols)
      _cd2 = _column_picker(_cd2, "cols_clients")
      # Totals row — appended after picker so it matches the visible columns
      _ot_pct_names_c = {f"שעות {OT_LABELS[c]}" for c in _avail_ot}
      _cd2 = _with_total_row(_cd2,
        label_col="לקוח",
        empty_cols={"#","מגמה","סטטוס"} | _ot_pct_names_c,
        recalc={
          "עלות/שעה": lambda d: (d["עלות"].sum()/d["שעות"].sum()) if "שעות" in d.columns and d["שעות"].sum()>0 else 0,
          "מרג'ין %": lambda d: (d["רווח"].sum()/d["הכנסה"].sum()*100) if "הכנסה" in d.columns and d["הכנסה"].sum()>0 else 0,
          "% עלות":  lambda d: 100.0,
          "עובדים":  lambda d: NE,
        })
      _sty=_styled(_cd2,_fmt2,hi_col="עלות/שעה",hi_thresh=CPH*1.15)
      if _has_profit and "רווח" in _cd2.columns:
        def _neg_profit(v):
          try: return "background:#FEF2F2;color:#7F1D1D;font-weight:700" if float(v)<0 else ""
          except: return ""
        _sty=_sty.map(_neg_profit,subset=["רווח"])
      if "סטטוס" in _cd2.columns:
        def _pill_style(v):
          if "קריטי" in str(v): return "background:#FEE2E2;color:#7F1D1D;font-weight:700"
          if "לטיפול" in str(v): return "background:#FEF3C7;color:#78350F;font-weight:700"
          return "background:#D1FAE5;color:#14532D;font-weight:600"
        _sty=_sty.map(_pill_style,subset=["סטטוס"])
      _sty = _hl_total_row(_sty)
      _sel=st.dataframe(_sty,use_container_width=True,hide_index=True,
             height=min(600,42+len(_cd2)*35),
             on_select="rerun",selection_mode="single-row",key="clients_table")
      if _sel and _sel.selection.rows:
        _ri=_sel.selection.rows[0]
        # Skip selection if user clicked the totals row (last row, beyond _cd_full)
        if _ri >= len(_cd_full):
          _picked = ""
        else:
          _picked=_cd_full.iloc[_ri].get("לקוח","") or _cd_full.iloc[_ri].get("client","")
        if _picked:
          _cld=df[df["client"]==_picked]
          _ce1,_ce2,_ce3=st.columns(3)
          _ce1.metric("עלות הלקוח",f"₪{_cld['cost'].sum()/1000:.0f}K")
          _ce2.metric("שעות",f"{_cld['total_hours'].sum():,.0f}")
          _ce3.metric("עובדים",f"{_cld['employee_id'].nunique()}")
      st.download_button("⬇️ הורד טבלה (CSV)",_cd_full.to_csv(index=False,encoding="utf-8-sig"),
                f"clients_{_rl}.csv","text/csv")

      # ── פיצ'ר 2: מגמת מרג'ין ────────────────────────────────────────────────
      if "billing_amount" in _raw_hist.columns and "margin_pct" in _raw_hist.columns and HAS_PLOTLY:

        with st.expander("מגמת מרג'ין לאורך זמן", expanded=True):
          # Uses _raw_hist so margin trend covers full history even when the
          # global date filter is set to a single month.
          _margin_matrix = (_raw_hist.drop_duplicates(["month","client"])
                              .pivot_table(index="client", columns="month",
                                           values="margin_pct", aggfunc="first"))
          _months_mm = sorted(_margin_matrix.columns, key=_mkey)
          _margin_matrix = _margin_matrix.reindex(columns=_months_mm)

          _top_cl_margin = (_raw_hist.drop_duplicates(["month","client"])
                              .groupby("client")["billing_amount"].sum()
                              .nlargest(15).index.tolist())
          _top_cl_margin = [c for c in _top_cl_margin if c in _margin_matrix.index]
          if _top_cl_margin:
            _margin_matrix = _margin_matrix.loc[_top_cl_margin]

          if not _margin_matrix.empty:
            import numpy as _np_mm
            # Clip extreme outliers to a sane display range. Real margins live
            # in roughly [-100%, +60%]; values like -884% almost always come
            # from a near-zero billing denominator and are not meaningful for
            # a heat map. We keep the raw value in the hover tooltip ("ערך
            # גולמי") but the color scale + on-cell text use a clipped view.
            _z_raw = _margin_matrix.values.tolist()
            _CLIP_MIN, _CLIP_MAX = -100.0, 60.0
            def _clip_val(v):
              if v is None: return float("nan")
              fv = float(v)
              if _np_mm.isnan(fv): return fv
              return max(_CLIP_MIN, min(_CLIP_MAX, fv))
            _z = [[_clip_val(v) for v in row] for row in _z_raw]
            # On-cell text — show clipped %, but mark outliers with "⚠ חישוב לא תקין"
            def _cell_text(raw):
              if raw is None: return ""
              fv = float(raw)
              if _np_mm.isnan(fv): return ""
              if fv < _CLIP_MIN or fv > _CLIP_MAX: return "⚠"
              return f"{fv:.0f}%"
            _txt = [[_cell_text(v) for v in row] for row in _z_raw]
            # Custom data passes the RAW value into the hovertemplate so the
            # user can see the actual underlying number, including outliers.
            _cd_raw = [[("—" if (v is None or _np_mm.isnan(float(v)))
                          else f"{float(v):.1f}%") for v in row] for row in _z_raw]
            fig_mm = go.Figure(go.Heatmap(
                z=_z, x=_months_mm, y=_margin_matrix.index.tolist(),
                customdata=_cd_raw,
                colorscale=[[0,"#DC2626"],[0.3,"#F59E0B"],[0.5,"#FCD34D"],
                            [0.7,"#84CC16"],[1,"#059669"]],
                zmid=15, zmin=_CLIP_MIN, zmax=_CLIP_MAX,
                text=_txt, texttemplate="%{text}",
                textfont={"size":10},
                hovertemplate=("<b>%{y}</b><br>%{x}<br>"
                                "מרג'ין: %{customdata}<extra></extra>"),
                colorbar=dict(title="מרג'ין %", ticksuffix="%",
                               thickness=14,len=0.85,
                               tickfont=dict(size=10,color="#475569")),
            ))
            fig_mm.update_layout(
                **{**_PL,
                   "height":max(320, len(_margin_matrix)*30),
                   "margin":dict(l=10,r=14,t=50,b=40)},
                title=dict(text="מגמת מרג'ין: לקוח × חודש",
                            font=dict(size=13,color="#0F172A"),
                            x=0.02,xanchor="left",y=0.97),
                xaxis=dict(tickangle=-30, title="חודש", tickfont=dict(size=10)),
                yaxis=dict(automargin=True, autorange="reversed",
                            title="לקוח", tickfont=dict(size=10)))
            _chart(fig_mm)
            st.caption(
              "🟢 ירוק = מרג'ין גבוה (≥30%) · 🟡 צהוב = בינוני (15%) · "
              "🔴 אדום = הפסד · ⬜ ריק = לקוח לא פעיל בחודש זה · "
              "⚠ = ערך קיצוני (חיוב <1₪) — לא תקין לחישוב."
            )

            # ירידת מרג'ין
            _declining_mm = []
            for _cli in _margin_matrix.index:
              _vals_mm = _margin_matrix.loc[_cli].dropna()
              if len(_vals_mm) >= 4:
                _early = float(_vals_mm.head(3).mean())
                _recent = float(_vals_mm.tail(3).mean())
                if _recent < _early - 5:
                  _declining_mm.append((_cli, _early, _recent, _recent - _early))
            if _declining_mm:
              st.warning("⚠️ לקוחות עם ירידת מרג'ין (>5 נק' מתחילת תקופה לסופה):")
              for _cli, _e, _r, _ch in _declining_mm:
                st.markdown(f"- **{_cli}**: {_e:.1f}% → {_r:.1f}% ({_ch:+.1f} נקודות)")

  with s2_top:
    with st.expander("טבלת עובדים (עובד × לקוח × אתר)", expanded=True):
      if isinstance(_ea_detail,pd.DataFrame) and not _ea_detail.empty:
        _te1,_te2=st.columns([6,1])
        with _te2: _show_all_e=st.toggle("הצג הכל",key="tog_e")
        _e_cols=["#","employee_id","employee_name","לקוח","אתר","ימים","שעות","עלות",
                 "שכר ברוטו","ביטוח לאומי","פנסיה","אגרות",
                 "חופשה/מחלה","פיצויים","ביטוח רפואי","היטל תעסוקה","קרן עידוד","פיקדון","אחר",
                 "avg_cph","cost_driver","% עלות"]
        _ot_e=[c for c in _avail_ot if c in _ea_detail.columns]
        _ea_disp=_ea_detail if _show_all_e else _ea_detail.head(20)
        _ed=_ea_disp[[c for c in _e_cols+_ot_e if c in _ea_disp.columns]].rename(
          columns={"employee_id":"מס' עובד","employee_name":"שם","avg_cph":"עלות/שעה","cost_driver":"מצב"})
        if "מצב" in _ed.columns:
          _ed["מצב"] = _ed["מצב"].apply(lambda v: "—" if str(v)=="תקין" else v)
        _ed = _drop_empty_cols(_ed)
        _ed2,_fmt3=_ot_cols_display(_ed)
        _ot_fmt_e={OT_LABELS[c]:"{:,.0f}" for c in _avail_ot if OT_LABELS[c] in _ed2.columns}
        _fmt3.update({"ימים":"{:,.0f}","שכר ברוטו":"₪{:,.0f}","ביטוח לאומי":"₪{:,.0f}",
                      "פנסיה":"₪{:,.0f}","אגרות":"₪{:,.0f}","חופשה/מחלה":"₪{:,.0f}",
                      "פיצויים":"₪{:,.0f}","ביטוח רפואי":"₪{:,.0f}","היטל תעסוקה":"₪{:,.0f}",
                      "קרן עידוד":"₪{:,.0f}","פיקדון":"₪{:,.0f}","אחר":"₪{:,.0f}"})
        # Column picker — affects display only; CSV keeps all columns
        _ed2_disp = _column_picker(_ed2, "cols_employees")
        # Totals row for employees — # is empty; cost/hour weighted; % עלות = 100
        _ed2_disp = _with_total_row(_ed2_disp,
          label_col="שם",
          empty_cols={"#","מס' עובד","לקוח","אתר","מצב"},
          recalc={
            "עלות/שעה": lambda d: (d["עלות"].sum()/d["שעות"].sum()) if "שעות" in d.columns and d["שעות"].sum()>0 else 0,
            "% עלות":  lambda d: 100.0,
          })
        _sty_e = _styled(_ed2_disp,{**_fmt3,**_ot_fmt_e},hi_col="עלות/שעה",hi_thresh=CPH*1.15)
        _sty_e = _hl_total_row(_sty_e)
        st.dataframe(_sty_e,
               use_container_width=True,hide_index=True,
               height=min(600,42+len(_ed2_disp)*35))
        st.download_button("⬇️ הורד טבלה (CSV)",_ed2.to_csv(index=False,encoding="utf-8-sig"),
                  f"employees_{_rl}.csv","text/csv")

      # ── Employee drill-down (item #16) — pick one employee, see their
      # full breakdown across clients/sites/months on demand. Closed by
      # default so the table above stays the primary view.
      if isinstance(_ea, pd.DataFrame) and not _ea.empty and "employee_id" in df.columns:
        with st.expander("🔍 דריל-דאון לעובד יחיד", expanded=False):
          # Build a "Name (#id)" label so the picker stays human-readable
          _emp_options = _ea.copy()
          if "employee_name" in _emp_options.columns:
            _emp_options["_lbl"] = (
              _emp_options["employee_name"].fillna("").astype(str)
              + " (#" + _emp_options["employee_id"].astype(str) + ")"
            )
          else:
            _emp_options["_lbl"] = _emp_options["employee_id"].astype(str)
          _emp_lbl_to_id = dict(zip(_emp_options["_lbl"], _emp_options["employee_id"]))
          _picked = st.selectbox(
            "בחר עובד",
            options=list(_emp_lbl_to_id.keys()),
            key="emp_drilldown_pick",
          )
          if _picked:
            _eid = _emp_lbl_to_id[_picked]
            _emp_df = df[df["employee_id"] == _eid].copy()
            if not _emp_df.empty:
              # KPIs for this employee
              _e_hours = float(_emp_df["total_hours"].sum())
              _e_cost  = float(_emp_df["cost"].sum())
              _e_cph   = _e_cost / _e_hours if _e_hours > 0 else 0
              _e_clients = _emp_df["client"].nunique() if "client" in _emp_df.columns else 0
              _e_sites   = _emp_df["site"].nunique()   if "site"   in _emp_df.columns else 0
              _e_months  = _emp_df["month"].nunique()  if "month"  in _emp_df.columns else 0
              _ek1, _ek2, _ek3, _ek4, _ek5 = st.columns(5)
              _ek1.metric("שעות",      f"{_e_hours:,.0f}")
              _ek2.metric("עלות",      f"₪{_e_cost:,.0f}")
              _ek3.metric("עלות/שעה",  f"₪{_e_cph:.2f}")
              _ek4.metric("לקוחות",    _e_clients)
              _ek5.metric("חודשים",   _e_months)
              # Per (client/site/month) breakdown
              st.markdown("##### פירוט לפי לקוח · אתר · חודש")
              _e_grp_keys = [c for c in ("client","site","month") if c in _emp_df.columns]
              if _e_grp_keys:
                _e_brk = (_emp_df.groupby(_e_grp_keys, as_index=False)
                            .agg(שעות=("total_hours","sum"),
                                 עלות=("cost","sum"),
                                 ימים=("work_days","sum")))
                _e_brk["עלות/שעה"] = (_e_brk["עלות"] /
                                       _e_brk["שעות"].replace(0,float("nan"))
                                       ).fillna(0).round(2)
                # Translate columns FIRST, THEN pick the sort key from the
                # translated frame. The old one-liner evaluated the ternary
                # against the pre-translation columns (which still contained
                # "client"), then called .sort_values on the post-translation
                # frame — which had renamed "client"→"לקוח" — causing KeyError.
                _e_brk = _heb_cols(_e_brk)
                _sort_col = ("חודש" if "חודש" in _e_brk.columns
                              else _e_brk.columns[0])
                _e_brk = _e_brk.sort_values(_sort_col)
                # Total row: weighted CPH instead of sum, label in first text col
                _e_brk_t = _with_total_row(
                  _e_brk,
                  recalc={"עלות/שעה": lambda x: (x["עלות"].sum()/x["שעות"].sum())
                                                if x["שעות"].sum()>0 else 0},
                )
                _e_brk_sty = _e_brk_t.style.format(
                  {"שעות":"{:,.1f}", "עלות":"₪{:,.0f}",
                   "עלות/שעה":"₪{:,.2f}", "ימים":"{:,.0f}"},
                  na_rep="")
                _e_brk_sty = _hl_total_row(_e_brk_sty)
                st.dataframe(_e_brk_sty, use_container_width=True,
                              hide_index=True,
                              height=min(390, 42 + len(_e_brk_t)*35))
              # OT breakdown (h100..h200) if available
              _ot_avail_e = [c for c in OT_LEVELS if c in _emp_df.columns]
              if _ot_avail_e:
                _ot_sums = {OT_LABELS[c]: float(_emp_df[c].sum()) for c in _ot_avail_e}
                _h100_e  = float(_emp_df.get("h100", pd.Series([0])).sum())
                st.markdown("##### שעות לפי רמה")
                _ot_df = pd.DataFrame([
                  {"רמה":"100% רגיל", "שעות": _h100_e},
                  *[{"רמה":k, "שעות": v} for k,v in _ot_sums.items() if v > 0],
                ])
                _ot_df["% מסך"] = (_ot_df["שעות"] / max(_e_hours,1) * 100).round(1)
                # Append סה"כ row — sum hours, % becomes 100
                _ot_df_t = _with_total_row(
                  _ot_df,
                  recalc={"% מסך": lambda x: 100.0},
                )
                _ot_sty = _ot_df_t.style.format(
                  {"שעות":"{:,.1f}", "% מסך":"{:.1f}%"})
                _ot_sty = _hl_total_row(_ot_sty)
                st.dataframe(_ot_sty, use_container_width=True,
                              hide_index=True)

  def _dim_table(col,label):
    if col not in df.columns: st.info(f"אין עמודת {label}"); return
    _g=(df.groupby(col,as_index=False).agg(
        עלות=("cost","sum"),שעות=("total_hours","sum"),
        עובדים=("employee_id","nunique"))
      .sort_values("עלות",ascending=False).reset_index(drop=True))
    # Weighted CPH per dimension — was simple mean (biased)
    _g["avg_cph"]=(_g["עלות"]/_g["שעות"].replace(0,float("nan"))).fillna(0).round(2)
    _g["% עלות"]=(_g["עלות"]/_g["עלות"].sum()*100).round(1)
    _g.insert(0,"#",range(1,len(_g)+1))
    _g=_g.rename(columns={col:label,"avg_cph":"עלות/שעה"})
    _g=_drop_empty_cols(_g)
    # Totals row — sum cost/hours, weighted CPH, employees = NE, %=100
    _g_t = _with_total_row(
      _g,
      label_col=label,
      empty_cols={"#"},
      recalc={
        "עלות/שעה": lambda x: (x["עלות"].sum()/x["שעות"].sum())
                                if "שעות" in x.columns and x["שעות"].sum()>0 else 0,
        "% עלות":   lambda x: 100.0,
        "עובדים":   lambda x: NE,
      })
    _sty = _styled(_g_t,_FMT,hi_col="עלות/שעה",hi_thresh=CPH*1.15)
    _sty = _hl_total_row(_sty)
    st.dataframe(_sty,
           use_container_width=True,hide_index=True,height=min(600,42+len(_g_t)*35))
    # CSV export keeps the ORIGINAL data (no totals row) — keeps export clean.
    st.download_button(f"⬇️ הורד טבלה (CSV)",_g.to_csv(index=False,encoding="utf-8-sig"),
              f"{col}_{_rl}.csv","text/csv")

  with s3_top:
    with st.expander("טבלת אתרים (אתר × לקוח)", expanded=True):
      _grp_site = ["site"]
      if "client" in df.columns: _grp_site.append("client")
      _sa_agg = dict(עלות=("cost","sum"),שעות=("total_hours","sum"),
                     ימים=("work_days","sum"),עובדים=("employee_id","nunique"))
                     # avg_cph removed — recomputed weighted below after groupby
      if "gross_salary_alloc"      in df.columns: _sa_agg["שכר ברוטו"]   =("gross_salary_alloc","sum")
      if "bituach_alloc"           in df.columns: _sa_agg["ביטוח לאומי"] =("bituach_alloc","sum")
      if "pension_alloc"           in df.columns: _sa_agg["פנסיה"]        =("pension_alloc","sum")
      if "adjusted_levy_alloc"     in df.columns: _sa_agg["אגרות"]        =("adjusted_levy_alloc","sum")
      if "vacation_fund_alloc"     in df.columns: _sa_agg["חופשה/מחלה"]  =("vacation_fund_alloc","sum")
      if "severance_alloc"         in df.columns: _sa_agg["פיצויים"]      =("severance_alloc","sum")
      if "medical_insurance_alloc" in df.columns: _sa_agg["ביטוח רפואי"] =("medical_insurance_alloc","sum")
      if "employment_levy_alloc"   in df.columns: _sa_agg["היטל תעסוקה"] =("employment_levy_alloc","sum")
      if "incentive_fund_alloc"    in df.columns: _sa_agg["קרן עידוד"]    =("incentive_fund_alloc","sum")
      if "savings_deposit_alloc"   in df.columns: _sa_agg["פיקדון"]       =("savings_deposit_alloc","sum")
      if "other_alloc"             in df.columns: _sa_agg["אחר"]          =("other_alloc","sum")
      _sa = (df.groupby(_grp_site,as_index=False).agg(**_sa_agg)
               .sort_values("עלות",ascending=False).reset_index(drop=True))
      # Weighted CPH per site (was simple-mean: biased by short-shift rows)
      _sa["avg_cph"]=(_sa["עלות"]/_sa["שעות"].replace(0,float("nan"))).fillna(0).round(2)
      _sa["% עלות"] = (_sa["עלות"]/_sa["עלות"].sum()*100).round(1)
      _sa.insert(0,"#",range(1,len(_sa)+1))
      # ── Mapping check — flag sites that look like raw / unmatched ────
      # Heuristics: site name is empty / "Unknown" / falls through to client
      # name verbatim. Also flag sites with NO billing match (no agreement).
      def _site_mapping_flag(row):
        _s = str(row.get("site","")).strip()
        if not _s or _s in ("Unknown","unknown","—","-"):
          return "⚠ חסר מיפוי"
        # If the site name === client name verbatim → likely a fallback
        if "client" in row.index and str(row.get("client","")).strip() == _s:
          return "🟡 כפול ללקוח"
        return "✓ ממופה"
      if "site" in _sa.columns:
        _sa["מיפוי"] = _sa.apply(_site_mapping_flag, axis=1)
      _sa = _sa.rename(columns={"site":"אתר","client":"לקוח","avg_cph":"עלות/שעה"})
      _sa = _drop_empty_cols(_sa)
      _sa_fmt = {**_FMT,"ימים":"{:,.0f}","שכר ברוטו":"₪{:,.0f}","ביטוח לאומי":"₪{:,.0f}",
                 "פנסיה":"₪{:,.0f}","אגרות":"₪{:,.0f}","חופשה/מחלה":"₪{:,.0f}",
                 "פיצויים":"₪{:,.0f}","ביטוח רפואי":"₪{:,.0f}","היטל תעסוקה":"₪{:,.0f}",
                 "קרן עידוד":"₪{:,.0f}","פיקדון":"₪{:,.0f}","אחר":"₪{:,.0f}"}
      # Column picker — affects display only; CSV keeps all columns
      _sa_disp = _column_picker(_sa, "cols_sites")
      _sa_disp = _with_total_row(_sa_disp,
        label_col="אתר",
        empty_cols={"#","לקוח"},
        recalc={
          "עלות/שעה": lambda d: (d["עלות"].sum()/d["שעות"].sum()) if "שעות" in d.columns and d["שעות"].sum()>0 else 0,
          "% עלות":  lambda d: 100.0,
          "עובדים":  lambda d: NE,
        })
      _sty_s = _styled(_sa_disp,_sa_fmt,hi_col="עלות/שעה",hi_thresh=CPH*1.15)
      _sty_s = _hl_total_row(_sty_s)
      st.dataframe(_sty_s,
             use_container_width=True,hide_index=True,height=min(600,42+len(_sa_disp)*35))
      st.download_button("⬇️ הורד טבלה (CSV)",_sa.to_csv(index=False,encoding="utf-8-sig"),
                f"sites_{_rl}.csv","text/csv")
  with s4_top:
    with st.expander("טבלת מדינות", expanded=True):
      if "country" not in df.columns:
        st.info("אין עמודת country בנתונים")
      else:
        _co_agg = dict(עלות=("cost","sum"),שעות=("total_hours","sum"),
                       ימים=("work_days","sum"),עובדים=("employee_id","nunique"))
                       # avg_cph recomputed weighted below after groupby
        if "gross_salary_alloc"      in df.columns: _co_agg["שכר ברוטו"]   =("gross_salary_alloc","sum")
        if "bituach_alloc"           in df.columns: _co_agg["ביטוח לאומי"] =("bituach_alloc","sum")
        if "pension_alloc"           in df.columns: _co_agg["פנסיה"]        =("pension_alloc","sum")
        if "adjusted_levy_alloc"     in df.columns: _co_agg["אגרות"]        =("adjusted_levy_alloc","sum")
        if "vacation_fund_alloc"     in df.columns: _co_agg["חופשה/מחלה"]  =("vacation_fund_alloc","sum")
        if "severance_alloc"         in df.columns: _co_agg["פיצויים"]      =("severance_alloc","sum")
        if "medical_insurance_alloc" in df.columns: _co_agg["ביטוח רפואי"] =("medical_insurance_alloc","sum")
        if "employment_levy_alloc"   in df.columns: _co_agg["היטל תעסוקה"] =("employment_levy_alloc","sum")
        if "incentive_fund_alloc"    in df.columns: _co_agg["קרן עידוד"]    =("incentive_fund_alloc","sum")
        if "savings_deposit_alloc"   in df.columns: _co_agg["פיקדון"]       =("savings_deposit_alloc","sum")
        if "other_alloc"             in df.columns: _co_agg["אחר"]          =("other_alloc","sum")
        _co = (df.groupby("country",as_index=False).agg(**_co_agg)
                 .sort_values("עלות",ascending=False).reset_index(drop=True))
        # Weighted CPH per country (was simple-mean: biased)
        _co["avg_cph"]=(_co["עלות"]/_co["שעות"].replace(0,float("nan"))).fillna(0).round(2)
        _co["% עלות"] = (_co["עלות"]/_co["עלות"].sum()*100).round(1)
        _co.insert(0,"#",range(1,len(_co)+1))
        _co = _co.rename(columns={"country":"מדינה","avg_cph":"עלות/שעה"})
        _co = _drop_empty_cols(_co)
        _co_fmt = {**_FMT,"ימים":"{:,.0f}","שכר ברוטו":"₪{:,.0f}","ביטוח לאומי":"₪{:,.0f}",
                   "פנסיה":"₪{:,.0f}","אגרות":"₪{:,.0f}","חופשה/מחלה":"₪{:,.0f}",
                   "פיצויים":"₪{:,.0f}","ביטוח רפואי":"₪{:,.0f}","היטל תעסוקה":"₪{:,.0f}",
                   "קרן עידוד":"₪{:,.0f}","פיקדון":"₪{:,.0f}","אחר":"₪{:,.0f}"}
        # Column picker — affects display only; CSV keeps all columns
        _co_disp = _column_picker(_co, "cols_countries")
        _co_disp = _with_total_row(_co_disp,
          label_col="מדינה",
          empty_cols={"#"},
          recalc={
            "עלות/שעה": lambda d: (d["עלות"].sum()/d["שעות"].sum()) if "שעות" in d.columns and d["שעות"].sum()>0 else 0,
            "% עלות":  lambda d: 100.0,
            "עובדים":  lambda d: NE,
          })
        _sty_co = _styled(_co_disp,_co_fmt,hi_col="עלות/שעה",hi_thresh=CPH*1.15)
        _sty_co = _hl_total_row(_sty_co)
        st.dataframe(_sty_co,
               use_container_width=True,hide_index=True,height=min(400,42+len(_co_disp)*35))
        st.download_button("⬇️ הורד טבלה (CSV)",_co.to_csv(index=False,encoding="utf-8-sig"),
                  f"countries_{_rl}.csv","text/csv")

  with s5_top:
    with st.expander("טבלה לפי חודש", expanded=True):
      if "month" in df.columns:
        _mon=(df.groupby("month",as_index=False).agg(
             עלות=("cost","sum"),שעות=("total_hours","sum"),
             עובדים=("employee_id","nunique"),
             לקוחות=("client","nunique"))
           .sort_values("month",key=lambda s:s.map(_mkey))
           .rename(columns={"month":"חודש"}))
        # Weighted CPH per month (was simple-mean — biased by partial rows)
        _mon["עלות/שעה"]=(_mon["עלות"]/_mon["שעות"].replace(0,float("nan"))).fillna(0).round(2)
        _mon["% מהכולל"]=(_mon["עלות"]/_mon["עלות"].sum()*100).round(1)
        _mon["Δ עלות"]=_mon["עלות"].diff().fillna(0)
        # Revenue / profit / margin per month (dedup by month×client to avoid replication)
        if _has_profit and "billing_amount" in df.columns and "profit" in df.columns:
          _mon_inc = (df.drop_duplicates(["month","client"])
                        .groupby("month",as_index=False)
                        .agg(הכנסה=("billing_amount","sum"),
                             רווח=("profit","sum")))
          _mon = _mon.merge(_mon_inc, left_on="חודש", right_on="month", how="left").drop(columns=["month"])
          _mon["מרג'ין %"] = (_mon["רווח"] / _mon["הכנסה"].replace(0,float("nan")) * 100).round(1).fillna(0.0)
          _mon["Δ רווח"] = _mon["רווח"].diff().fillna(0)
          # Order columns: time + scale + financial deltas + components
          _col_order = ["חודש","הכנסה","עלות","רווח","מרג'ין %","שעות","עלות/שעה",
                        "עובדים","לקוחות","% מהכולל","Δ עלות","Δ רווח"]
          _mon = _mon[[c for c in _col_order if c in _mon.columns]]
          _mon_fmt = {**_FMT,"% מהכולל":"{:.1f}%","Δ עלות":"₪{:+,.0f}",
                      "הכנסה":"₪{:,.0f}","רווח":"₪{:,.0f}",
                      "מרג'ין %":"{:.1f}%","Δ רווח":"₪{:+,.0f}",
                      "לקוחות":"{:,.0f}"}
          # Column picker — affects display only; CSV keeps all columns
          _mon_disp = _column_picker(_mon, "cols_months")
          # Totals row — deltas are blanked (sum of sequential deltas = net change,
          # which equals last - first; less meaningful than absolute totals)
          _mon_disp = _with_total_row(_mon_disp,
            label_col="חודש",
            empty_cols={"Δ עלות","Δ רווח"},
            recalc={
              "עלות/שעה": lambda d: (d["עלות"].sum()/d["שעות"].sum()) if "שעות" in d.columns and d["שעות"].sum()>0 else 0,
              "מרג'ין %": lambda d: (d["רווח"].sum()/d["הכנסה"].sum()*100) if "הכנסה" in d.columns and d["הכנסה"].sum()>0 else 0,
              "% מהכולל": lambda d: 100.0,
              "עובדים":   lambda d: NE,
              "לקוחות":   lambda d: NC,
            })
          # Style: highlight negative profit rows + color delta cells
          _sty_mon = _styled(_mon_disp, _mon_fmt)
          def _neg_p(v):
            try: return "background:#FEF2F2;color:#7F1D1D;font-weight:700" if float(v)<0 else ""
            except: return ""
          def _delta_clr(v):
            try:
              v=float(v)
              if v>0: return "color:#DC2626;font-weight:600"
              if v<0: return "color:#059669;font-weight:600"
            except: pass
            return ""
          if "רווח" in _mon_disp.columns:
            _sty_mon = _sty_mon.map(_neg_p, subset=["רווח"])
          if "Δ עלות" in _mon_disp.columns:
            _sty_mon = _sty_mon.map(_delta_clr, subset=["Δ עלות"])
          if "Δ רווח" in _mon_disp.columns:
            # invert: positive Δprofit is green, negative is red
            def _delta_p_clr(v):
              try:
                v=float(v)
                if v>0: return "color:#059669;font-weight:600"
                if v<0: return "color:#DC2626;font-weight:600"
              except: pass
              return ""
            _sty_mon = _sty_mon.map(_delta_p_clr, subset=["Δ רווח"])
          _sty_mon = _hl_total_row(_sty_mon)
          st.dataframe(_sty_mon, use_container_width=True, hide_index=True)
        else:
          # Column picker for no-profit fallback path
          _mon_disp = _column_picker(_mon, "cols_months_nb")
          _mon_disp = _with_total_row(_mon_disp,
            label_col="חודש",
            empty_cols={"Δ עלות"},
            recalc={
              "עלות/שעה": lambda d: (d["עלות"].sum()/d["שעות"].sum()) if "שעות" in d.columns and d["שעות"].sum()>0 else 0,
              "% מהכולל": lambda d: 100.0,
              "עובדים":   lambda d: NE,
              "לקוחות":   lambda d: NC,
            })
          _sty_mon_nb = _styled(_mon_disp,{**_FMT,"% מהכולל":"{:.1f}%","Δ עלות":"₪{:+,.0f}",
                                     "לקוחות":"{:,.0f}"})
          _sty_mon_nb = _hl_total_row(_sty_mon_nb)
          st.dataframe(_sty_mon_nb,
                 use_container_width=True,hide_index=True)
        st.download_button("⬇️ הורד טבלה (CSV)",_mon.to_csv(index=False,encoding="utf-8-sig"),
                  f"months_{_rl}.csv","text/csv")

    # ⚖️ השוואת תקופות — A vs B (moved from floating-above-tabs to Months sub-tab)
    with st.expander("⚖️ השוואת תקופות", expanded=False):
      _cmp_a,_cmp_b = st.columns(2)
      with _cmp_a: _ma=st.selectbox("חודש A",all_months,index=max(0,len(all_months)-2),key="cmp_ma")
      with _cmp_b: _mb=st.selectbox("חודש B",all_months,index=len(all_months)-1,key="cmp_mb")
      if _ma != _mb:
        _da=raw[raw["month"]==_ma].groupby("client",as_index=False)["cost"].sum()
        _db=raw[raw["month"]==_mb].groupby("client",as_index=False)["cost"].sum()
        _cmp=_da.merge(_db,on="client",how="outer",suffixes=(f"_{_ma}",f"_{_mb}")).fillna(0)
        _cmp["Δ"]=_cmp[f"cost_{_mb}"]-_cmp[f"cost_{_ma}"]
        _cmp["Δ%"]=(_cmp["Δ"]/_cmp[f"cost_{_ma}"].replace(0,float("nan"))*100).round(1)
        _cmp=_cmp.sort_values("Δ",key=abs,ascending=False)
        _cmp=_cmp.rename(columns={"client":"לקוח",f"cost_{_ma}":_ma,f"cost_{_mb}":_mb})
        def _dc(v):
          try:
            v=float(v)
            if v>0: return "color:#DC2626;font-weight:700"
            if v<0: return "color:#059669;font-weight:700"
          except: pass
          return ""
        _sty_cmp=_cmp.style.format({_ma:"₪{:,.0f}",_mb:"₪{:,.0f}","Δ":"₪{:+,.0f}","Δ%":"{:+.1f}%"}).map(_dc,subset=["Δ","Δ%"])
        st.dataframe(_sty_cmp,use_container_width=True,hide_index=True,height=min(500,42+len(_cmp)*32))

  # ══════════════════════════════════════════════════════════════════════════════
  # 4 — תובנות
  # ══════════════════════════════════════════════════════════════════════════════
with t4:
  if not _ca.empty:
    _hc=_hcph if _hcph is not None else _ca.iloc[0]
    _lc=_lcph if _lcph is not None else _ca.iloc[-1]

    # 💎 כוח תמחור — איפה אפשר להעלות מחירים? (UNIQUE)
    if "billing_amount" in df.columns and "billed_hours" in df.columns and HAS_PLOTLY:
      import numpy as np
      with st.expander("💎 כוח תמחור — איפה אפשר להעלות מחירים?", expanded=True):
        _pp_bill = (df.drop_duplicates(["month","client"])
                      .groupby("client", as_index=False)
                      .agg(billing=("billing_amount","sum"), billed_h=("billed_hours","sum")))
        _pp_cost = (df.groupby("client", as_index=False)
                      .agg(cost=("cost","sum"), work_h=("total_hours","sum")))
        _pp = _pp_bill.merge(_pp_cost, on="client")
        _pp["price_per_hour"] = (_pp["billing"] / _pp["billed_h"].replace(0, float("nan")))
        _pp["cost_per_hour"]  = (_pp["cost"]    / _pp["work_h"].replace(0, float("nan")))
        _pp = _pp.dropna(subset=["price_per_hour","cost_per_hour"])
        _pp = _pp[(_pp["billed_h"] > 100) & (_pp["billing"] > 0)]

        if "billing_type_actual" in df.columns:
          _pp_hourly = (df.drop_duplicates(["month","client"])
                          .groupby("client")["billing_type_actual"]
                          .agg(lambda s: s.mode().iloc[0] if len(s) > 0 else "none"))
          _pp_hourly_clients = _pp_hourly[_pp_hourly == "hourly"].index
          _pp = _pp[_pp["client"].isin(_pp_hourly_clients)]
        else:
          _pp = _pp[_pp["price_per_hour"] < 200]

        if len(_pp) >= 5:
          _ppx = _pp["cost_per_hour"].values
          _ppy = _pp["price_per_hour"].values
          _pp_slope, _pp_int = np.polyfit(_ppx, _ppy, 1)
          _pp["fair_price"]    = _pp_slope * _pp["cost_per_hour"] + _pp_int
          _pp["price_gap"]     = _pp["price_per_hour"] - _pp["fair_price"]
          _pp["price_gap_pct"] = (_pp["price_gap"] / _pp["fair_price"] * 100).round(1)

          _underpriced = _pp[_pp["price_gap_pct"] < -8].sort_values("price_gap")

          _pp_colors = ["#DC2626" if g < -8 else "#059669" if g > 8 else "#64748B"
                        for g in _pp["price_gap_pct"]]
          _x_rng = np.array([_ppx.min(), _ppx.max()])
          _fig_pp = go.Figure()
          _fig_pp.add_trace(go.Scatter(
              x=_x_rng, y=_pp_slope * _x_rng + _pp_int,
              mode="lines", name="מחיר הוגן",
              line=dict(color=SLATE, width=2, dash="dash")))
          _fig_pp.add_trace(go.Scatter(
              x=_pp["cost_per_hour"], y=_pp["price_per_hour"],
              mode="markers+text",
              marker=dict(size=12, color=_pp_colors, line=dict(color="white",width=1)),
              text=_pp["client"].apply(lambda s: s[:15]),
              textposition="top center", textfont=dict(size=9),
              hovertemplate="<b>%{customdata[0]}</b><br>עלות/שעה: ₪%{x:.1f}<br>"
                            "מחיר/שעה: ₪%{y:.1f}<br>פער: %{customdata[1]:+.1f}%<extra></extra>",
              customdata=list(zip(_pp["client"], _pp["price_gap_pct"]))))
          _fig_pp.update_layout(**{**_PL,"height":400},
              xaxis=dict(title="עלות/שעה (פנימית)", tickprefix="₪"),
              yaxis=dict(title="מחיר/שעה (ללקוח)", tickprefix="₪"),
              showlegend=False)
          _chart(_fig_pp)
          st.caption("ירוק = מתומחר טוב · אדום = פוטנציאל העלאת מחיר · אפור = במחיר הוגן · הניתוח כולל רק לקוחות שעתיים")

          if len(_underpriced) > 0:
            st.warning(f"💎 {len(_underpriced)} לקוחות עם פוטנציאל העלאת מחיר:")
            _up_disp = _underpriced[["client","cost_per_hour","price_per_hour",
                                      "fair_price","price_gap","price_gap_pct"]].copy()
            _up_disp.columns = ["לקוח","עלות/שעה","מחיר נוכחי","מחיר הוגן","פער ₪","פער %"]
            # Total row: averages for rates, sum for gap. Use the WEIGHTED
            # avg gap (sum of gap × hours / sum hours) since clients have
            # different volumes.
            _up_disp_t = _with_total_row(
              _up_disp,
              label_col="לקוח",
              recalc={
                "עלות/שעה":  lambda x: x["עלות/שעה"].mean(),
                "מחיר נוכחי": lambda x: x["מחיר נוכחי"].mean(),
                "מחיר הוגן":  lambda x: x["מחיר הוגן"].mean(),
                "פער %":      lambda x: x["פער %"].mean(),
              })
            _up_sty = _up_disp_t.style.format({
                "עלות/שעה":   "₪{:.1f}",
                "מחיר נוכחי":"₪{:.1f}",
                "מחיר הוגן":  "₪{:.1f}",
                "פער ₪":      "₪{:+.1f}",
                "פער %":      "{:+.1f}%",
            }, na_rep="")
            _up_sty = _hl_total_row(_up_sty)
            st.dataframe(_up_sty, use_container_width=True, hide_index=True)
            _pot = float((_underpriced["price_gap"] * _underpriced["billed_h"]).abs().sum())
            st.success(f"פוטנציאל הכנסה נוספת בהעלאת תמחור: ₪{_pot:,.0f} (לפי נתונים נוכחיים)")
        else:
          st.info("נדרשים לפחות 5 לקוחות עם נתוני חיוב לחישוב תמחור אופטימלי")
    # ✅ הזדמנויות חיסכון (UNIQUE — קצר ופוקאלי, t8.4 הוא רשימה ארוכה יותר)
    with st.expander("✅ שלוש הזדמנויות חיסכון המובילות", expanded=True):
      savs=[]
      if OTP>0:
        savs.append(("green","💡","הפחת שעות נוספות 20% — כלל הלקוחות",
                     f"חיסכון: ₪{OTP*0.2:,.0f}"))
      if float(_hc["avg_cph"])>CPH*1.1:
        savs.append(("green","📋",f"הסכם עם {_hc['client']}",
                     f"חיסכון: ₪{float(_hc['loss']):,.0f}"))
      if not _crit.empty:
        _w=_crit.iloc[0]; _wp=float(_w.get("cl_ot_prem",0))
        savs.append(("green","⏱️",f"הפחת שעות 175% ב{_w['client']}",
                     f"חיסכון: ₪{_wp*0.5:,.0f}"))
      if not savs: savs.append(("green","✅","שמור על הרמה הנוכחית","כל המדדים תקינים"))
      for s in savs[:3]: _ins(*s)

    # 🔬 Correlation matrix — moved to "ניתוח מתקדם" because the technical
    # term and visualization don't speak to non-analysts. CEO view stays
    # clean; analyst can drill in. Default closed; opens on demand.
    if HAS_PLOTLY:
      _cc=[c for c in ("cost_per_hour","overtime_ratio","fee_ratio","utilization","total_hours") if c in df.columns]
      if len(_cc)>=3:
        with st.expander("🔬 ניתוח מתקדם · מטריצת קורלציה (לאנליסטים)",
                         expanded=False):
          st.markdown(
            "<div style='font-size:11.5px;color:#64748B;line-height:1.55;"
            "margin-bottom:10px'>"
            "🛈 <b>מה זה קורלציה?</b> מדד שמתאר אם שני משתנים נעים ביחד. "
            "ערך 1 = עלייה באחד מלווה תמיד בעלייה בשני. "
            "ערך -1 = עלייה באחד מלווה תמיד בירידה בשני. "
            "ערך קרוב ל-0 = אין קשר. הגרף מיועד למשתמשים אנליטיים."
            "</div>",
            unsafe_allow_html=True)
          _corr=df[_cc].corr().round(2)
          _heb2={"cost_per_hour":"עלות/שעה","overtime_ratio":"שעות נוספות",
                 "fee_ratio":"אגרות","utilization":"מילוי תקן","total_hours":"שעות"}
          _corr.index=[_heb2.get(c,c) for c in _corr.index]
          _corr.columns=[_heb2.get(c,c) for c in _corr.columns]
          _fig_corr=go.Figure(go.Heatmap(z=_corr.values,x=list(_corr.columns),y=list(_corr.index),
            colorscale="RdBu",zmid=0,zmin=-1,zmax=1,
            text=_corr.values,texttemplate="%{text:.2f}",
            hovertemplate="%{x} ↔ %{y}: %{z:.2f}<extra></extra>",
            colorbar=dict(title="קורלציה",thickness=14,len=0.85,
                           tickfont=dict(size=10,color="#475569"))))
          _fig_corr.update_layout(
            **{**_PL,
               "height":360,
               "margin":dict(l=10,r=14,t=50,b=20)},
            title=dict(text="מטריצת קורלציה: רכיבי עלות מרכזיים",
                        font=dict(size=13,color="#0F172A"),
                        x=0.02,xanchor="left",y=0.97),
            xaxis=dict(side="bottom",tickfont=dict(size=11,color="#475569")),
            yaxis=dict(tickfont=dict(size=11,color="#475569")))
          _chart(_fig_corr)
          _cph_corr=_corr.loc["עלות/שעה"].drop("עלות/שעה")
          _strongest=_cph_corr.abs().idxmax()
          _str_val=_cph_corr[_strongest]
          st.caption(f"הקורלציה החזקה ביותר עם עלות/שעה היא **{_strongest}** "
                     f"({'חיובית' if _str_val>0 else 'שלילית'}, {abs(_str_val):.2f}). "
                     f"{'עלייה ב-' if _str_val>0 else 'ירידה ב-'}{_strongest} "
                     f"{'מעלה' if _str_val>0 else 'מורידה'} את עלות/שעה.")


  # 7 — חיוב
  # ══════════════════════════════════════════════════════════════════════════════
# t1 (Overview) block has now closed → record its duration before t7 starts.
st.session_state.setdefault("_perf",[]).append(("render tab: סקירה (Overview)",
   _time_mod.perf_counter()-_t_overview))
_t_billing = _time_mod.perf_counter()
with t7:
  # ── סיכום חיוב ותקן — quick triage card at the top of the tab.
  # 4 quadrants: missing standard / unmatched clients / sites without price /
  # hours without billing. Each shows count + clickable hint to scroll to
  # the detailed section below.
  _bill_summary_cards = []
  # 1. רשומות בלי תקן
  _no_std_n = 0
  if "cost_driver" in df.columns:
    _no_std_n = int(df["cost_driver"].astype(str).str.contains("אין תקן",
                       na=False).sum())
  _bill_summary_cards.append(
    ("רשומות בלי תקן", _no_std_n,
     "high" if _no_std_n > 0 else "ok",
     "ti-file-off",
     "השלם את 'תקן.xlsx' עם הלקוח / האתר החסר"))
  # 2. לקוחות לא מותאמים (בלי billing_amount)
  _unmatched_cli = 0
  if "client" in df.columns and "billing_amount" in df.columns:
    _by_cli = df.drop_duplicates(["month","client"]).groupby("client")["billing_amount"].sum()
    _unmatched_cli = int((_by_cli == 0).sum())
  _bill_summary_cards.append(
    ("לקוחות בלי חיוב", _unmatched_cli,
     "high" if _unmatched_cli > 3 else "med" if _unmatched_cli > 0 else "ok",
     "ti-user-off",
     "הוסף שורת חיוב ל-income.xlsx של החודש הרלוונטי"))
  # 3. אתרים בלי מחיר/סוג — count unique sites where total billing is 0
  _sites_no_price = 0
  if "site" in df.columns and "billing_amount" in df.columns:
    _by_site = df.groupby("site")["billing_amount"].sum()
    _sites_no_price = int((_by_site == 0).sum())
  _bill_summary_cards.append(
    ("אתרים בלי מחיר", _sites_no_price,
     "med" if _sites_no_price > 5 else "ok",
     "ti-building-off",
     "ייתכן שצריך לבצע מיפוי לקוח-אתר ב-תקן.xlsx"))
  # 4. שעות בלי חיוב — work_hours > 0 but billing_amount == 0
  _hours_no_bill = 0
  if all(c in df.columns for c in ("total_hours","billing_amount","client","month")):
    _by_cm = (df.drop_duplicates(["month","client"])
                .assign(_h=df.groupby(["month","client"])["total_hours"].transform("sum"))
                .query("billing_amount == 0 and _h > 0"))
    _hours_no_bill = float(_by_cm["_h"].sum()) if not _by_cm.empty else 0
  _bill_summary_cards.append(
    ("שעות עבודה בלי חיוב", int(_hours_no_bill),
     "high" if _hours_no_bill > 500 else "med" if _hours_no_bill > 0 else "ok",
     "ti-clock-off",
     "אבדן הכנסה משוער: בדוק חיובים חסרים בקובץ income.xlsx"))

  _sev_color = {"high":"#DC2626","med":"#D97706","ok":"#16A34A"}
  _sev_bg    = {"high":"#FEF2F2","med":"#FFFBEB","ok":"#F0FDF4"}
  _sev_brd   = {"high":"#FECACA","med":"#FDE68A","ok":"#BBF7D0"}
  _sev_lbl   = {"high":"קריטי","med":"דורש בדיקה","ok":"תקין"}

  _b_html = '<div class="billing-triage">'
  for _lbl, _n, _sev, _ic, _hint in _bill_summary_cards:
    _c = _sev_color[_sev]
    _b_html += (
      f'<div class="billing-triage-card" '
      f'style="background:{_sev_bg[_sev]};border:1px solid {_sev_brd[_sev]};'
      f'border-right:4px solid {_c}">'
      f'<div class="billing-triage-head">'
      f'<i class="ti {_ic}" style="color:{_c};font-size:20px"></i>'
      f'<span class="billing-triage-label">{_lbl}</span>'
      f'</div>'
      f'<div class="billing-triage-val" style="color:{_c}">{_n:,}</div>'
      f'<div class="billing-triage-status" style="color:{_c}">'
      f'{_sev_lbl[_sev]}</div>'
      f'<div class="billing-triage-hint">{_hint}</div>'
      f'</div>'
    )
  _b_html += '</div>'
  st.markdown(_b_html, unsafe_allow_html=True)

  with st.expander("חיוב מול עלות לפי לקוח", expanded=True):

    if "billed_hours" not in df.columns or "billing_amount" not in df.columns:
      st.warning("אין נתוני חיוב — ודא שיש קבצי income.xlsx בכל חודש ושהרצת את ה-preprocessor.")
      st.code(
        'python -c "from core.preprocessor import build_and_save; build_and_save()"',
        language="bash"
      )
    else:
      # אגרגציה: billing_amount הוא per (month×client) → deduplicate
      _dedup = df.drop_duplicates(["month","client"])
      _has_detail = all(c in df.columns for c in
                        ("billed_base_hours","billed_completion_hours","billed_overtime_hours"))

      _bill_agg = {"שעות חיוב": ("billed_hours","sum"), "חיוב": ("billing_amount","sum")}
      if "billed_days" in _dedup.columns:
        _bill_agg["ימי חיוב"] = ("billed_days","sum")
      if _has_detail:
        _bill_agg["שעות השלמה"]  = ("billed_completion_hours","sum")
        _bill_agg["שעות נוספות"] = ("billed_overtime_hours","sum")

      _cmp_bill = _dedup.groupby("client", as_index=False).agg(**_bill_agg)
      _cmp_cost = (df.groupby("client", as_index=False)
                     .agg(**{"שעות עלות": ("total_hours","sum"),
                             "עלות":      ("cost","sum")}))
      _cmp = _cmp_cost.merge(_cmp_bill, on="client", how="left")

      for _fc in ("שעות חיוב","חיוב","ימי חיוב","שעות השלמה","שעות נוספות"):
        if _fc in _cmp.columns:
          _cmp[_fc] = _cmp[_fc].fillna(0)

      _cmp["רווח"]     = _cmp["חיוב"] - _cmp["עלות"]
      _cmp["מרג'ין %"] = (_cmp["רווח"] / _cmp["חיוב"].replace(0, float("nan")) * 100).round(1)

      # סוג הסכם פר לקוח
      _t7_kind_map = {
          "hourly_no_completion": "⏱️ שעתי", "hourly_with_completion": "⏱️ שעתי+השלמה",
          "daily_with_ot": "📅 יומי+OT", "daily_no_ot": "📅 יומי", "mixed": "🔀 מעורב",
          "daily_or_monthly_min": "🔀 יומי/חודשי", "daily_min_only": "📅 מינ'",
          "no_pricing": "➖ ללא תמחור",
          "hourly": "⏱️ שעתי", "daily": "📅 יומי", "none": "—",
          "unknown": "❓", "missing_data": "⚠️",
      }
      _kind_col = "billing_kind" if "billing_kind" in df.columns else "billing_type_actual" if "billing_type_actual" in df.columns else None
      if _kind_col:
        _types_t7 = (df.groupby("client")[_kind_col]
                       .agg(lambda s: s.mode().iloc[0] if len(s) > 0 else "unknown"))
        _cmp["סוג"] = _cmp["client"].map(_types_t7).map(_t7_kind_map).fillna("—")

      # עמודת "שעות/ימים חיוב" — ימים ללקוחות יומיים, שעות לשאר
      _has_days = "ימי חיוב" in _cmp.columns
      def _fmt_billing_units(row):
        d = float(row.get("ימי חיוב", 0)) if _has_days else 0
        h = float(row.get("שעות חיוב", 0))
        bill = float(row.get("חיוב", 0))
        if d > 0 and h > 0:
          return f"{int(d)}d + {int(h)}h OT"
        if d > 0:
          return f"{int(d)}d"
        if h > 0:
          return f"{h:,.0f}h"
        if bill > 0:
          return "—"   # הכנסה שלא מ-שעות (אגרות, התחשבנות)
        return ""
      _cmp["יחידות חיוב"] = _cmp.apply(_fmt_billing_units, axis=1)

      _cmp = _cmp.sort_values("חיוב", ascending=False)

      # סדר עמודות — ללא "שעות חיוב" הגולמי (מוחלף ב-"יחידות חיוב")
      _col_order = ["client"]
      if "סוג" in _cmp.columns: _col_order.append("סוג")
      _col_order += ["עלות","שעות עלות","חיוב","יחידות חיוב"]
      if _has_detail:
        _col_order += ["שעות השלמה","שעות נוספות"]
      _col_order += ["רווח","מרג'ין %"]
      _cmp = _cmp[[c for c in _col_order if c in _cmp.columns]]
      _cmp_display = _cmp.rename(columns={"client":"לקוח"})

      def _fmt_h_zero_blank(v):
        try:
          v = float(v)
          return "" if v == 0 else f"{v:,.0f}h"
        except Exception:
          return ""

      _fmt = {
        "עלות":       "₪{:,.0f}",
        "שעות עלות":  "{:,.0f}h",
        "חיוב":       "₪{:,.0f}",
        "רווח":       "₪{:,.0f}",
        "מרג'ין %":   "{:.1f}%",
      }
      if _has_detail:
        _fmt["שעות השלמה"]  = _fmt_h_zero_blank
        _fmt["שעות נוספות"] = _fmt_h_zero_blank

      def _profit_color(v):
        try:
          v = float(v)
          if v < 0: return "color:#DC2626;font-weight:700"
          if v > 0: return "color:#059669;font-weight:700"
          return "color:#64748B"
        except Exception:
          return ""

      # Totals row — sum numeric absolute cols, weighted-recalc rates
      _cmp_display = _with_total_row(_cmp_display,
        label_col="לקוח",
        empty_cols={"סוג","יחידות חיוב"},
        recalc={
          "מרג'ין %": lambda d: (d["רווח"].sum()/d["חיוב"].sum()*100) if "חיוב" in d.columns and d["חיוב"].sum()>0 else 0,
        })
      _t7_styled = (_cmp_display.style
                    .format(_fmt, na_rep="")
                    .map(_profit_color, subset=["רווח","מרג'ין %"]))
      _t7_styled = _hl_total_row(_t7_styled)
      st.dataframe(_t7_styled, use_container_width=True, hide_index=True,
                    height=min(700, 42 + len(_cmp_display) * 35))
      st.caption("'—' בעמודת יחידות חיוב = הכנסה לא מ-שעות (אגרות, התחשבנות, מיגורים)")

      # KPIs בתחתית
      _total_cost = float(_cmp["עלות"].sum())
      _total_rev  = float(_cmp["חיוב"].sum())
      _total_prf  = float(_cmp["רווח"].sum())
      _avg_margin = (_total_prf / _total_rev * 100) if _total_rev > 0 else 0

      c1,c2,c3,c4 = st.columns(4)
      c1.metric("סך עלות", f"₪{_total_cost:,.0f}")
      c2.metric("סך חיוב", f"₪{_total_rev:,.0f}")
      c3.metric("סך רווח", f"₪{_total_prf:,.0f}",
                 delta=f"{_avg_margin:.1f}% מרג'ין")

      if _has_detail:
        _tot_comp = float(_cmp["שעות השלמה"].sum())  if "שעות השלמה"  in _cmp.columns else 0
        _tot_ot   = float(_cmp["שעות נוספות"].sum()) if "שעות נוספות" in _cmp.columns else 0
        c4.metric("שעות השלמה+נוספות", f"{_tot_comp+_tot_ot:,.0f}h",
                   delta=f"השלמה {_tot_comp:,.0f} · נוספות {_tot_ot:,.0f}")
      else:
        _lossy = _cmp[_cmp["רווח"] < 0]
        c4.metric("לקוחות הפסדיים", len(_lossy),
                   delta=f"מתוך {len(_cmp)}")

      # התראה ללקוחות הפסדיים
      _lossy = _cmp_display[_cmp_display["רווח"] < 0]
      if len(_lossy) > 0:
        st.error(
          f"🔴 {len(_lossy)} לקוחות עם רווח שלילי — מסומנים באדום בטבלה מעלה. "
          "בדוק תמחור או עלויות."
        )

    # 📈 Monthly trend of billing / cost / profit
    # Uses _raw_hist (not df) so the trend shows full history even when the
    # user has selected only a single month in the global filter.
    with st.expander("📈 מגמת חיוב, עלות ורווח חודשית", expanded=True):
      if "month" in _raw_hist.columns and "billing_amount" in _raw_hist.columns and HAS_PLOTLY:
        _trend_b = (_raw_hist.drop_duplicates(["month","client"])
                      .groupby("month", as_index=False)
                      .agg(חיוב=("billing_amount","sum")))
        _trend_c = (_raw_hist.groupby("month", as_index=False)
                      .agg(עלות=("cost","sum")))
        _trend_t = _trend_b.merge(_trend_c, on="month", how="outer").fillna(0)
        _trend_t["רווח"] = _trend_t["חיוב"] - _trend_t["עלות"]
        _trend_t = _trend_t.sort_values("month", key=lambda s: s.map(_mkey)).reset_index(drop=True)
        if len(_trend_t) >= 2:
          _fig_bt = go.Figure()
          _fig_bt.add_trace(go.Scatter(x=_trend_t["month"], y=_trend_t["חיוב"],
              name="חיוב", mode="lines+markers",
              line=dict(color=BLUE, width=3), marker=dict(size=8),
              hovertemplate="<b>%{x}</b><br>חיוב: ₪%{y:,.0f}<extra></extra>"))
          _fig_bt.add_trace(go.Scatter(x=_trend_t["month"], y=_trend_t["עלות"],
              name="עלות", mode="lines+markers",
              line=dict(color=RED, width=3), marker=dict(size=8),
              hovertemplate="<b>%{x}</b><br>עלות: ₪%{y:,.0f}<extra></extra>"))
          _fig_bt.add_trace(go.Scatter(x=_trend_t["month"], y=_trend_t["רווח"],
              name="רווח", mode="lines+markers",
              line=dict(color=GREEN, width=3, dash="dot"), marker=dict(size=8),
              hovertemplate="<b>%{x}</b><br>רווח: ₪%{y:,.0f}<extra></extra>"))
          _fig_bt.update_layout(**{**_PL,"height":300},
              legend=dict(orientation="h", y=-0.15, x=0.5, xanchor="center"),
              yaxis=dict(tickprefix="₪", showgrid=True, gridcolor="#F1F5F9"),
              xaxis=dict(tickangle=-30, showgrid=False))
          st.plotly_chart(_fig_bt, use_container_width=True, config={"displayModeBar":False})
          _trend_t["מרג'ין %"] = (_trend_t["רווח"] / _trend_t["חיוב"].replace(0, float("nan")) * 100).round(1).fillna(0)
          _mom_avg = float(_trend_t["מרג'ין %"].mean())
          _b_now = float(_trend_t.iloc[-1]["חיוב"]); _b_pre = float(_trend_t.iloc[-2]["חיוב"])
          _chg_b = (_b_now-_b_pre)/_b_pre*100 if _b_pre>0 else 0
          _p_now = float(_trend_t.iloc[-1]["רווח"]); _p_pre = float(_trend_t.iloc[-2]["רווח"])
          _chg_p = (_p_now-_p_pre)/_p_pre*100 if _p_pre>0 else 0
          st.caption(
            f"מרג'ין ממוצע בתקופה: **{_mom_avg:.1f}%** · "
            f"{len(_trend_t)} חודשים · "
            f"חודש אחרון: חיוב {'▲' if _chg_b>=0 else '▼'}{abs(_chg_b):.1f}% · "
            f"רווח {'▲' if _chg_p>=0 else '▼'}{abs(_chg_p):.1f}%")
        else:
          st.info("נדרשים לפחות 2 חודשים בטווח הנבחר לגרף מגמה — בחר טווח רחב יותר בסרגל הצד")
      else:
        st.info("אין נתוני billing_amount/חודש/plotly")

    with st.expander("📑 דוח חיוב חודשי — ייצוא", expanded=True):

      if "billing_amount" not in df.columns:
        st.warning("אין נתוני חיוב — ודא שיש קבצי income.xlsx ושהרצת את ה-preprocessor.")
      else:
        _r8_col, _ = st.columns([1,2])
        with _r8_col:
          _rep_month = st.selectbox("בחר חודש לדוח",
              sorted(df["month"].unique(), key=_mkey, reverse=True), key="report_month")

        _rd = df[df["month"] == _rep_month]

        _rep_bill = (_rd.drop_duplicates(["month","client"])
                        .groupby("client", as_index=False)
                        .agg(שעות_חיוב=("billed_hours","sum"),
                             סכום_חיוב=("billing_amount","sum")))
        _rep_cost = (_rd.groupby("client", as_index=False)
                        .agg(שעות_עבודה=("total_hours","sum"),
                             עלות=("cost","sum"),
                             עובדים=("employee_id","nunique")))
        _report8 = _rep_bill.merge(_rep_cost, on="client", how="outer").fillna(0)

        if "billed_completion_hours" in _rd.columns:
          _rep_comp = (_rd.drop_duplicates(["month","client"])
                          .groupby("client", as_index=False)
                          .agg(שעות_השלמה=("billed_completion_hours","sum")))
          _report8 = _report8.merge(_rep_comp, on="client", how="left")
        if "billed_overtime_hours" in _rd.columns:
          _rep_ot = (_rd.drop_duplicates(["month","client"])
                        .groupby("client", as_index=False)
                        .agg(שעות_נוספות=("billed_overtime_hours","sum")))
          _report8 = _report8.merge(_rep_ot, on="client", how="left")

        _report8["מחיר_ממוצע"] = (_report8["סכום_חיוב"] /
                                    _report8["שעות_חיוב"].replace(0, float("nan"))).round(1)
        _report8["רווח"]       = _report8["סכום_חיוב"] - _report8["עלות"]
        _report8["מרגין_%"]    = (_report8["רווח"] /
                                    _report8["סכום_חיוב"].replace(0, float("nan")) * 100).round(1)
        _report8 = _report8[_report8["סכום_חיוב"] > 0].sort_values("סכום_חיוב", ascending=False)

        # סוג הסכם פר לקוח
        _t8_kind_col = "billing_kind" if "billing_kind" in _rd.columns else "billing_type_actual" if "billing_type_actual" in _rd.columns else None
        if _t8_kind_col:
          _t8_kind_map = {
              "hourly_no_completion":"⏱️ שעתי","hourly_with_completion":"⏱️+השלמה",
              "daily_with_ot":"📅+OT","daily_no_ot":"📅","mixed":"🔀",
              "daily_or_monthly_min":"🔀יומי/חודשי","daily_min_only":"📅מינ'",
              "hourly":"⏱️ שעתי","daily":"📅 יומי","none":"—","unknown":"❓","missing_data":"⚠️",
          }
          _t8_types = (_rd.groupby("client")[_t8_kind_col]
                          .agg(lambda s: s.mode().iloc[0] if len(s) > 0 else "unknown"))
          _report8["סוג"] = _report8["client"].map(_t8_types).map(_t8_kind_map).fillna("—")

        _r8_cols = ["client"]
        if "סוג" in _report8.columns: _r8_cols.append("סוג")
        _r8_cols += ["עובדים","שעות_עבודה","שעות_חיוב"]
        if "שעות_השלמה" in _report8.columns: _r8_cols.append("שעות_השלמה")
        if "שעות_נוספות" in _report8.columns: _r8_cols.append("שעות_נוספות")
        _r8_cols += ["מחיר_ממוצע","סכום_חיוב","עלות","רווח","מרגין_%"]
        _report8 = _report8[[c for c in _r8_cols if c in _report8.columns]]
        _report8 = _report8.rename(columns={"client":"לקוח"})

        # Summary KPIs for the selected month (table itself removed —
        # duplicates the main billing-vs-cost table at the top of the tab).
        st.caption(f"סיכום חודש **{_rep_month}** — לטבלה המפורטת ראה למעלה. כאן רק הייצוא.")
        _r8a, _r8b, _r8c, _r8d = st.columns(4)
        _r8a.metric("לקוחות פעילים", len(_report8))
        _r8b.metric("סך הכנסה", f"₪{_report8['סכום_חיוב'].sum():,.0f}")
        _r8c.metric("סך עלות",  f"₪{_report8['עלות'].sum():,.0f}")
        _r8_tp = float(_report8["רווח"].sum())
        _r8_tb = float(_report8["סכום_חיוב"].sum())
        _r8d.metric("רווח כולל", f"₪{_r8_tp:,.0f}",
                     delta=f"{_r8_tp/_r8_tb*100:.1f}%" if _r8_tb > 0 else None)

        st.markdown("---")
        _dl1, _dl2 = st.columns(2)
        with _dl1:
          st.download_button(
              f"⬇️ הורד CSV — {_rep_month}",
              _report8.to_csv(index=False, encoding="utf-8-sig"),
              f"billing_report_{_rep_month}.csv", "text/csv",
              use_container_width=True)
        with _dl2:
          try:
            import io as _io8
            _buf8 = _io8.BytesIO()
            with pd.ExcelWriter(_buf8, engine="openpyxl") as _w8:
              _report8.to_excel(_w8, index=False, sheet_name=f"חיוב {_rep_month}")
            st.download_button(
                f"⬇️ הורד Excel — {_rep_month}",
                _buf8.getvalue(),
                f"billing_report_{_rep_month}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)
          except ImportError:
            st.caption("להפעיל ייצוא Excel: pip install openpyxl")

      # ══════════════════════════════════════════════════════════════════════════════
      # 5 — סימולציה
      # ══════════════════════════════════════════════════════════════════════════════
with t5:
  st.info(
    "⚙️ **סימולציה** — חיסכון עלות מחושב לפי עלות/שעה אמיתית. "
    "הפסד הכנסה מחושב לפי **תעריפי ההסכמים מ-תקן.xlsx** (billing_kind). "
    "לקוחות ללא תקן מסומנים 'unknown' — הפסד הכנסה שלהם מוערך מנתוני חיוב בפועל."
  )
  _rate_sim=df["cost_per_hour"].replace(0,float("nan"))

  # Pre-compute hour costs (used in form info cards, which render before submit)
  _sim_defs=[
    ("h125",0.25,"⏱️","הפחתת שעות 125%","שינוי בשעות רגילות נוספות","s125","הפחת שעות 125% ב-%",80,0),
    ("h150",0.50,"⏱️","הפחתת שעות 150%","שינוי בשעות מוגברות","s150","הפחת שעות 150% ב-%",80,0),
    ("h175",0.75,"🔴","הפחתת שעות 175%","שעות לילה / חג — יקרות","s175","הפחת שעות 175% ב-%",80,20),
    ("h200",1.00,"🔴","הפחתת שעות 200%","שעות חג מיוחד — הכי יקרות","s200","הפחת שעות 200% ב-%",80,20),
  ]
  _h_costs={hcol: float(df.get(hcol,pd.Series(0,index=df.index)).fillna(0)
                         .mul(_rate_sim.fillna(0)).sum())*mult
            for hcol,mult,*_ in _sim_defs}

  if "sim_results" not in st.session_state:
    st.session_state["sim_results"] = None

  with st.form("sim_form"):
    with st.expander("שעות נוספות — סימולציה לפי רמה", expanded=True):
      cols4=st.columns(4)
      _pcts={}
      for i,(hcol,mult,ico,ttl,sub,skey,slbl,mx,dflt) in enumerate(_sim_defs):
        with cols4[i]:
          h_tot=float(df.get(hcol,pd.Series(0,index=df.index)).fillna(0).sum())
          st.markdown(
            f'<div class="sim-card">'
            f'<div class="sim-lbl">{ico} {ttl}</div>'
            f'<div style="font-size:11px;color:#64748B;margin-bottom:10px">{sub}<br>'
            f'<b>{h_tot:,.0f}</b> שעות · עלות פרמיה <b>₪{_h_costs[hcol]:,.0f}</b></div>'
            f'</div>',unsafe_allow_html=True)
          _pcts[hcol]=st.slider(slbl,0,mx,dflt,5,key=skey)/100

      st.markdown("<div style='height:8px'></div>",unsafe_allow_html=True)
      with st.expander("הפחתת עלות לשעה", expanded=True):
        st.markdown(
          f'<div class="sim-card">'
          f'<div class="sim-lbl">⚡ עלות לשעה</div>'
          f'<div style="font-size:11px;color:#64748B">'
          f'עלות לשעה נוכחית: <b>₪{CPH:.1f}</b> · סה"כ שעות: <b>{TH:,.0f}</b></div>'
          f'</div>',unsafe_allow_html=True)
        _cr=st.slider("הפחת עלות לשעה ב-%",0,30,5,1,key="cph_red")/100
        submitted=st.form_submit_button("⚡ חשב חיסכון",use_container_width=True,type="primary")

  if submitted:
    _gross_total = _rev_total = 0.0
    _sims_detail = []
    for hcol, mult, ico, ttl, sub, skey, slbl, mx, dflt in _sim_defs:
      pct = _pcts.get(hcol, 0)
      g, r, n = _calc_ot_savings(df, hcol, mult, pct)
      _gross_total += g; _rev_total += r
      _sims_detail.append({"hcol":hcol,"pct":pct,"gross":g,"revenue":r,"net":n,"ttl":ttl})
    _sv_cph = TC * _cr
    st.session_state["sim_results"] = {
      "gross_total": _gross_total, "revenue_total": _rev_total,
      "net_total": _gross_total - _rev_total, "sv_cph": _sv_cph, "cr": _cr,
      "sims_detail": _sims_detail,
      "total_sv_ot": _gross_total,  # backward compat
    }

  if st.session_state["sim_results"]:
    _r = st.session_state["sim_results"]
    _sv_cph = _r["sv_cph"]; _cr = _r["cr"]
    _gross = _r.get("gross_total", 0)
    _rev   = _r.get("revenue_total", 0)
    _net   = _r.get("net_total", _gross)
    total_sv = _net + _sv_cph

    # כרטיסי פירוט לכל רמת OT
    _res_cols = st.columns(4)
    for i, sd in enumerate(_r.get("sims_detail", [])):
      with _res_cols[i % 4]:
        if sd["gross"] > 0:
          _bg = "#F0FDF4" if sd["net"] >= 0 else "#FEF2F2"
          _fc = "#059669" if sd["net"] >= 0 else "#DC2626"
          st.markdown(
            f'<div style="background:{_bg};border:1px solid;border-radius:8px;padding:10px 12px;margin-bottom:6px">'
            f'<div style="font-size:9px;font-weight:700;color:{_fc};margin-bottom:3px">{sd["ttl"]}</div>'
            f'<div style="font-size:16px;font-weight:900;color:{_fc}">₪{sd["net"]:,.0f} נטו</div>'
            f'<div style="font-size:10px;color:#64748B">ברוטו ₪{sd["gross"]:,.0f} · הפסד הכנסה ₪{sd["revenue"]:,.0f}</div>'
            f'<div style="font-size:10px;color:{_fc}">{sd["pct"]*100:.0f}% הפחתה</div>'
            f'</div>', unsafe_allow_html=True)

    st.markdown("---")
    _s1, _s2, _s3 = st.columns(3)
    _s1.metric("חיסכון ברוטו (עלות עובדים)", f"₪{_gross:,.0f}")
    _s2.metric("הפסד הכנסה", f"₪{_rev:,.0f}", delta="הפסד" if _rev > 0 else None, delta_color="inverse")
    _net_color = "normal" if _net >= 0 else "inverse"
    _s3.metric("חיסכון נטו", f"₪{_net:,.0f}", delta_color=_net_color)

    if _net < 0:
      st.error(f"⚠️ לא משתלם — הפחתת OT תייקר אותך ב-₪{abs(_net):,.0f}. רוב לקוחותיך שעתיים → הפחתת שעות = הפחתת חיוב.")
    elif _rev > 0 and _net < _gross * 0.4:
      st.warning(f"⚠️ חיסכון נטו רק {_net/_gross*100:.0f}% מהברוטו. שקול בזהירות.")
    elif _net > 0:
      st.success(f"✅ חיסכון נטו: ₪{_net:,.0f}")

    # גרף
    if HAS_PLOTLY and total_sv > 0:
      _chart(go.Figure(go.Bar(
        x=["עלות נוכחית","אחרי שינוי שעות (נטו)","אחרי שינוי עלות","אחרי שניהם"],
        y=[TC, TC-_net, TC-_sv_cph, TC-total_sv],
        marker_color=[RED,GREEN if _net>=0 else RED,AMBER,GREEN if total_sv>=0 else RED],
        opacity=0.88,
        text=[f"₪{v/1000:.0f}K" for v in [TC,TC-_net,TC-_sv_cph,TC-total_sv]],
        textposition="outside",
      )).update_layout(**{**_PL,"height":240}, showlegend=False, yaxis=dict(visible=False)))
    st.caption("השוואת עלות נוכחית מול תרחישי הפחתה (שעות נוספות, עלות לשעה, או שניהם).")
  else:
    st.info("👆 הזז את הסליידרים ולחץ **חשב חיסכון** לקבלת תוצאות")

  # ── פיצ'ר B — תקרת שעות נוספות ──────────────────────────────────────────────
  _OT_MULTS = {"h125":0.25,"h150":0.50,"h175":0.75,"h200":1.00}
  _CAP_OPTIONS = ["150%","175%","200% (נוכחי, אין הגבלה)"]
  _CAP_MAP = {
    "150%":                          {"h175":0.50,"h200":0.50},
    "175%":                          {"h200":0.75},
    "200% (נוכחי, אין הגבלה)":      {},
  }

  with st.expander("⛔ תקרת שעות נוספות", expanded=False):
    # radio מחוץ לform — מציג מיד את ההשלכות ללא לחיצה
    _cap_choice=st.radio("הגבל שעות נוספות עד:",_CAP_OPTIONS,index=2,
                          key="ot_cap",horizontal=True)
    with st.form("form_ot_cap"):
      _cap_submit=st.form_submit_button("חשב",type="primary",use_container_width=True)

    if _cap_submit:
      _rate_cap = df["cost_per_hour"].replace(0,float("nan")).fillna(0)
      _cmap     = _CAP_MAP[_cap_choice]

      _prem_curr  = sum(
        float((df[c].fillna(0)*_rate_cap*m).sum())
        for c,m in _OT_MULTS.items() if c in df.columns
      )
      _prem_after = sum(
        float((df[c].fillna(0)*_rate_cap*_cmap.get(c,m)).sum())
        for c,m in _OT_MULTS.items() if c in df.columns
      )
      _saving_cap = _prem_curr - _prem_after

      if HAS_PLOTLY:
        _chart(go.Figure(go.Bar(
          x=["פרמיה נוכחית","פרמיה אחרי תקרה"],
          y=[_prem_curr,_prem_after],
          marker_color=[RED, GREEN if _saving_cap>0 else AMBER],
          text=[f"₪{v/1000:.0f}K" for v in [_prem_curr,_prem_after]],
          textposition="outside",opacity=0.88,
        )).update_layout(**{**_PL,"height":220},showlegend=False,
            yaxis=dict(visible=False)))

      st.metric("חיסכון חודשי בפרמיית שעות נוספות",f"₪{_saving_cap:,.0f}")
      st.caption("ההנחה: שעות שמעל התקרה אכן ייעשו, אבל יסומנו כקטגוריה הנמוכה יותר. "
                 "סימולציה אינדיקטיבית — לא בודקת זמינות עובדים או אילוצי משמרת.")

  # Part 3 — פירוק עלות לשעה לרכיבים
  with st.expander("📉 איך להפחית עלות לשעה — תכנית פעולה", expanded=False):
    st.markdown('<div class="focus blue">עלות לשעה היא תוצאה של כמה גורמים. הסימולציה מפרקת אותה לרכיבים ומציגה כמה כל אחד תורם.</div>',unsafe_allow_html=True)

    _components={}
    if OTP>0:
      _components["פרמיית שעות נוספות"]={"value":OTP,"pct_of_cph":OTP/TH if TH>0 else 0,"action":"הפחת שעות 175%/200% — סידור משמרות","potential":OTP*0.5}
    if _has_fee and _fee_total>0:
      _components["אגרות מועברות"]={"value":_fee_total,"pct_of_cph":_fee_total/TH if TH>0 else 0,"action":"וודא שאגרות מתואמות עם הסכמי החיוב","potential":_fee_total*0.1}
    if isinstance(_ea,pd.DataFrame) and not _ea.empty:
      _exp2=_ea[_ea["avg_cph"]>CPH*1.2]
      _exp_excess=float((_exp2["avg_cph"]-CPH*1.2).mul(_exp2["שעות"]).sum())
      if _exp_excess>0:
        _components[f"עובדים יקרים (>120% ממוצע)"]={"value":_exp_excess,"pct_of_cph":_exp_excess/TH if TH>0 else 0,"action":f"בדוק חוזים של {len(_exp2)} עובדים","potential":_exp_excess*0.3}
    if "site" in df.columns:
      _scph=(df.groupby("site").agg(c=("cost","sum"),h=("total_hours","sum")).assign(cph=lambda d:d["c"]/d["h"].replace(0,float("nan"))))
      _se=_scph[_scph["cph"]>CPH*1.15]
      _sex=float((_se["cph"]-CPH*1.15).mul(_se["h"]).sum())
      if _sex>0:
        _components[f"אתרים יקרים"]={"value":_sex,"pct_of_cph":_sex/TH if TH>0 else 0,"action":f"בדוק תמחור של {len(_se)} אתרים","potential":_sex*0.4}

    if _components:
      _comp_rows=[{"מרכיב":n,"השפעה ₪/שעה":c["pct_of_cph"],"סך עלות":c["value"],"פעולה מומלצת":c["action"],"פוטנציאל חיסכון":c["potential"]} for n,c in _components.items()]
      _comp_df=pd.DataFrame(_comp_rows).sort_values("פוטנציאל חיסכון",ascending=False)
      # Totals — sum numeric, leave action blank
      _comp_df_t = _with_total_row(
        _comp_df, label_col="מרכיב",
        empty_cols={"פעולה מומלצת"},
      )
      _comp_sty = _comp_df_t.style.format(
        {"השפעה ₪/שעה":"₪{:.2f}","סך עלות":"₪{:,.0f}",
         "פוטנציאל חיסכון":"₪{:,.0f}"}, na_rep="")
      _comp_sty = _hl_total_row(_comp_sty)
      st.dataframe(_comp_sty, use_container_width=True, hide_index=True)
      _total_pot=sum(c["potential"] for c in _components.values())
      _new_cph_est=(TC-_total_pot)/TH if TH>0 else CPH
      _r1,_r2,_r3=st.columns(3)
      _r1.metric("עלות לשעה נוכחית",f"₪{CPH:.1f}")
      _r2.metric("פוטנציאל הפחתה",f"₪{_total_pot/1000:.0f}K",delta=f"-{_total_pot/TC*100:.1f}% מהעלות")
      _r3.metric("עלות לשעה צפויה",f"₪{_new_cph_est:.1f}",delta=f"{(_new_cph_est-CPH):.1f}",delta_color="inverse")
      if HAS_PLOTLY:
        _chart(go.Figure(go.Bar(x=_comp_df["פוטנציאל חיסכון"],y=_comp_df["מרכיב"],
          orientation="h",marker_color=GREEN,
          text=[f"₪{v/1000:.0f}K" for v in _comp_df["פוטנציאל חיסכון"]],textposition="outside"
          )).update_layout(**{**_PL,"height":200+len(_comp_df)*30},showlegend=False,
            title="פוטנציאל חיסכון לפי רכיב",xaxis=dict(visible=False)))
        st.caption("פוטנציאל החיסכון השנתי לכל גורם — ככל שהעמודה ארוכה יותר, כך השפעת התיקון גדולה יותר.")
    else:
      st.success("✅ אין רכיבים בולטים להפחתה — עלות לשעה תקינה")

# ══════════════════════════════════════════════════════════════════════════════
# 6 — התראות
# ══════════════════════════════════════════════════════════════════════════════
with t6:
  # ── 🏷️ תמחור חסר — אתרים מ-תקן.xlsx + לקוחות מ-billing_kind ──────────────
  # Combined: both pricing gaps under one roof for a single source-of-truth.
  _std_pq = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output","cache","standards.parquet")
  _incomplete_std = pd.DataFrame()
  if os.path.exists(_std_pq):
    try:
      _std_df = pd.read_parquet(_std_pq)
      _incomplete_std = (
        _std_df[_std_df.get("is_complete", pd.Series(True, index=_std_df.index)) == False]
        if "is_complete" in _std_df.columns else pd.DataFrame()
      )
    except Exception:
      _incomplete_std = pd.DataFrame()
  _unk_clients = []
  if "billing_kind" in df.columns:
    _unk = df[df["billing_kind"].isin(["unknown","missing_data"])]
    _unk_clients = sorted(_unk["client"].dropna().unique().tolist())

  if len(_incomplete_std) > 0 or _unk_clients:
    with st.expander("🏷️ תמחור חסר — דרוש השלמה", expanded=True):
      _msg_parts = []
      if len(_incomplete_std): _msg_parts.append(f"{len(_incomplete_std)} אתרים")
      if _unk_clients:         _msg_parts.append(f"{len(_unk_clients)} לקוחות")
      st.warning("⚠️ דרוש להשלים תמחור עבור: " + " · ".join(_msg_parts))

      if len(_incomplete_std) > 0:
        st.markdown("**🏗️ אתרים ללא מחיר/סוג ב-תקן.xlsx**")
        _show_inc = _incomplete_std[["client_full","site"]].rename(
            columns={"client_full":"לקוח","site":"אתר"})
        st.dataframe(_show_inc, use_container_width=True, hide_index=True,
                      height=min(300, 42 + len(_show_inc) * 32))

      if len(_incomplete_std) > 0 and _unk_clients:
        st.markdown("---")

      if _unk_clients:
        st.markdown("**👥 לקוחות לא מותאמים ל-תקן.xlsx**")
        for _uc in _unk_clients[:15]:
          st.markdown(f"- {_uc}")
        if len(_unk_clients) > 15:
          st.caption(f"ועוד {len(_unk_clients)-15}...")

      st.caption(
          "💡 פתרון: ערוך **data/תקן.xlsx** והשלם תמחור · "
          "או הוסף ב-**core/client_mapping.json** תחת '_no_match' (אם הלקוח לא דורש תמחור) · "
          "לאחר העדכון לחץ 'רענן' למעלה."
      )

  # ── זיכויים גדולים ────────────────────────────────────────────────────────
  if "amount_credit" in df.columns:
    _cr_dedup = df.drop_duplicates(["month","client"])
    _cr_data = (_cr_dedup.groupby(["month","client"], as_index=False)
                  .agg(זיכוי=("amount_credit","sum")))
    # זיכוי אמיתי = סכום שלילי
    _cr_data = _cr_data[_cr_data["זיכוי"] < -1000].copy()
    _cr_data["זיכוי"] = _cr_data["זיכוי"].abs()
    _cr_data = _cr_data.sort_values("זיכוי", ascending=False)
    if len(_cr_data) > 0:
      with st.expander("זיכויים גדולים (מעל ₪1,000)", expanded=True):
        _total_cr = float(_cr_data["זיכוי"].sum())
        st.error(f"סך זיכויים: ₪{_total_cr:,.0f} ב-{len(_cr_data)} מקרים")
        _cr_disp = _cr_data.rename(columns={"month":"חודש","client":"לקוח"}).head(20)
        _cr_t = _with_total_row(_cr_disp, label_col="חודש",
                                  empty_cols={"לקוח"})
        _cr_sty = _cr_t.style.format({"זיכוי":"₪{:,.0f}"}, na_rep="")
        _cr_sty = _hl_total_row(_cr_sty)
        st.dataframe(_cr_sty, use_container_width=True, hide_index=True)
        st.caption("זיכויים מקטינים את ההכנסה. בדוק שהם מוצדקים — תקופת מבחן, פסילת עובד, הפסקות.")
        st.markdown("---")

    # ── שורות חריגות (qty וסכום בסימנים מנוגדים) ─────────────────────────────
  if "amount_anomaly" in df.columns:
    _an_dedup = df.drop_duplicates(["month","client"])
    _an_data = (_an_dedup.groupby(["month","client"], as_index=False)
                  .agg(כמות=("qty_anomaly","sum"),
                       סכום=("amount_anomaly","sum")))
    _an_data = _an_data[(_an_data["כמות"] != 0) | (_an_data["סכום"] != 0)]
    if len(_an_data) > 0:
      with st.expander("שורות חריגות בקובץ הכנסות", expanded=True):
        st.warning(f"{len(_an_data)} שורות עם כמות וסכום בסימנים מנוגדים — בדוק תקינות הרישום")
        _an_disp = _an_data.rename(columns={"month":"חודש","client":"לקוח"})
        _an_disp_t = _with_total_row(_an_disp, label_col="חודש",
                                       empty_cols={"לקוח"})
        _an_sty = _an_disp_t.style.format(
          {"כמות":"{:,.2f}","סכום":"₪{:,.2f}"}, na_rep="")
        _an_sty = _hl_total_row(_an_sty)
        st.dataframe(_an_sty, use_container_width=True, hide_index=True)
        st.markdown("---")

    # ── פיצ'ר 1: חיוב חסר ────────────────────────────────────────────────────
  if "billing_amount" in df.columns and "client" in df.columns:
    from core.internal_entities import is_internal, internal_list

    with st.expander("חיוב חסר — לקוחות שלא חויבו", expanded=True):

      _pipe_miss = df.groupby(["month","client"], as_index=False).agg(
          שעות=("total_hours","sum"),
          עלות=("cost","sum"))
      _pipe_miss = _pipe_miss[_pipe_miss["שעות"] > 0]

      _bill_lkp = (df.drop_duplicates(["month","client"])
                     [["month","client","billing_amount","billed_hours"]]
                     .copy())
      _chk = _pipe_miss.merge(_bill_lkp, on=["month","client"], how="left")
      _chk["billing_amount"] = _chk["billing_amount"].fillna(0)
      _chk["billed_hours"]   = _chk["billed_hours"].fillna(0)

      # Split out internal entities (e.g. ינאי פרסונל) — they have costs but
      # intentionally no external billing. Show separately, not as a leak.
      _is_internal_mask = _chk["client"].astype(str).map(is_internal)
      _internal_costs   = _chk[_is_internal_mask].copy()

      _missing_bill = _chk[(~_is_internal_mask) &
                           (_chk["billing_amount"] == 0) &
                           (_chk["שעות"] > 0)].copy()

      if len(_missing_bill) > 0:
        # אומדן מחיר/שעה לפי ממוצע לקוח בחודשים שכן חויבו
        _has_billing_rows = _bill_lkp[_bill_lkp["billing_amount"] > 0].copy()
        _has_billing_rows = _has_billing_rows[_has_billing_rows["billed_hours"] > 0]
        _avg_price = (_has_billing_rows.groupby("client")
                      .apply(lambda g: g["billing_amount"].sum() / g["billed_hours"].sum())
                      .to_dict())
        _missing_bill["מחיר_ממוצע"]        = _missing_bill["client"].map(_avg_price).fillna(70.0)
        _missing_bill["הכנסה_אבודה"]       = (_missing_bill["שעות"] * _missing_bill["מחיר_ממוצע"]).round(0)
        _missing_bill = _missing_bill.sort_values("הכנסה_אבודה", ascending=False)

        _total_miss = float(_missing_bill["הכנסה_אבודה"].sum())
        st.error(f"⚠️ {len(_missing_bill)} צירופי לקוח-חודש לא חויבו — הכנסה אבודה משוערת: ₪{_total_miss:,.0f}")

        _miss_disp = _missing_bill.rename(columns={
            "month":"חודש","client":"לקוח","שעות":"שעות בצינור",
            "עלות":"עלות עובדים","מחיר_ממוצע":"מחיר/שעה ממוצע","הכנסה_אבודה":"הכנסה אבודה"
        })[["חודש","לקוח","שעות בצינור","עלות עובדים","מחיר/שעה ממוצע","הכנסה אבודה"]]
        _miss_t = _with_total_row(
          _miss_disp, label_col="חודש", empty_cols={"לקוח"},
          recalc={"מחיר/שעה ממוצע":
            lambda x: (x["הכנסה אבודה"].sum()/x["שעות בצינור"].sum())
                       if x["שעות בצינור"].sum()>0 else 0},
        )
        _miss_sty = _miss_t.style.format({
            "שעות בצינור":     "{:,.0f}h",
            "עלות עובדים":     "₪{:,.0f}",
            "מחיר/שעה ממוצע":  "₪{:.1f}",
            "הכנסה אבודה":     "₪{:,.0f}",
        }, na_rep="")
        _miss_sty = _hl_total_row(_miss_sty)
        st.dataframe(_miss_sty, use_container_width=True, hide_index=True,
                      height=min(420, 42 + len(_miss_t) * 35))

        st.download_button("⬇️ הורד רשימה (CSV)",
            _miss_disp.to_csv(index=False, encoding="utf-8-sig"),
            "missing_billing.csv", "text/csv")
        st.markdown("---")
      else:
        st.success("✅ כל הלקוחות עם שעות עבודה חויבו")
        st.markdown("---")

      # ── עלויות פנימיות (Overhead) — REMOVED from Billing tab ──────────────
      # Moved exclusively to Conclusions tab section 8 (more comprehensive
      # there: includes zero-hour overhead AND total summary). Avoid showing
      # it in two places.
      if False:  # was: if len(_internal_costs) > 0:
        with st.expander("עלויות פנימיות (Overhead) — ישויות שלנו", expanded=True):
          _int_total_cost  = float(_internal_costs["עלות"].sum())
          _int_total_hours = float(_internal_costs["שעות"].sum())
          _names = " · ".join(internal_list())

          st.info(
              f"ℹ️ {_names} — ישות פנימית של החברה (לא לקוח). "
              f"סך עלויות פנימיות: **₪{_int_total_cost:,.0f}** "
              f"על פני {_int_total_hours:,.0f} שעות. "
              "תקין שאין חיוב — אלה עובדי הנהלה / אדמיניסטרציה."
          )

          _int_disp = (_internal_costs
                       .rename(columns={"month":"חודש","client":"ישות",
                                        "שעות":"שעות","עלות":"עלות"})
                       [["חודש","ישות","שעות","עלות"]]
                       .sort_values("חודש"))
          st.dataframe(
              _int_disp.style.format({
                  "שעות": "{:,.0f}h",
                  "עלות": "₪{:,.0f}",
              }),
              use_container_width=True, hide_index=True,
              height=min(300, 42 + len(_int_disp) * 35))
          st.markdown("---")

  _warn_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "output","cache","warnings.parquet")
  _SEVERITY = {
    "missing_client":"🔴 קריטי","missing_site":"🔴 קריטי",
    "no_standard":"🔴 קריטי","zero_hours":"🔴 קריטי",
    "duplicate":"🟠 בינוני","abnormal_hours":"🟠 בינוני",
    "missing_country":"🟢 השלמה","excel_only":"🟢 השלמה","high_shortage":"🟢 השלמה",
  }
  _FIX = {
    "missing_client":"הוסף עובד ל-costs.xlsx",
    "missing_site":"הוסף שם פרויקט ל-costs.xlsx",
    "missing_country":"הוסף מדינה ל-costs.xlsx",
    "excel_only":"בדוק אם העובד עזב / בעיה ב-PDF",
    "duplicate":"בדוק כפילות ב-costs.xlsx או ב-PDF",
    "zero_hours":"בדוק אם עובד עזב באמצע החודש",
    "abnormal_hours":"בדוק תקינות PDF — h100>280 חריג",
    "no_standard":"הוסף תקן ל-תקן.xlsx",
    "high_shortage":"אשר חיוב תקן גבוה עם הלקוח",
  }
  with st.expander("🚨 התראות ובעיות נתונים", expanded=True):
    if os.path.exists(_warn_path):
      _wdf_all = pd.read_parquet(_warn_path)
      # KPIs global
      _wkc=st.columns(4)
      _wkc[0].metric('סה"כ התראות',len(_wdf_all))
      _critical=len(_wdf_all[_wdf_all["category"].isin(["missing_client","no_standard","zero_hours"])])
      _wkc[1].metric("קריטיות",_critical)
      _last_m=max(all_months, key=_mkey) if all_months else ""
      _wkc[2].metric(f"חודש אחרון ({_last_m})",
                     len(_wdf_all[_wdf_all["month"]==_last_m]) if "month" in _wdf_all.columns else 0)
      _wkc[3].metric("עובדים מושפעים",_wdf_all["employee_id"].nunique() if "employee_id" in _wdf_all.columns else 0)
      _wkc2=st.columns(4)
      _wkc2[0].metric("סוגי בעיות",_wdf_all["category"].nunique() if "category" in _wdf_all.columns else 0)
      _wkc2[1].metric("חודשים מושפעים",_wdf_all["month"].nunique() if "month" in _wdf_all.columns else 0)
      _aff_emps=set(_wdf_all["employee_id"].astype(str)) if "employee_id" in _wdf_all.columns else set()
      _wkc2[2].metric("לקוחות מושפעים",df[df["employee_id"].astype(str).isin(_aff_emps)]["client"].nunique() if _aff_emps else 0)
      _wkc2[3].metric("ממוצע התראות/עובד",f"{len(_wdf_all)/max(_wdf_all['employee_id'].nunique(),1):.1f}" if "employee_id" in _wdf_all.columns else "—")
      # filter to selection
      _wdf=_wdf_all.copy()
      if "month" in _wdf.columns:
        _wdf=_wdf[_wdf["month"].isin(df["month"].unique())]
      if _wdf.empty:
        st.success("✅ אין התראות פעילות בטווח המסונן")
      else:
        _wdf["category_heb"]=_wdf["category"].map(lambda c:_ALERT_HEB.get(c,c))
        _wdf["חומרה"]=_wdf["category"].map(_SEVERITY).fillna("⚪ לא ידוע")
        _wdf["פתרון מוצע"]=_wdf["category"].map(_FIX).fillna("")
        # pie + severity breakdown
        if HAS_PLOTLY:
          _pp,_pb=st.columns([1,2])
          with _pp:
            _wcat=_wdf["category_heb"].value_counts()
            _chart(go.Figure(go.Pie(labels=_wcat.index.tolist(),values=_wcat.values.tolist(),
              hole=0.5,textinfo="percent",textfont_size=10)).update_layout(**{**_PL,"height":240},showlegend=True,
              legend=dict(font=dict(size=9))))
            st.caption("התפלגות סוגי ההתראות בנתונים — הפיצוח הכי גדול = הסוג שדורש טיפול ראשון.")
          with _pb:
            st.markdown("##### לפי חומרה")
            for sev,cnt in _wdf["חומרה"].value_counts().items():
              st.markdown(f"**{sev}:** {cnt} התראות")
        # summary table
        _wsum=(_wdf.groupby("category_heb",as_index=False).size()
               .rename(columns={"size":"כמות"}).sort_values("כמות",ascending=False))
        _wsum.insert(0,"#",range(1,len(_wsum)+1))
        st.dataframe(_wsum.rename(columns={"category_heb":"סוג בעיה"}),use_container_width=True,hide_index=True)

        with st.expander("פירוט עם פתרונות", expanded=True):
          _wdf2=_wdf.rename(columns={"month":"חודש","employee_id":"מס' עובד",
                                       "category_heb":"סוג","issue":"תיאור"})
          if "category" in _wdf2.columns: _wdf2=_wdf2.drop(columns=["category"])
          _wdf2=_wdf2[[c for c in ["חודש","מס' עובד","חומרה","סוג","תיאור","פתרון מוצע"] if c in _wdf2.columns]]
          st.dataframe(_wdf2.head(200),use_container_width=True,hide_index=True,height=min(600,42+len(_wdf2)*35))
          st.download_button("⬇️ הורד טבלה (CSV)",_wdf2.to_csv(index=False,encoding="utf-8-sig"),
                             "warnings.csv","text/csv")
    else:
      st.info("אין קובץ התראות. הרץ את ה-preprocessor כדי לייצר נתונים.")

    # ══════════════════════════════════════════════════════════════════════════════
    # 8 — מסקנות (דוח מנהלים)
    # ══════════════════════════════════════════════════════════════════════════════
st.session_state.setdefault("_perf",[]).append(("render tab: חיוב ותקן (Billing)",
   _time_mod.perf_counter()-_t_billing))
_t_summary = _time_mod.perf_counter()
with t8:
  # Outer "📋 מסקנות — דוח מנהלים" expander removed — sections render flat in the tab.
  with st.container():

    # Month-to-analyze: implicitly the LATEST month in the filtered data
    # (previously: explicit "חודש לניתוח" selectbox — removed per user request).
    # Section 1 (סיכום ביצועים KPIs) also removed — duplicates the global KPI strip.
    _all_mn = sorted(df["month"].dropna().unique().tolist(), key=_mkey)
    _mn_month = _all_mn[-1] if _all_mn else ""
    _mn  = df[df["month"] == _mn_month].copy()
    _pms = _prev_m(_mn_month)
    # Previous month: pull from _raw_hist (not date-filtered) so MoM comparison
    # still works when user has only one month selected globally.
    _pmd = (_raw_hist[_raw_hist["month"] == _pms].copy()
            if _pms and _pms in _raw_hist["month"].values else pd.DataFrame())

    # ── Canonical metrics for the conclusions scope (last month in range).
    # Routes through the SAME calculate_metrics() function the KPI strip uses
    # → no possibility of a number on the main page disagreeing with the
    # same number in the conclusions tab when the user picks a single month.
    _M_mn  = calculate_metrics(_mn,  label=f"מסקנות ({_mn_month})")
    _M_pmd = calculate_metrics(_pmd, label=f"מסקנות (חודש קודם {_pms})") if not _pmd.empty else None

    def _delta(cur, prev, fmt="+.1f"):
      if prev and prev != 0: return f"{(cur-prev)/abs(prev)*100:{fmt}}%"
      return None

    # Aliases — keep the short names that the rest of the conclusions section
    # uses, but pull every value from _M_mn / _M_pmd. NO direct sums anywhere.
    _rev       = _M_mn["total_revenue"]
    _cost      = _M_mn["total_cost"]
    _prof      = _M_mn["gross_profit"]
    _marg      = _M_mn["margin_pct"]
    _emp_n     = _M_mn["employee_count"]
    _ot_h      = _M_mn["overtime_hours"]
    _tot_h     = _M_mn["total_hours"]
    _ot_pct    = _M_mn["ot_pct"]
    if _M_pmd is not None:
      _prev_rev = _M_pmd["total_revenue"]
      _prev_cost = _M_pmd["total_cost"]
      _prev_prof = _M_pmd["gross_profit"]
      _prev_marg = _M_pmd["margin_pct"]
      _prev_emp  = _M_pmd["employee_count"]
      _prev_ot_h = _M_pmd["overtime_hours"]
      _prev_ot_pct = _M_pmd["ot_pct"]
    else:
      _prev_rev = _prev_cost = _prev_prof = _prev_marg = 0.0
      _prev_emp = 0
      _prev_ot_h = 0.0
      _prev_ot_pct = 0.0
    # `_ot_c` kept for legacy callers (heatmaps below); _prev_ot_pct / _prev_tot_h
    # were already populated above from _M_pmd.
    _ot_c = [c for c in ("h125","h150","h175","h200") if c in _mn.columns]
    _prev_tot_h = _M_pmd["total_hours"] if _M_pmd is not None else 1.0

    # Shared chronological months list — defined ONCE here so any section
    # (e.g. 11 OT analysis or 9 cost/hour analysis) can use it regardless
    # of source order. Uses _raw_hist for full historical context.
    _cph_months = sorted(_raw_hist["month"].unique(),
                         key=lambda m: int(m[3:]) * 100 + int(m[:2]))

      # ════════════════════════════════════════════════════════════════════════
      # 4. המלצות פעולה
      # ════════════════════════════════════════════════════════════════════════

    # Pre-compute alerts list BEFORE rendering the expander, so we can
    # show count + total-impact badges in the expander's title.
    _alerts = []
    if not _pmd.empty:
      # ── helper: pull abs(₪) impact for each rule ──────────────────────
      def _add_alert(*row): _alerts.append(row)
      if _prev_cost > 0:
        _cd = (_cost - _prev_cost) / _prev_cost * 100
        _delta_cost = _cost - _prev_cost
        if _cd > 10:
          _add_alert("high","📈","עלייה חדה בעלויות מול חודש קודם",
                      abs(_delta_cost),
                      f"+{_delta_cost/1000:,.0f}K ({_cd:+.1f}%)",
                      "בדוק העלאות שכר חדשות, גידול בכמות עובדים או "
                      "שינוי בתמהיל שעות (100% מול שעות נוספות).",
                      False, "כלל החברה")
        elif _cd < -10:
          _add_alert("low","📉","ירידה בעלויות מול חודש קודם",
                      abs(_delta_cost),
                      f"{_delta_cost/1000:,.0f}K ({_cd:+.1f}%)",
                      "התוצאה חיובית — שמור על הרמה והמשך לעקוב במגמה רב-חודשית.",
                      True, "כלל החברה")
      if _prev_rev > 0 and _rev > 0:
        _rd2 = (_rev - _prev_rev) / _prev_rev * 100
        _delta_rev = _rev - _prev_rev
        if _rd2 < -15:
          _add_alert("high","💰","ירידה חדה בהכנסות מול חודש קודם",
                      abs(_delta_rev),
                      f"{_delta_rev/1000:,.0f}K ({_rd2:+.1f}%)",
                      "בדוק לקוחות שירדו בנפח עבודה או סיימו עבודה. "
                      "התקשר ללקוחות בולטים שירדו ובחן הזדמנויות חדשות.",
                      False, "כלל החברה")
        elif _rd2 > 15:
          _add_alert("low","💰","עלייה בהכנסות מול חודש קודם",
                      abs(_delta_rev),
                      f"+{_delta_rev/1000:,.0f}K ({_rd2:+.1f}%)",
                      "מצוין — בדוק מהיכן הגיע הגידול ושכפל את ההצלחה.",
                      True, "כלל החברה")
      if _prev_ot_pct > 0:
        _ot_d = _ot_pct - _prev_ot_pct
        if _ot_d > 5:
          _extra_h = (_ot_pct - _prev_ot_pct) / 100 * _tot_h
          _extra_prem = _extra_h * (TC / TH if TH > 0 else 0) * 0.40
          _add_alert("med","⏱️","עלייה משמעותית בשעות נוספות",
                      abs(_extra_prem),
                      f"~{_extra_prem/1000:,.0f}K פרמיה נוספת ({_ot_d:+.1f}pp)",
                      "בחן אם ניתן לחלק עומס בין יותר עובדים, "
                      "או לתמחר את הפרמיה ללקוחות בהסכמים העתידיים.",
                      False, "כלל החברה")
      _PRIO_ORDER = {"high":0, "med":1, "low":2}
      _alerts.sort(key=lambda a: (_PRIO_ORDER.get(a[0],3), -a[3]))

    # Compose a badge-rich expander title — counts + total impact + dot.
    _open_alerts   = [a for a in _alerts if a[0] != "low"]
    _total_impact  = sum(a[3] for a in _open_alerts)
    _high_count    = sum(1 for a in _alerts if a[0]=="high")
    if _high_count:
      _dot = "🔴"
    elif _open_alerts:
      _dot = "🟠"
    else:
      _dot = "🟢"
    _expander_label = (
      f"{_dot}  5. התרעות ומגמות"
      + (f"  ·  {len(_open_alerts)} פתוחות" if _open_alerts else "  ·  אין חריגות")
      + (f"  ·  ₪{_total_impact/1000:,.0f}K השפעה משוערת" if _total_impact else "")
    )

    with st.expander(_expander_label, expanded=True):
      # short caption above the insight cards — sets reader expectation
      st.markdown(
        '<div style="font-size:11.5px;color:#64748B;margin-bottom:10px">'
        'כל התרעה מציגה: <b>בעיה · השפעה כספית · עדיפות · המלצה לפעולה</b>. '
        'התרעות ממוינות מהשפעה גבוהה לנמוכה.'
        '</div>', unsafe_allow_html=True)

      if not _pmd.empty:
        # _alerts was built BEFORE the expander (see badge-title block above);
        # we just render here. Don't re-build — would double-count.
        if _alerts:
          for _prio, _ic, _prob, _imp_abs, _imp_str, _act, _pos, _who in _alerts:
            _exec_ins(_prio, _ic, _prob, impact=_imp_str, who=_who,
                       action=_act, impact_positive=_pos)

          # ── Action table — same insights as cards above, but as a single
          # tabular view with priority, impact, who, action, and a Status
          # column the user can update in-table. Also exportable to CSV.
          st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
          st.markdown(
            '<div style="font-size:12px;font-weight:700;color:#0F172A;'
            'margin-bottom:6px">📋 טבלת משימות פעולה (מיון לפי השפעה)</div>',
            unsafe_allow_html=True)
          _PRIO_HEB = {"high":"🔴 גבוהה","med":"🟠 בינונית","low":"🟢 נמוכה"}
          _action_rows = []
          for i, (_prio, _ic, _prob, _imp_abs, _imp_str, _act, _pos, _who) in enumerate(_alerts, 1):
            _action_rows.append({
              "#": i,
              "עדיפות": _PRIO_HEB.get(_prio, _prio),
              "בעיה": _prob,
              "השפעה כספית": _imp_str,
              "₪ השפעה (מוחלט)": int(_imp_abs),
              "מקור": _who,
              "המלצה לפעולה": _act,
              "סטטוס": "פתוח",
            })
          _action_df = pd.DataFrame(_action_rows)
          st.data_editor(
            _action_df,
            use_container_width=True, hide_index=True,
            disabled=["#","עדיפות","בעיה","השפעה כספית",
                       "₪ השפעה (מוחלט)","מקור","המלצה לפעולה"],
            column_config={
              "₪ השפעה (מוחלט)": st.column_config.NumberColumn(
                "₪ השפעה (מוחלט)", format="₪%d",),
              "סטטוס": st.column_config.SelectboxColumn(
                "סטטוס", options=["פתוח","בטיפול","טופל"], required=True),
            },
            key="actions_editor_t5",
          )
          st.download_button(
            "⬇️ ייצוא טבלת משימות (CSV)",
            _action_df.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"action_items_{_mn_month}.csv",
            mime="text/csv",
            key="dl_actions_t5",
          )
        else:
          _exec_ins("low", "✅", "אין חריגות מהותיות מול חודש קודם",
                     action="המשך לעקוב — אין צורך בפעולה כרגע.")
      else:
        st.caption("אין נתוני חודש קודם להשוואה")

    # Section 1 (סיכום ביצועים) removed — KPIs already shown globally above tabs.

    # ════════════════════════════════════════════════════════════════════════
    # 6. צפי החודש הבא
      # ════════════════════════════════════════════════════════════════════════

    with st.expander("6. צפי החודש הבא (ממוצע משוקלל 6 חודשים)", expanded=True):
      # ── "Forecast" framing — make crystal clear this is a model, not a fact
      st.markdown(
        '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;'
        'margin-bottom:10px">'
        '<span style="background:#EFF6FF;color:#1E3A8A;border:1px solid #BFDBFE;'
        'border-radius:99px;padding:4px 12px;font-size:11px;font-weight:800;'
        'letter-spacing:.4px;text-transform:uppercase">🔮 תחזית · לא נתון סופי</span>'
        '<span style="font-size:12px;color:#64748B">'
        'התחזית מבוססת על <b>ממוצע משוקלל</b> של 6 חודשים אחרונים, '
        'עם משקל גבוה יותר לחודשים קרובים. '
        'אינה לוקחת בחשבון אירועים מתוכננים (כניסת לקוח חדש, סיום הסכם וכו\').'
        '</span></div>',
        unsafe_allow_html=True,
      )
      _hist3 = sorted(all_months, key=_mkey)
      # get last 6 months UP TO AND INCLUDING the selected month
      _before = [m for m in _hist3 if _mkey(m) <= _mkey(_mn_month)][-6:]

      # Weights: exponential decay — latest month dominates but older still contribute.
      _WEIGHT_TABLE = {
        6: [0.05, 0.08, 0.12, 0.17, 0.23, 0.35],
        5: [0.07, 0.11, 0.16, 0.24, 0.42],
        4: [0.10, 0.16, 0.24, 0.50],
        3: [0.17, 0.33, 0.50],
        2: [0.40, 0.60],
      }

      if len(_before) >= 2:
        _w3 = _WEIGHT_TABLE.get(len(_before), _WEIGHT_TABLE[2])
        _fr, _fc_list = [], []
        for _m3 in _before:
          # Use _raw_hist so forecast has full 6-month history even when
          # user selects only one month in the global date filter.
          # Pull rev + cost via calculate_metrics — single source of truth
          # (replaces the old _mn_rev / _mn_cost helpers that were removed
          # when the conclusions block migrated to _M_mn).
          _d3   = _raw_hist[_raw_hist["month"] == _m3]
          _M_d3 = calculate_metrics(_d3, label=f"forecast({_m3})")
          _fr.append(_M_d3["total_revenue"])
          _fc_list.append(_M_d3["total_cost"])
        _fc_rev  = sum(r * w for r, w in zip(_fr,      _w3[-len(_fr):]))
        _fc_cost = sum(c * w for c, w in zip(_fc_list, _w3[-len(_fc_list):]))
        _fc_prof = _fc_rev - _fc_cost
        _fc_marg = _fc_prof / _fc_rev * 100 if _fc_rev > 0 else 0

        # ── compute next month label ─────────────────────────────────────
        _last_m = _before[-1]
        _lmo, _lyr = int(_last_m[:2]), int(_last_m[3:])
        _nmo = _lmo + 1; _nyr = _lyr
        if _nmo > 12: _nmo, _nyr = 1, _lyr + 1
        _next_m_str = f"{_nmo:02d}-{_nyr}"

        # ── KPIs ──────────────────────────────────────────────────────────
        _f1, _f2, _f3, _f4 = st.columns(4)
        _f1.metric("צפי הכנסות", f"₪{_fc_rev/1000:.0f}K")
        _f2.metric("צפי עלות",   f"₪{_fc_cost/1000:.0f}K")
        _f3.metric("צפי רווח",   f"₪{_fc_prof/1000:.0f}K")
        _f4.metric("צפי מרג׳ין", f"{_fc_marg:.1f}%")

        # ── Chart: history bars + forecast bar ────────────────────────────
        if HAS_PLOTLY:
          _fp = list(zip(_fr, _fc_list))
          _hist_prof = [r - c for r, c in _fp]
          _x_all  = _before + [_next_m_str]
          _y_rev  = list(_fr) + [_fc_rev]
          _y_cost = list(_fc_list) + [_fc_cost]
          _y_prof = list(_hist_prof) + [_fc_prof]
          # Mark forecast bar with different color
          _rev_clr  = [BLUE]*len(_before) + ["#3B82F6"]
          _cost_clr = [RED]*len(_before)  + ["#FCA5A5"]
          _prof_clr = [GREEN]*len(_before) + ["#86EFAC"]

          fig_fc = go.Figure()
          fig_fc.add_trace(go.Bar(
            name="הכנסות", x=_x_all, y=_y_rev, marker_color=_rev_clr, opacity=0.85,
            text=[f"₪{v/1000:.0f}K" for v in _y_rev], textposition="outside",
            hovertemplate="<b>%{x}</b><br>הכנסות: ₪%{y:,.0f}<extra></extra>"))
          fig_fc.add_trace(go.Bar(
            name="עלות", x=_x_all, y=_y_cost, marker_color=_cost_clr, opacity=0.85,
            text=[f"₪{v/1000:.0f}K" for v in _y_cost], textposition="outside",
            hovertemplate="<b>%{x}</b><br>עלות: ₪%{y:,.0f}<extra></extra>"))
          fig_fc.add_trace(go.Scatter(
            name="רווח", x=_x_all, y=_y_prof, mode="lines+markers+text",
            line=dict(color="#0F6E56", width=3, dash="dot"),
            marker=dict(size=10, color=_prof_clr,
                        line=dict(color="#0F6E56", width=2)),
            text=[f"₪{v/1000:.0f}K" for v in _y_prof], textposition="top center",
            hovertemplate="<b>%{x}</b><br>רווח: ₪%{y:,.0f}<extra></extra>"))
          # Highlight forecast column with a vertical band
          fig_fc.add_vrect(
            x0=len(_before)-0.5, x1=len(_before)+0.5,
            fillcolor="#F1F5F9", opacity=0.5, layer="below", line_width=0,
            annotation_text="צפי", annotation_position="top",
            annotation=dict(font=dict(size=11, color="#64748B")))
          fig_fc.update_layout(
            **{**_PL, "height":340,
               "hoverlabel": dict(bgcolor="#0F172A", font_color="#FFFFFF")},
            barmode="group",
            legend=dict(orientation="h", y=-0.15, x=0.5, xanchor="center"),
            xaxis=dict(showgrid=False, tickangle=-30),
            yaxis=dict(tickprefix="₪", showgrid=True, gridcolor="#F1F5F9"))
          _chart(fig_fc)

        st.caption(
          f"מבוסס על {len(_before)} חודשים אחרונים: {', '.join(_before)} | "
          f"משקולות (מהישן לחדש): {' · '.join(f'{w*100:.0f}%' for w in _w3)}"
        )

        # ── Risk flags ────────────────────────────────────────────────────
        _risks = []
        if _fc_marg < 5: _risks.append("מרג׳ין צפוי נמוך מאוד")
        if _fc_rev < _rev * 0.85: _risks.append("ירידה צפויה בהכנסות >15%")
        if _fc_cost > _fc_rev: _risks.append("עלויות עולות על הכנסות בתחזית")
        if _risks:
          st.warning("⚠️ **סיכוני תחזית:** " + " | ".join(_risks))
        else:
          _ins("green", "✅", "תחזית חיובית", f"צפי מרג׳ין {_fc_marg:.1f}% — ביצועים יציבים")
      else:
        st.info("נדרשים לפחות 2 חודשי נתונים לתחזית")

      # ════════════════════════════════════════════════════════════════════════
      # 2. ניתוח לקוחות
    # ════════════════════════════════════════════════════════════════════════

    with st.expander("2. ניתוח לקוחות", expanded=True):
      _cl2a, _cl2b, _cl2c = st.columns(3)

      # Client profitability table
      _cl_grp = _mn.groupby("client", as_index=False).agg(
        hours=("total_hours","sum"), cost=("cost","sum")
      )
      if "billing_amount" in _mn.columns:
        _cl_bill2 = _mn.drop_duplicates(["month","client"]).groupby("client", as_index=False).agg(
          billing=("billing_amount","sum")
        )
        _cl_grp = _cl_grp.merge(_cl_bill2, on="client", how="left").fillna(0)
        _cl_grp["profit"] = _cl_grp["billing"] - _cl_grp["cost"]
        _cl_grp["margin"] = (_cl_grp["profit"] / _cl_grp["billing"].replace(0, float("nan")) * 100).round(1)
        _cl_grp_b = _cl_grp[_cl_grp["billing"] > 0]

        with _cl2a:
          st.markdown("**🏆 Top 5 רווחיים**")
          for _, r in _cl_grp_b.nlargest(5, "margin").iterrows():
            m = float(r["margin"]); col = GREEN if m > 15 else AMBER if m > 0 else RED
            st.markdown(f'<span style="color:{col}">●</span> **{str(r["client"])[:28]}** {m:.1f}%', unsafe_allow_html=True)

        with _cl2b:
          st.markdown("**⚠️ דורשים תשומת לב**")
          _bad2 = _cl_grp_b[_cl_grp_b["margin"] < 10].nsmallest(5, "margin")
          if _bad2.empty:
            st.success("כולם מעל 10%")
          else:
            for _, r in _bad2.iterrows():
              m = float(r["margin"]); col = RED if m < 0 else AMBER
              st.markdown(f'<span style="color:{col}">●</span> **{str(r["client"])[:28]}** {m:.1f}%', unsafe_allow_html=True)

      with _cl2c:
        st.markdown("**🆕 חדשים / נושרים**")
        if not _pmd.empty:
          _new_cl = set(_mn["client"].unique()) - set(_pmd["client"].unique())
          _lost_cl = set(_pmd["client"].unique()) - set(_mn["client"].unique())
          if _new_cl:
            for cl in list(_new_cl)[:3]:
              st.markdown(f'<span style="color:{GREEN}">+</span> {cl[:30]}', unsafe_allow_html=True)
          if _lost_cl:
            for cl in list(_lost_cl)[:3]:
              st.markdown(f'<span style="color:{RED}">−</span> {cl[:30]}', unsafe_allow_html=True)
          if not _new_cl and not _lost_cl:
            st.write("אין שינוי")
        else:
          st.caption("אין נתון חודש קודם")

      # ════════════════════════════════════════════════════════════════════════
      # 3. ניתוח עלויות
      # ════════════════════════════════════════════════════════════════════════

    with st.expander("3. ניתוח עלויות", expanded=True):
      _c3a, _c3b = st.columns(2)

      with _c3a:
        st.markdown("**💡 פירוט שעות נוספות**")
        _ot_map = {"h125":"125%","h150":"150%","h175":"175%","h200":"200%"}
        # Weighted CPH for the selected month (was simple-mean — biased)
        _h_sum_mn = float(_mn["total_hours"].sum()) if not _mn.empty else 0
        _c_sum_mn = float(_mn["cost"].sum()) if not _mn.empty else 0
        _avg_cph = (_c_sum_mn / _h_sum_mn) if _h_sum_mn > 0 else 0
        _ot_total_prem = 0.0
        for _hc, _pct_lbl in _ot_map.items():
          if _hc in _mn.columns:
            _h = float(_mn[_hc].sum())
            if _h > 0:
              _mult = {"h125":0.25,"h150":0.50,"h175":0.75,"h200":1.0}[_hc]
              _prem = _h * _mult * _avg_cph
              _ot_total_prem += _prem
              st.markdown(f"● **{_pct_lbl}**: {_h:,.0f}h → פרמיה ₪{_prem:,.0f}")
        if _ot_total_prem > 0:
          st.markdown(f"**סה״כ עלות OT: ₪{_ot_total_prem:,.0f}** ({_ot_total_prem/_cost*100:.1f}% מהעלות)")

      with _c3b:
        st.markdown("**🔴 דולפי עלות (עלות > הכנסה)**")
        if "billing_amount" in _mn.columns and "billing" in _cl_grp.columns:
          _drain = _cl_grp[(_cl_grp["profit"] < 0) & (_cl_grp["billing"] > 0)].sort_values("profit")
          if _drain.empty:
            st.success("✅ אין לקוחות מפסידים")
          else:
            for _, r in _drain.head(5).iterrows():
              st.markdown(f'<span style="color:{RED}">●</span> **{str(r["client"])[:30]}**: הפסד ₪{abs(float(r["profit"])):,.0f}', unsafe_allow_html=True)
        else:
          st.caption("נדרשים נתוני חיוב")

        # High-cost employees — weighted CPH per employee (cost÷hours), not simple mean
        if "cost_per_hour" in _mn.columns and _avg_cph > 0:
          _hi_cost = (_mn.groupby("employee_id", as_index=False)
                        .agg(name=("employee_name","first"),
                             _cost=("cost","sum"), h=("total_hours","sum"))
                        .assign(cph=lambda d: d["_cost"]/d["h"].replace(0,float("nan")))
                        .dropna(subset=["cph"])
                        .query(f"cph > {_avg_cph * 1.5:.0f} and h > 20")
                        .nlargest(3, "cph"))
          if not _hi_cost.empty:
            st.markdown("**עובדים יקרים מהממוצע ×1.5:**")
            for _, r in _hi_cost.iterrows():
              st.markdown(f"● **{str(r['name'])[:25]}** ({r['employee_id']}): ₪{r['cph']:.0f}/h")


    with st.expander("7. המלצות תקן לכל לקוח ואתר", expanded=True):
      st.caption(
        "התעריף המומלץ מחושב מהחיוב בפועל (income.xlsx) מחולק בשעות עבודה. "
        "מבוסס על כל החודשים — כדי לקבל הערכה יציבה ולא מושפעת מחודש בודד."
      )

      # אגרגציה לפי (לקוח, אתר) על כל החודשים — יציב יותר ממדגם של חודש אחד
      _all = df.copy()
      # סינון: רק שורות עם שעות בפועל
      _all = _all[_all["total_hours"] > 0]
      # החרגת ישויות פנימיות
      try:
        from core.internal_entities import is_internal as _is_int
        _all = _all[~_all["client"].astype(str).map(_is_int)]
      except Exception:
        pass

      if _all.empty or "billing_amount" not in _all.columns:
        st.info("אין נתוני חיוב או שעות מספיקים לחישוב המלצה.")
      else:
        # סה"כ שעות לכל (לקוח, אתר) ולכל לקוח
        _ag_site = _all.groupby(["client","site"], as_index=False).agg(
            שעות=("total_hours","sum"),
            תקן_נוכחי=("hourly_rate","first"),
            billing_kind=("billing_kind","first"),
            match_type=("match_type","first"),
        )
        _ag_cli  = _all.groupby("client", as_index=False).agg(
            client_hours=("total_hours","sum"),
        )
        # חיוב כולל לכל לקוח (deduped)
        _ag_bill = (_all.drop_duplicates(["month","client"])
                    .groupby("client", as_index=False)
                    .agg(client_billing=("billing_amount","sum")))
        _r = _ag_site.merge(_ag_cli, on="client").merge(_ag_bill, on="client", how="left")
        _r["client_billing"] = _r["client_billing"].fillna(0)

        # חלוקת החיוב לאתרים לפי משקל השעות
        _r["חיוב_מחושב"]  = (_r["client_billing"] *
                              (_r["שעות"] / _r["client_hours"]).replace([float("inf"), -float("inf")], 0)
                             ).fillna(0).round(0)
        _r["תעריף_מומלץ"] = (_r["חיוב_מחושב"] / _r["שעות"].replace(0, float("nan"))).round(1)

        # מי בלי תקן נכון לעכשיו?
        _r["תקן_נוכחי"]  = _r["תקן_נוכחי"].fillna(0)
        _r["הפרש_שח"]    = (_r["תעריף_מומלץ"] - _r["תקן_נוכחי"]).round(1)
        _r["הפרש_אחוז"]  = ((_r["תעריף_מומלץ"] / _r["תקן_נוכחי"].replace(0, float("nan")) - 1) * 100
                             ).round(0).fillna(0)

        # המלצה טקסטואלית
        def _reco(row):
          if row["שעות"] < 50:
            return "ℹ️ מדגם קטן — לא להחליט לפי זה"
          if row["match_type"] == "none" or row["תקן_נוכחי"] <= 0:
            return f"🆕 אין תקן — להוסיף לתקן.xlsx ב-₪{row['תעריף_מומלץ']:.1f}/שעה"
          if row["billing_kind"] not in ("hourly", "hourly_extra", "hourly_with_floor", None) and not pd.isna(row["billing_kind"]):
            return "⚙️ חיוב לא שעתי — לבדוק ידנית"
          d = float(row["הפרש_אחוז"])
          if abs(d) < 5:
            return "✅ תקן מדויק"
          if d > 0:
            return f"⬆️ העלאה ל-₪{row['תעריף_מומלץ']:.1f} (פער +{d:.0f}%)"
          return f"⬇️ הורדה ל-₪{row['תעריף_מומלץ']:.1f} (פער {d:.0f}%)"
        _r["המלצה"] = _r.apply(_reco, axis=1)

        # סינון: רק שורות עם נפח עבודה משמעותי (מעל 20 שעות סה"כ)
        _r = _r[_r["שעות"] >= 20].copy()

        # סיכום
        _need_new   = int((_r["match_type"] == "none").sum())
        _gap_big    = int((_r["הפרש_אחוז"].abs() >= 15).sum())
        _gap_small  = int(((_r["הפרש_אחוז"].abs() >= 5) & (_r["הפרש_אחוז"].abs() < 15)).sum())
        _ok         = int((_r["הפרש_אחוז"].abs() < 5).sum())

        _s1, _s2, _s3, _s4 = st.columns(4)
        _s1.metric("✅ תקן מדויק (פחות מ-5%)", _ok)
        _s2.metric("⚠️ פער קטן (5-15%)", _gap_small)
        _s3.metric("🚨 פער גדול (מעל 15%)", _gap_big)
        _s4.metric("🆕 חסר תקן לחלוטין", _need_new)

        # סינון תצוגה
        _f1, _f2 = st.columns([2, 3])
        with _f1:
          _show = st.selectbox(
            "תצוגה",
            ["הכל", "רק פערים גדולים (>15%)", "רק חסרי תקן", "רק תקן מדויק"],
            key="reco_filter",
          )
        _disp = _r.copy()
        if _show == "רק פערים גדולים (>15%)":
          _disp = _disp[_disp["הפרש_אחוז"].abs() >= 15]
        elif _show == "רק חסרי תקן":
          _disp = _disp[_disp["match_type"] == "none"]
        elif _show == "רק תקן מדויק":
          _disp = _disp[_disp["הפרש_אחוז"].abs() < 5]

        _disp = _disp.sort_values("הפרש_אחוז", key=lambda s: s.abs(), ascending=False)
        _disp_view = _disp.rename(columns={"client":"לקוח","site":"אתר"})[
          ["לקוח","אתר","שעות","תקן_נוכחי","תעריף_מומלץ","הפרש_שח","הפרש_אחוז","המלצה"]
        ]
        # Totals — sum hours/diff; rates and % shown as weighted-volume avg
        _dv_t = _with_total_row(
          _disp_view, label_col="לקוח", empty_cols={"אתר","המלצה"},
          recalc={
            "תקן_נוכחי":   lambda x: (x["תקן_נוכחי"]*x["שעות"]).sum()/x["שעות"].sum()
                                       if x["שעות"].sum()>0 else 0,
            "תעריף_מומלץ": lambda x: (x["תעריף_מומלץ"]*x["שעות"]).sum()/x["שעות"].sum()
                                       if x["שעות"].sum()>0 else 0,
            "הפרש_אחוז":   lambda x: (x["הפרש_שח"].sum()/(x["תקן_נוכחי"]*x["שעות"]).sum()*100)
                                       if (x["תקן_נוכחי"]*x["שעות"]).sum()>0 else 0,
          })
        _dv_sty = _dv_t.style.format({
          "שעות":         "{:,.0f}h",
          "תקן_נוכחי":    "₪{:.1f}",
          "תעריף_מומלץ":  "₪{:.1f}",
          "הפרש_שח":      "₪{:+.1f}",
          "הפרש_אחוז":    "{:+.0f}%",
        }, na_rep="").map(
          lambda v: "color:#DC2626;font-weight:600" if isinstance(v, str) and v.startswith("⬆️")
          else "color:#059669;font-weight:600" if isinstance(v, str) and v.startswith("⬇️")
          else "color:#0F172A;font-weight:600" if isinstance(v, str) and v.startswith("🆕")
          else "",
          subset=["המלצה"]
        )
        _dv_sty = _hl_total_row(_dv_sty)
        st.dataframe(_dv_sty, use_container_width=True, hide_index=True,
                      height=min(590, 42 + len(_dv_t) * 36))

        st.download_button(
          "⬇️ הורד המלצות (CSV)",
          _disp_view.to_csv(index=False, encoding="utf-8-sig"),
          "tekken_recommendations.csv", "text/csv",
        )

        st.caption(
          "🔍 איך לקרוא: התעריף המומלץ הוא ממוצע משוקלל של החיוב בפועל פר שעה. "
          "פער חיובי = הלקוח משלם יותר ממה שכתוב בתקן (יכול להיות בגלל שעות נוספות, מיגורים, או תקן ישן). "
          "פער שלילי = הלקוח משלם פחות ממה שתקן צופה (אולי תת-חיוב או תקן מנופח). "
          "לפעולה: לעדכן את תקן.xlsx או לבדוק עם הלקוח."
        )

      # ════════════════════════════════════════════════════════════════════════
      # 6. תחזית חודש הבא
      # ════════════════════════════════════════════════════════════════════════
      # ════════════════════════════════════════════════════════════════════════
      # 11. ניתוח שעות נוספות — מה ההשפעה האמיתית על הרווח?
      # ════════════════════════════════════════════════════════════════════════

    with st.expander("11. ניתוח שעות נוספות — האם הן יתרון או חיסרון?", expanded=True):
      st.caption(
        "שעות נוספות (h125, h150) הן הזדמנות לרווח נוסף — אם הלקוח משלם פרמיה. "
        "כאן נראים הנתונים האמיתיים לכל לקוח: כמה הוא משלם בפועל מול התקן, "
        "ומה ההשפעה הכוללת על הרווח."
      )

      # ── 11A. סיכום OT לכל החודשים ────────────────────────────────────────
      _ot_rows = []
      for m in _cph_months:
        _d = df[df["month"] == m]
        _h100 = float(_d.get("h100", pd.Series([0])).sum())
        _h125 = float(_d.get("h125", pd.Series([0])).sum())
        _h150 = float(_d.get("h150", pd.Series([0])).sum())
        _h175 = float(_d.get("h175", pd.Series([0])).sum())
        _h200 = float(_d.get("h200", pd.Series([0])).sum())
        _ot   = _h125 + _h150 + _h175 + _h200
        _total = _h100 + _ot
        _bill = float(_d.drop_duplicates(["month","client"])["billing_amount"].sum())
        _cost = float(_d["cost"].sum())
        _ot_rows.append({
          "month": m,
          "h_regular": _h100, "h_ot": _ot,
          "ot_pct": _ot / _total * 100 if _total else 0,
          "billing": _bill, "cost": _cost,
          "rph": _bill / _total if _total else 0,
          "cph": _cost / _total if _total else 0,
          "profit_per_h": (_bill - _cost) / _total if _total else 0,
        })
      _ot_tbl = pd.DataFrame(_ot_rows)

      # Correlation
      if len(_ot_tbl) >= 4:
        _corr_total = _ot_tbl["ot_pct"].corr(_ot_tbl["billing"] - _ot_tbl["cost"])
        _corr_per_h = _ot_tbl["ot_pct"].corr(_ot_tbl["profit_per_h"])

        _o1, _o2, _o3 = st.columns(3)
        _o1.metric("שעות OT סה\"כ (16 חודשים)",
                   f"{int(_ot_tbl['h_ot'].sum()):,}h",
                   help="h125 + h150 + h175 + h200 — שעות מעבר ל-8/יום")
        _o2.metric("מתאם OT עם רווח כולל",
                   f"{_corr_total:+.2f}",
                   help="חיובי = OT מעלה רווח כולל. אצלכם: כן (יותר שעות = יותר רווח)")
        _o3.metric("מתאם OT עם רווח/שעה",
                   f"{_corr_per_h:+.2f}",
                   help="חיובי = OT משפר רווחיות יחסית. שלילי = OT פוגע ברווח/שעה.")

        # Plain-language conclusion
        if _corr_total > 0.3 and _corr_per_h < -0.3:
          _conclusion = (
            "🟡 **OT מעלה את הרווח הכולל אבל מצמצם את הרווח לשעה.** "
            "כלומר: יותר שעות OT = יותר כסף בקופה, אבל פחות יעיל לשעה. "
            "הסיבה: לקוחות לא משלמים את מלוא פרמיית 1.25×/1.5×."
          )
        elif _corr_per_h > 0.3:
          _conclusion = (
            "🟢 **OT הוא יתרון מובהק.** הלקוחות משלמים פרמיה מלאה — "
            "כל שעה נוספת מייצרת רווח גבוה יותר משעה רגילה."
          )
        elif _corr_per_h < -0.3:
          _conclusion = (
            "🔴 **OT פוגע ברווחיות.** העובד מקבל 1.25× אבל הלקוח לא משלם פרמיה. "
            "מומלץ להתייחס לחוזים מחדש או להגביל שעות נוספות."
          )
        else:
          _conclusion = "➖ אין השפעה ברורה של OT על הרווחיות."
        st.markdown(
          f"<div style='background:#F8FAFC;border-right:4px solid #2563EB;"
          f"border-radius:6px;padding:12px 16px;margin:12px 0;font-size:14px'>"
          f"{_conclusion}</div>",
          unsafe_allow_html=True,
        )

      # ── 11B. תעריף בפועל לעומת תקן — לקוח לקוח ────────────────────────
      st.markdown("##### א. תעריף בפועל מול תקן — מי משלם פרמיה ומי לא?")
      _cl_rows = []
      for client, grp in df.groupby("client"):
        _h100 = float(grp.get("h100", pd.Series([0])).sum())
        _ot   = float((grp.get("h125", pd.Series([0])) +
                       grp.get("h150", pd.Series([0])) +
                       grp.get("h175", pd.Series([0])) +
                       grp.get("h200", pd.Series([0]))).sum())
        _total = _h100 + _ot
        if _total < 500:    # ignore tiny clients
          continue
        _base_rate = float(grp[grp["hourly_rate"] > 0]["hourly_rate"].median() or 0)
        _bill = float(grp.drop_duplicates(["month","client"])["billing_amount"].sum())
        _actual_rate = _bill / _total if _total else 0
        # Expected rate if client paid full OT premium:
        _ot_share = _ot / _total
        _expected = _base_rate * ((1 - _ot_share) + _ot_share * 1.30)  # ~30% OT premium average
        _cl_rows.append({
          "client": client,
          "שעות סה\"כ": _total,
          "OT%": _ot / _total * 100,
          "תעריף תקן (₪/h)": _base_rate,
          "תעריף בפועל (₪/h)": _actual_rate,
          "פרמיה ₪/h": _actual_rate - _base_rate if _base_rate else 0,
          "תעריף צפוי עם OT": _expected,
        })
      _cl_tbl = pd.DataFrame(_cl_rows).sort_values("שעות סה\"כ", ascending=False)

      def _color_premium(v):
        if pd.isna(v): return ""
        if v > 5:   return "background-color: #D1FAE5; color: #065F46; font-weight: 700"
        if v < -2:  return "background-color: #FEE2E2; color: #991B1B; font-weight: 700"
        return ""

      if not _cl_tbl.empty:
        # Total row — sum hours; rates are volume-weighted; OT% recomputed
        _cl_t = _with_total_row(
          _cl_tbl, label_col=_cl_tbl.columns[0],
          recalc={
            "OT%":               lambda x: x["OT%"].mean(),
            "תעריף תקן (₪/h)":   lambda x: (x["תעריף תקן (₪/h)"]*x["שעות סה\"כ"]).sum()/x["שעות סה\"כ"].sum() if x["שעות סה\"כ"].sum()>0 else 0,
            "תעריף בפועל (₪/h)": lambda x: (x["תעריף בפועל (₪/h)"]*x["שעות סה\"כ"]).sum()/x["שעות סה\"כ"].sum() if x["שעות סה\"כ"].sum()>0 else 0,
            "פרמיה ₪/h":         lambda x: (x["פרמיה ₪/h"]*x["שעות סה\"כ"]).sum()/x["שעות סה\"כ"].sum() if x["שעות סה\"כ"].sum()>0 else 0,
            "תעריף צפוי עם OT":  lambda x: (x["תעריף צפוי עם OT"]*x["שעות סה\"כ"]).sum()/x["שעות סה\"כ"].sum() if x["שעות סה\"כ"].sum()>0 else 0,
          })
        _cl_sty = (_cl_t.style
          .map(_color_premium, subset=["פרמיה ₪/h"])
          .format({
              "שעות סה\"כ":          "{:,.0f}h",
              "OT%":                  "{:.1f}%",
              "תעריף תקן (₪/h)":      "₪{:.1f}",
              "תעריף בפועל (₪/h)":    "₪{:.1f}",
              "פרמיה ₪/h":            "₪{:+.1f}",
              "תעריף צפוי עם OT":     "₪{:.1f}",
          }, na_rep=""))
        _cl_sty = _hl_total_row(_cl_sty)
        st.dataframe(_cl_sty, use_container_width=True, hide_index=True,
                      height=min(590, 42 + len(_cl_t) * 36))
        st.caption(
          "💡 **איך לקרוא:** *פרמיה* = תעריף בפועל פחות תקן. "
          "🟩 פרמיה חיובית גדולה (>₪5) = הלקוח משלם תוספת OT — שעות נוספות שם רווחיות. "
          "🟥 פרמיה שלילית = הלקוח משלם פחות מהתקן — שעות נוספות שם פוגעות ברווח. "
          "*תעריף צפוי עם OT* = מה היה התעריף אם הלקוח היה משלם 1.30× ממוצע על OT."
        )

      # ── 11C. מסקנה ופעולות ────────────────────────────────────────────
      st.markdown("##### ב. מסקנה — מתי OT הוא יתרון אצלכם?")
      st.info(
        "**הנתונים אצלכם מראים:**\n\n"
        "- שעות נוספות מעלות את **הרווח הכולל** של החודש (יותר שעות = יותר כסף).\n"
        "- אבל **רווח/שעה** יורד מעט בחודשים עם OT גבוה — כי לקוחות לא משלמים פרמיה מלאה.\n"
        "- **לקוחות שמשלמים פרמיה חיובית (🟩 מעל ₪5):** קבוצת טלאור, נח רפפורט וכמה קטנים — "
        "  שעות נוספות שם הן יתרון נקי.\n"
        "- **לקוחות שמשלמים פחות מהתקן (🟥 שלילי):** ולפמן — שעות נוספות שם פוגעות ברווח. "
        "  שווה לבדוק את החוזה.\n\n"
        "**פעולה מומלצת:** לוודא בחוזים חדשים שיש בנד מפורש של תשלום 1.25× ו-1.5× "
        "על שעות נוספות. אצל לקוחות קיימים — לדבר עם הלקוחות שמשלמים פחות מהתקן."
      )

      # ════════════════════════════════════════════════════════════════════════
      # 8. עלות נסתרת — overhead החברה (ישות פנימית + עובדים בלי שעות)
      # ════════════════════════════════════════════════════════════════════════

    with st.expander("9. עלות לשעה — סקירה והסבר חודשי", expanded=True):

      # ── Per-month metrics — built once, used by all sub-sections ─────────────
      # Uses _raw_hist so the per-month analysis covers full history even when
      # only one month is selected globally.
      _cph_months = sorted(_raw_hist["month"].unique(),
                           key=lambda m: int(m[3:]) * 100 + int(m[:2]))
      _data = {}
      for _m in _cph_months:
        _d = _raw_hist[_raw_hist["month"] == _m]
        _h  = float(_d["total_hours"].sum())
        _c  = float(_d["cost"].sum())
        _per_emp = _d.drop_duplicates(["month","employee_id"])
        _data[_m] = {
          "hours":     _h,
          "cost":      _c,
          "cph":       _c / _h if _h > 0 else 0,
          "employees": int(_d["employee_id"].nunique()),
          "gross_salary":     float(_per_emp.get("gross_salary",     pd.Series([0])).sum()),
          "bituach":          float(_per_emp.get("bituach",          pd.Series([0])).sum()),
          "levy":             float(_per_emp.get("levy",             pd.Series([0])).sum()),
          "employment_levy":  float(_per_emp.get("employment_levy",  pd.Series([0])).sum()),
          "pension":          float(_per_emp.get("pension",          pd.Series([0])).sum()),
          "medical_insurance":float(_per_emp.get("medical_insurance",pd.Series([0])).sum()),
        }

      # ────────────────────────────────────────────────────────────────────────
      # ── 9.1 — סטטוס נוכחי (TL;DR) ──────────────────────────────────────────
      # ────────────────────────────────────────────────────────────────────────
      st.markdown("##### 9.1 · סטטוס נוכחי")

      _cph_vals = [_data[m]["cph"] for m in _cph_months]
      _cur_m = _cph_months[-1]
      _prev_m = _cph_months[-2] if len(_cph_months) > 1 else None
      _cur_cph  = _data[_cur_m]["cph"]
      _prev_cph = _data[_prev_m]["cph"] if _prev_m else None

      _avg_cph    = float(np.mean(_cph_vals))                  # all-time average
      _last_6     = _cph_vals[-7:-1] if len(_cph_vals) >= 7 else _cph_vals[:-1]
      _hist_avg   = float(np.mean(_last_6)) if _last_6 else _cur_cph
      _min_cph    = min(_cph_vals); _min_m = _cph_months[_cph_vals.index(_min_cph)]
      _max_cph    = max(_cph_vals); _max_m = _cph_months[_cph_vals.index(_max_cph)]

      _q1, _q2, _q3, _q4 = st.columns(4)
      _q1.metric(f"חודש נוכחי ({_cur_m})", f"₪{_cur_cph:.2f}/h",
                 delta=(f"{(_cur_cph/_prev_cph-1)*100:+.1f}% vs {_prev_m}"
                        if _prev_cph else None))
      # The "baseline" is the avg of the 6 months BEFORE current — that's
      # how you measure if the current month is anomalous. We label it
      # explicitly so the user knows current is NOT in the average.
      _baseline_months = (
          _cph_months[-7:-1] if len(_cph_months) >= 7 else _cph_months[:-1]
      )
      _q2.metric("ממוצע 6 חודשים קודמים (ללא הנוכחי)", f"₪{_hist_avg:.2f}/h",
                 delta=(f"{(_cur_cph-_hist_avg)/_hist_avg*100:+.1f}% חודש נוכחי vs ממוצע"
                        if _hist_avg else None),
                 delta_color="inverse",
                 help=("מבוסס על: " + ", ".join(_baseline_months)) if _baseline_months else None)
      _q3.metric(f"הזול ביותר ({_min_m})", f"₪{_min_cph:.2f}/h")
      _q4.metric(f"היקר ביותר ({_max_m})", f"₪{_max_cph:.2f}/h")

      # One-line conclusion
      if _prev_cph:
        _delta_pct_cur = (_cur_cph / _prev_cph - 1) * 100
        _vs_hist_pct   = (_cur_cph - _hist_avg) / _hist_avg * 100 if _hist_avg else 0
        if abs(_vs_hist_pct) < 3:
          _badge = ("🟢 ", "תקין", "#16A34A")
          _explain = f"עלות השעה ({_cur_cph:.1f}₪) קרובה לממוצע 6 חודשים ({_hist_avg:.1f}₪) — אין חריגה."
        elif _vs_hist_pct > 3:
          _badge = ("🟡 ", "מעל הממוצע", "#D97706")
          _explain = (f"עלות השעה גבוהה ב-{_vs_hist_pct:+.1f}% מהממוצע. "
                      f"גורמים אפשריים: חופשה (פחות שעות), אגרות עונתיות, או יוקר רכיבי שכר.")
        else:
          _badge = ("🟢 ", "מתחת לממוצע", "#16A34A")
          _explain = (f"עלות השעה נמוכה ב-{abs(_vs_hist_pct):.1f}% מהממוצע — "
                      f"חודש יעיל יותר מהרגיל.")
        st.markdown(
          f"<div style='background:#F8FAFC;border-right:4px solid {_badge[2]};"
          f"border-radius:6px;padding:12px 16px;margin:8px 0;font-size:14px;color:#1E293B'>"
          f"<b>{_badge[0]}{_badge[1]}.</b> {_explain}</div>",
          unsafe_allow_html=True,
        )

      # ────────────────────────────────────────────────────────────────────────
      # ── 9.2 — גרף מגמה ─────────────────────────────────────────────────────
      # ────────────────────────────────────────────────────────────────────────
      st.markdown("##### 9.2 · גרף מגמה")
      if HAS_PLOTLY and len(_cph_months) >= 2:
        _trend = pd.DataFrame([{"month": m, "cph": _data[m]["cph"]} for m in _cph_months])
        _fig = go.Figure()
        # Reference line: 6-month rolling average
        _fig.add_trace(go.Scatter(
          x=[_cph_months[0], _cph_months[-1]],
          y=[_hist_avg, _hist_avg],
          mode="lines",
          line=dict(color="#94A3B8", width=1.5, dash="dash"),
          name=f"ממוצע 6 חודשים קודמים (₪{_hist_avg:.1f})",
          hoverinfo="skip",
        ))
        _fig.add_trace(go.Scatter(
          x=_trend["month"], y=_trend["cph"],
          mode="lines+markers+text",
          text=[f"₪{c:.0f}" for c in _trend["cph"]],
          textposition="top center",
          line=dict(color="#1E3A5F", width=2.5),
          marker=dict(size=9, color="#1E3A5F"),
          name="עלות לשעה",
          hovertemplate="<b>%{x}</b><br>₪%{y:.2f}/h<extra></extra>",
        ))
        _fig.update_layout(
          height=300, hovermode="x",
          yaxis_title="₪ / שעה", xaxis_title=None,
          margin=dict(l=30, r=20, t=20, b=30),
          plot_bgcolor="#F8FAFC",
          legend=dict(orientation="h", y=1.1, x=1, xanchor="right"),
        )
        _fig.update_yaxes(gridcolor="#E2E8F0")
        st.plotly_chart(_fig, use_container_width=True)
        st.caption("מגמת עלות לשעה לאורך זמן — כל קו מציג רכיב עלות (שכר/ביטוח/פנסיה וכו') ביחס לשעת עבודה.")

      # ── Build a plain-language story for each month ──────────────────────────
      def _make_story(cur_month, prev_month):
        """Return a human-readable explanation in Hebrew."""
        cur, prev = _data[cur_month], _data[prev_month]
        delta_cph = cur["cph"] - prev["cph"]
        delta_pct = delta_cph / prev["cph"] * 100 if prev["cph"] else 0
        bullets = []

        # 1. Hours change
        h_diff = cur["hours"] - prev["hours"]
        h_pct  = h_diff / prev["hours"] * 100 if prev["hours"] else 0
        if abs(h_pct) >= 5:
          direction = "ירדו" if h_diff < 0 else "עלו"
          bullets.append(
            f"**שעות העבודה {direction} ב-{abs(h_pct):.0f}%** — "
            f"מ-{int(prev['hours']):,} שעות ב-{prev_month} ל-{int(cur['hours']):,} שעות ב-{cur_month} "
            f"(הפרש של {int(abs(h_diff)):,} שעות)."
          )
          # Holiday/seasonal guess
          mm = int(cur_month[:2])
          if h_diff < 0 and mm in (4, 9, 10):
            bullets.append(
              f"   הסיבה הסבירה: **{'חופשת פסח' if mm==4 else 'חגי תשרי'}** — פחות ימי עבודה."
            )
          elif h_diff < 0 and mm == 5:
            bullets.append("   הסיבה הסבירה: **יום העצמאות וחגי אייר**.")

        # 2. Cost change
        cost_diff = cur["cost"] - prev["cost"]
        cost_pct  = cost_diff / prev["cost"] * 100 if prev["cost"] else 0
        if abs(cost_pct) >= 3:
          direction = "ירדה" if cost_diff < 0 else "עלתה"
          bullets.append(
            f"**עלות סה\"כ {direction} ב-{abs(cost_pct):.0f}%** — "
            f"₪{int(prev['cost']):,} → ₪{int(cur['cost']):,} (שינוי ₪{int(abs(cost_diff)):,})."
          )
        else:
          bullets.append(
            f"**עלות סה\"כ נשארה כמעט זהה** — ₪{int(prev['cost']):,} → ₪{int(cur['cost']):,} "
            f"(שינוי קטן של {cost_pct:+.1f}%)."
          )

        # 3. Big component changes
        component_labels = {
          "gross_salary":     "משכורת ברוטו",
          "bituach":          "ביטוח לאומי",
          "levy":             "אגרות (היטל)",
          "employment_levy":  "היטל תעסוקה",
          "pension":          "פנסיה",
          "medical_insurance":"ביטוח רפואי",
        }
        for col, label in component_labels.items():
          cv = cur.get(col, 0)
          pv = prev.get(col, 0)
          diff = cv - pv
          # Only flag if it's a meaningful change: > ₪30K AND > 30%
          if abs(diff) >= 30000 and (pv == 0 or abs(diff / pv) >= 0.30):
            direction = "ירד" if diff < 0 else "קפץ"
            # Special hint for periodic levy spikes
            hint = ""
            if col == "employment_levy" and diff > 50000:
              hint = " (כנראה תשלום רבעוני / שנתי שאינו חוזר על עצמו כל חודש)"
            elif col == "bituach" and diff > 30000:
              hint = " (יכול לנבוע מתוספת עובדים יקרים או מאיפוס שנתי)"
            bullets.append(
              f"**{label} {direction} ב-₪{int(abs(diff)):,}** "
              f"(₪{int(pv):,} → ₪{int(cv):,}){hint}."
            )

        # 4. Employee count change
        e_diff = cur["employees"] - prev["employees"]
        if abs(e_diff) >= 15:
          direction = "נוספו" if e_diff > 0 else "פרשו / לא דווחו"
          bullets.append(
            f"**מצבת העובדים: {direction} {abs(e_diff)} עובדים** — "
            f"מ-{prev['employees']} ל-{cur['employees']}."
          )

        # 5. Bottom-line interpretation
        if delta_cph > 0 and h_diff < 0 and abs(cost_pct) < 3:
          conclusion = (
            f"**המסקנה:** העלייה ב-CPH (+₪{delta_cph:.1f}/h) נובעת מ-**פחות שעות עבודה** "
            f"ולא מעלייה ביוקר העובדים. החברה משלמת אותו סכום משכורות וביטוח לאומי, "
            f"אבל מחלקת אותו על פחות שעות. **זה תקין ועונתי.**"
          )
        elif delta_cph > 0 and abs(h_pct) < 3 and cost_pct > 5:
          conclusion = (
            f"**המסקנה:** העלייה ב-CPH (+₪{delta_cph:.1f}/h) נובעת מ-**עלייה אמיתית בעלויות** "
            f"(+{cost_pct:.0f}%) על אותו נפח שעות. שווה לבדוק את הרכיב הספציפי שעלה."
          )
        elif delta_cph < 0 and h_diff > 0:
          conclusion = (
            f"**המסקנה:** הירידה ב-CPH ({delta_cph:.1f}/h) משקפת **יעילות טובה יותר** — "
            f"יותר שעות עבודה על אותה מצבת עובדים."
          )
        elif abs(delta_pct) < 5:
          conclusion = "**המסקנה:** חודש יציב — אין שינוי משמעותי בעלות לשעה."
        else:
          conclusion = (
            f"**המסקנה:** שינוי של ₪{delta_cph:+.1f}/h ({delta_pct:+.0f}%) — "
            f"שילוב של שינויים בשעות ובעלויות."
          )
        bullets.append(conclusion)
        return bullets, delta_cph, delta_pct

      # ────────────────────────────────────────────────────────────────────────
      # ── 9.3 — פירוט חודשי (כרטיסים) ────────────────────────────────────────
      # ────────────────────────────────────────────────────────────────────────
      st.markdown("##### 9.3 · פירוט חודש-אחר-חודש")
      st.caption(
        "לכל חודש: הסבר ברור מה גרם לעלייה או לירידה בעלות לשעה, עם המספרים האמיתיים. "
        "ירוק = ירידה, אדום = עלייה, אפור = יציב."
      )

      # Add a quick filter for which months to show
      _show_all = st.checkbox("הצג את כל החודשים (לא רק 6 אחרונים)", value=False, key="cph_show_all")
      _months_to_show = _cph_months[::-1] if _show_all else _cph_months[::-1][:6]

      for cur_m in _months_to_show:
        cur_idx = _cph_months.index(cur_m)
        if cur_idx == 0:
          # First month — no previous, just summary
          d = _data[cur_m]
          st.markdown(
            f"""
            <div style='background:#F1F5F9;border:1px solid #E2E8F0;border-radius:10px;
                        padding:14px 18px;margin:8px 0'>
              <div style='font-size:16px;font-weight:700;color:#0F172A'>{cur_m} — חודש ראשון בנתונים</div>
              <div style='font-size:14px;color:#475569;margin-top:8px'>
                עלות לשעה: <b>₪{d['cph']:.2f}</b> ·
                {int(d['hours']):,} שעות ·
                ₪{int(d['cost']):,} עלות ·
                {d['employees']} עובדים.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
          )
          continue

        prev_m = _cph_months[cur_idx - 1]
        bullets, delta_cph, delta_pct = _make_story(cur_m, prev_m)

        # Card styling — green if CPH dropped, red if rose, grey if stable
        if delta_pct > 5:
          bg, border, badge_bg, badge_color = "#FEF2F2", "#FECACA", "#DC2626", "#fff"
          arrow_text = f"▲ עלייה של ₪{delta_cph:.2f}/h ({delta_pct:+.0f}%)"
        elif delta_pct < -5:
          bg, border, badge_bg, badge_color = "#F0FDF4", "#BBF7D0", "#16A34A", "#fff"
          arrow_text = f"▼ ירידה של ₪{abs(delta_cph):.2f}/h ({delta_pct:+.0f}%)"
        else:
          bg, border, badge_bg, badge_color = "#F8FAFC", "#E2E8F0", "#64748B", "#fff"
          arrow_text = f"● יציב ({delta_pct:+.1f}%)"

        bullet_html = ""
        for b in bullets:
          # Convert markdown bold (**X**) to HTML <b>X</b>
          import re as _re
          b_html = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", b)
          bullet_html += f"<li style='margin:6px 0;line-height:1.7'>{b_html}</li>"

        cur_cph = _data[cur_m]["cph"]
        prev_cph = _data[prev_m]["cph"]

        st.markdown(
          f"""
          <div style='background:{bg};border:1px solid {border};border-radius:12px;
                      padding:16px 20px;margin:10px 0;
                      box-shadow:0 1px 3px rgba(0,0,0,0.04)'>
            <div style='display:flex;justify-content:space-between;align-items:center;
                        border-bottom:1px solid {border};padding-bottom:10px;margin-bottom:12px'>
              <div>
                <div style='font-size:18px;font-weight:800;color:#0F172A'>{cur_m}</div>
                <div style='font-size:13px;color:#64748B;margin-top:2px'>
                  ₪{cur_cph:.2f}/h · קודם (₪{prev_cph:.2f}/h ב-{prev_m})
                </div>
              </div>
              <div style='background:{badge_bg};color:{badge_color};padding:6px 14px;
                          border-radius:20px;font-weight:700;font-size:13px;white-space:nowrap'>
                {arrow_text}
              </div>
            </div>
            <ul style='margin:0;padding-right:20px;font-size:14px;color:#1E293B'>
              {bullet_html}
            </ul>
          </div>
          """,
          unsafe_allow_html=True,
        )

      # ════════════════════════════════════════════════════════════════════════
  
      # ════════════════════════════════════════════════════════════════════════
      # 9. עלות לשעה — מבנה היררכי: סטטוס נוכחי → מגמה → פירוט חודשי
      # ════════════════════════════════════════════════════════════════════════
    # 10. ניתוח רווחיות עובד — שווי משקל, רכיבי עלות ועובדים הכי רווחיים
      # ════════════════════════════════════════════════════════════════════════

    with st.expander("10. ניתוח רווחיות עובד — שווי משקל ומה עושה עובד רווחי", expanded=True):
      st.caption(
        "מה מרכיב את עלות העובד החודשית, כמה שעות הוא חייב לעבוד כדי לכסות את "
        "עלותו (שווי משקל), ואילו עובדים מייצרים הכי הרבה רווח."
      )

      # ── 10A. רכיבי עלות לעובד ממוצע ─────────────────────────────────────────
      st.markdown("##### א. ממה מורכבת עלות העובד?")

      _comp_labels = {
        "gross_salary":      "משכורת ברוטו",
        "bituach":           "ביטוח לאומי",
        "levy":              "אגרות (היטל)",
        "pension":           "פנסיה / קרן השתלמות",
        "medical_insurance": "ביטוח רפואי",
        "employment_levy":   "היטל תעסוקה",
        "incentive_fund":    "קרן עידוד",
        "savings_deposit":   "פיקדון",
        "vacation_fund":     "קרן חופשה",
        "severance":         "פיצויים",
      }
      _per_emp_all = df.drop_duplicates(["month","employee_id"])
      _comp_totals = {}
      for col, label in _comp_labels.items():
        if col in _per_emp_all.columns:
          v = float(_per_emp_all[col].sum())
          if v > 0:
            _comp_totals[label] = v
      _total_comp = sum(_comp_totals.values())

      if _comp_totals and _total_comp > 0:
        # Consolidate slices smaller than 3% into a single "אחר" group —
        # the donut becomes readable on a 16-month window where many tiny
        # cost components clutter the labels. The detailed breakdown table
        # to the right still shows EVERY component, no data hidden.
        _SMALL_PCT_THRESHOLD = 3.0
        _comp_for_pie = {}
        _other_sum = 0.0
        for _lab, _v in _comp_totals.items():
          _pct = _v / _total_comp * 100
          if _pct < _SMALL_PCT_THRESHOLD:
            _other_sum += _v
          else:
            _comp_for_pie[_lab] = _v
        if _other_sum > 0:
          _comp_for_pie[f"אחר (פחות מ-3%)"] = _other_sum

        _c1, _c2 = st.columns([3, 2])
        with _c1:
          if HAS_PLOTLY:
            _pie = go.Figure(go.Pie(
              labels=list(_comp_for_pie.keys()),
              values=list(_comp_for_pie.values()),
              hole=0.55,
              textinfo="label+percent",
              textposition="outside",
              marker=dict(colors=[
                "#1E40AF","#16A34A","#DC2626","#D97706","#7C3AED",
                "#0891B2","#DB2777","#65A30D","#92400E","#475569",
              ]),
              # Show absolute ₪ inside hover for the consolidated values
              hovertemplate="<b>%{label}</b><br>"
                             "₪%{value:,.0f}<br>%{percent}<extra></extra>",
            ))
            _pie.update_layout(
              height=380, showlegend=False,
              margin=dict(l=10, r=10, t=20, b=10),
            )
            st.plotly_chart(_pie, use_container_width=True)
            st.caption("התפלגות עלות העובד הנבחר לפי רכיבים — נותן תמונה מהירה איפה ההוצאה הגדולה.")
        with _c2:
          st.markdown("**הרכב עלות העובד (16 חודשים, ₪):**")
          _comp_pct = [(lab, v, v / _total_comp * 100)
                       for lab, v in sorted(_comp_totals.items(), key=lambda t: -t[1])]
          _comp_html = "<table style='width:100%;font-size:14px'>"
          for lab, v, pct in _comp_pct:
            _comp_html += (
              f"<tr><td style='padding:5px 0'>{lab}</td>"
              f"<td style='text-align:left;color:#64748B;padding:5px 0'>₪{v:,.0f}</td>"
              f"<td style='text-align:left;width:60px;color:#0F172A;font-weight:600'>{pct:.1f}%</td>"
              f"</tr>"
            )
          _comp_html += "</table>"
          st.markdown(_comp_html, unsafe_allow_html=True)

          _avg_cost_per_emp_month = _total_comp / max(len(_per_emp_all), 1)
          st.caption(
            f"💡 ממוצע עלות לעובד בחודש: **₪{_avg_cost_per_emp_month:,.0f}** "
            f"({len(_per_emp_all):,} עובדים-חודשים)"
          )

      st.markdown("")
      st.markdown("##### ב. שווי משקל — כמה שעות חייב כל עובד לעבוד כדי לכסות את עלותו?")

      # ── 10B. Break-even analysis per employee — focused on selected month ───
      # Allow user to pick which month to analyze
      _be_months = sorted(df["month"].unique(),
                          key=lambda m: int(m[3:])*100 + int(m[:2]), reverse=True)
      _bm = st.selectbox(
        "חודש לניתוח",
        options=_be_months,
        key="be_month_sel",
        help="בחר את החודש שלגביו תרצה לראות שווי משקל וניתוח רווחיות",
      )

      _be_df = df[df["month"] == _bm].copy()
      if _be_df.empty:
        st.info(f"אין נתונים לחודש {_bm}.")
      else:
        # Per-employee aggregation: hours, cost, weighted billing rate
        _be_emp = (_be_df.groupby(["employee_id", "employee_name"], as_index=False)
                   .agg(שעות=("total_hours", "sum"),
                        עלות=("employer_cost", "first"),
                        n_sites=("site", "nunique")))

        # Compute weighted billing rate per employee — sum(hours_at_site × rate_at_site) / total_hours
        _wr_rows = []
        for emp_id in _be_emp["employee_id"].unique():
          emp_rows = _be_df[_be_df["employee_id"] == emp_id]
          tot_h = float(emp_rows["total_hours"].sum())
          if tot_h <= 0:
            _wr_rows.append({"employee_id": emp_id, "תעריף_משוקלל": 0.0})
            continue
          weighted_sum = float((emp_rows["total_hours"] * emp_rows["hourly_rate"]).sum())
          _wr_rows.append({"employee_id": emp_id, "תעריף_משוקלל": round(weighted_sum / tot_h, 1)})
        _wr_df = pd.DataFrame(_wr_rows)
        _be_emp = _be_emp.merge(_wr_df, on="employee_id", how="left")

        # Break-even: hours needed to cover monthly cost at the weighted rate
        _be_emp["שווי_משקל"] = (_be_emp["עלות"] /
                                  _be_emp["תעריף_משוקלל"].replace(0, np.nan)).round(0)

        # Actual revenue (allocated billing for this employee, this month)
        _emp_rev = []
        for emp_id in _be_emp["employee_id"].unique():
          emp_rows = _be_df[_be_df["employee_id"] == emp_id]
          rev = 0.0
          # billing_amount is at (month, client) level — allocate to employee by hour share
          for client in emp_rows["client"].unique():
            client_rows = _be_df[_be_df["client"] == client]
            client_total_hours = float(client_rows["total_hours"].sum())
            client_billing = float(client_rows.drop_duplicates(["client"])["billing_amount"].fillna(0).iloc[0])
            emp_hours_at_client = float(emp_rows[emp_rows["client"] == client]["total_hours"].sum())
            if client_total_hours > 0:
              rev += client_billing * (emp_hours_at_client / client_total_hours)
          _emp_rev.append({"employee_id": emp_id, "הכנסה": round(rev, 0)})
        _be_emp = _be_emp.merge(pd.DataFrame(_emp_rev), on="employee_id", how="left")
        _be_emp["הכנסה"] = _be_emp["הכנסה"].fillna(0)
        _be_emp["רווח"] = (_be_emp["הכנסה"] - _be_emp["עלות"]).round(0)
        _be_emp["מצב"] = _be_emp["רווח"].apply(
          lambda r: "רווח" if r > 1000 else ("הפסד" if r < -1000 else "שווי משקל")
        )
        _be_emp["חסר_שעות"] = (_be_emp["שווי_משקל"] - _be_emp["שעות"]).clip(lower=0).round(0)
        _be_emp["עודף_שעות"] = (_be_emp["שעות"] - _be_emp["שווי_משקל"]).clip(lower=0).round(0)

        # Summary cards
        _profitable = _be_emp[_be_emp["מצב"] == "רווח"]
        _loss       = _be_emp[_be_emp["מצב"] == "הפסד"]
        _balance    = _be_emp[_be_emp["מצב"] == "שווי משקל"]

        _b1, _b2, _b3, _b4 = st.columns(4)
        _b1.metric("עובדים רווחיים", len(_profitable))
        _b2.metric("עובדים בהפסד", len(_loss),
                   delta=f"{len(_loss)/len(_be_emp)*100:.0f}% מהעובדים",
                   delta_color="inverse")
        _b3.metric("בשווי משקל", len(_balance))
        _b4.metric("רווח נקי כולל", f"₪{_be_emp['רווח'].sum():,.0f}")

        # ── Profitable employees table ───────────────────────────────────────
        st.markdown(f"##### ג. עובדים הכי רווחיים ב-{_bm}")
        _top_p = _profitable.nlargest(15, "רווח")[
          ["employee_name", "שעות", "עלות", "תעריף_משוקלל",
           "שווי_משקל", "עודף_שעות", "הכנסה", "רווח"]
        ].rename(columns={
          "employee_name": "עובד",
          "תעריף_משוקלל": "תעריף ₪/h",
          "עודף_שעות":    "שעות מעבר ל-BE",
        })
        if _top_p.empty:
          st.warning("אין עובדים רווחיים בחודש זה.")
        else:
          _top_p_t = _with_total_row(
            _top_p, label_col="עובד",
            recalc={"תעריף ₪/h":
              lambda x: (x["הכנסה"].sum()/x["שעות"].sum()) if x["שעות"].sum()>0 else 0,
            })
          _top_p_sty = _top_p_t.style.format({
            "שעות":          "{:,.0f}",
            "עלות":          "₪{:,.0f}",
            "תעריף ₪/h":     "₪{:.1f}",
            "שווי_משקל":     "{:,.0f}h",
            "שעות מעבר ל-BE":"{:,.0f}h",
            "הכנסה":         "₪{:,.0f}",
            "רווח":          "₪{:+,.0f}",
          }, na_rep="")
          _top_p_sty = _hl_total_row(_top_p_sty)
          st.dataframe(_top_p_sty, use_container_width=True, hide_index=True,
                        height=min(590, 42 + len(_top_p_t) * 36))
          st.caption(
            "💡 **איך לקרוא:** שווי משקל = עלות חודשית ÷ תעריף משוקלל. "
            "כל שעה מעבר לזה היא רווח נקי. "
            "**הנחה:** התעריף המשוקלל מבוסס על תקן.xlsx; הרווח בפועל תלוי בחיוב האמיתי ללקוח."
          )

        # ── Loss employees table ─────────────────────────────────────────────
        st.markdown(f"##### ד. עובדים שלא הצליחו להגיע לשווי משקל ב-{_bm}")
        _top_l = _loss.nsmallest(15, "רווח")[
          ["employee_name", "שעות", "עלות", "תעריף_משוקלל",
           "שווי_משקל", "חסר_שעות", "הכנסה", "רווח"]
        ].rename(columns={
          "employee_name": "עובד",
          "תעריף_משוקלל": "תעריף ₪/h",
          "חסר_שעות":     "שעות חסרות ל-BE",
        })
        if _top_l.empty:
          st.success(f"✓ כל העובדים ב-{_bm} מעל שווי משקל.")
        else:
          def _color_loss(v):
            if pd.isna(v): return ""
            return "color: #DC2626; font-weight: 600" if v < 0 else ""
          _top_l_t = _with_total_row(
            _top_l, label_col="עובד",
            recalc={"תעריף ₪/h":
              lambda x: (x["הכנסה"].sum()/x["שעות"].sum()) if x["שעות"].sum()>0 else 0,
            })
          _top_l_sty = (_top_l_t.style
            .map(_color_loss, subset=["רווח"])
            .format({
                "שעות":          "{:,.0f}",
                "עלות":          "₪{:,.0f}",
                "תעריף ₪/h":     "₪{:.1f}",
                "שווי_משקל":     "{:,.0f}h",
                "שעות חסרות ל-BE":"{:,.0f}h",
                "הכנסה":         "₪{:,.0f}",
                "רווח":          "₪{:+,.0f}",
            }, na_rep=""))
          _top_l_sty = _hl_total_row(_top_l_sty)
          st.dataframe(_top_l_sty, use_container_width=True, hide_index=True,
                        height=min(590, 42 + len(_top_l_t) * 36))

          # Diagnose root cause for losses
          _total_loss = float(_top_l["רווח"].sum())
          _avg_missing = float(_top_l["שעות חסרות ל-BE"].mean()) if not _top_l.empty else 0
          st.caption(
            f"💰 **סך ההפסד מהעובדים האלה ב-{_bm}: ₪{abs(_total_loss):,.0f}**. "
            f"בממוצע, כל עובד כזה חסר ~{_avg_missing:.0f} שעות כדי להגיע לשווי משקל. "
            f"**גורמים אפשריים:** חופשה ארוכה, מחלה, אתר ללא תקן (תעריף=0), "
            f"או עובד שעדיין בתקופת קליטה."
          )

        # ── Sweet spot guidance ────────────────────────────────────────────────
        st.markdown(f"##### ה. תובנה — איפה הסיירת הרווחית?")
        if not _be_emp.empty:
          _med_rate = float(_be_emp["תעריף_משוקלל"].median())
          _med_cost = float(_be_emp["עלות"].median())
          _med_be   = _med_cost / _med_rate if _med_rate > 0 else 0
          _med_h    = float(_be_emp["שעות"].median())
          _avg_profit_per_extra_h = _med_rate * 0.7  # rough estimate: 70% of rate is gross profit per extra hour

          st.info(
            f"📌 **לעובד טיפוסי בחודש זה:**\n\n"
            f"- **עלות חודשית ממוצעת:** ₪{_med_cost:,.0f}\n"
            f"- **תעריף חיוב משוקלל:** ₪{_med_rate:.1f}/שעה\n"
            f"- **שעות שווי משקל:** ~{_med_be:.0f}h/חודש (זה המינימום ההכרחי)\n"
            f"- **שעות בפועל (חציון):** {_med_h:,.0f}h\n\n"
            f"💡 **לכל שעה מעבר לשווי משקל, העובד מייצר ~₪{_avg_profit_per_extra_h:.0f} רווח לחברה.** "
            f"הטווח הרווחי ביותר הוא **{_med_be:.0f}-186 שעות** — לפני שמתחיל פרמיית שעות נוספות (×1.25)."
          )

        # Download CSV
        _dl = _be_emp[[
          "employee_id","employee_name","שעות","עלות","תעריף_משוקלל",
          "שווי_משקל","חסר_שעות","עודף_שעות","הכנסה","רווח","מצב"
        ]]
        st.download_button(
          "⬇️ הורד את כל ניתוח העובדים (CSV)",
          _dl.to_csv(index=False, encoding="utf-8-sig"),
          f"employee_profitability_{_bm}.csv", "text/csv",
        )


    with st.expander("8. עלות נסתרת (Overhead) של החברה", expanded=True):
      st.caption(
        "כל מה שלא מוצג בעלויות/הכנסות של הדשבורד אבל מהווה הוצאה אמיתית: "
        "(א) עובדי ינאי פרסונל — הנהלה ואדמיניסטרציה — מסוננים מהטוטלים. "
        "(ב) עובדים שמופיעים בעלויות אבל ללא שעות עבודה (חופשה ממושכת, מחלה, וכו')."
      )

      # ── 8a. Internal entity (ינאי פרסונל) ───────────────────────────────────
      st.markdown("##### א. ישות פנימית — ינאי פרסונל")
      _int = _internal_summary.copy()  # raw, before exclusion
      if _int.empty:
        st.success("✅ אין רשומות פנימיות בקובץ")
      else:
        _i1, _i2, _i3, _i4 = st.columns(4)
        _i1.metric("עובדים-חודשים",  len(_int.drop_duplicates(["month","employee_id"])))
        _i2.metric("עובדים ייחודיים", int(_int["employee_id"].nunique()))
        _i3.metric("שעות עבודה",     f"{float(_int['total_hours'].sum()):,.0f}h")
        _i4.metric("עלות מצטברת",    f"₪{float(_int['cost'].sum()):,.0f}")

        _int_disp = (_int.groupby("month", as_index=False)
                     .agg(rows=("employee_id","count"),
                          emps=("employee_id","nunique"),
                          hours=("total_hours","sum"),
                          cost=("cost","sum"))
                     .rename(columns={"month":"חודש","rows":"שורות",
                                      "emps":"עובדים","hours":"שעות","cost":"עלות"})
                     .sort_values("חודש"))
        _int_t = _with_total_row(_int_disp, label_col="חודש")
        _int_sty = _int_t.style.format(
          {"שעות": "{:,.0f}h", "עלות": "₪{:,.0f}"}, na_rep="")
        _int_sty = _hl_total_row(_int_sty)
        st.dataframe(_int_sty, use_container_width=True, hide_index=True,
                      height=min(420, 42 + len(_int_t) * 36))

      # ── 8b. Zero-hour employees (vacation/sick/etc.) ───────────────────────
      st.markdown("##### ב. עובדים בלי שעות עבודה (חופשה / מחלה ממושכת)")
      _zero = df[(df["total_hours"] == 0) &
                 (df["emp_total_hours"] == 0) &
                 (df["employer_cost"] > 0)].copy()
      if _zero.empty:
        st.success("✅ אין עובדים עם עלות ללא שעות עבודה")
      else:
        _zero_uniq = _zero.drop_duplicates(["month","employee_id"])
        _zh_total = float(_zero_uniq["employer_cost"].sum())
        _zh_n     = int(len(_zero_uniq))
        _zh_emp_n = int(_zero_uniq["employee_id"].nunique())

        _h1, _h2, _h3 = st.columns(3)
        _h1.metric("עובדים-חודשים מושפעים", _zh_n)
        _h2.metric("עובדים ייחודיים",       _zh_emp_n)
        _h3.metric("עלות נסתרת מצטברת",     f"₪{_zh_total:,.0f}")

        _zd = (_zero_uniq[["month","employee_id","employee_name","employer_cost"]]
                .rename(columns={"month":"חודש","employee_id":"מס׳ עובד",
                                 "employee_name":"שם","employer_cost":"עלות"})
                .sort_values("עלות", ascending=False))
        _zd_t = _with_total_row(_zd, label_col="חודש",
                                  empty_cols={"מס׳ עובד","שם"})
        _zd_sty = _zd_t.style.format({"עלות": "₪{:,.0f}"}, na_rep="")
        _zd_sty = _hl_total_row(_zd_sty)
        st.dataframe(_zd_sty, use_container_width=True, hide_index=True,
                      height=min(420, 42 + len(_zd_t) * 36))

        st.download_button(
          "⬇️ הורד רשימה (CSV)",
          _zd.to_csv(index=False, encoding="utf-8-sig"),
          "overhead_employees.csv", "text/csv",
        )

      # ── 8c. Total company overhead ──────────────────────────────────────────
      st.markdown("##### ג. סך עלויות overhead של החברה")
      _total_internal = float(_int["cost"].sum()) if not _int.empty else 0.0
      _total_zero     = float(_zero.drop_duplicates(["month","employee_id"])["employer_cost"].sum()) if not _zero.empty else 0.0
      _grand          = _total_internal + _total_zero
      _t1, _t2, _t3 = st.columns(3)
      _t1.metric("ישות פנימית (ינאי פרסונל)", f"₪{_total_internal:,.0f}")
      _t2.metric("עובדים בלי שעות",            f"₪{_total_zero:,.0f}")
      _t3.metric("סך הכל overhead",            f"₪{_grand:,.0f}",
                 delta=f"לא נכלל ברווח של הדשבורד")
      if _grand > 0:
        st.caption(
          f"💡 ה-overhead הזה הוא הוצאה אמיתית של החברה אבל לא נכלל "
          f"בחישוב הרווח של הדשבורד (כי הוא לא קשור ללקוח ספציפי). "
          f"לקבלת רווח נטו של החברה: רווח הדשבורד ({df.attrs.get('_label_profit','')}) "
          f"פחות ₪{_grand:,.0f}."
        )

# ═══ FOOTER: technical / debug info ══════════════════════════════════════════
# Sits collapsed at the very bottom of the page so the CEO sees a clean
# dashboard by default. Contains: internal-entity exclusion summary
# (previously a blue info banner above the KPIs), data freshness, parquet
# row counts. Hidden under an expander → invisible until clicked.

# ── Data Quality Score — computed from cheap checks on the current df ──
# Each check returns (score 0-100, issue count, label, severity).
def _dq_checks(_df, _raw_full):
  _checks = []
  _tot = max(len(_df), 1)
  # 1. Missing client
  _miss_cli = int(_df["client"].isna().sum()) if "client" in _df.columns else 0
  _checks.append(("לקוח חסר", _miss_cli, _miss_cli/_tot*100,
                   "high" if _miss_cli/_tot > 0.02 else "ok"))
  # 2. Missing country
  _miss_ctr = int(_df["country"].isna().sum()) if "country" in _df.columns else 0
  _checks.append(("מדינה חסרה", _miss_ctr, _miss_ctr/_tot*100,
                   "med" if _miss_ctr/_tot > 0.05 else "ok"))
  # 3. Zero hours but cost > 0
  _zero_h_cost = 0
  if "total_hours" in _df.columns and "cost" in _df.columns:
    _zero_h_cost = int(((_df["total_hours"] == 0) & (_df["cost"] > 0)).sum())
  _checks.append(("שעות אפס עם עלות", _zero_h_cost, _zero_h_cost/_tot*100,
                   "med" if _zero_h_cost > 0 else "ok"))
  # 4. Negative margin clients
  _neg_margin_cli = 0
  if "margin_pct" in _df.columns and "client" in _df.columns:
    _neg_margin_cli = int(_df.dropna(subset=["margin_pct"])
                           .drop_duplicates("client")
                           .query("margin_pct < 0")["client"].nunique())
  _checks.append(("לקוחות במרג'ין שלילי", _neg_margin_cli, 0,
                   "high" if _neg_margin_cli > 3
                   else "med" if _neg_margin_cli > 0 else "ok"))
  # 5. Missing standard agreements
  _no_std = 0
  if "cost_driver" in _df.columns:
    _no_std = int(_df["cost_driver"].astype(str).str.contains("אין תקן",
                    na=False).sum())
  _checks.append(("רשומות בלי תקן", _no_std, _no_std/_tot*100,
                   "high" if _no_std/_tot > 0.01 else "ok"))
  return _checks

_dq_checks_list = _dq_checks(df, raw)
# Score: start at 100, subtract per severity. Max one deduction per check.
_dq_score = 100
for _name, _n, _pct, _sev in _dq_checks_list:
  if _sev == "high": _dq_score -= 15
  elif _sev == "med": _dq_score -= 7
_dq_score = max(_dq_score, 0)
_dq_lbl = ("תקין" if _dq_score >= 85
            else "דורש בדיקה" if _dq_score >= 65 else "בעייתי")
_dq_color = ("#16A34A" if _dq_score >= 85
              else "#D97706" if _dq_score >= 65 else "#DC2626")

with st.expander(
    f"🔧 מידע טכני · Debug  ·  ציון איכות נתונים: {_dq_score}/100 · {_dq_lbl}",
    expanded=False):
  st.caption(
    "מידע הנוגע למבנה הנתונים, לישויות מיוחדות ולציון איכות נתונים. "
    "אינו רלוונטי לקריאה היומית — שמור לצורכי בקרה ותחזוקה."
  )

  # ══════════════════════════════════════════════════════════════════════
  # ⚡ PERFORMANCE PANEL — last render's stage timings
  # ══════════════════════════════════════════════════════════════════════
  # Record the conclusions-tab render time before showing the panel.
  try:
    st.session_state.setdefault("_perf",[]).append(
      ("render tab: מסקנות (Conclusions)",
       _time_mod.perf_counter() - _t_summary))
  except NameError:
    pass
  st.markdown("##### ⚡ Performance · זמני ריצה של הרנדור האחרון")
  _perf_list = st.session_state.get("_perf", [])
  _t_total = _time_mod.perf_counter() - st.session_state.get("_perf_t0", _time_mod.perf_counter())
  if _perf_list:
    _perf_df = pd.DataFrame(_perf_list, columns=["שלב","זמן (שנ')"])
    _perf_df["% מסה\"כ"] = (_perf_df["זמן (שנ')"] / _t_total * 100).round(1)
    _perf_df["זמן (שנ')"] = _perf_df["זמן (שנ')"].round(3)
    # Flag slow stages (>0.5s) so the eye finds them
    def _hl(v):
      try:
        if float(v) > 1.0:  return "background:#FEE2E2;color:#7F1D1D;font-weight:700"
        if float(v) > 0.5:  return "background:#FEF3C7;color:#78350F"
      except: pass
      return ""
    _perf_styled = _perf_df.style.map(_hl, subset=["זמן (שנ')"])
    st.dataframe(_perf_styled, use_container_width=True, hide_index=True,
                  height=min(450, 42 + len(_perf_df)*32))
    _total_color = ("#16A34A" if _t_total < 3 else "#D97706" if _t_total < 8 else "#DC2626")
    st.markdown(
      f"<div style='font-size:13px;font-weight:700;margin-top:6px'>"
      f"⏱️ סה\"כ עד נקודת המדידה: "
      f"<span style='color:{_total_color};font-variant-numeric:tabular-nums'>"
      f"{_t_total:.2f} שנ'</span></div>",
      unsafe_allow_html=True,
    )
    st.caption(
      "שלבים בצהוב = >0.5 שנ' · בורדו = >1 שנ'. "
      "ה-log המלא נמצא ב-`logs/dashboard.log` תחת `[PERF]`."
    )
  else:
    st.caption("אין נתוני זמן — הריצה הראשונה אחרי refresh.")
  st.markdown("---")

  # ══════════════════════════════════════════════════════════════════════
  # 🩺 DATA INTEGRITY PANEL — what's in `df` right now?
  # ══════════════════════════════════════════════════════════════════════
  st.markdown("##### 🩺 Data Integrity · מצב הנתונים אחרי סינון")

  # 1. Filter state ──────────────────────────────────────────────────────
  _di_c1, _di_c2 = st.columns(2)
  with _di_c1:
    st.markdown(
      "<b>סינון פעיל</b>"
      f"<div style='font-size:12px;color:#475569;line-height:1.75;'>"
      f"• <b>טווח חודשים:</b> {RNG[0]} ↔ {RNG[1]}<br>"
      f"• <b>לקוחות:</b> {len(sel_cl) if sel_cl else 'כל הלקוחות'}<br>"
      f"• <b>מדינות:</b> {len(sel_ctr) if sel_ctr else 'כל המדינות'}<br>"
      f"• <b>רק בעיות:</b> {'כן' if only_problems else 'לא'}<br>"
      f"• <b>חיפוש:</b> {repr(search_q.strip()) if search_q.strip() else '—'}"
      f"</div>",
      unsafe_allow_html=True,
    )
  with _di_c2:
    _months_in_df_str = (
      ", ".join(_M["months"][:3])
      + (f" … {_M['months'][-1]}" if _M["month_count"] > 3 else "")
    ) if _M["months"] else "—"
    st.markdown(
      "<b>מצב ה-DataFrame המסונן</b>"
      f"<div style='font-size:12px;color:#475569;line-height:1.75;'>"
      f"• <b>שורות:</b> {_M['row_count']:,}<br>"
      f"• <b>חודשים שמצויים בפועל:</b> {_M['month_count']} ({_months_in_df_str})<br>"
      f"• <b>עובדים ייחודיים:</b> {_M['employee_count']:,}<br>"
      f"• <b>לקוחות ייחודיים:</b> {_M['client_count']:,}<br>"
      f"</div>",
      unsafe_allow_html=True,
    )

  # 2. Cross-month leakage assertion — only meaningful when user picked a
  # SINGLE month and the data must contain exactly that month.
  if RNG[0] == RNG[1]:
    _leak = _assert_no_cross_month(df, RNG[0])
    if _leak:
      st.error(
        f"⚠️ זוהה ערבוב חודשים: בחרת **{RNG[0]}** אבל ה-DataFrame "
        f"מכיל גם: {', '.join(_leak)}. "
        f"זה באג — דווח על זה."
      )
    else:
      st.success(f"✅ אין ערבוב חודשים — ה-DataFrame מכיל רק את {RNG[0]}.")

  st.markdown("---")

  # 3. Canonical metrics — the SINGLE source of truth ───────────────────
  st.markdown("##### 📐 מדדים קנוניים (calculate_metrics)")
  st.caption(
    "כל המספרים האלה מחושבים פעם אחת ב-`calculate_metrics(df)` בקובץ. "
    "אם רואים פער בין מספר כאן לבין מספר באחד הטאבים — זה באג בטאב, "
    "לא בנתונים."
  )
  _met_rows = [
    ("סך עלות",              f"₪{_M['total_cost']:,.0f}"),
    ("סך הכנסה",             f"₪{_M['total_revenue']:,.0f}"),
    ("רווח גולמי",          f"₪{_M['gross_profit']:,.0f}"),
    ("מרג'ין %",            f"{_M['margin_pct']:.2f}%"),
    ("שעות רגילות (h100)", f"{_M['h_regular']:,.0f}"),
    ("שעות 125%",           f"{_M['h_125']:,.0f}"),
    ("שעות 150%",           f"{_M['h_150']:,.0f}"),
    ("שעות 175%",           f"{_M['h_175']:,.0f}"),
    ("שעות 200%",           f"{_M['h_200']:,.0f}"),
    ("סך שעות נוספות",     f"{_M['overtime_hours']:,.0f}"),
    ("סך שעות (h100..h200)",  f"{_M['total_hours']:,.0f}"),
    ("שעות לדיווח",         f"{_M['reportable_hours']:,.0f}"),
    ("עלות לשעה (שעות רגילות בלבד)",   f"₪{_M['cph_regular_only']:.2f}"),
    ("עלות לשעה (סה\"כ שעות) ★ ראשי", f"₪{_M['cph_total']:.2f}"),
    ("עלות לשעה (שעות לדיווח)",       f"₪{_M['cph_reportable']:.2f}"
        if _M['cph_reportable']>0 else "—"),
    ("שעות נוספות %",        f"{_M['ot_pct']:.2f}%"),
    ("פרמיית שעות נוספות (₪)", f"₪{_M['ot_premium']:,.0f}"),
    ("הכנסה לשעה",            f"₪{_M['revenue_per_hour']:.2f}"
        if _M['revenue_per_hour']>0 else "—"),
    ("מספר לקוחות",          f"{_M['client_count']:,}"),
    ("מספר עובדים",          f"{_M['employee_count']:,}"),
  ]
  _met_df = pd.DataFrame(_met_rows, columns=["מדד","ערך"])
  st.dataframe(_met_df, use_container_width=True, hide_index=True,
                height=min(700, 38 + len(_met_df)*32))

  # 4. CPH formula explainer ────────────────────────────────────────────
  st.markdown(
    "<div style='background:#EFF6FF;border:1px solid #BFDBFE;"
    "border-radius:10px;padding:12px 14px;margin:6px 0 14px;"
    "font-size:12.5px;color:#1E3A8A;line-height:1.6'>"
    "<b>🧮 נוסחת עלות לשעה:</b><br>"
    "<code>עלות_לשעה = sum(cost) / sum(total_hours)</code><br>"
    "<b>total_hours</b> = h100 + h125 + h150 + h175 + h200 — "
    "כולל שעות נוספות. <i>זה ה-KPI הראשי שמופיע למעלה.</i><br>"
    "ה-CPH הראשון בטבלה (\"שעות רגילות בלבד\") הוא אך ורק "
    "להשוואה — לא משמש כ-KPI."
    "</div>", unsafe_allow_html=True,
  )

  # 5. Tab consistency check ────────────────────────────────────────────
  st.markdown("##### 🔄 בדיקת עקביות בין טאבים")
  st.caption(
    "בודק שהטאבים השונים מציגים את אותם מספרים. תזכורת: "
    "טאב סקירה / טבלאות / חיוב מציגים את כל הטווח שנבחר, "
    "טאב מסקנות מתמקד בחודש האחרון בטווח בלבד (לפי תכנון)."
  )
  # Compute the alt-scope conclusions sees (last month only)
  if _M["months"]:
    _last_month = _M["months"][-1]
    _M_last = calculate_metrics(df[df["month"] == _last_month].copy(),
                                  label=f"חודש אחרון בלבד ({_last_month})")
    _cons_rows = [
      ("חודשים בסקירה",      _M["month_count"],          _M_last["month_count"]),
      ("שורות",                _M["row_count"],            _M_last["row_count"]),
      ("סך עלות",             f"₪{_M['total_cost']:,.0f}",  f"₪{_M_last['total_cost']:,.0f}"),
      ("סך הכנסה",            f"₪{_M['total_revenue']:,.0f}", f"₪{_M_last['total_revenue']:,.0f}"),
      ("רווח",                f"₪{_M['gross_profit']:,.0f}",  f"₪{_M_last['gross_profit']:,.0f}"),
      ("מרג'ין %",           f"{_M['margin_pct']:.1f}%",     f"{_M_last['margin_pct']:.1f}%"),
      ("סך שעות",              f"{_M['total_hours']:,.0f}",   f"{_M_last['total_hours']:,.0f}"),
      ("עלות לשעה",           f"₪{_M['cph_total']:.2f}",      f"₪{_M_last['cph_total']:.2f}"),
      ("שעות נוספות %",       f"{_M['ot_pct']:.1f}%",         f"{_M_last['ot_pct']:.1f}%"),
    ]
    _cons_df = pd.DataFrame(_cons_rows, columns=[
      "מדד",
      f"סקירה / טבלאות / חיוב ({_rl})",
      f"מסקנות (חודש אחרון בלבד: {_last_month})",
    ])
    _cons_df["תאימות"] = _cons_df.apply(
      lambda r: "✓ זהה" if str(r.iloc[1]) == str(r.iloc[2]) else "≠ שונה",
      axis=1,
    )
    st.dataframe(_cons_df, use_container_width=True, hide_index=True)
    if RNG[0] != RNG[1]:
      st.info(
        f"📘 בחרת **טווח** ({RNG[0]} ↔ {RNG[1]}). KPIs ו-טאבי סקירה/"
        f"טבלאות/חיוב מציגים את הטווח כולו. טאב מסקנות מתמקד בחודש "
        f"האחרון ({_last_month}) בלבד — זו התנהגות מתוכננת. "
        f"בחר חודש בודד כדי שכל הטאבים יציגו אותם מספרים."
      )

  st.markdown("---")

  # ── Data Quality Score Card ─────────────────────────────────────────
  st.markdown("##### 🎯 ציון איכות נתונים")
  st.markdown(
    f'<div style="display:flex;align-items:center;gap:18px;'
    f'background:#F8FAFC;border:1px solid #E2E8F0;border-radius:14px;'
    f'padding:14px 18px;margin-bottom:14px">'
    f'<div style="width:78px;height:78px;border-radius:50%;'
    f'background:conic-gradient({_dq_color} {_dq_score}%,#E2E8F0 0);'
    f'display:flex;align-items:center;justify-content:center;flex-shrink:0">'
    f'<div style="width:62px;height:62px;border-radius:50%;background:#fff;'
    f'display:flex;flex-direction:column;align-items:center;justify-content:center">'
    f'<div style="font-size:18px;font-weight:800;color:{_dq_color};'
    f'line-height:1">{_dq_score}</div>'
    f'<div style="font-size:9px;color:#94A3B8;font-weight:700">/100</div>'
    f'</div></div>'
    f'<div style="flex:1">'
    f'<div style="font-size:14px;font-weight:800;color:{_dq_color}">'
    f'{_dq_lbl}</div>'
    f'<div style="font-size:11.5px;color:#64748B;margin-top:3px">'
    f'מחושב על בסיס: לקוח חסר · מדינה חסרה · שעות אפס עם עלות · '
    f'לקוחות במרג\'ין שלילי · רשומות בלי תקן.'
    f'</div></div></div>',
    unsafe_allow_html=True,
  )
  # Per-check breakdown table
  _dq_rows = []
  for _name, _n, _pct, _sev in _dq_checks_list:
    _badge = ({"ok":"🟢 תקין","med":"🟠 דורש בדיקה","high":"🔴 קריטי"}
                .get(_sev, "⚪"))
    _dq_rows.append({"בדיקה": _name, "כמות": _n,
                     "אחוז": f"{_pct:.1f}%" if _pct else "—",
                     "סטטוס": _badge})
  st.dataframe(pd.DataFrame(_dq_rows), use_container_width=True,
                hide_index=True)
  st.markdown("---")

  st.markdown("##### 📂 מידע על המערכת")
  _dbg_c1, _dbg_c2 = st.columns(2)
  with _dbg_c1:
    st.markdown("**ישות פנימית — ינאי פרסונל**")
    if _internal_summary_for_footer:
      _ifs = _internal_summary_for_footer
      st.markdown(
        f"<div style='font-size:12px;color:#475569;line-height:1.7'>"
        f"• {_ifs['emps']} עובדים על פני {_ifs['months']} חודשים<br>"
        f"• {_ifs['hours']:,.0f} שעות עבודה<br>"
        f"• עלות פנימית: <b>₪{_ifs['cost']:,.0f}</b><br>"
        f"<span style='color:#94A3B8;font-size:11px'>"
        f"לא נכלל בעלויות/הכנסות של הדשבורד. הפירוט המלא בטאב 'מסקנות' → סעיף 8."
        f"</span></div>",
        unsafe_allow_html=True,
      )
    else:
      st.caption("אין נתוני ישות פנימית בטווח הנבחר.")
  with _dbg_c2:
    st.markdown("**נתוני המערכת**")
    _rows = len(raw) if isinstance(raw, pd.DataFrame) else 0
    _mn = sorted(raw["month"].dropna().unique().tolist(), key=_mkey) if "month" in raw.columns else []
    st.markdown(
      f"<div style='font-size:12px;color:#475569;line-height:1.7'>"
      f"• סך שורות בקובץ הראשי: <b>{_rows:,}</b><br>"
      f"• חודשים זמינים: <b>{len(_mn)}</b> ({_mn[0] if _mn else '—'} ↔ {_mn[-1] if _mn else '—'})<br>"
      f"• לקוחות ייחודיים: <b>{len(all_clients)}</b><br>"
      f"• מדינות ייחודיות: <b>{len(all_countries)}</b>"
      f"</div>",
      unsafe_allow_html=True,
    )
