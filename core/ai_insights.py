"""
core/ai_insights.py — CFO-grade financial analysis layer.

Loads from output/master/master_full.xlsx (created by pipeline.build_master_full()).
Simple questions answered with pandas; complex ones fall back to Claude.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

_DATA_PATH = Path(__file__).parent.parent / "output" / "master" / "master_full.xlsx"


# ---------------------------------------------------------------------------
# Data loader (module-level cache)
# ---------------------------------------------------------------------------

_cache: dict[str, pd.DataFrame] = {}


def load_data(force: bool = False) -> pd.DataFrame:
    """Load master_full.xlsx, cached for the process lifetime."""
    if "df" not in _cache or force:
        df = pd.read_excel(_DATA_PATH)
        # Ensure numeric types are clean
        num_cols = ["billing", "cost", "profit", "margin", "hours", "days",
                    "profit_per_hour", "cost_per_hour", "revenue_per_hour"]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        for col in ("client", "site", "employee_id", "employee_name", "month"):
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str).str.strip()
        _cache["df"] = df
    return _cache["df"]


# ---------------------------------------------------------------------------
# 1. Top profitable clients
# ---------------------------------------------------------------------------

def get_top_profitable_clients(n: int = 5) -> pd.DataFrame:
    """Return top N clients sorted by total profit."""
    df = load_data()
    result = (
        df.groupby("client", as_index=False)
        .agg(billing=("billing", "sum"),
             cost    =("cost",    "sum"),
             profit  =("profit",  "sum"),
             hours   =("hours",   "sum"))
        .assign(margin=lambda x: (
            x["profit"] / x["billing"].replace(0, float("nan")) * 100
        ).round(1).fillna(0.0))
        .nlargest(n, "profit")
        .reset_index(drop=True)
    )
    for col in ("billing", "cost", "profit", "hours"):
        result[col] = result[col].round(2)
    return result


# ---------------------------------------------------------------------------
# 2. Loss-making clients
# ---------------------------------------------------------------------------

def get_loss_clients() -> pd.DataFrame:
    """Return all clients with negative total profit, worst first."""
    df = load_data()
    result = (
        df.groupby("client", as_index=False)
        .agg(billing=("billing", "sum"),
             cost    =("cost",    "sum"),
             profit  =("profit",  "sum"),
             hours   =("hours",   "sum"))
        .assign(margin=lambda x: (
            x["profit"] / x["billing"].replace(0, float("nan")) * 100
        ).round(1).fillna(0.0))
        .query("profit < 0")
        .sort_values("profit")           # worst first
        .reset_index(drop=True)
    )
    for col in ("billing", "cost", "profit", "hours"):
        result[col] = result[col].round(2)
    return result


# ---------------------------------------------------------------------------
# 3. Profit / revenue by month
# ---------------------------------------------------------------------------

def get_profit_by_month() -> pd.DataFrame:
    """
    Return monthly aggregated financials with MoM change columns.
    Sorted chronologically.
    """
    df = load_data()
    monthly = (
        df.groupby("month", as_index=False)
        .agg(billing=("billing", "sum"),
             cost    =("cost",    "sum"),
             profit  =("profit",  "sum"),
             hours   =("hours",   "sum"))
    )
    # Sort chronologically (MM-YYYY strings sort wrong lexicographically)
    monthly["_sort"] = pd.to_datetime(monthly["month"], format="%m-%Y", errors="coerce")
    monthly = monthly.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)
    monthly["margin"] = (
        monthly["profit"] / monthly["billing"].replace(0, float("nan")) * 100
    ).round(1).fillna(0.0)

    # Month-over-month percentage change
    monthly["billing_mom_pct"] = monthly["billing"].pct_change().mul(100).round(1)
    monthly["profit_mom_pct"]  = monthly["profit"].pct_change().mul(100).round(1)

    for col in ("billing", "cost", "profit", "hours"):
        monthly[col] = monthly[col].round(2)
    return monthly


# ---------------------------------------------------------------------------
# 4. Top expensive employees (by employer cost)
# ---------------------------------------------------------------------------

def get_top_expensive_employees(n: int = 5) -> pd.DataFrame:
    """Return top N employees by total employer cost."""
    df = load_data()
    result = (
        df.groupby("employee_name", as_index=False)
        .agg(cost   =("cost",    "sum"),
             billing=("billing", "sum"),
             profit =("profit",  "sum"),
             hours  =("hours",   "sum"))
        .assign(cost_per_hour=lambda x: (
            x["cost"] / x["hours"].replace(0, float("nan"))
        ).round(2).fillna(0.0))
        .nlargest(n, "cost")
        .reset_index(drop=True)
    )
    for col in ("cost", "billing", "profit", "hours"):
        result[col] = result[col].round(2)
    return result


# ---------------------------------------------------------------------------
# 5. Detect problems
# ---------------------------------------------------------------------------

def detect_problems() -> dict[str, list]:
    """
    Scan for four business problem categories:

    - loss_clients          : negative total profit
    - low_margin_clients    : margin < 10% (but not losing)
    - cost_exceeds_billing  : total cost > total billing
    - revenue_drops         : billing dropped > 15% MoM

    Internal entities (our own company units — see ``core.internal_entities``)
    are excluded: their cost-without-billing is overhead, not a problem.
    """
    from core.internal_entities import is_internal

    df = load_data()

    by_client = (
        df.groupby("client", as_index=False)
        .agg(billing=("billing", "sum"),
             cost    =("cost",    "sum"),
             profit  =("profit",  "sum"))
        .assign(margin=lambda x: (
            x["profit"] / x["billing"].replace(0, float("nan")) * 100
        ).round(1).fillna(-999.0))
    )

    # Mask out internal entities from problem detection
    _is_internal = by_client["client"].astype(str).map(is_internal)

    loss_clients = by_client.loc[
        (by_client["profit"] < 0) & (~_is_internal), "client"
    ].tolist()

    low_margin_clients = by_client.loc[
        (by_client["profit"] >= 0) & (by_client["margin"] < 10) & (~_is_internal),
        "client",
    ].tolist()

    cost_exceeds_billing = by_client.loc[
        (by_client["cost"] > by_client["billing"]) & (~_is_internal),
        "client",
    ].tolist()

    # Revenue drops: find months where billing fell > 15% vs previous month
    monthly = get_profit_by_month()
    revenue_drops: list[dict] = []
    for _, row in monthly.iterrows():
        pct = row.get("billing_mom_pct")
        if pd.notna(pct) and pct < -15:
            revenue_drops.append({
                "month":    row["month"],
                "billing":  round(float(row["billing"]), 2),
                "drop_pct": round(float(pct), 1),
            })

    return {
        "loss_clients":         loss_clients,
        "low_margin_clients":   low_margin_clients,
        "cost_exceeds_billing": cost_exceeds_billing,
        "revenue_drops":        revenue_drops,
    }


# ---------------------------------------------------------------------------
# 6. Generate plain-language business summary
# ---------------------------------------------------------------------------

def generate_summary_text() -> str:
    """
    Return a concise plain-English business summary suitable for
    executives or feeding into an LLM as context.
    """
    df = load_data()
    _m_series = pd.to_datetime(df["month"].unique(), format="%m-%Y", errors="coerce")
    months = [
        m for _, m in sorted(
            zip(_m_series, df["month"].unique()),
            key=lambda t: t[0],
        )
    ]
    if not months:
        return "No data available."

    latest = months[-1]
    prev   = months[-2] if len(months) >= 2 else None

    total_billing = df["billing"].sum()
    total_cost    = df["cost"].sum()
    total_profit  = df["profit"].sum()
    overall_margin = total_profit / total_billing * 100 if total_billing > 0 else 0

    lm     = df[df["month"] == latest]
    lm_b   = lm["billing"].sum()
    lm_p   = lm["profit"].sum()
    lm_m   = lm_p / lm_b * 100 if lm_b > 0 else 0

    mom_note = ""
    if prev:
        pm_b = df[df["month"] == prev]["billing"].sum()
        if pm_b > 0:
            pct = (lm_b - pm_b) / pm_b * 100
            arrow = "(+)" if pct >= 0 else "(-)"
            mom_note = f" {arrow} {abs(pct):.1f}% vs {prev}"

    # Top client by profit
    client_profit = df.groupby("client")["profit"].sum()
    top_client        = client_profit.idxmax()
    top_client_profit = client_profit.max()

    # Loss clients — exclude internal entities (overhead is not a "loss")
    from core.internal_entities import is_internal
    loss_clients = [
        c for c in client_profit[client_profit < 0].index.tolist()
        if not is_internal(c)
    ]

    lines = [
        f"=== Business Summary ({months[0]} – {latest}) ===",
        "",
        f"Total revenue : ₪{total_billing:>14,.0f}",
        f"Total cost    : ₪{total_cost:>14,.0f}",
        f"Total profit  : ₪{total_profit:>14,.0f}  ({overall_margin:.1f}% margin)",
        "",
        f"Latest month  : {latest} — revenue ₪{lm_b:,.0f}, "
        f"profit ₪{lm_p:,.0f} ({lm_m:.1f}%){mom_note}",
        "",
        f"Top client    : {top_client}  (profit ₪{top_client_profit:,.0f})",
    ]

    if loss_clients:
        sample = loss_clients[:5]
        more   = f" (+{len(loss_clients)-5} more)" if len(loss_clients) > 5 else ""
        lines.append(f"Loss clients  : {', '.join(sample)}{more}")
    else:
        lines.append("Loss clients  : None — all clients profitable ✓")

    problems = detect_problems()
    if problems["revenue_drops"]:
        drops_str = ", ".join(f"{d['month']} ({d['drop_pct']:+.1f}%)" for d in problems["revenue_drops"])
        lines.append(f"Revenue drops : {drops_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. Natural-language question answering
# ---------------------------------------------------------------------------

def ask_question(question: str) -> str:
    """
    Answer a business finance question.
    Tries pandas first; falls back to Claude API for complex questions.
    """
    df  = load_data()
    q   = question.lower().strip()

    # ── Pandas fast-path answers ─────────────────────────────────────────────

    # Total / aggregate metrics
    if any(w in q for w in ("total billing", "total revenue", "סה\"כ הכנסה", "סה\"כ חיוב")):
        v = df["billing"].sum()
        return f"Total billing: ₪{v:,.0f}"

    if any(w in q for w in ("total profit", "total loss", "סה\"כ רווח")):
        v = df["profit"].sum()
        label = "profit" if v >= 0 else "loss"
        return f"Total {label}: ₪{abs(v):,.0f}"

    if any(w in q for w in ("total cost", "סה\"כ עלות")):
        v = df["cost"].sum()
        return f"Total cost: ₪{v:,.0f}"

    if any(w in q for w in ("margin", "מרג'ין", "אחוז רווח")):
        b = df["billing"].sum()
        p = df["profit"].sum()
        m = p / b * 100 if b > 0 else 0
        return f"Overall margin: {m:.1f}%"

    # Top / best client
    if any(w in q for w in ("top client", "best client", "most profitable client",
                             "הכי רווחי", "לקוח מוביל")):
        by_profit = df.groupby("client")["profit"].sum()
        best = by_profit.idxmax()
        return f"Most profitable client: {best}  (₪{by_profit[best]:,.0f} profit)"

    # Top client by billing
    if any(w in q for w in ("biggest client", "largest client", "highest billing",
                             "הכי גדול", "הכי גבוה")):
        by_billing = df.groupby("client")["billing"].sum()
        best = by_billing.idxmax()
        return f"Highest-billing client: {best}  (₪{by_billing[best]:,.0f})"

    # Loss clients
    if any(w in q for w in ("loss", "losing", "negative profit", "הפסד", "מפסיד")):
        loss = df.groupby("client")["profit"].sum()
        loss = loss[loss < 0].sort_values()
        if loss.empty:
            return "No loss-making clients — all clients are profitable."
        rows = [f"{name}: ₪{abs(v):,.0f} loss" for name, v in loss.items()]
        return "Loss-making clients:\n" + "\n".join(f"  • {r}" for r in rows)

    # Best / worst month
    if any(w in q for w in ("best month", "highest month", "הכי טוב", "חודש הכי")):
        by_month = df.groupby("month")["billing"].sum()
        best = by_month.idxmax()
        return f"Best month by billing: {best}  (₪{by_month[best]:,.0f})"

    if any(w in q for w in ("worst month", "lowest month", "הכי גרוע")):
        by_month = df.groupby("month")["profit"].sum()
        worst = by_month.idxmin()
        return f"Worst month by profit: {worst}  (₪{by_month[worst]:,.0f})"

    # How many months
    if any(w in q for w in ("how many months", "כמה חודשים", "months available")):
        _ms = pd.to_datetime(df["month"].unique(), format="%m-%Y", errors="coerce")
        months = [m for _, m in sorted(zip(_ms, df["month"].unique()), key=lambda t: t[0])]
        return f"{len(months)} months: {', '.join(months)}"

    # Most expensive employee
    if any(w in q for w in ("expensive employee", "highest cost employee",
                             "עובד יקר", "עלות עובד")):
        by_cost = df.groupby("employee_name")["cost"].sum()
        top = by_cost.idxmax()
        return f"Most expensive employee: {top}  (₪{by_cost[top]:,.0f} cost)"

    # Monthly summary request
    if any(w in q for w in ("monthly summary", "by month", "month by month",
                             "סיכום חודשי", "לפי חודש")):
        monthly = get_profit_by_month()[["month", "billing", "profit", "margin"]]
        lines = ["Month-by-month:"]
        for _, r in monthly.iterrows():
            lines.append(
                f"  {r['month']}  billing ₪{r['billing']:>10,.0f}  "
                f"profit ₪{r['profit']:>10,.0f}  {r['margin']:.1f}%"
            )
        return "\n".join(lines)

    # ── Claude API fallback ───────────────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return (
            "I couldn't answer with the available quick patterns. "
            "Set ANTHROPIC_API_KEY to enable complex question answering."
        )

    import anthropic as _anthropic

    context = "\n".join([
        generate_summary_text(),
        "",
        "Monthly breakdown:",
        get_profit_by_month()[["month", "billing", "cost", "profit", "margin"]]
        .to_string(index=False),
        "",
        "Top 5 clients by profit:",
        get_top_profitable_clients(5)[["client", "billing", "profit", "margin"]]
        .to_string(index=False),
    ])

    try:
        _client = _anthropic.Anthropic(api_key=api_key)
        res = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=(
                "You are a CFO assistant. Answer business finance questions "
                "concisely in 2–3 sentences using numbers from the context. "
                "Be direct and specific."
            ),
            messages=[{
                "role":    "user",
                "content": f"Context:\n{context}\n\nQuestion: {question}",
            }],
        )
        return res.content[0].text.strip()
    except Exception as e:
        return f"AI error: {e}"
