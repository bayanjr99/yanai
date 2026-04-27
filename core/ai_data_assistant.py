"""
AI Data Assistant — answers business questions using billing data.

Rules:
- Answers ONLY from available data (master.parquet / history files)
- If data is missing → says so clearly
- All answers in Hebrew with numbers
- Local pandas analytics first, then Claude for narrative
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_ai_dataset() -> pd.DataFrame:
    """
    Load the master dataset.  Falls back to rebuilding from history files
    if master.parquet is missing.
    """
    from pipeline import get_all_data, HISTORY_ROOT, load_month_clients, get_available_months

    master = get_all_data()
    if not master.empty:
        return master

    # Fallback: build from per-month clients.csv files
    frames = []
    for m in get_available_months():
        df = load_month_clients(m)
        if not df.empty:
            if "month" not in df.columns:
                df["month"] = m
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# Local analytics (no AI needed for these)
# ---------------------------------------------------------------------------

def _fmt(v: float) -> str:
    return f"₪{v:,.0f}"


def top_clients_summary(df: pd.DataFrame, n: int = 5) -> str:
    if df.empty or "client" not in df.columns:
        return "אין נתונים."
    grp = (
        df.groupby("client", as_index=False)
        .agg(billing=("billing", "sum"), profit=("profit", "sum"))
        .nlargest(n, "profit")
    )
    lines = []
    for _, r in grp.iterrows():
        margin = r["profit"] / r["billing"] * 100 if r["billing"] else 0
        lines.append(f"• {r['client']}: חיוב {_fmt(r['billing'])}, רווח {_fmt(r['profit'])} ({margin:.1f}%)")
    return "\n".join(lines)


def loss_clients_summary(df: pd.DataFrame) -> str:
    if df.empty or "profit" not in df.columns:
        return "אין נתונים."
    grp = (
        df.groupby("client", as_index=False)
        .agg(billing=("billing", "sum"), profit=("profit", "sum"))
        .query("profit < 0")
        .sort_values("profit")
    )
    if grp.empty:
        return "אין לקוחות עם הפסד."
    lines = [f"• {r['client']}: הפסד {_fmt(abs(r['profit']))} על חיוב {_fmt(r['billing'])}"
             for _, r in grp.iterrows()]
    return "\n".join(lines)


def month_comparison_summary(df: pd.DataFrame, current: str) -> str:
    from pipeline import _prev_month  # type: ignore[attr-defined]
    if df.empty or "month" not in df.columns:
        return "אין נתונים להשוואה."

    prev = _prev_month(current)
    curr_d = df[df["month"] == current]
    prev_d = df[df["month"] == prev] if prev else pd.DataFrame()

    if curr_d.empty:
        return f"אין נתונים לחודש {current}."

    cb = curr_d["billing"].sum(); cp = curr_d["profit"].sum()
    pb = prev_d["billing"].sum() if not prev_d.empty else None
    pp = prev_d["profit"].sum()  if not prev_d.empty else None

    lines = [
        f"חודש {current}: חיוב {_fmt(cb)}, רווח {_fmt(cp)} ({cp/cb*100:.1f}%)" if cb else f"חודש {current}: חיוב 0"
    ]
    if pb is not None:
        db = (cb - pb) / abs(pb) * 100 if pb else 0
        dp = (cp - pp) / abs(pp) * 100 if pp else 0
        lines.append(f"vs {prev}: חיוב {'+' if db>=0 else ''}{db:.1f}%, רווח {'+' if dp>=0 else ''}{dp:.1f}%")
    return "\n".join(lines)


def expensive_employees_summary(df: pd.DataFrame, n: int = 5) -> str:
    if df.empty or "employee_name" not in df.columns or "cost" not in df.columns:
        return "אין נתוני עלויות עובדים."
    grp = (
        df.groupby("employee_name", as_index=False)
        .agg(cost=("cost", "sum"), billing=("billing", "sum"), profit=("profit", "sum"))
        .nlargest(n, "cost")
    )
    lines = [f"• {r['employee_name']}: עלות {_fmt(r['cost'])}, רווח {_fmt(r['profit'])}"
             for _, r in grp.iterrows()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context builder for Claude
# ---------------------------------------------------------------------------

def build_summary_context(df: pd.DataFrame) -> str:
    """Build a concise Hebrew data summary to pass as context to Claude."""
    if df.empty:
        return "אין נתונים זמינים."

    months = sorted(df["month"].unique()) if "month" in df.columns else []
    total_b  = df["billing"].sum()  if "billing"  in df.columns else 0
    total_p  = df["profit"].sum()   if "profit"   in df.columns else 0
    total_c  = df["cost"].sum()     if "cost"     in df.columns else 0
    n_clients = df["client"].nunique()   if "client"        in df.columns else 0
    n_emp     = df["employee_name"].nunique() if "employee_name" in df.columns else 0
    margin    = total_p / total_b * 100 if total_b else 0

    # Per-client totals
    client_lines = []
    if "client" in df.columns:
        for _, r in df.groupby("client").agg(
            billing=("billing","sum"), profit=("profit","sum"), cost=("cost","sum")
        ).iterrows():
            m = r["profit"] / r["billing"] * 100 if r["billing"] else 0
            client_lines.append(
                f"  {_}: חיוב {_fmt(r['billing'])}, עלות {_fmt(r['cost'])}, "
                f"רווח {_fmt(r['profit'])} ({m:.1f}%)"
            )

    # Monthly trend (last 6 months)
    trend_lines = []
    if "month" in df.columns:
        trend = df.groupby("month").agg(billing=("billing","sum"), profit=("profit","sum"))
        for m, r in trend.tail(6).iterrows():
            trend_lines.append(f"  {m}: חיוב {_fmt(r['billing'])}, רווח {_fmt(r['profit'])}")

    ctx = f"""=== נתוני מערכת החיוב ===
תקופה: {months[0] if months else '—'} עד {months[-1] if months else '—'} ({len(months)} חודשים)
סה"כ חיוב: {_fmt(total_b)}
סה"כ עלות: {_fmt(total_c)}
סה"כ רווח: {_fmt(total_p)} ({margin:.1f}%)
לקוחות פעילים: {n_clients}
עובדים: {n_emp}

=== לקוחות ===
{chr(10).join(client_lines) or 'אין נתונים'}

=== מגמה חודשית (6 אחרונים) ===
{chr(10).join(trend_lines) or 'אין נתונים'}
"""
    return ctx


# ---------------------------------------------------------------------------
# Question answering
# ---------------------------------------------------------------------------

def _local_answer(question: str, df: pd.DataFrame) -> Optional[str]:
    """
    Try to answer common questions locally with pandas — no API call needed.
    Returns None if the question needs Claude.
    """
    q = question.strip().lower()
    months = sorted(df["month"].unique()) if "month" in df.columns else []

    if any(w in q for w in ["רווחי", "רווח הכי", "מי הכי רוו"]):
        return "**לקוחות רווחיים ביותר:**\n" + top_clients_summary(df)

    if any(w in q for w in ["הפסד", "מפסיד", "שלילי"]):
        return "**לקוחות בהפסד:**\n" + loss_clients_summary(df)

    if any(w in q for w in ["יקר", "עלות גבוה", "עובד יקר"]):
        return "**עובדים עם עלות גבוהה:**\n" + expensive_employees_summary(df)

    if any(w in q for w in ["חודש קודם", "שינוי", "השוואה"]) and months:
        last = months[-1]
        return "**השוואה לחודש קודם:**\n" + month_comparison_summary(df, last)

    # Specific month query: "02-2026" or "פברואר 2026"
    for m in months:
        if m in question:
            return "**" + month_comparison_summary(df, m) + "**"

    return None   # needs Claude


def answer_question(question: str, df: pd.DataFrame) -> str:
    """
    Answer a business question about the billing data.
    1. Try local pandas analytics first.
    2. Fall back to Claude with full context.
    """
    if df.empty:
        return "אין נתונים זמינים במערכת. חשב לפחות חודש אחד כדי לקבל תשובות."

    # Local fast-path
    local = _local_answer(question, df)
    if local:
        return local

    # Claude with context
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return (
            "AI לא מוגדר. הוסף ANTHROPIC_API_KEY כמשתנה סביבה.\n\n"
            "**נתונים זמינים:**\n" + build_summary_context(df)[:500]
        )

    try:
        import anthropic
        context = build_summary_context(df)
        client  = anthropic.Anthropic(api_key=api_key)
        resp    = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=(
                "אתה עוזר עסקי חכם שמנתח נתוני חיוב ורווחיות של חברת כוח אדם. "
                "ענה תמיד בעברית. ציין מספרים ספציפיים. "
                "אם השאלה לא ניתנת לתשובה מהנתונים — אמור 'אין מספיק נתונים'. "
                "אל תמציא מידע.\n\n" + context
            ),
            messages=[{"role": "user", "content": question}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"שגיאה בחיבור ל-AI: {e}"
