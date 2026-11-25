"""Phase 1 step 4: /agents/pipeline returns the requirement records, attached regulations and quality flags. Offline."""
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.compliance_agent as comp_mod
import app.agents.quality_agent as quality_mod
import app.agents.requirement_agent as req_mod
import app.agents.risk_agent as risk_mod
import app.agents.workflow_agent as workflow_mod
import app.agents.sdlc_agent as sdlc_mod
from app.agents.compliance_agent import attach_regulations
from app.main import app
from tests.helpers import RISK_PROMPT_MARKER, WORKFLOW_PROMPT_MARKER, record, risk_reply

client = TestClient(app)
SCENARIO = "A digital loan origination system that verifies applicant identity documents and explains credit decisions."
REQS = {
    "domain_inferred": "Digital Loan Origination", "assumptions": [],
    "functional_requirements": [
        record("FR-1", "The system shall verify applicant identity documents.", source_text="verifies applicant identity documents", assumptions=[]),
        record("FR-2", "The system shall explain every declined credit decision.", source_text="explains credit decisions", assumptions=[]),
    ],
    "non_functional_requirements": [
        record("NFR-1", "The system shall be fast.", acceptance_criteria=["Feels quick"]),
    ],
}
COMPLIANCE = {"risk_level": "High", "summary": "s", "considerations": [
    {"related_requirement_ids": ["FR-1"], "note": "KYC", "source": "kyc_aml"},
    {"related_requirement_ids": ["FR-1", "NFR-1"], "note": "baseline", "source": "general_security_baseline"},
    {"related_requirement_ids": ["FR-2"], "note": "explainability", "source": "fair_lending"},
]}
TRAITS = {k: {"score": 3, "evidence": "e", "reason": "r"} for k in sdlc_mod.TRAIT_KEYS}
JUSTIFICATION = {"requirement_characteristics": "a", "project_characteristics": "b",
                 "engineering_justification": "c", "key_tradeoffs": "d"}
CONFLICTS = {"conflicts": []}


def router(conflicts=CONFLICTS, seen=None):
    def fake(prompt, temperature=0.3):
        if seen is not None:
            seen.append(prompt)
        if "business analyst" in prompt:
            return json.dumps(REQS)
        if "compliance and risk analyst" in prompt:
            return json.dumps(COMPLIANCE)
        if "requirements list for CONFLICTS" in prompt:
            return conflicts if isinstance(conflicts, str) else json.dumps(conflicts)
        if RISK_PROMPT_MARKER in prompt:
            return risk_reply()
        if WORKFLOW_PROMPT_MARKER in prompt:
            return json.dumps({"additions": []})
        if "rating project characteristics" in prompt:
            return json.dumps(TRAITS)
        return json.dumps(JUSTIFICATION)
    return fake


@pytest.fixture
def mocked(monkeypatch):
    seen = []

    def install(conflicts=CONFLICTS):
        fake = router(conflicts, seen)
        for mod in (req_mod, comp_mod, quality_mod, risk_mod, sdlc_mod, workflow_mod):
            monkeypatch.setattr(mod, "generate_text", fake)
        monkeypatch.setattr(comp_mod, "retrieve_many", lambda qs, top_k=3: [[
            {"id": "kyc_aml-0", "source": "kyc_aml", "text": "KYC", "distance": 0.40},
            {"id": "fair_lending-1", "source": "fair_lending", "text": "FL", "distance": 0.45},
            {"id": "general_security_baseline-1", "source": "general_security_baseline", "text": "GB", "distance": 0.50},
        ] for _ in qs])
        return seen
    return install


def test_pipeline_returns_records_regulations_and_quality(mocked):
    mocked()
    d = client.post("/agents/pipeline", json={"scenario": SCENARIO}).json()
    assert {"scenario", "requirements", "compliance", "compliance_mapping", "quality", "risk_register",
            "sdlc_recommendation", "sdlc_workflow", "meta"} == set(d)
    fr1, fr2 = d["requirements"]["functional_requirements"]
    (nfr1,) = d["requirements"]["non_functional_requirements"]
    # domain-specific citations only; the generic baseline is not a regulation
    assert fr1["applicable_regulations"] == ["kyc_aml"]
    assert fr2["applicable_regulations"] == ["fair_lending"]
    assert nfr1["applicable_regulations"] == []
    assert fr1["confidence"] == "High" and nfr1["confidence"] == "Low" and fr1["approval_status"] == "proposed"
    q = d["quality"]
    assert q["counts"] == {"ambiguity": 1, "untestable": 1, "duplicate": 0, "conflict": 0}
    assert {f["type"] for f in q["flags"]} == {"ambiguity", "untestable"}
    assert all(f["requirement_ids"] == ["NFR-1"] for f in q["flags"])


def test_pipeline_passes_answers_to_agent_1(mocked):
    seen = mocked()
    answer = {"role": "Compliance and legal officers", "question": "Which rules apply?", "answer": "RBI KYC norms."}
    assert client.post("/agents/pipeline", json={"scenario": SCENARIO, "answers": [answer]}).status_code == 200
    assert "[Compliance and legal officers] Q: Which rules apply?" in seen[0]


def test_pipeline_quality_step_retries_then_502(mocked):
    seen = mocked(conflicts={"conflicts": [{"requirement_ids": ["FR-1", "FR-77"], "reason": "r", "suggested_fix": "f"}]})
    r = client.post("/agents/pipeline", json={"scenario": SCENARIO})
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Quality check failed: model output failed validation")
    assert sum("requirements list for CONFLICTS" in p for p in seen) == 2
    assert not any("rating project characteristics" in p for p in seen)  # SDLC never ran on a failed pipeline


def test_attach_regulations_ignores_generic_and_unknown_ids():
    reqs = {"functional_requirements": [{"id": "FR-1"}], "non_functional_requirements": [{"id": "NFR-1"}]}
    attach_regulations(reqs, {"considerations": [
        {"related_requirement_ids": ["FR-1", "FR-9"], "source": "payment_security"},
        {"related_requirement_ids": ["FR-1"], "source": "data_protection"},
        {"related_requirement_ids": ["NFR-1"], "source": "general_security_baseline"},
    ]})
    assert reqs["functional_requirements"][0]["applicable_regulations"] == ["data_protection", "payment_security"]
    assert reqs["non_functional_requirements"][0]["applicable_regulations"] == []
