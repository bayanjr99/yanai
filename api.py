"""
Billing System REST API  (Phase 4 — SaaS Foundation)

Run:
    uvicorn api:app --reload --port 8000

Endpoints:
    POST /api/run              — run the billing pipeline for a month
    GET  /api/results/{month}  — return KPI summary for a month
    GET  /api/runs             — list recent runs
    POST /api/auth/login       — authenticate user
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from fastapi import FastAPI, HTTPException, Depends, status
    from fastapi.security import HTTPBasic, HTTPBasicCredentials
    from pydantic import BaseModel
    import secrets
except ImportError:
    raise ImportError(
        "FastAPI not installed. Run: pip install fastapi uvicorn"
    )

from pipeline import run_month_pipeline, list_available_months, DATA_ROOT
from core.analytics import kpi_summary
from db import get_runs, log_run, init_db
from auth import login_user

init_db()

app  = FastAPI(title="Billing System API", version="1.0")
_sec = HTTPBasic()


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _require_auth(creds: HTTPBasicCredentials = Depends(_sec)) -> dict:
    ok, user = login_user(creds.username, creds.password)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    month:      str             # MM-YYYY
    company_id: Optional[int] = None


class KpiResponse(BaseModel):
    month:          str
    total_billing:  float
    total_cost:     float
    total_profit:   float
    active_clients: int
    active_employees: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"status": "ok", "service": "Billing System API"}


@app.get("/api/months")
def available_months():
    """List all months that have data in data/."""
    return {"months": list_available_months(DATA_ROOT)}


@app.post("/api/run", response_model=KpiResponse)
def run(req: RunRequest, user: dict = Depends(_require_auth)):
    """
    Run the billing pipeline for a given month.
    Saves output to output/{month}/ and records the run in the DB.
    """
    available = list_available_months(DATA_ROOT)
    if req.month not in available:
        raise HTTPException(
            status_code=400,
            detail=f"Month '{req.month}' not found in data/. "
                   f"Available: {available}",
        )

    try:
        result = run_month_pipeline(req.month)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    kpis = kpi_summary(result.detail_df)
    kpis["n_issues"] = len(result.issues_df)

    log_run(
        month      = req.month,
        kpis       = kpis,
        user_id    = user.get("id"),
        company_id = req.company_id,
    )

    return KpiResponse(
        month            = req.month,
        total_billing    = kpis["total_billing"],
        total_cost       = kpis["total_cost"],
        total_profit     = kpis["total_profit"],
        active_clients   = kpis["active_clients"],
        active_employees = kpis["active_employees"],
    )


@app.get("/api/results/{month}", response_model=KpiResponse)
def get_results(month: str, user: dict = Depends(_require_auth)):
    """Return KPI summary for a previously-run month."""
    import json

    kpis_path = os.path.join("output", month, "kpis.json")
    if not os.path.exists(kpis_path):
        raise HTTPException(
            status_code=404,
            detail=f"No results found for month '{month}'. Run it first via POST /api/run.",
        )

    with open(kpis_path) as f:
        kpis = json.load(f)

    return KpiResponse(
        month            = month,
        total_billing    = kpis.get("total_billing",    0),
        total_cost       = kpis.get("total_cost",       0),
        total_profit     = kpis.get("total_profit",     0),
        active_clients   = kpis.get("active_clients",   0),
        active_employees = kpis.get("active_employees", 0),
    )


@app.get("/api/runs")
def list_runs(
    limit: int = 20,
    company_id: Optional[int] = None,
    user: dict = Depends(_require_auth),
):
    """List recent billing runs."""
    return {"runs": get_runs(company_id=company_id, limit=limit)}
