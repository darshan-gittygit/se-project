"""Phase 3 step 4: project-specific SDLC workflow (section 15). Offline."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.workflow_agent as wa
from app.agents.sdlc_rules import SDLC_PROFILES
from app.agents.sdlc_workflows import BASE_WORKFLOWS, WORKFLOW_FIELDS
from app.main import app

client = TestClient(app)
REQS = [{"id": "FR-1", "requirement": "Process loan disbursement transactions.", "categories": ["Functional"]},
        {"id": "NFR-1", "requirement": "Encrypt applicant data.", "categories": ["Security"]}]


def scripted(*replies):
    calls = []

    def fake(prompt, temperature=0.3):
        calls.append(prompt)
        r = replies[min(len(calls), len(replies)) - 1]
        return r if isinstance(r, str) else json.dumps(r)
    return fake, calls


def add(phase, field, item, ids=("FR-1",), reason="needed here"):
    return {"phase": phase, "field": field, "item": item, "requirement_ids": list(ids), "reason": reason}


GOOD = {"additions": [
    add("System and architecture design", "security_activities", "Threat modelling of the disbursement flow"),
    add("System and acceptance testing", "testing_requirements", "Transaction-integrity tests for disbursements"),
    add("Deployment", "compliance_checkpoints", "Compliance approval of the KYC process before go-live", ["NFR-1"]),
]}


def run(monkeypatch, *replies, model="V-Model", **kw):
    fake, calls = scripted(*replies)
    monkeypatch.setattr(wa, "generate_text", fake)
    return wa.generate_workflow(model, "Loans", REQS, **kw), calls


# --- base data ------------------------------------------------------------------

def test_every_model_has_a_base_workflow_with_all_section_15_fields():
    assert set(BASE_WORKFLOWS) == set(SDLC_PROFILES)
    assert WORKFLOW_FIELDS == ("activities", "responsible_roles", "deliverables", "security_activities",
                               "testing_requirements", "compliance_checkpoints", "approval_gates",
                               "entry_criteria", "exit_criteria", "traceability_requirements")
    for model, phases in BASE_WORKFLOWS.items():
        assert phases, model
        names = [p["name"] for p in phases]
        assert len(names) == len(set(names)), model
        for p in phases:
            assert set(p) == {"name", *WORKFLOW_FIELDS}, (model, p["name"])
            assert p["activities"] and p["responsible_roles"] and p["entry_criteria"] and p["exit_criteria"]
        # every workflow ends with a human production-readiness gate somewhere
        assert any("Production readiness" in g for p in phases for g in p["approval_gates"]), model


# --- merging ----------------------------------------------------------------------

def test_additions_are_merged_and_base_is_kept_in_order(monkeypatch):
    wf, _ = run(monkeypatch, GOOD)
    assert [p["name"] for p in wf["phases"]] == [p["name"] for p in BASE_WORKFLOWS["V-Model"]]
    assert [p["order"] for p in wf["phases"]] == list(range(1, len(wf["phases"]) + 1))
    design = next(p for p in wf["phases"] if p["name"] == "System and architecture design")
    assert design["security_activities"][0] == {"text": "Threat assessment of the architecture", "source": "base",
                                                "requirement_ids": []}
    assert design["security_activities"][-1] == {"text": "Threat modelling of the disbursement flow",
                                                 "source": "scenario", "requirement_ids": ["FR-1"], "reason": "needed here"}
    assert wf["added_items"] == 3 and wf["requirements_linked"] == ["FR-1", "NFR-1"]
    base_count = sum(len(p[f]) for p in BASE_WORKFLOWS["V-Model"] for f in WORKFLOW_FIELDS)
    assert sum(len(p[f]) for p in wf["phases"] for f in WORKFLOW_FIELDS) == base_count + 3


def test_no_additions_returns_the_base_workflow(monkeypatch):
    wf, _ = run(monkeypatch, {"additions": []}, model="Agile")
    assert wf["added_items"] == 0
    assert all(i["source"] == "base" for p in wf["phases"] for f in WORKFLOW_FIELDS for i in p[f])


def test_base_data_is_never_mutated(monkeypatch):
    before = copy.deepcopy(BASE_WORKFLOWS)
    run(monkeypatch, GOOD)
    assert BASE_WORKFLOWS == before


def test_prompt_carries_base_compliance_and_risks(monkeypatch):
    mapping = {"requirements": [{"requirement_id": "FR-1", "mappings": [
        {"status": "gap", "source": "kyc_aml", "compliance_gap": "No address check.",
         "checkpoints": ["Compliance sign-off of KYC rules"], "retention_and_reporting": ["Keep records"]}]}]}
    register = {"risks": [{"id": "RISK-1", "level": "High", "category": "Security", "description": "Data exposure",
                           "linked_requirement_ids": ["NFR-1"]}]}
    _, calls = run(monkeypatch, GOOD, compliance_mapping=mapping, risk_register=register)
    p = calls[0]
    assert "1. Requirements analysis" in p and "approval_gates: Requirements baseline approved" in p
    assert "FR-1: gap against kyc_aml: No address check." in p and "FR-1: checkpoint: Compliance sign-off of KYC rules" in p
    assert "RISK-1 (High, Security): Data exposure [NFR-1]" in p


# --- validation: additions only ------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"additions": [add("Design and threat modelling", "activities", "x")]},                 # renamed / invented phase
    {"additions": [add("Deployment", "rollback_plan", "x")]},                               # unknown field
    {"additions": [add("Deployment", "activities", "x", ["FR-9"])]},                        # unknown requirement
    {"additions": [add("Deployment", "activities", "")]},                                   # empty item
    {"additions": [add("Deployment", "activities", "x", reason=" ")]},                      # no reason
    {"additions": [add("Deployment", "activities", "Run X")] * 2},                          # repeated
    {"additions": [add("Deployment", "activities", "release to PRODUCTION.")]},             # repeats a base item
    {"additions": [add("Deployment", "activities", f"Item {i}") for i in range(26)]},       # too many
])
def test_invalid_additions_are_retried_then_rejected(monkeypatch, bad):
    fake, calls = scripted(bad)
    monkeypatch.setattr(wa, "generate_text", fake)
    with pytest.raises(Exception, match="failed validation after 2 attempts"):
        wa.generate_workflow("V-Model", "Loans", REQS)
    assert len(calls) == 2


def test_invalid_then_valid_recovers(monkeypatch):
    wf, calls = run(monkeypatch, {"additions": [add("Threat modelling", "activities", "x")]}, GOOD)
    assert wf["added_items"] == 3 and len(calls) == 2 and "not a base phase" in calls[1]


def test_pipeline_workflow_step_retries_then_502(monkeypatch):
    import tests.test_pipeline_phase1 as p1
    import app.agents.risk_agent as ra
    seen = []
    fake = p1.router(seen=seen)
    for mod in (p1.req_mod, p1.comp_mod, p1.quality_mod, p1.sdlc_mod, ra):
        monkeypatch.setattr(mod, "generate_text", fake)
    monkeypatch.setattr(wa, "generate_text", lambda p, temperature=0.3: seen.append(p) or json.dumps(
        {"additions": [add("Not a phase", "activities", "x")]}))
    monkeypatch.setattr(p1.comp_mod, "retrieve_many", lambda qs, top_k=3: [[] for _ in qs])
    r = client.post("/agents/pipeline", json={"scenario": p1.SCENARIO})
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Workflow generator failed: model output failed validation")
    assert sum("software development workflow to one project" in p for p in seen) == 2


def test_pipeline_returns_the_workflow_for_the_recommended_model(monkeypatch):
    import tests.test_pipeline_phase1 as p1
    import app.agents.risk_agent as ra
    fake = p1.router()
    for mod in (p1.req_mod, p1.comp_mod, p1.quality_mod, p1.sdlc_mod, ra, wa):
        monkeypatch.setattr(mod, "generate_text", fake)
    monkeypatch.setattr(p1.comp_mod, "retrieve_many", lambda qs, top_k=3: [[] for _ in qs])
    d = client.post("/agents/pipeline", json={"scenario": p1.SCENARIO}).json()
    assert d["sdlc_workflow"]["model"] == d["sdlc_recommendation"]["recommended_model"]
    assert [p["name"] for p in d["sdlc_workflow"]["phases"]] == \
        [p["name"] for p in BASE_WORKFLOWS[d["sdlc_recommendation"]["recommended_model"]]]
    assert len(d["sdlc_recommendation"]["scored_models"]) == 7
