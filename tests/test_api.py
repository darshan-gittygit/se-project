"""
End-to-end API tests with the LLM and vector retrieval mocked.
These check wiring and response shapes (what the web UI depends on), not
the quality of real Gemini output.
"""
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.requirement_agent as req_mod
import app.agents.compliance_agent as comp_mod
import app.agents.sdlc_agent as sdlc_mod
from app.main import app

REQS = {
    "domain_inferred": "Payroll Processing System",
    "assumptions": ["Assumed monthly payroll cycle"],
    "functional_requirements": [
        {"id": "FR-1", "requirement": "The system shall calculate salaries.", "priority": "High"},
        {"id": "FR-2", "requirement": "The system shall generate payslips.", "priority": "High"},
    ],
    "non_functional_requirements": [
        {"id": "NFR-1", "requirement": "The system shall keep an audit trail.", "priority": "High"},
    ],
}

TRAITS = {
    "clarity": 5, "volatility": 1, "complexity": 3, "criticality": 5,
    "risk_uncertainty": 1, "verification_validation": 5,
    "stakeholder_feedback": 1, "incremental_suitability": 1, "prototyping_need": 1,
}

COMPLIANCE = {
    "risk_level": "High",
    "kb_relevance": "relevant",
    "considerations": [{"related_requirement_ids": ["FR-1"], "note": "Needs audit trail.", "source": "kyc_aml"}],
    "summary": "High regulatory risk.",
}

JUSTIFICATION = {
    "requirement_characteristics": "Stable, clear requirements (volatility 1/5).",
    "project_characteristics": "High criticality (5/5) with low uncertainty.",
    "engineering_justification": "V-Model fits NFR-1 verification needs.",
    "key_tradeoffs": "Rigid; late changes are costly.",
}


def fake_llm(prompt, temperature=0.3):
    if "business analyst" in prompt:
        return json.dumps(REQS)
    if "rating a project on 9 characteristics" in prompt:
        return "```json\n" + json.dumps({
            k: {"score": v, "evidence": f"evidence for {k}", "reason": f"reason for {k}"}
            for k, v in TRAITS.items()
        }) + "\n```"
    if "structured recommendation report" in prompt:
        return json.dumps(JUSTIFICATION)
    if "compliance and risk analyst" in prompt:
        return json.dumps(COMPLIANCE)
    raise AssertionError("unexpected prompt: " + prompt[:80])


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    for mod in (req_mod, comp_mod, sdlc_mod):
        monkeypatch.setattr(mod, "generate_text", fake_llm)
    monkeypatch.setattr(comp_mod, "retrieve", lambda q, top_k=5: [
        {"text": "KYC text", "source": "kyc_aml", "distance": 0.3},
        {"text": "Health text", "source": "healthcare_privacy", "distance": 1.4},
    ])


client = TestClient(app)
BODY = {
    "domain": REQS["domain_inferred"],
    "functional_requirements": REQS["functional_requirements"],
    "non_functional_requirements": REQS["non_functional_requirements"],
}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_ui_and_static_files_are_served():
    r = client.get("/")
    assert r.status_code == 200 and "REQUORA" in r.text
    for f in ("app.js", "style.css"):
        assert client.get(f"/static/{f}").status_code == 200


def test_requirements_endpoint():
    r = client.post("/agents/requirements", json={"scenario": "A payroll system for a large company"})
    assert r.status_code == 200
    d = r.json()
    assert d["domain_inferred"] == "Payroll Processing System"
    assert d["functional_requirements"][0]["priority"] == "High"
    assert "model" in d["meta"]


def test_requirements_rejects_short_scenario():
    assert client.post("/agents/requirements", json={"scenario": "hi"}).status_code == 400


def test_compliance_endpoint_only_counts_relevant_sources():
    d = client.post("/agents/compliance", json=BODY).json()
    assert d["risk_level"] == "High"
    assert d["sources_used"] == ["kyc_aml"]  # weak healthcare match filtered out
    assert set(d["sources_consulted"]) == {"kyc_aml", "healthcare_privacy"}


def test_sdlc_endpoint_payroll_gets_v_model_not_high_volatility():
    d = client.post("/agents/sdlc", json=BODY).json()
    assert d["recommended_model"] == "V-Model"
    assert d["traits"]["volatility"] == 1
    assert len(d["traits"]) == 9
    assert d["trait_details"]["volatility"]["evidence"]
    assert len(d["scored_models"]) == 5
    assert d["scored_models"][0]["why_not"] is None
    assert all(m["why_not"] for m in d["scored_models"][1:])
    assert set(d["justification"]) == {
        "requirement_characteristics", "project_characteristics",
        "engineering_justification", "key_tradeoffs",
    }
    assert isinstance(d["is_close_call"], bool)


def test_full_pipeline_endpoint():
    d = client.post("/agents/pipeline", json={"scenario": "A payroll system for a large company"}).json()
    assert d["requirements"]["domain_inferred"] == "Payroll Processing System"
    assert d["compliance"]["risk_level"] == "High"
    assert d["sdlc_recommendation"]["recommended_model"] == "V-Model"


def test_agent_failure_returns_clean_json_error(monkeypatch):
    def boom(prompt, temperature=0.3):
        raise RuntimeError("Gemini down")
    monkeypatch.setattr(sdlc_mod, "generate_text", boom)
    r = client.post("/agents/sdlc", json=BODY)
    assert r.status_code == 502 and "Gemini down" in r.json()["detail"]
