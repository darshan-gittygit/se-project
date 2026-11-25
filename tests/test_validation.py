"""Fix 2: every LLM output is schema-validated, retried once, then a clean 502. Offline."""
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.compliance_agent as comp_mod
import app.agents.requirement_agent as req_mod
import app.agents.sdlc_agent as sdlc_mod
from app.main import app
from app.schemas import (
    ComplianceOutput, Justification, LLMOutputError, RequirementsOutput, generate_validated,
)

client = TestClient(app)

GOOD_REQS = {
    "domain_inferred": "Payroll",
    "assumptions": [],
    "functional_requirements": [{"id": "FR-1", "requirement": "Calculate salaries.", "priority": "High"}],
    "non_functional_requirements": [{"id": "NFR-1", "requirement": "Keep an audit trail.", "priority": "Low"}],
}
BODY = {"domain": "Payroll",
        "functional_requirements": GOOD_REQS["functional_requirements"],
        "non_functional_requirements": GOOD_REQS["non_functional_requirements"]}
GOOD_JUSTIFICATION = {
    "requirement_characteristics": "Stable.", "project_characteristics": "Critical.",
    "engineering_justification": "Fits NFR-1.", "key_tradeoffs": "Rigid.",
}
TRAITS = {k: {"score": 3, "evidence": "e", "reason": "r"} for k in sdlc_mod.TRAIT_KEYS}


def scripted(*replies):
    """Fake LLM that returns the given replies in order and records prompts."""
    calls = []

    def fake(prompt, temperature=0.3):
        calls.append(prompt)
        r = replies[min(len(calls), len(replies)) - 1]
        return r if isinstance(r, str) else json.dumps(r)
    return fake, calls


# --- generate_validated ----------------------------------------------------

def test_valid_first_answer_is_not_retried():
    fake, calls = scripted(GOOD_REQS)
    out = generate_validated("P", RequirementsOutput, generate=fake, temperature=0)
    assert out.domain_inferred == "Payroll" and len(calls) == 1


def test_invalid_then_valid_retries_once_with_the_error():
    bad = dict(GOOD_REQS, functional_requirements=[dict(GOOD_REQS["functional_requirements"][0], priority="Critical")])
    fake, calls = scripted(bad, GOOD_REQS)
    generate_validated("P", RequirementsOutput, generate=fake, temperature=0)
    assert len(calls) == 2
    assert calls[1].startswith("P") and "rejected" in calls[1] and "priority" in calls[1]


def test_non_json_then_valid_succeeds():
    fake, calls = scripted("Sure! Here you go.", GOOD_REQS)
    generate_validated("P", RequirementsOutput, generate=fake, temperature=0)
    assert len(calls) == 2 and "not valid JSON" in calls[1]


def test_two_invalid_answers_raise():
    fake, calls = scripted("nope", "still nope")
    with pytest.raises(LLMOutputError, match="after 2 attempts"):
        generate_validated("P", RequirementsOutput, generate=fake, temperature=0)
    assert len(calls) == 2


# --- requirements schema ---------------------------------------------------

def _req_variant(**changes):
    return dict(GOOD_REQS, **changes)


@pytest.mark.parametrize("bad", [
    _req_variant(functional_requirements=[]),
    _req_variant(non_functional_requirements=None),
    _req_variant(domain_inferred="  "),
    _req_variant(functional_requirements=[{"id": "NFR-9", "requirement": "x", "priority": "High"}]),
    _req_variant(functional_requirements=[{"id": "FR-1", "requirement": "x", "priority": "High"},
                                          {"id": "FR-1", "requirement": "y", "priority": "Low"}]),
    _req_variant(functional_requirements=[{"id": "FR-1", "requirement": "", "priority": "High"}]),
    _req_variant(functional_requirements=[{"id": "FR-1", "requirement": "x", "priority": "Urgent"}]),
])
def test_requirement_schema_rejects(bad):
    with pytest.raises(ValueError):
        RequirementsOutput.model_validate(bad)


def test_priority_case_is_normalised_and_extra_keys_are_dropped():
    data = _req_variant(extra="ignored", functional_requirements=[
        {"id": "FR-1", "requirement": "x", "priority": "high", "rationale": "dropped"}])
    out = RequirementsOutput.model_validate(data).model_dump()
    assert out["functional_requirements"][0] == {"id": "FR-1", "requirement": "x", "priority": "High"}
    assert set(out) == {"domain_inferred", "assumptions", "functional_requirements", "non_functional_requirements"}


def test_requirements_endpoint_returns_502_after_two_bad_answers(monkeypatch):
    fake, calls = scripted({"domain_inferred": "X"})
    monkeypatch.setattr(req_mod, "generate_text", fake)
    r = client.post("/agents/requirements", json={"scenario": "A payroll system for a company"})
    assert r.status_code == 502 and len(calls) == 2
    assert r.json()["detail"].startswith("Agent 1 failed: model output failed validation")


def test_requirements_endpoint_recovers_on_retry(monkeypatch):
    fake, calls = scripted("garbage", GOOD_REQS)
    monkeypatch.setattr(req_mod, "generate_text", fake)
    r = client.post("/agents/requirements", json={"scenario": "A payroll system for a company"})
    assert r.status_code == 200 and len(calls) == 2
    assert set(r.json()) == {"domain_inferred", "assumptions", "functional_requirements",
                             "non_functional_requirements", "meta"}


# --- compliance schema -----------------------------------------------------

CTX = {"requirement_ids": {"FR-1", "NFR-1"}, "kb_sources": {"kyc_aml", "payment_security"}}
GOOD_COMPLIANCE = {"risk_level": "High", "summary": "s", "considerations": [
    {"related_requirement_ids": ["FR-1"], "note": "n", "source": "kyc_aml"}]}


@pytest.mark.parametrize("bad", [
    dict(GOOD_COMPLIANCE, risk_level="Extreme"),
    dict(GOOD_COMPLIANCE, summary=""),
    dict(GOOD_COMPLIANCE, considerations=[{"related_requirement_ids": ["FR-99"], "note": "n", "source": "kyc_aml"}]),
    dict(GOOD_COMPLIANCE, considerations=[{"related_requirement_ids": [], "note": "n", "source": "kyc_aml"}]),
    dict(GOOD_COMPLIANCE, considerations=[{"related_requirement_ids": ["FR-1"], "note": "n", "source": "GDPR Article 17"}]),
])
def test_compliance_schema_rejects(bad):
    with pytest.raises(ValueError):
        ComplianceOutput.model_validate(bad, context=CTX)


def test_compliance_endpoint_fabricated_source_is_retried_then_502(monkeypatch):
    fabricated = dict(GOOD_COMPLIANCE, considerations=[
        {"related_requirement_ids": ["FR-1"], "note": "n", "source": "GDPR Article 17"}])
    fake, calls = scripted(fabricated)
    monkeypatch.setattr(comp_mod, "generate_text", fake)
    monkeypatch.setattr(comp_mod, "retrieve_many", lambda q, top_k=3: [[] for _ in q])
    r = client.post("/agents/compliance", json=BODY)
    assert r.status_code == 502 and len(calls) == 2
    assert "GDPR Article 17" in r.json()["detail"]


# --- justification schema --------------------------------------------------

def test_justification_rejects_missing_section_and_unknown_ids():
    ctx = {"requirement_ids": {"FR-1", "NFR-1"}}
    with pytest.raises(ValueError):
        Justification.model_validate({k: v for k, v in GOOD_JUSTIFICATION.items() if k != "key_tradeoffs"}, context=ctx)
    with pytest.raises(ValueError, match="FR-99"):
        Justification.model_validate(dict(GOOD_JUSTIFICATION, key_tradeoffs="See FR-99."), context=ctx)
    Justification.model_validate(GOOD_JUSTIFICATION, context=ctx)


def test_sdlc_endpoint_retries_bad_justification(monkeypatch):
    replies = iter([json.dumps(TRAITS), json.dumps(dict(GOOD_JUSTIFICATION, key_tradeoffs="FR-42 hurts.")),
                    json.dumps(GOOD_JUSTIFICATION)])
    monkeypatch.setattr(sdlc_mod, "generate_text", lambda p, temperature=0.3: next(replies))
    r = client.post("/agents/sdlc", json=BODY)
    assert r.status_code == 200
    assert r.json()["justification"]["key_tradeoffs"] == "Rigid."
