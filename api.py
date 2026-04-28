"""
api.py — FastAPI backend for the BI analytics system.

Run:
    uvicorn api:app --reload --port 8000

Endpoints:
    GET  /summary          — plain-language business summary
    GET  /top-clients      — top N profitable clients  (?n=5)
    GET  /loss-clients     — all loss-making clients
    GET  /employees        — top N expensive employees (?n=5)
    GET  /monthly          — month-by-month financials
    GET  /problems         — detected business problems
    GET  /export           — full dataset as JSON or CSV (?format=csv)
    POST /ask              — natural-language question answering
"""

from __future__ import annotations

import io

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.ai_insights import (
    load_data,
    get_top_profitable_clients,
    get_loss_clients,
    get_profit_by_month,
    get_top_expensive_employees,
    detect_problems,
    generate_summary_text,
    ask_question,
)

app = FastAPI(
    title="BI Analytics API",
    description="CFO-grade financial analytics for billing data",
    version="1.0.0",
)

# Allow local Power BI / browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _records(df) -> list[dict]:
    """Convert DataFrame to JSON-safe list of dicts."""
    return (
        df.fillna(0)
        .assign(**{
            col: df[col].astype(str)
            for col in df.select_dtypes(include="object").columns
        })
        .to_dict(orient="records")
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/summary")
def summary() -> dict:
    """Plain-language business summary."""
    return {"summary": generate_summary_text()}


@app.get("/top-clients")
def top_clients(n: int = Query(default=5, ge=1, le=50)) -> dict:
    """Top N clients by total profit."""
    df = get_top_profitable_clients(n=n)
    return {"n": n, "clients": _records(df)}


@app.get("/loss-clients")
def loss_clients() -> dict:
    """All clients with negative total profit."""
    df = get_loss_clients()
    return {"count": len(df), "clients": _records(df)}


@app.get("/employees")
def employees(n: int = Query(default=5, ge=1, le=50)) -> dict:
    """Top N employees by employer cost."""
    df = get_top_expensive_employees(n=n)
    return {"n": n, "employees": _records(df)}


@app.get("/monthly")
def monthly() -> dict:
    """Month-by-month revenue, cost, profit, margin, MoM change."""
    df = get_profit_by_month()
    return {"months": len(df), "data": _records(df)}


@app.get("/problems")
def problems() -> dict:
    """Detected business problems across all categories."""
    return detect_problems()


@app.get("/export")
def export(format: str = Query(default="json", pattern="^(json|csv)$")) -> object:
    """
    Full master_full dataset.
    ?format=json  → JSON array (default)
    ?format=csv   → downloadable CSV file
    """
    df = load_data().copy()

    # Ensure clean export: no nulls in key fields
    for col in ("client", "site", "employee_id", "employee_name", "month"):
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)
    for col in df.select_dtypes(include="number").columns:
        df[col] = df[col].fillna(0)

    if format == "csv":
        buf = io.StringIO()
        df.to_csv(buf, index=False, encoding="utf-8-sig")
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=master_full.csv"},
        )

    return {"rows": len(df), "columns": list(df.columns), "data": _records(df)}


class _AskBody(BaseModel):
    question: str


@app.post("/ask")
def ask(body: _AskBody) -> dict:
    """
    Answer a natural-language business question.
    Simple questions are answered with pandas; complex ones call Claude.
    """
    answer = ask_question(body.question)
    return {"question": body.question, "answer": answer}


# ---------------------------------------------------------------------------
# Dev runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
