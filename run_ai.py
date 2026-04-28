"""
run_ai.py — CLI runner for financial analysis.

Usage:
    python run_ai.py              # full report
    python run_ai.py --ask "..."  # single question
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from core.ai_insights import (
    get_top_profitable_clients,
    get_loss_clients,
    get_profit_by_month,
    get_top_expensive_employees,
    detect_problems,
    generate_summary_text,
    ask_question,
)


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

W = 64


def _header(title: str) -> None:
    print(f"\n{'─' * 4} {title} {'─' * max(0, W - 6 - len(title))}")


def _rule() -> None:
    print("═" * W)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def _print_top_clients() -> None:
    _header("TOP PROFITABLE CLIENTS")
    df = get_top_profitable_clients(5)
    if df.empty:
        print("  No data.")
        return
    for _, r in df.iterrows():
        bar = "█" * max(0, int(r["margin"] / 5)) if r["margin"] > 0 else "░"
        print(
            f"  {str(r['client'])[:38]:38}"
            f"  profit ₪{r['profit']:>12,.0f}"
            f"  {r['margin']:>6.1f}%  {bar}"
        )


def _print_loss_clients() -> None:
    _header("LOSS-MAKING CLIENTS")
    df = get_loss_clients()
    if df.empty:
        print("  ✓ None — all clients are profitable")
        return
    for _, r in df.iterrows():
        print(
            f"  ❌ {str(r['client'])[:35]:35}"
            f"  loss ₪{abs(r['profit']):>12,.0f}"
            f"  margin {r['margin']:.1f}%"
        )


def _print_monthly() -> None:
    _header("MONTHLY SUMMARY")
    df = get_profit_by_month()
    for _, r in df.iterrows():
        mom = ""
        if pd.notna(r.get("billing_mom_pct")):
            arrow = "(+)" if r["billing_mom_pct"] >= 0 else "(-)"
            mom = f"  {arrow}{abs(r['billing_mom_pct']):.1f}%"
        profit_sign = "+" if r["profit"] >= 0 else ""
        print(
            f"  {r['month']}"
            f"  billing ₪{r['billing']:>12,.0f}"
            f"  profit {profit_sign}₪{r['profit']:>12,.0f}"
            f"  {r['margin']:>6.1f}%"
            f"{mom}"
        )


def _print_employees() -> None:
    _header("TOP EXPENSIVE EMPLOYEES (cost to company)")
    df = get_top_expensive_employees(5)
    if df.empty:
        print("  No data.")
        return
    for _, r in df.iterrows():
        print(
            f"  {str(r['employee_name'])[:30]:30}"
            f"  cost ₪{r['cost']:>12,.0f}"
            f"  {r['hours']:.0f}h"
            f"  ₪{r['cost_per_hour']:.0f}/h"
        )


def _print_problems() -> None:
    _header("PROBLEMS DETECTED")
    p = detect_problems()
    found_any = False

    if p["loss_clients"]:
        found_any = True
        names = ", ".join(str(c)[:25] for c in p["loss_clients"][:4])
        more  = f" (+{len(p['loss_clients'])-4} more)" if len(p["loss_clients"]) > 4 else ""
        print(f"  ❌ Loss clients ({len(p['loss_clients'])}): {names}{more}")

    if p["low_margin_clients"]:
        found_any = True
        names = ", ".join(str(c)[:20] for c in p["low_margin_clients"][:4])
        more  = f" (+{len(p['low_margin_clients'])-4} more)" if len(p["low_margin_clients"]) > 4 else ""
        print(f"  ⚠️  Low margin <10% ({len(p['low_margin_clients'])}): {names}{more}")

    if p["cost_exceeds_billing"]:
        found_any = True
        names = ", ".join(str(c)[:20] for c in p["cost_exceeds_billing"][:4])
        print(f"  ⚠️  Cost > billing: {names}")

    if p["revenue_drops"]:
        found_any = True
        for drop in p["revenue_drops"]:
            print(f"  ⚠️  Revenue drop: {drop['month']}  {drop['drop_pct']:+.1f}%  (₪{drop['billing']:,.0f})")

    if not found_any:
        print("  ✓ No critical problems detected")


def _print_summary() -> None:
    _header("BUSINESS SUMMARY")
    print(generate_summary_text())


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------

def run_report() -> None:
    _rule()
    print(f"{'  CFO ANALYTICS REPORT':^{W}}")
    _rule()

    _print_top_clients()
    _print_loss_clients()
    _print_monthly()
    _print_employees()
    _print_problems()
    _print_summary()

    _rule()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BI Analytics CLI")
    parser.add_argument(
        "--ask",
        metavar="QUESTION",
        help="Answer a single business question and exit",
    )
    args = parser.parse_args()

    if args.ask:
        print(ask_question(args.ask))
    else:
        run_report()
