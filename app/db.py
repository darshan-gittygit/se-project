"""
SQLite storage (standard library sqlite3) in data/app.db, created on first use.

Tables:
- runs            one row per /agents/pipeline run: the full result JSON as
                  generated (never edited), the baseline state
- approval_items  every version of every approvable item (requirement,
                  compliance interpretation, SDLC selection) and its status
- decisions       every approval action (accept, reject, modify, regenerate,
                  freeze): append-only
- audit_events    the hash-chained audit log (app/audit.py): append-only

"Append-only" is enforced in the database: triggers abort any UPDATE or
DELETE on decisions and audit_events, so the application cannot rewrite them.
Someone with direct write access to the file can still drop the triggers;
the audit hash chain is what makes such tampering detectable.

A new connection is opened per operation (FastAPI runs sync endpoints on a
thread pool), so there is no shared connection state.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    role TEXT NOT NULL,
    app_version TEXT NOT NULL,
    kb_version TEXT NOT NULL,
    scenario TEXT NOT NULL,
    result_json TEXT NOT NULL,
    baseline_status TEXT NOT NULL DEFAULT 'open',
    baseline_frozen_at TEXT,
    baseline_frozen_by TEXT
);
CREATE TABLE IF NOT EXISTS approval_items (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    mandatory INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    regenerations INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, item_type, item_id, version)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    role TEXT NOT NULL,
    comment TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    stage TEXT,
    model TEXT,
    kb_version TEXT NOT NULL,
    app_version TEXT NOT NULL,
    details_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_by_run ON audit_events(run_id);
CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
    BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
    BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events are append-only'); END;
"""

_initialised: set[str] = set()


def db_path() -> Path:
    return config.DB_PATH


@contextmanager
def connect():
    """A connection with the schema in place; commits on success, rolls back on error."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if str(path) not in _initialised:
            conn.executescript(SCHEMA)
            _initialised.add(str(path))
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
