"""Phase 5A: append-only, hash-chained audit log and GET /audit. Offline."""
import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.agents.artefact_agent as aa
import app.agents.risk_agent as ra
import app.agents.workflow_agent as wa
import tests.test_pipeline_phase1 as p1
from app import audit, config
from app.db import connect
from app.main import app

client = TestClient(app)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """A private database so chain tests see only their own rows."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "audit.db")
    return tmp_path / "audit.db"


def _raw(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# --- the chain ----------------------------------------------------------------------

def test_chain_links_each_row_to_the_previous_one(fresh_db):
    a = audit.append_event("first", "auditor", details={"n": 1})
    b = audit.append_event("second", "product owner", run_id="r1", stage="x", model="m", details={"n": 2})
    assert a["prev_hash"] == audit.GENESIS and b["prev_hash"] == a["row_hash"]
    assert b["row_hash"] == audit.row_hash(a["row_hash"], b)
    assert audit.verify_chain() == {"ok": True, "checked": 2, "first_bad_seq": None, "reason": None}


def test_every_event_carries_versions_and_a_timestamp(fresh_db):
    e = audit.append_event("x", None)
    assert e["role"] == "unspecified" and e["app_version"] == config.APP_VERSION
    assert e["kb_version"].startswith("kb-") and e["ts"].endswith("+00:00")


def test_database_refuses_update_and_delete(fresh_db):
    audit.append_event("x", "auditor")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with connect() as conn:
            conn.execute("UPDATE audit_events SET action = 'y'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with connect() as conn:
            conn.execute("DELETE FROM audit_events")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with connect() as conn:
            conn.execute("INSERT INTO decisions (run_id, item_type, item_id, version, action, role, comment, created_at) "
                         "VALUES ('r', 'requirement', 'FR-1', 1, 'accept', 'x', '', 't')")
            conn.execute("DELETE FROM decisions")


def _tamper(path, sql, *args):
    """What an attacker with direct file access could do: drop the guards, then edit."""
    conn = _raw(path)
    conn.executescript("DROP TRIGGER audit_no_update; DROP TRIGGER audit_no_delete;")
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def test_verify_detects_a_changed_row(fresh_db):
    for i in range(4):
        audit.append_event("approval_accept", "product owner", run_id="r1", details={"i": i})
    _tamper(fresh_db, "UPDATE audit_events SET role = 'auditor' WHERE seq = 3")
    out = audit.verify_chain()
    assert out["ok"] is False and out["first_bad_seq"] == 3 and out["checked"] == 2
    assert "changed" in out["reason"]


def test_verify_detects_a_changed_details_payload(fresh_db):
    audit.append_event("approval_reject", "product owner", details={"comment": "too vague"})
    audit.append_event("x", "auditor")
    _tamper(fresh_db, "UPDATE audit_events SET details_json = ? WHERE seq = 1", json.dumps({"comment": "fine"}))
    assert audit.verify_chain()["first_bad_seq"] == 1


def test_verify_detects_a_deleted_row(fresh_db):
    for i in range(3):
        audit.append_event("x", "auditor", details={"i": i})
    _tamper(fresh_db, "DELETE FROM audit_events WHERE seq = 2")
    out = audit.verify_chain()
    assert out["ok"] is False and out["first_bad_seq"] == 3 and "removed" in out["reason"]


def test_rehashing_one_row_still_breaks_the_next_link(fresh_db):
    for i in range(3):
        audit.append_event("x", "auditor", details={"i": i})
    conn = _raw(fresh_db)
    row = dict(conn.execute("SELECT * FROM audit_events WHERE seq = 2").fetchone())
    row["role"] = "product owner"
    forged = audit.row_hash(row["prev_hash"], row)
    conn.close()
    _tamper(fresh_db, "UPDATE audit_events SET role = 'product owner', row_hash = ? WHERE seq = 2", forged)
    out = audit.verify_chain()
    assert out["first_bad_seq"] == 3 and "prev_hash" in out["reason"]


# --- what gets recorded --------------------------------------------------------------------

def _run_pipeline(monkeypatch, role="business analyst"):
    prompts = []

    def fake(prompt, temperature=0.3):
        prompts.append(prompt)
        return p1.router()(prompt, temperature)
    for mod in (p1.req_mod, p1.comp_mod, p1.quality_mod, p1.sdlc_mod, ra, wa, aa):
        monkeypatch.setattr(mod, "generate_text", fake)
    monkeypatch.setattr(p1.comp_mod, "retrieve_many", lambda qs, top_k=3: [[
        {"id": "kyc_aml-0", "source": "kyc_aml", "text": "KYC", "distance": 0.40}] for _ in qs])
    d = client.post("/agents/pipeline", headers={"X-Role": role}, json={"scenario": p1.SCENARIO}).json()
    return d, prompts


def test_pipeline_records_every_stage_with_hashed_prompts_models_chunks_and_output(fresh_db, monkeypatch):
    d, prompts = _run_pipeline(monkeypatch)
    events = audit.query_events(run_id=d["run_id"])
    stages = [e["stage"] for e in events if e["action"] == "stage_run"]
    assert stages == ["requirements", "compliance", "quality", "risk_register", "sdlc", "workflow", "artefacts"]
    assert events[0]["action"] == "pipeline_started" and events[-1]["action"] == "pipeline_completed"
    assert all(e["role"] == "business analyst" and e["kb_version"].startswith("kb-") for e in events)
    req = next(e for e in events if e["stage"] == "requirements")
    # mocked above the SDK, so no model call is recorded here; the hash path is tested below
    assert req["details"]["output"]["domain_inferred"] == p1.REQS["domain_inferred"]
    comp = next(e for e in events if e["stage"] == "compliance")
    assert comp["details"]["retrieved_chunk_ids"] == ["kyc_aml-0"]
    kinds = [e["details"]["artefact"] for e in events if e["action"] == "artefact_generated"]
    assert set(kinds) == {"user_stories", "use_cases", "process_workflows", "data_requirements", "interface_requirements",
                          "traceability_matrix", "compliance_control_matrix", "assumptions_register", "open_issues"}
    # prompts are never stored in clear
    blob = json.dumps([e["details"] for e in events])
    assert not any(p[:200] in blob for p in prompts)
    assert audit.verify_chain()["ok"]


def test_model_calls_are_logged_as_prompt_hashes(fresh_db, monkeypatch):
    import types
    import app.gemini_client as gc
    seen = []

    def generate_content(model, contents, config):
        seen.append(contents)
        return types.SimpleNamespace(text=p1.router()(contents))
    monkeypatch.setattr(gc, "_client", types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content)))
    for mod in (p1.req_mod, p1.comp_mod, p1.quality_mod, p1.sdlc_mod, ra, wa, aa):
        monkeypatch.setattr(mod, "generate_text", gc.generate_text)
    monkeypatch.setattr(p1.comp_mod, "retrieve_many", lambda qs, top_k=3: [[] for _ in qs])
    d = client.post("/agents/pipeline", headers={"X-Role": "architect"}, json={"scenario": p1.SCENARIO}).json()
    events = [e for e in audit.query_events(run_id=d["run_id"]) if e["action"] == "stage_run"]
    logged = [c for e in events for c in e["details"]["prompts"]]
    assert len(logged) == len(seen) == 8
    assert [c["prompt_sha256"] for c in logged] == [hashlib.sha256(p.encode()).hexdigest() for p in seen]
    assert all(set(c) == {"model", "prompt_sha256", "prompt_chars"} for c in logged)
    assert all(e["model"] == gc._candidate_models()[0] for e in events)


def test_pipeline_failure_is_recorded(fresh_db, monkeypatch):
    for mod in (p1.req_mod,):
        monkeypatch.setattr(mod, "generate_text", lambda p, temperature=0.3: "not json")
    r = client.post("/agents/pipeline", headers={"X-Role": "product owner"}, json={"scenario": p1.SCENARIO})
    assert r.status_code == 502
    failed = audit.query_events(action="pipeline_failed")
    assert failed and failed[-1]["stage"] == "requirements" and "validation" in failed[-1]["details"]["error"]


def test_user_actions_and_downloads_are_recorded(fresh_db, monkeypatch):
    d, _ = _run_pipeline(monkeypatch)
    rid = d["run_id"]
    client.post(f"/runs/{rid}/approvals", headers={"X-Role": "product owner"},
                json={"item_type": "requirement", "item_id": "FR-1", "action": "reject", "comment": "Too vague"})
    client.post(f"/runs/{rid}/baseline/freeze", headers={"X-Role": "product owner"})
    client.get(f"/agents/srs/{rid}?format=html", headers={"X-Role": "auditor"})
    actions = [(e["action"], e["role"]) for e in audit.query_events(run_id=rid)]
    assert ("approval_reject", "product owner") in actions
    assert ("baseline_freeze_blocked", "product owner") in actions
    assert ("srs_downloaded", "auditor") in actions
    reject = audit.query_events(run_id=rid, action="approval_reject")[0]
    assert reject["details"]["comment"] == "Too vague" and reject["details"]["status_after"] == "rejected"


def test_single_stage_endpoints_are_recorded_too(fresh_db, monkeypatch):
    monkeypatch.setattr(p1.req_mod, "generate_text", p1.router())
    client.post("/agents/requirements", json={"scenario": p1.SCENARIO})
    (e,) = audit.query_events(action="stage_run")
    assert e["stage"] == "requirements" and e["role"] == "unspecified" and e["run_id"] is None


# --- GET /audit ------------------------------------------------------------------------------

def test_audit_endpoint_is_auditor_only_and_filters(fresh_db, monkeypatch):
    d, _ = _run_pipeline(monkeypatch, role="architect")
    assert client.get("/audit").status_code == 403
    assert client.get("/audit", headers={"X-Role": "product owner"}).status_code == 403
    assert client.get("/audit", headers={"X-Role": "compliance officer"}).status_code == 200
    r = client.get(f"/audit?run_id={d['run_id']}&action=stage_run", headers={"X-Role": "auditor"}).json()
    assert len(r["events"]) == 7 and all(e["action"] == "stage_run" for e in r["events"])
    assert r["chain"]["ok"] is True
    by_role = client.get("/audit?role=architect", headers={"X-Role": "auditor"}).json()["events"]
    assert by_role and all(e["role"] == "architect" for e in by_role)
    assert audit.query_events(action="audit_viewed")[-1]["role"] == "auditor"


def test_audit_endpoint_reports_a_broken_chain(fresh_db):
    audit.append_event("x", "auditor")
    audit.append_event("y", "auditor")
    _tamper(fresh_db, "UPDATE audit_events SET action = 'z' WHERE seq = 1")
    chain = client.get("/audit", headers={"X-Role": "auditor"}).json()["chain"]
    assert chain["ok"] is False and chain["first_bad_seq"] == 1


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_there_are_no_write_routes_for_the_audit_log(method):
    assert getattr(client, method)("/audit", headers={"X-Role": "auditor"}).status_code == 405
