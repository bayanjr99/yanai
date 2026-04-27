import os
import sqlite3
from pathlib import Path

# On Render: mount /data disk and set DATA_ROOT=/data → DB lives at /data/users.db
# Locally: DB lives next to db.py (same as before)
_data_root = os.getenv("DATA_ROOT", str(Path(__file__).parent))
DB_PATH    = Path(_data_root) / "users.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS company_users (
        company_id INTEGER NOT NULL REFERENCES companies(id),
        user_id    INTEGER NOT NULL REFERENCES users(id),
        role       TEXT NOT NULL DEFAULT 'member',
        PRIMARY KEY (company_id, user_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id      INTEGER REFERENCES companies(id),
        user_id         INTEGER REFERENCES users(id),
        month           TEXT NOT NULL,
        total_billing   REAL,
        total_cost      REAL,
        total_profit    REAL,
        n_employees     INTEGER,
        n_clients       INTEGER,
        n_issues        INTEGER,
        run_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS uploads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT NOT NULL,
        hours_file TEXT,
        agreements_file TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


def log_run(
    month: str,
    kpis: dict,
    user_id: int | None = None,
    company_id: int | None = None,
) -> None:
    """Record a billing run in the runs table."""
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO runs
               (company_id, user_id, month,
                total_billing, total_cost, total_profit,
                n_employees, n_clients, n_issues)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                company_id, user_id, month,
                kpis.get("total_billing"),
                kpis.get("total_cost"),
                kpis.get("total_profit"),
                kpis.get("active_employees"),
                kpis.get("active_clients"),
                kpis.get("n_issues", 0),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_runs(company_id: int | None = None, limit: int = 50) -> list[dict]:
    """Return recent runs, optionally filtered by company."""
    conn = get_conn()
    try:
        if company_id is not None:
            rows = conn.execute(
                "SELECT * FROM runs WHERE company_id=? ORDER BY run_at DESC LIMIT ?",
                (company_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY run_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()