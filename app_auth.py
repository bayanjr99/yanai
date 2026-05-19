"""
Reusable login gate for Streamlit dashboards (Hebrew/RTL) with persistent
cookie-based sessions — refreshing the page does NOT log the user out.

Cookie persistence is provided by `streamlit-cookies-controller`, which is
simpler and more reliable than other Streamlit cookie libraries (no
'widget-in-cached-function' warnings, no stale-instance pitfalls).

Configuration in .streamlit/secrets.toml:

    auth_secret = "<long-random-string>"       # required, signs cookies

    [users]
    Yanai = "<bcrypt-hash>"                    # username = bcrypt(password)

Usage (top of your Streamlit script, AFTER st.set_page_config):

    from app_auth import require_login
    require_login()
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

import bcrypt
import streamlit as st

try:
    from streamlit_cookies_controller import CookieController
    _HAS_COOKIES = True
except ImportError:
    _HAS_COOKIES = False

# Reuse the dashboard's logger so auth events end up in logs/dashboard.log
# (and on Streamlit Cloud's app logs which capture stderr).
_auth_logger = logging.getLogger("auth")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COOKIE_NAME = "bi_auth"
# "Remember me" persistence: when the user checks the box on the login form,
# the cookie lasts this many hours. Otherwise NO cookie is written and the
# user will need to re-authenticate as soon as the browser-session state
# is lost (close tab/restart browser).
_REMEMBER_HOURS = 4
_COOKIE_TTL_SEC = _REMEMBER_HOURS * 3600
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_DELAY_SEC = 1.5


# ---------------------------------------------------------------------------
# Token signing (stateless cookies)
# ---------------------------------------------------------------------------

def _get_secret() -> str:
    try:
        s = str(st.secrets.get("auth_secret", ""))
    except Exception:
        s = ""
    return s or "INSECURE_DEFAULT_change_me_in_secrets_toml"


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign_token(username: str) -> str:
    payload = {"u": username, "exp": int(time.time()) + _COOKIE_TTL_SEC}
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_get_secret().encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64u(sig)}"


def _verify_token(token: str) -> str | None:
    try:
        body, sig_b64 = token.rsplit(".", 1)
    except (ValueError, AttributeError):
        return None
    try:
        expected = hmac.new(_get_secret().encode(), body.encode(), hashlib.sha256).digest()
        actual = _b64u_decode(sig_b64)
        if not hmac.compare_digest(expected, actual):
            return None
        data = json.loads(_b64u_decode(body))
        if int(data.get("exp", 0)) < time.time():
            return None
        return data.get("u")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# User lookup + password check
# ---------------------------------------------------------------------------

def _load_users() -> dict[str, str]:
    try:
        return dict(st.secrets.get("users", {}))
    except Exception:
        return {}


def _verify_password(stored_hash: str, candidate: str) -> bool:
    try:
        return bcrypt.checkpw(candidate.encode(), stored_hash.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Cookie controller — created fresh every render (the library handles its
# own internal state). Returns None if the library isn't installed.
# ---------------------------------------------------------------------------

def _make_controller():
    if not _HAS_COOKIES:
        return None
    try:
        return CookieController()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _render_loading_screen() -> None:
    """Professional loading state shown while data is being prepared.

    Renders a brand-coloured spinner with status text, plus a dashboard
    skeleton (header bar + KPI cards + chart placeholders) so the user
    instantly perceives the structure of what's about to appear — far less
    jarring than a blank screen with a tiny spinner.
    """
    st.markdown(
        """
        <style>
        body, .stApp {
            direction: rtl;
            font-family: 'Inter','Segoe UI', Arial, sans-serif;
            background: #F0F4F8;
        }
        section[data-testid="stSidebar"], [data-testid="collapsedControl"] {
            display: none !important;
        }
        .block-container{padding:0!important;max-width:100%!important;}
        @keyframes _yp_spin { to { transform: rotate(360deg); } }
        @keyframes _yp_shimmer {
          0%   { background-position: -400px 0; }
          100% { background-position:  400px 0; }
        }
        .yp-load-shell { padding: 18px 20px 60px; max-width: 1400px; margin: 0 auto; }
        /* Top bar skeleton — matches the real green header */
        .yp-load-topbar {
            background: linear-gradient(135deg,#052E16 0%,#0E5A2E 55%,#16A34A 100%);
            color:#fff; height:58px; border-radius:0 0 14px 14px;
            display:flex; align-items:center; justify-content:space-between;
            padding:0 1.5rem; margin-bottom:18px;
            box-shadow:0 2px 12px rgba(5,46,22,.35);
        }
        .yp-load-brand { display:flex; align-items:center; gap:10px; font-weight:800; font-size:15px; }
        .yp-load-logo-circle {
            width:38px; height:38px; border-radius:50%; background:#fff;
            display:flex; align-items:center; justify-content:center;
            color:#0E5A2E; font-size:18px; font-weight:900;
            box-shadow:0 1px 4px rgba(0,0,0,.2);
        }
        .yp-load-spinner {
            width:22px; height:22px; border:3px solid rgba(255,255,255,.35);
            border-top-color:#fff; border-radius:50%;
            animation:_yp_spin .8s linear infinite;
        }
        .yp-load-status {
            display:flex; align-items:center; gap:9px; font-size:12px;
            font-weight:600; opacity:.95;
        }
        /* Status message panel */
        .yp-load-msg {
            background:#FFFFFF; border:1px solid #E2E8F0; border-radius:14px;
            padding:18px 22px; margin-bottom:16px;
            display:flex; align-items:center; gap:14px;
            box-shadow:0 1px 4px rgba(15,23,42,.04);
        }
        .yp-load-msg-spinner {
            width:34px; height:34px; flex-shrink:0;
            border:4px solid #BBF7D0; border-top-color:#16A34A;
            border-radius:50%; animation:_yp_spin .9s linear infinite;
        }
        .yp-load-msg-text { display:flex; flex-direction:column; gap:3px; }
        .yp-load-msg-title { font-size:14px; font-weight:800; color:#0F172A; }
        .yp-load-msg-sub { font-size:11.5px; color:#64748B; }
        /* Skeleton boxes */
        .yp-skeleton {
            background: linear-gradient(90deg, #E5E7EB 8%, #F1F5F9 18%, #E5E7EB 33%);
            background-size: 800px 100%;
            animation: _yp_shimmer 1.4s linear infinite;
            border-radius: 8px;
        }
        .yp-skel-kpi-row {
            display:grid; grid-template-columns:repeat(6,minmax(0,1fr));
            gap:10px; margin-bottom:18px;
        }
        .yp-skel-kpi {
            background:#fff; border:1px solid #E8EAED; border-radius:12px;
            padding:14px; height:108px;
            box-shadow:0 1px 3px rgba(0,0,0,.04);
        }
        .yp-skel-kpi .yp-skeleton { height:10px; margin-bottom:8px; }
        .yp-skel-kpi .yp-skel-val { height:22px; width:60%; }
        .yp-skel-chart {
            background:#fff; border:1px solid #E8EAED; border-radius:12px;
            padding:14px; height:280px; margin-bottom:12px;
            box-shadow:0 1px 3px rgba(0,0,0,.04);
        }
        .yp-skel-chart .yp-skel-title { height:12px; width:35%; margin-bottom:14px; }
        .yp-skel-chart .yp-skel-body  { height:220px; }
        @media (max-width: 900px) {
          .yp-skel-kpi-row { grid-template-columns:repeat(3,minmax(0,1fr)); }
        }
        </style>
        <div class='yp-load-shell'>
          <div class='yp-load-topbar'>
            <div class='yp-load-brand'>
              <div class='yp-load-logo-circle'>👷</div>
              <span>ינאי פרסונל בע"מ · מערכת ניתוח עלויות</span>
            </div>
            <div class='yp-load-status'>
              <div class='yp-load-spinner'></div>
              טוען נתונים...
            </div>
          </div>
          <div class='yp-load-msg'>
            <div class='yp-load-msg-spinner'></div>
            <div class='yp-load-msg-text'>
              <div class='yp-load-msg-title'>טוען את נתוני המערכת</div>
              <div class='yp-load-msg-sub'>מאמת הרשאות וטוען את הדשבורד · רק רגע...</div>
            </div>
          </div>
          <div class='yp-skel-kpi-row'>
            <div class='yp-skel-kpi'><div class='yp-skeleton'></div><div class='yp-skeleton yp-skel-val'></div><div class='yp-skeleton'></div></div>
            <div class='yp-skel-kpi'><div class='yp-skeleton'></div><div class='yp-skeleton yp-skel-val'></div><div class='yp-skeleton'></div></div>
            <div class='yp-skel-kpi'><div class='yp-skeleton'></div><div class='yp-skeleton yp-skel-val'></div><div class='yp-skeleton'></div></div>
            <div class='yp-skel-kpi'><div class='yp-skeleton'></div><div class='yp-skeleton yp-skel-val'></div><div class='yp-skeleton'></div></div>
            <div class='yp-skel-kpi'><div class='yp-skeleton'></div><div class='yp-skeleton yp-skel-val'></div><div class='yp-skeleton'></div></div>
            <div class='yp-skel-kpi'><div class='yp-skeleton'></div><div class='yp-skeleton yp-skel-val'></div><div class='yp-skeleton'></div></div>
          </div>
          <div class='yp-skel-chart'>
            <div class='yp-skeleton yp-skel-title'></div>
            <div class='yp-skeleton yp-skel-body'></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def require_login() -> None:
    """Block the page until the user is authenticated.

    On refresh, the cookie controller needs 2 render cycles to fetch the
    cookie from the browser. We force a loading screen for the FIRST TWO
    render cycles before falling back to the login form — this guarantees
    that a returning user with a valid cookie never sees the login form
    flash, even on slow JS replies.
    """
    # Fast path: already authenticated in this Streamlit session.
    if st.session_state.get("_auth"):
        _auth_logger.debug("require_login: fast-path, user=%s",
                           st.session_state.get("_user"))
        return

    ctrl = _make_controller()

    # If the user just hit "logout", ignore any still-present cookie for THIS
    # render. The cookie controller's remove() is dispatched to the browser
    # asynchronously via JS, so ctrl.getAll() can still return the old token
    # on the very next render and would silently log the user back in.
    # The flag is consumed (popped) so the next render reads cookies normally.
    if st.session_state.pop("_just_logged_out", False):
        _auth_logger.info("require_login: skipping cookie read (just logged out)")
        token: str | None = None
    else:
        # Try to read the cookie. On the first 1-2 renders this returns nothing
        # (the JS hasn't replied), so we'll show a loading screen instead of
        # the login form below.
        token = None
        if ctrl is not None:
            try:
                all_cookies = ctrl.getAll()
                if isinstance(all_cookies, dict):
                    token = all_cookies.get(_COOKIE_NAME)
                elif all_cookies is None:
                    # try direct .get() too — some library versions populate it
                    try:
                        token = ctrl.get(_COOKIE_NAME)
                    except Exception:
                        token = None
            except Exception:
                token = None

    # If we have a valid token, log in and return immediately.
    if token:
        username = _verify_token(token)
        if username and username in _load_users():
            st.session_state["_auth"] = True
            st.session_state["_user"] = username
            # Clear the "give cookies more time" counter — we're in.
            st.session_state.pop("_cookie_wait_count", None)
            _auth_logger.info("require_login: cookie-restored user=%s", username)
            return
        else:
            _auth_logger.warning("require_login: invalid/stale token, dropping")

    # No valid token. Two reasons this could be:
    #   (a) Cookies haven't loaded yet (need another render cycle).
    #   (b) User really has no cookie / cookie expired → show login form.
    # We can't tell these apart on a single render, so we force a few
    # render cycles of loading screen before giving up and showing the
    # login form. The "_login_form_submitted" flag short-circuits this
    # after the user has explicitly tried to log in.
    # NOTE: bumped from 4 → 8 waits because on Streamlit Cloud the
    # JS round-trip for cookie reads can exceed 2 seconds, and users
    # with a valid "remember me" cookie were prematurely seeing the
    # login form on browser refresh. 8 × 0.5s = 4s max wait.
    if (ctrl is not None
            and not st.session_state.get("_login_form_submitted")):
        waits = st.session_state.get("_cookie_wait_count", 0)
        if waits < 8:
            st.session_state["_cookie_wait_count"] = waits + 1
            _auth_logger.debug("require_login: waiting for cookie (cycle %d/8)",
                               waits + 1)
            _render_loading_screen()
            time.sleep(0.5)
            st.rerun()
        else:
            _auth_logger.info("require_login: no cookie after 8 waits → login form")

    # ----- Fall back to login form ───────────────────────────────────────────
    users = _load_users()
    if not users:
        st.error(
            "❌ אין משתמשים מוגדרים. "
            "הוסף [users] ל-`.streamlit/secrets.toml` עם hash של bcrypt."
        )
        st.stop()

    _render_login(users, ctrl)
    st.stop()


def current_user() -> str | None:
    return st.session_state.get("_user")


def logout() -> None:
    """Clear cookie + session, then rerun so require_login() shows the form.

    Previous implementation used `window.parent.location.reload()` injected
    via streamlit.components.v1.html. On Streamlit Community Cloud the
    component iframe is sandboxed cross-origin, so the parent reload throws
    a SecurityError and falls through to reloading only the (invisible)
    iframe — which does nothing user-visible. The button appeared dead.

    This version:
      • Removes the auth cookie (best-effort via streamlit-cookies-controller).
      • Sets `_just_logged_out` so require_login() ignores any cookie that
        hasn't yet been wiped on the browser side (the JS removal is async
        and may not have flushed before the next render).
      • Clears session_state auth keys.
      • Calls st.rerun() — require_login() then renders the login form.

    No splash screen: st.rerun() is fast enough that one isn't needed, and
    the previous splash relied on the same iframe mechanism that doesn't
    work on the cloud anyway.
    """
    _auth_logger.info("logout: user=%s", st.session_state.get("_user"))

    # 1. Cookie removal (server-side request to the browser via JS).
    ctrl = _make_controller()
    if ctrl is not None:
        try:
            ctrl.remove(_COOKIE_NAME)
        except Exception as _ex:
            _auth_logger.warning("logout: cookie remove failed: %s", _ex)

    # 2. Force require_login() to ignore any still-present cookie this render.
    st.session_state["_just_logged_out"] = True

    # 3. Session-state cleanup. Auth-related keys ONLY.
    _AUTH_KEYS = (
        "_auth", "_user", "_login_attempts", "_cookie_wait_count",
        "_login_form_submitted",
    )
    for k in _AUTH_KEYS:
        st.session_state.pop(k, None)

    # 4. Rerun. require_login() runs first at the top of the dashboard and
    #    will short-circuit to the login form.
    st.rerun()


# ---------------------------------------------------------------------------
# Login UI
# ---------------------------------------------------------------------------

def _render_login(users: dict[str, str], ctrl) -> None:
    # Optional: worker photo as background. If file missing → soft gradient.
    import os, base64 as _b64
    _here = os.path.dirname(os.path.abspath(__file__))
    _bg_path = os.path.join(_here, "static", "login_bg.jpg")
    _bg_css = "linear-gradient(180deg,#F1F5F9 0%,#E2E8F0 100%)"
    if os.path.exists(_bg_path):
        try:
            with open(_bg_path, "rb") as _f:
                _bg_b64 = _b64.b64encode(_f.read()).decode("ascii")
            # Lighter overlay so the worker photo is clearly visible.
            _bg_css = (
                "linear-gradient(rgba(248,250,252,0.60),rgba(241,245,249,0.78)),"
                f"url('data:image/jpeg;base64,{_bg_b64}') center/cover no-repeat fixed"
            )
        except Exception:
            pass

    # Logo handling: load PNG, programmatically replace white/near-white
    # pixels with full transparency (alpha=0), then base64-embed. This is more
    # reliable than CSS mix-blend-mode (which leaves anti-aliased gray edges).
    _logo_path = os.path.join(_here, "static", "logo.png")
    _logo_html = '<div class="yp-logo-fallback">👷</div>'

    def _load_logo_no_white_bg(path, threshold=235):
        """Read PNG and return PNG bytes with white background removed."""
        try:
            import io as _io
            import numpy as _np
            from PIL import Image as _PIL_Image
            img = _PIL_Image.open(path).convert("RGBA")
            arr = _np.array(img)
            # Pixels where R, G AND B are all above threshold → treat as
            # "white-ish" and make transparent (anti-aliased edges included).
            white_mask = _np.all(arr[:, :, :3] >= threshold, axis=2)
            arr[white_mask, 3] = 0
            buf = _io.BytesIO()
            _PIL_Image.fromarray(arr).save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            # PIL/numpy missing — fall back to raw file (white bg stays)
            with open(path, "rb") as _f:
                return _f.read()

    if os.path.exists(_logo_path):
        try:
            _logo_bytes = _load_logo_no_white_bg(_logo_path)
            _logo_b64 = _b64.b64encode(_logo_bytes).decode("ascii")
            # IMPORTANT: width/height + inline style cap the size BEFORE CSS
            # loads. Without them the browser briefly paints the logo at its
            # natural size (~1280px) and then the .yp-logo CSS shrinks it —
            # producing the "image opens then closes" flash on every render.
            _logo_html = (
                f'<img class="yp-logo" '
                f'src="data:image/png;base64,{_logo_b64}" '
                f'alt="Yanai Personnel" '
                f'width="160" height="160" '
                f'style="max-width:160px;max-height:160px;width:auto;height:auto;'
                f'display:block;margin:0 auto 6px;">'
            )
        except Exception:
            pass

    # Single centered column: logo on top, form in the middle-lower area.
    st.markdown(
        f"""
        <style>
        body, .stApp {{
            direction: rtl;
            font-family: 'Segoe UI', Arial, sans-serif;
            background: {_bg_css};
        }}
        section[data-testid="stSidebar"], [data-testid="collapsedControl"] {{
            display: none !important;
        }}
        .main .block-container {{
            max-width: 100% !important;
            padding: 0 !important;
        }}
        /* Push content to the MIDDLE-LOWER part of the viewport:
           top padding much bigger than bottom (~22vh top, 4vh bottom). */
        .main .block-container > div[data-testid="stVerticalBlock"] {{
            min-height: 100vh;
            padding: 22vh 4vw 4vh !important;
            box-sizing: border-box;
        }}
        /* Brand block — centered, sits above the form */
        .yp-brand {{
            text-align: center;
            display: flex; flex-direction: column;
            align-items: center; justify-content: center;
            margin-bottom: 18px;
        }}
        /* Logo — the PNG is preprocessed in Python to have a real transparent
           background (alpha=0 for white-ish pixels), so the logo "floats"
           cleanly on the worker photo with no white frame and no anti-aliased
           gray halo. */
        .yp-logo {{
            max-width: 160px; max-height: 160px;
            width: auto; height: auto;
            display: block; margin: 0 auto 6px;
            filter: saturate(1.15) contrast(1.05)
                    drop-shadow(0 4px 12px rgba(22,163,74,0.30));
        }}
        .yp-logo-fallback {{
            width: 96px; height: 96px; border-radius: 50%;
            background: linear-gradient(135deg,#0E5A2E,#16A34A);
            display: flex; align-items: center; justify-content: center;
            font-size: 44px; color: #fff;
            box-shadow: 0 10px 28px rgba(14,90,46,.3);
            margin: 0 auto 6px;
        }}
        .yp-name {{
            font-size: 24px; font-weight: 900; color: #0E5A2E;
            letter-spacing: .3px; line-height: 1.15;
        }}
        .yp-name .ltd {{ color: #0f172a; font-weight: 700; }}
        .yp-tag {{
            font-size: 12px; color: #16A34A; font-weight: 700;
            margin-top: 6px; letter-spacing: 1px; text-transform: uppercase;
            display:inline-block;padding:3px 10px;border-radius:99px;
            background:rgba(22,163,74,0.10);
            border:1px solid rgba(22,163,74,0.22);
        }}
        .yp-contact {{
            font-size: 11px; color: #64748b; margin-top: 6px; direction: ltr;
            letter-spacing:.5px;
        }}
        /* Form card — same width as logo block, centered horizontally */
        div[data-testid="stForm"] {{
            background: rgba(255,255,255,0.97) !important;
            border-radius: 16px !important;
            padding: 20px 26px 14px !important;
            box-shadow: 0 20px 50px rgba(14,90,46,0.18),
                        0 4px 10px rgba(14,90,46,0.08) !important;
            border: 1px solid rgba(14,90,46,0.10) !important;
            max-width: 420px;
            margin: 0 auto;
        }}
        div[data-testid="stForm"] div[data-testid="stTextInput"] {{
            margin-bottom: 4px;
        }}
        /* Hide Streamlit's "Press Enter to submit form" hint that auto-
           appears under text_input inside a form — user-flagged as noise. */
        div[data-testid="stForm"] div[data-testid="InputInstructions"],
        div[data-testid="stForm"] [data-testid="InputInstructions"],
        div[data-testid="stForm"] .stTooltipHoverTarget + div small,
        div[data-testid="stForm"] [class*="InputInstructions"] {{
            display: none !important;
        }}
        /* "Secure connection" trust badge below the form */
        .yp-trust {{
            max-width: 420px; margin: 10px auto 0; text-align: center;
            font-size: 10.5px; color: #475569; letter-spacing: .4px;
        }}
        .yp-trust .lock {{
            display:inline-flex;align-items:center;gap:5px;
            padding:3px 9px;border-radius:99px;
            background:rgba(255,255,255,0.7);
            border:1px solid rgba(14,90,46,0.12);
            font-weight:600;color:#0E5A2E;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Single centered column (no side-by-side). Use Streamlit's column
    # ratios to keep the content centered horizontally at any screen width.
    _l, _c, _r = st.columns([1, 2, 1])
    with _c:
        # Brand: logo + name + tag + contact (above form)
        st.markdown(
            f"""
            <div class="yp-brand">
              {_logo_html}
              <div class="yp-name">
                <span>ינאי פרסונל</span>
                <span class="ltd">בע"מ</span>
              </div>
              <div class="yp-tag">YANAI PERSONNEL · Human Resource</div>
              <div class="yp-contact">Tel: 08-922-8543</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        attempts = st.session_state.get("_login_attempts", 0)

        # Suppress Windows Hello / Passkey / browser autofill prompts.
        # Streamlit's st.text_input doesn't accept an `autocomplete` prop.
        # We use components.v1.html (NOT st.markdown — Streamlit strips
        # <script> tags from markdown for XSS protection) which mounts an
        # invisible iframe whose JS reaches up to `window.parent.document`
        # and patches the rendered inputs.
        try:
            from streamlit.components.v1 import html as _components_html_login
            _components_html_login(
                """
                <script>
                (function() {
                  // The iframe runs inside Streamlit's main page, so we
                  // need window.parent.document to reach the actual inputs.
                  const docs = [];
                  try { if (window.parent && window.parent.document)
                          docs.push(window.parent.document); } catch(e) {}
                  try { docs.push(document); } catch(e) {}
                  const setAttrs = () => {
                    docs.forEach(doc => {
                      if (!doc) return;
                      const inputs = doc.querySelectorAll(
                        'input[type="text"], input[type="password"]'
                      );
                      inputs.forEach(i => {
                        // Suppress Windows Hello / Passkey / autofill.
                        i.setAttribute("autocomplete", "off");
                        i.setAttribute("autocorrect", "off");
                        i.setAttribute("autocapitalize", "off");
                        i.setAttribute("spellcheck", "false");
                        i.setAttribute("data-form-type", "other");
                        i.setAttribute("data-lpignore", "true");
                        if (i.type === "password") {
                          i.setAttribute("name", "field-secret");
                        } else {
                          i.setAttribute("name", "field-user");
                        }
                      });
                      doc.querySelectorAll("form").forEach(f =>
                        f.setAttribute("autocomplete", "off"));
                    });
                  };
                  setAttrs();
                  // Streamlit re-renders inputs on every action — retry
                  // briefly to catch late-mounted nodes.
                  [50, 150, 400, 900, 1800].forEach(t =>
                      setTimeout(setAttrs, t));
                })();
                </script>
                """,
                height=0, scrolling=False,
            )
        except Exception:
            # Components missing? Silent fail — passkey may pop up but
            # login still works.
            pass

        with st.form("login_form", clear_on_submit=False):
            user_name = st.text_input("שם משתמש", placeholder="הכנס שם משתמש")
            password = st.text_input("סיסמה", type="password",
                                     placeholder="הכנס סיסמה")
            remember_me = st.checkbox(
                "זכור אותי",
                value=True,
                help=(f"אם מסומן: לא תידרש להתחבר שוב במשך {_REMEMBER_HOURS} שעות. "
                      "אם לא מסומן: יבקש סיסמה מחדש בכל פתיחת דפדפן."),
            )
            ok = st.form_submit_button("כניסה →", use_container_width=True,
                                       type="primary")

        # "Secure connection" trust line beneath the form.
        st.markdown(
            '<div class="yp-trust">'
            '<span class="lock">🔒 התחברות מאובטחת · הצפנת SSL</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    if ok:
        if attempts >= _MAX_LOGIN_ATTEMPTS:
            st.error("חשבון נעול זמנית עקב ניסיונות כניסה חוזרים.")
            return
        stored = users.get(user_name)
        if stored and _verify_password(stored, password):
            _auth_logger.info("login: success user=%s remember=%s",
                              user_name, remember_me)
            st.session_state.update({
                "_auth": True,
                "_user": user_name,
                "_login_attempts": 0,
                "_login_form_submitted": True,
            })
            # Persist signed token in a cookie ONLY if the user opted in.
            # Without "remember me", we rely on Streamlit's session_state
            # only — closing the browser / losing the session means the
            # user will see the login form again on next visit.
            if remember_me and ctrl is not None:
                try:
                    # secure=False so the cookie works on both HTTP (localhost
                    # dev) and HTTPS (Cloudflare tunnel). Path="/" makes it
                    # available across the whole app, not just the form route.
                    ctrl.set(
                        _COOKIE_NAME,
                        _sign_token(user_name),
                        max_age=_COOKIE_TTL_SEC,
                        secure=False,
                        same_site="lax",
                        path="/",
                    )
                    # Longer wait — give the JS controller a full round-trip
                    # to actually write the cookie before st.rerun() tears
                    # the page down. 0.5s was racing on slow networks.
                    time.sleep(1.2)
                except Exception:
                    # Worst case: cookie didn't save → user re-logs in next time
                    pass
            elif not remember_me and ctrl is not None:
                # Belt-and-suspenders: explicitly clear any pre-existing
                # cookie so unchecking the box reliably ends persistence.
                try:
                    ctrl.remove(_COOKIE_NAME)
                except Exception:
                    pass
            st.rerun()
        else:
            _auth_logger.warning("login: failed user=%s attempts=%d",
                                 user_name, attempts + 1)
            time.sleep(_LOGIN_DELAY_SEC)
            st.session_state["_login_attempts"] = attempts + 1
            st.error("שם משתמש או סיסמה שגויים")
