"""
core/audit_clients.py — כלי אבחון מיפוי לקוחות.

מריצים: python -m core.audit_clients

מציג:
  1. לקוחות/אתרים בצינור שאין להם תאמה ב-תקן.xlsx (billing_kind = unknown/missing_data)
  2. התפלגות billing_kind לפי שעות ועלות
  3. לקוחות ב-_no_match (no_pricing)
  4. המלצות פעולה
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

CACHE_PATH     = _HERE / "output" / "cache" / "processed_data.parquet"
STANDARDS_PATH = _HERE / "data" / "תקן.xlsx"
DATA_DIR       = _HERE / "data"

W = 72
SEP = "─" * W
DSEP = "═" * W


def _hdr(title: str) -> None:
    print(f"\n{DSEP}")
    print(f"  {title}")
    print(DSEP)


def _sub(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def run_audit() -> None:
    # ── טעינת נתונים ──────────────────────────────────────────────────────────
    if not CACHE_PATH.exists():
        print(f"ERROR: לא נמצא פרקט ב-{CACHE_PATH}")
        print("הרץ: python -c \"from core.preprocessor import build_and_save; build_and_save()\"")
        return

    df = pd.read_parquet(CACHE_PATH)

    from core.standards_loader import load_standards, site_billing_lookup, get_billing_rule
    std = load_standards(DATA_DIR)

    # ── no_match list ──────────────────────────────────────────────────────────
    _no_match: set[str] = set()
    _map_path = Path(__file__).parent / "client_mapping.json"
    if _map_path.exists():
        try:
            with open(_map_path, encoding="utf-8") as f:
                _no_match = set(json.load(f).get("_no_match", []))
        except Exception:
            pass

    _hdr("דוח אבחון מיפוי לקוחות — ינאי פרסונל")

    # ── 1. סטטיסטיקות כלליות ─────────────────────────────────────────────────
    _sub("1. סטטיסטיקות כלליות")
    total_rows  = len(df)
    n_clients   = df["client"].nunique()
    n_sites     = df["site"].nunique() if "site" in df.columns else 0
    total_hours = df["total_hours"].sum()
    total_cost  = df["cost"].sum() if "cost" in df.columns else df["allocated_cost"].sum()

    print(f"  שורות בפרקט  : {total_rows:,}")
    print(f"  לקוחות ייחודיים: {n_clients}")
    print(f"  אתרים ייחודיים : {n_sites}")
    print(f"  סה\"כ שעות     : {total_hours:,.0f}")
    print(f"  סה\"כ עלות     : ₪{total_cost:,.0f}")
    if std.empty:
        print("\n  ⚠️  לא נטען תקן.xlsx — בדוק שהקובץ קיים ב-data/")
    else:
        print(f"\n  שורות ב-תקן.xlsx: {len(std)}")
        print(f"  לקוחות בתקן    : {std['client_full'].nunique()}")

    # ── 2. התפלגות billing_kind ───────────────────────────────────────────────
    _sub("2. התפלגות billing_kind (לפי שעות ועלות)")
    if "billing_kind" in df.columns:
        bk_agg = (
            df.groupby("billing_kind", as_index=False)
            .agg(
                שורות=("employee_id", "count"),
                שעות=("total_hours", "sum"),
                עלות=("cost" if "cost" in df.columns else "allocated_cost", "sum"),
                לקוחות=("client", "nunique"),
            )
            .sort_values("שעות", ascending=False)
        )
        bk_agg["% שעות"] = (bk_agg["שעות"] / total_hours * 100).round(1)
        print(f"\n  {'billing_kind':30} {'שורות':>6} {'לקוחות':>7} {'שעות':>8} {'% שעות':>8} {'עלות (₪K)':>10}")
        print(f"  {'─'*30} {'─'*6} {'─'*7} {'─'*8} {'─'*8} {'─'*10}")
        for _, r in bk_agg.iterrows():
            flag = " ⚠️" if r["billing_kind"] in ("unknown", "missing_data", "no_pricing") else ""
            print(
                f"  {str(r['billing_kind']):30} {int(r['שורות']):>6} "
                f"{int(r['לקוחות']):>7} {r['שעות']:>8,.0f} "
                f"{r['% שעות']:>7.1f}% {r['עלות']/1000:>9,.0f}K{flag}"
            )
    else:
        print("  עמודת billing_kind לא קיימת — הרץ build_and_save() מחדש")

    # ── 3. לקוחות/אתרים ב-unknown ─────────────────────────────────────────────
    _sub("3. לקוחות ב-'unknown' (אין תאמה ב-תקן.xlsx)")
    if "billing_kind" in df.columns:
        unknown_df = df[df["billing_kind"].isin(["unknown", "missing_data"])].copy()
        if unknown_df.empty:
            print("  ✅ כל הלקוחות מכוסים!")
        else:
            unk_agg = (
                unknown_df
                .groupby(["client", "site"], as_index=False)
                .agg(
                    שעות=("total_hours", "sum"),
                    עלות=("cost" if "cost" in df.columns else "allocated_cost", "sum"),
                    חודשים=("month", "nunique"),
                )
                .sort_values("שעות", ascending=False)
            )
            print(f"\n  {len(unk_agg)} (client, site) ללא תקן — סה\"כ {unknown_df['total_hours'].sum():,.0f} שעות\n")
            print(f"  {'לקוח':35} {'אתר':30} {'שעות':>8} {'עלות ₪K':>9} {'חודשים':>7}")
            print(f"  {'─'*35} {'─'*30} {'─'*8} {'─'*9} {'─'*7}")
            for _, r in unk_agg.head(30).iterrows():
                print(
                    f"  {str(r['client'])[:35]:35} {str(r['site'])[:30]:30} "
                    f"{r['שעות']:>8,.0f} {r['עלות']/1000:>8,.0f}K {int(r['חודשים']):>7}"
                )
            if len(unk_agg) > 30:
                print(f"  ... ועוד {len(unk_agg)-30} (client,site) נוספים")

    # ── 4. לקוחות ב-no_pricing ────────────────────────────────────────────────
    _sub("4. לקוחות ב-no_pricing (_no_match)")
    if _no_match:
        print(f"  {len(_no_match)} לקוחות מוגדרים כ-no_pricing (לא מחשבים הפסד הכנסה):")
        for c in sorted(_no_match):
            rows_c = df[df["client"] == c]
            hrs = rows_c["total_hours"].sum() if not rows_c.empty else 0
            print(f"    • {c} — {hrs:,.0f}ש'")
    else:
        print("  אין לקוחות ב-_no_match (client_mapping.json)")

    # ── 5. לקוחות בצינור שחסרים לגמרי מ-תקן ─────────────────────────────────
    _sub("5. לקוחות בצינור שחסרים לגמרי מ-תקן.xlsx")
    if not std.empty:
        std_clients = set(std["client_full"].str.strip())
        pipeline_clients = set(df["client"].dropna().str.strip())

        # נסה להתאים לאחר נורמליזציה פשוטה
        from core.standards_v2 import _norm_for_match
        std_normed = {_norm_for_match(c): c for c in std_clients}

        missing_from_std = []
        for pc in sorted(pipeline_clients):
            if pc in std_clients or pc in _no_match:
                continue
            pn = _norm_for_match(pc)
            if pn in std_normed:
                continue  # מוצא ב-fuzzy
            rows_c = df[df["client"] == pc]
            hrs = rows_c["total_hours"].sum()
            cst = rows_c["cost"].sum() if "cost" in df.columns else rows_c["allocated_cost"].sum()
            months = rows_c["month"].nunique()
            missing_from_std.append((pc, hrs, cst, months))

        missing_from_std.sort(key=lambda x: x[1], reverse=True)

        if not missing_from_std:
            print("  ✅ כל הלקוחות בצינור מוצאים תאמה (ישירה או fuzzy) ב-תקן.xlsx")
        else:
            print(f"\n  {len(missing_from_std)} לקוחות בצינור ללא תאמה ב-תקן.xlsx:\n")
            print(f"  {'לקוח':40} {'שעות':>8} {'עלות ₪K':>9} {'חודשים':>7}")
            print(f"  {'─'*40} {'─'*8} {'─'*9} {'─'*7}")
            for pc, hrs, cst, months in missing_from_std:
                print(f"  {pc[:40]:40} {hrs:>8,.0f} {cst/1000:>8,.0f}K {months:>7}")

    # ── 6. סיכום ─────────────────────────────────────────────────────────────
    _sub("6. המלצות")
    if "billing_kind" in df.columns:
        unk_hrs = float(df[df["billing_kind"].isin(["unknown","missing_data"])]["total_hours"].sum())
        unk_pct = unk_hrs / total_hours * 100 if total_hours > 0 else 0
        if unk_pct > 5:
            print(f"  ⚠️  {unk_pct:.1f}% מהשעות ({unk_hrs:,.0f}) ללא תקן — כדאי להוסיף ל-data/תקן.xlsx")
        else:
            print(f"  ✅ {unk_pct:.1f}% בלבד ללא תקן ({unk_hrs:,.0f} שעות)")
        print()
        print("  פעולות מומלצות:")
        print("  1. הוסף שורות לתקן.xlsx עבור לקוחות החסרים (ראה סעיף 5)")
        print("  2. עדכן _no_match ב-client_mapping.json ללקוחות ללא הסכם חיוב")
        print("  3. הרץ build_and_save() לאחר עדכון תקן.xlsx")
    print(f"\n{DSEP}\n")


if __name__ == "__main__":
    run_audit()
