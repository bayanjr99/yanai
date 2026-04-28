# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

```
billing_system/
├── core/
│   ├── pdf_parser.py      ← Andromeda PDF → daily rows
│   ├── aggregator.py      ← daily → monthly aggregation
│   ├── excel_loaders.py   ← load agreements + costs Excel files
│   ├── matcher.py         ← resolve client & agreement per row
│   ├── billing_engine.py  ← apply agreement → BillingResult (Excel path)
│   ├── rules_engine.py    ← apply agreement → daily result (PDF path)
│   ├── report_builder.py  ← write Excel reports (rarely used)
│   ├── validation.py      ← PDF validation + billing sanity checks
│   └── analytics.py       ← KPIs, dashboard_table, insights_engine
├── utils/
│   └── hebrew.py          ← Hebrew normalization shared across core/
├── data/
│   ├── agreements.xlsx    ← shared billing agreements (all months)
│   ├── overrides.xlsx     ← per-employee rate overrides (optional)
│   ├── MM-YYYY/           ← one folder per month
│   │   ├── hours.pdf      ← Andromeda payroll PDF  (or hours.xlsx)
│   │   └── costs.xlsx     ← employer costs for this month
│   └── master_full.parquet← merged dataset, all months (auto-generated)
├── app.py                 ← Streamlit BI dashboard (Upload→Calculate→Analyze)
├── pipeline.py            ← orchestration + build_master_full()
├── ai_tools.py            ← Claude API chat (optional, needs ANTHROPIC_API_KEY)
├── requirements.txt
└── start.sh
```

## Running the system

```bash
# Web UI (from billing_system/)
streamlit run app.py

# Linux/Mac shortcut
./start.sh
```

## Architecture & data flow

The pipeline is a linear chain. Each stage consumes the output of the previous one:

1. **`pdf_parser.py`** — Reads an Andromeda payroll PDF (`pypdf`). Each page = one employee. Extracts **daily rows** using strict regex: each row must have a date, two HH:MM times (start/end), a Hebrew site name, and a decimal `hours_to_pay`. Summary rows are skipped. Output: `employee_id, employee_name, date, site, hours_to_pay`.

2. **`rules_engine.py`** — Applies one agreement to one daily row; handles include_breaks, daily_min, OT rates. Returns a `RuleResult`.

3. **`pipeline._aggregate()`** — Sums daily billing rows into monthly totals per `(employee_id × site)`. Applies `monthly_min` completion once per employee across all sites. Adds `cost`, `profit`, `margin_pct` per row.

4. **`excel_loaders.py`** — Loads reference files with lenient Hebrew/English column matching:
   - **Agreements** → list of dicts with billing rules
   - **Employee costs** → dict keyed by `employee_id`

5. **`matcher.py`** — Resolves `client` via `resolve_client()` and `agreement` via `find_agreement()` (4-tier priority: exact → fuzzy → wildcard → none).

6. **`pipeline.build_master_full()`** — Scans all `data/MM-YYYY/` folders, runs the pipeline for each, and saves the merged result to `data/master_full.parquet`.

7. **`app.py`** — Streamlit UI. No auth. Three areas:
   - **Sidebar**: upload files, select month, calculate, batch-calculate all
   - **Current month**: KPIs, charts, billing table, alerts
   - **BI Dashboard**: reads `master_full.parquet`; trend charts, top clients, profitability table

## Key conventions

**Hebrew-aware matching** — All string comparisons go through `utils/hebrew.py`: `normalize()` strips diacritics, `similarity()` uses Jaccard on tokens, `contains()` checks substring. Never compare Hebrew strings directly with `==` or `.lower()`.

**Lenient column loading** — `excel_loaders.py` matches column names against multiple Hebrew/English variants; do not assume fixed Excel column names.

**Master parquet schema** — `master_full.parquet` contains the same columns as `detail_df` from `pipeline.run_month_pipeline()`, plus a `month` (MM-YYYY string) column. Key columns: `employee_id, employee_name, client, site, days, total_hours, billable_hours, billing_amount, cost, profit, margin_pct, month`.

**AI integration** — `ai_tools.py` wraps the Anthropic SDK. Requires `ANTHROPIC_API_KEY` in the environment. Used for the optional "Ask AI" chat panel in `app.py`.
