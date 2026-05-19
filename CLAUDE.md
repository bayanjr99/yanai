# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

```
billing_system/
├── core/
│   ├── andromeda_loader.py ← load Andromeda Excel hours (per employee × client × site)
│   ├── pdf_parser.py       ← Andromeda PDF → daily rows (includes break_source column)
│   ├── cost_analysis.py    ← load hours/costs, merge, levy proration, OT columns
│   ├── preprocessor.py     ← one-shot build: all months → output/cache/processed_data.parquet
│   ├── standards_loader.py ← read תקן.xlsx → billing rules (do not modify)
│   ├── standards_v2.py     ← apply_standards(): billing_kind + expected_billing per row
│   ├── income_loader.py    ← load income.xlsx files → real billing per (month, client)
│   ├── client_mapping.py   ← map accounting names → pipeline names (do not modify)
│   ├── audit_clients.py    ← diagnostic: which clients/sites are missing from תקן.xlsx
│   ├── excel_loaders.py    ← load agreements + costs Excel files
│   ├── matcher.py          ← resolve client & agreement per row
│   ├── billing_engine.py   ← apply agreement → BillingResult (pre-aggregated Excel path)
│   ├── rules_engine.py     ← apply agreement → daily result (PDF path, per-day daily_min)
│   ├── validation.py       ← PDF validation + billing sanity checks
│   └── analytics.py        ← KPIs, dashboard_table, insights_engine
├── utils/
│   └── hebrew.py           ← Hebrew normalization shared across core/
├── data/
│   ├── תקן.xlsx            ← billing standards per client/site (58 rows)
│   └── MM-YYYY/            ← one folder per month
│       ├── employeeHoursAndromeda_*.xlsx ← PRIMARY hours source (per emp × client × site)
│       ├── hours1.pdf / hours.xls        ← fallback hours (01-2025, 02-2025 only)
│       ├── costs.pdf                     ← employer costs for this month
│       └── income.xlsx                   ← real billing from accounting system
├── output/cache/
│   ├── processed_data.parquet  ← merged dataset, all months (auto-generated)
│   ├── warnings.parquet        ← data-quality warnings
│   ├── income.parquet          ← real billing aggregated by (month, client)
│   └── build_meta.json         ← build statistics and per-month log
├── tests/
│   ├── test_andromeda_split.py   ← Andromeda loader + cost allocation tests
│   ├── test_client_site_split.py ← (employee × client × site) key integrity tests
│   ├── test_standards_v2.py      ← billing_kind formula tests (9 kinds)
│   └── test_fixes.py             ← regression tests for individual bug fixes
├── app_gpt_dashboard.py   ← PRIMARY BI dashboard (port 8514, Hebrew, 8 tabs)
├── cloud_app.py           ← Cloud/production wrapper with bcrypt login (Render entry point)
├── start.sh               ← Render deployment script (runs cloud_app.py)
├── pipeline.py            ← orchestration + build_master_full() → output/master/
├── ai_tools.py            ← simple Claude wrapper: ask_ai_about_report(df, q) — in-memory Q&A
├── api.py                 ← FastAPI backend (optional, port 8000); exposes /data, /ask endpoints
├── run_cost_analysis.py   ← CLI runner for per-month cost analysis (generates xlsx reports)
├── run_build.py           ← CLI shortcut: runs build_and_save() + optional month range
├── run_ai.py              ← CLI: asks ai_insights questions against master_full.xlsx
├── demo_data.py           ← generates synthetic demo data for testing/presentation
├── update_presentation.py ← updates PPTX slide 18 with billing data; use --src / --dest args
├── requirements.txt
└── runtime.txt            ← Python version pin for Render (e.g. python-3.11.x)
```

## Running the system

```bash
# Primary BI dashboard
streamlit run app_gpt_dashboard.py --server.port 8514

# Cloud/production version (requires .streamlit/secrets.toml with [users] bcrypt hashes)
streamlit run cloud_app.py

# Rebuild parquet cache after adding new month data
python -c "from core.preprocessor import build_and_save; build_and_save()"

# Generate a bcrypt hash for a new user password (paste result into secrets.toml)
python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"
```

## Architecture & data flow

The pipeline is a linear chain. Each stage consumes the output of the previous one:

1. **`andromeda_loader.py`** — Loads the Andromeda Excel hours file (`employeeHoursAndromeda_*.xlsx`). Granularity: **one row per (employee × client × site)**. Employees who worked at multiple (client, site) combinations appear in multiple rows with correct hour splits. Months 01-2025 and 02-2025 fall back to `hours1.pdf` (legacy PDF loader).

2. **`cost_analysis.merge_and_allocate()`** — Joins hours with employer costs. Allocates `employer_cost` proportionally by hours: `allocated_cost = (site_hours / emp_total_hours) × employer_cost`. Adjusts levy (`אגרות`) proportionally to days worked. Key output columns:
   - `cost` = `allocated_cost` — correctly split per (employee × client × site). **Always sum `cost`, never `employer_cost`** (employer_cost is the same value repeated across all site rows for the same employee).
   - `employer_cost` — full monthly cost for the employee (use `.first()` for per-employee display, never `.sum()` across sites).
   - `cost_per_hour` — employee's average cost per hour (same across all their rows).

3. **`preprocessor.build_and_save()`** — Orchestrates all 15 months, fills client/site from costs files, builds country map from Excel files, applies standards, loads income data, and saves to `output/cache/processed_data.parquet`. Run after adding new month data.

4. **`standards_v2.apply_standards()`** — Matches every (client, site) row to a rule in `תקן.xlsx` and computes billing fields. Uses `standards_loader.py` (read-only). Matching: exact → fuzzy normalize → wildcard "כל האתרים". Key output columns: `billing_kind`, `hourly_rate`, `ot_hourly_rate`, `std_hours_month`, `daily_min_hours`, `expected_billing`, `shortage_hours`.

5. **`income_loader.load_income_files()`** — Reads `income.xlsx` files from all month folders. Classifies each line item (daily_hours, hourly_hours, credit, housing, etc.) and aggregates to `(month, client_full)`. Saves `output/cache/income.parquet`.

6. **`app_gpt_dashboard.py`** — Primary Streamlit BI dashboard (port 8514). No sidebar. Eight tabs:
   - **📊 סקירה** — KPI strip, focus line, 4 analysis blocks, 6-month trend chart
   - **📈 גרפים** — Hebrew-question charts (cost by client, OT breakdown, trend, etc.)
   - **📋 טבלאות** — Sub-tabs: לקוח / עובד / אתר / מדינה / חודש
   - **💡 תובנות** — Top problems, anomalous clients/employees, action recommendations
   - **⚙️ סימולציה** — OT sliders + CPH slider → cost saving vs revenue loss (uses actual rates from תקן.xlsx)
   - **🚨 התראות** — Data-quality warnings from `output/cache/warnings.parquet`
   - **🧾 השוואת חיוב** — Expected billing (from standards) vs actual billing (from income)
   - **📑 דוח חיוב** — Billing report export

7. **`cloud_app.py`** — Production/cloud wrapper. Adds bcrypt login (`[users]` section in `.streamlit/secrets.toml` stores password hashes). Rate-limits failed logins (5 attempts, 1.5s delay). After auth passes, loads `app_gpt_dashboard.py` logic.

8. **`core/audit_clients.py`** — Diagnostic tool. Run `python -m core.audit_clients` to see which clients/sites in the pipeline are missing from `תקן.xlsx`, with hours/cost breakdown. No side effects.

## Key conventions

**Hebrew-aware matching** — All string comparisons go through `utils/hebrew.py`: `normalize()` strips diacritics, `similarity()` uses the **overlap coefficient** (|A∩B|/max(|A|,|B|), not Jaccard), `contains()` checks substring. Never compare Hebrew strings directly with `==` or `.lower()`.

**Lenient column loading** — `excel_loaders.py` matches column names against multiple Hebrew/English variants; do not assume fixed Excel column names.

**Cost columns — critical distinction:**
- `cost` = `allocated_cost` — the fraction of `employer_cost` attributed to this (employee × client × site) row. Safe to sum across any dimension.
- `employer_cost` — the employee's full monthly cost, **identical across all their rows for that month**. Summing it over multiple sites double-counts. Use `.first()` for per-employee display.
- `cost_per_hour` — employee's average cost per hour (same across all rows). Not a per-site rate.

**Granularity** — The canonical unit is **(employee × client × site × month)**. An employee working at 5 sites appears in 5 rows with proportional `cost` per row. The `billing_kind` and `expected_billing` are per (client, site) from `תקן.xlsx`.

**`תקן.xlsx` and `client_mapping.py`** — Do not modify `standards_loader.py` or `client_mapping.py` directly. To add a new client, add a row to `data/תקן.xlsx` and run `build_and_save()`. Run `python -m core.audit_clients` to diagnose missing clients.

**Parquet freshness** — If `output/cache/processed_data.parquet` predates a source file change, the dashboard shows a warning. Run `build_and_save()` to refresh.

**AI integration** — `ai_tools.py` wraps the Anthropic SDK. Requires `ANTHROPIC_API_KEY` in the environment (loaded from `.env`; never committed).

## Recent Fixes (2026)

### Employee Count Bug (March 2026)

Fixed critical bug in `core/andromeda_loader.py` that caused employees to disappear from dashboard counts.

- **Issue**: Employees with only vacation/sick hours (no regular work hours) were filtered out
- **Impact**: 25 employee-months missing across 13 months of data
- **Root causes**:
  1. Filter only checked `total_hours > 0 OR work_days > 0` — ignored absence-only rows
  2. `_find_col()` partial-match logic incorrectly mapped `total_reportable_hours` column
- **Fix**:
  1. Expanded filter to check all absence fields (vacation, sick\_paid, sick\_unpaid, holiday, rain\_paid, rain\_unpaid, work\_injury, total\_reportable\_hours)
  2. Updated `_find_col()` to prefer exact matches over partial matches
- **Result**: March 2026 now shows correct **232 employees** instead of 225
- **Regression test**: `tests/test_andromeda_filter.py` + `test_andromeda_employee_count_march_2026` in `tests/test_fixes.py`
