"""
Append-only, tamper-evident audit log (problem statement section 20: "All
prompts, retrieved evidence, agent decisions, user changes, approvals, and
generated artefacts should be recorded"; section 17: audit logging, model
and knowledge-base versioning).

Every event stores a timestamp, role, action, optional run id / stage /
model, the knowledge-base version and app version, and a JSON details
object. Prompts are never stored in clear: only their SHA-256 hash and
length (sha256_text), so sensitive input does not end up in the log.

Tamper evidence: row_hash = SHA-256(prev_hash + canonical JSON of the row),
and prev_hash is the previous row's row_hash (64 zeros for the first row).
Changing, deleting or reordering any row breaks the chain from that row on,
which verify_chain() reports. The database also refuses UPDATE and DELETE on
this table (app/db.py triggers).
"""
import hashlib
import json
from datetime import datetime, timezone

from app import config
from app.db import connect
from app.rag.kb_metadata import kb_version

GENESIS = "0" * 64


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(row: dict) -> str:
    fields = {k: row[k] for k in ("ts", "run_id", "role", "action", "stage", "model", "kb_version",
                                  "app_version", "details_json")}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def row_hash(prev_hash: str, row: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(row)).encode("utf-8")).hexdigest()


def append_event(action: str, role: str | None, run_id: str | None = None, stage: str | None = None,
                 model: str | None = None, details: dict | None = None) -> dict:
    row = {"ts": now(), "run_id": run_id, "role": role or "unspecified", "action": action, "stage": stage,
           "model": model, "kb_version": kb_version(), "app_version": config.APP_VERSION,
           "details_json": json.dumps(details or {}, sort_keys=True, ensure_ascii=False, default=str)}
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # serialise appends so the chain cannot fork
        last = conn.execute("SELECT row_hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        row["prev_hash"] = last["row_hash"] if last else GENESIS
        row["row_hash"] = row_hash(row["prev_hash"], row)
        cur = conn.execute(
            "INSERT INTO audit_events (ts, run_id, role, action, stage, model, kb_version, app_version, "
            "details_json, prev_hash, row_hash) VALUES (:ts, :run_id, :role, :action, :stage, :model, "
            ":kb_version, :app_version, :details_json, :prev_hash, :row_hash)", row)
        row["seq"] = cur.lastrowid
    return row


def verify_chain() -> dict:
    """Recomputes every hash in order. ok=False names the first row that does not match."""
    expected_prev, checked = GENESIS, 0
    with connect() as conn:
        for r in conn.execute("SELECT * FROM audit_events ORDER BY seq"):
            row = dict(r)
            if row["prev_hash"] != expected_prev:
                return {"ok": False, "checked": checked, "first_bad_seq": row["seq"],
                        "reason": "prev_hash does not match the previous row (a row was removed, inserted or reordered)"}
            if row_hash(row["prev_hash"], row) != row["row_hash"]:
                return {"ok": False, "checked": checked, "first_bad_seq": row["seq"],
                        "reason": "row content does not match its hash (the row was changed)"}
            expected_prev, checked = row["row_hash"], checked + 1
    return {"ok": True, "checked": checked, "first_bad_seq": None, "reason": None}


def query_events(run_id: str | None = None, role: str | None = None, action: str | None = None,
                 limit: int = 500) -> list[dict]:
    clauses, params = [], []
    for col, val in (("run_id", run_id), ("role", role), ("action", action)):
        if val:
            clauses.append(f"{col} = ?")
            params.append(val)
    sql = "SELECT * FROM audit_events" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY seq LIMIT ?"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, (*params, limit))]
    for r in rows:
        r["details"] = json.loads(r.pop("details_json"))
    return rows
