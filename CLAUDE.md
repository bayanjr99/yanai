# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This directory (`core/`) is a Python package inside the larger `billing_system/` project. The full tree that matters:

```
billing_system/
├── core/               ← you are here
│   ├── pdf_parser.py
│   ├── aggregator.py
│   ├── excel_loaders.py
│   ├── matcher.py
│   ├── billing_engine.py
│   └── report_builder.py
├── utils/
│   └── hebrew.py       ← Hebrew normalization shared across core/
├── app.py              ← Streamlit web UI
├── main.py             ← CLI entry point
├── auth.py / db.py     ← SQLite-backed user sessions
├── ai_tools.py         ← Claude API chat integration
└── run.bat             ← Windows launcher (runs main.py)
```

## Running the system

```bash
# Web UI (from billing_system/)
streamlit run app.py

# CLI (from billing_system/)
python main.py

# Windows shortcut
run.bat
```

There are no test files, linter configs, or build scripts in the current repo.

## Architecture & data flow

The pipeline is a linear chain. Each stage consumes the output of the previous one:

1. **`pdf_parser.py`** — Reads an Andromeda payroll PDF (`pypdf`). Each page = one employee. Extracts **daily rows** using strict regex: each row must have a date, two HH:MM times (start/end), a Hebrew site name, and a decimal `hours_to_pay`. Summary rows (`סה"כ`, `שעות חודשיות`, `שבת ללא דיווח`) are skipped. The parsed sum is validated against the PDF's own monthly total; warnings are logged on mismatch. Output: `employee_id, employee_name, date, site, hours_to_pay`.

2. **`aggregator.py`** — Collapses daily rows into **monthly totals per (employee_id, site)**. Output columns are defined by `MONTHLY_COLS`: `employee_id, employee_name, site, days, total_hours`.

3. **`excel_loaders.py`** — Loads two Excel reference files with lenient column matching (Hebrew + English variant names):
   - **Agreements** → list of dicts keyed by client name, containing billing rules
   - **Employee costs** → dict keyed by employee_id, containing employer cost per site

4. **`matcher.py`** — For each aggregated row, resolves:
   - **Client** via `resolve_client()`: looks up employee_id in the costs dict; when multiple entries exist, picks the best site match using Hebrew-aware fuzzy scoring.
   - **Agreement** via `find_agreement()`: 4-tier priority — exact name → fuzzy name → wildcard → none.

5. **`billing_engine.py`** — Applies one agreement to one (employee × site) row and returns a `BillingResult`. Two billing modes:
   - **hourly**: `billable_hours × rate`; completion fills the gap to `monthly_min` or `days × daily_min`
   - **daily**: `days × rate`

6. **`report_builder.py`** — Writes two timestamped Excel files to `output/`:
   - `final_YYYYMMDD_HHMMSS.xlsx` — Summary (by client), Detail (by employee×site), Profitability sheets
   - `issues_YYYYMMDD_HHMMSS.xlsx` — Data quality problems (missing costs, missing agreements, zero charges)

## Key conventions

**Hebrew-aware matching** — All string comparisons go through `utils/hebrew.py`: `normalize()` strips diacritics (NFKD), `similarity()` uses Jaccard on tokens, `contains()` checks substring. Never compare Hebrew strings directly with `==` or `.lower()`.

**Lenient column loading** — `excel_loaders.py` matches column names against multiple Hebrew/English variants; do not assume Excel columns use fixed names.

**Column contracts** — Each module declares its output schema as a module-level constant (`DAILY_COLS`, `MONTHLY_COLS`). These are the canonical column lists; downstream code relies on them.

**Session isolation** — The Streamlit app gives each browser session a UUID-based directory with `data/` and `output/` subdirs. `main.py` accepts a session directory as its first argument.

**AI integration** — `ai_tools.py` wraps the Anthropic SDK for in-app chat. Requires `ANTHROPIC_API_KEY` in the environment. Currently uses `claude-haiku-4-5-20251001`.
