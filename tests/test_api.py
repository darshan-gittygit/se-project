"""
End-to-end API tests with the LLM and vector retrieval mocked.
These check wiring and response shapes (what the web UI depends on), not
the quality of real Gemini output.
"""
import json
import sys

import pytest
from fastapi.testclient import TestClient

import app.agents.requirement_agent as req_mod
import app.agents.compliance_agent as comp_mod
import app.agents.sdlc_agent as sdlc_mod
import app.agents.quality_agent as quality_mod
import app.agents.risk_agent as risk_mod
from app.main import app
from tests.helpers import RISK_PROMPT_MARKER, record, risk_reply

REQS = {
    "domain_inferred": "Payroll Processing System",
    "assumptions": ["Assumed monthly payroll cycle"],
    "functional_requirements": [
        record("FR-1", "The system shall calculate salaries."),
        record("FR-2", "The system shall generate payslips."),
    ],
    "non_functional_requirements": [
        record("NFR-1", "The system shall keep an audit trail."),
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
    "considerations": [{"related_requirement_ids": ["FR-1"], "note": "Protect salary data.", "source": "data_protection"}],
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
    if "requirements list for CONFLICTS" in prompt:
        return json.dumps({"conflicts": []})
    if RISK_PROMPT_MARKER in prompt:
        return risk_reply()
    raise AssertionError("unexpected prompt: " + prompt[:80])


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    for mod in (req_mod, comp_mod, sdlc_mod, quality_mod, risk_mod):
        monkeypatch.setattr(mod, "generate_text", fake_llm)
    monkeypatch.setattr(comp_mod, "retrieve_many", lambda queries, top_k=3: [[
        {"id": "data_protection-0", "text": "DP text", "source": "data_protection", "distance": 0.3},
        {"id": "healthcare_privacy-1", "text": "Health text", "source": "healthcare_privacy", "distance": 1.4},
    ] for _ in queries])


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
    assert d["sources_used"] == ["data_protection"]  # weak healthcare match filtered out
    assert set(d["sources_consulted"]) == {"data_protection", "healthcare_privacy"}


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


def test_justification_never_shows_raw_trait_keys(monkeypatch):
    seen = {}

    def leaky_llm(prompt, temperature=0.3):
        if "structured recommendation report" in prompt:
            seen["prompt"] = prompt
            return json.dumps({
                "requirement_characteristics": "Clear and stable.",
                "project_characteristics": "Criticality 5/5 but risk_uncertainty is only 1/5.",
                "engineering_justification": "verification_validation of 5/5 suits V-Model (NFR-1).",
                "key_tradeoffs": "Low stakeholder_feedback means late changes are costly.",
            })
        return fake_llm(prompt, temperature)

    monkeypatch.setattr(sdlc_mod, "generate_text", leaky_llm)
    d = client.post("/agents/sdlc", json=BODY).json()
    text = " ".join(d["justification"].values())
    for key in d["traits"]:
        if "_" in key:
            assert key not in text, key
            assert key not in seen["prompt"], key
    assert "risk/uncertainty is only 1/5" in text
    assert "verification & validation intensity of 5/5" in text


def test_trait_extraction_runs_at_temperature_zero(monkeypatch):
    temps = {}

    def recording_llm(prompt, temperature=0.3):
        if "rating a project on 9 characteristics" in prompt:
            temps["traits"] = temperature
        return fake_llm(prompt, temperature)

    monkeypatch.setattr(sdlc_mod, "generate_text", recording_llm)
    client.post("/agents/sdlc", json=BODY)
    assert temps["traits"] == 0


SDLC_RESPONSE_KEYS = {
    "traits", "trait_details", "scored_models", "recommended_model",
    "is_close_call", "justification", "meta",
}


def _record_trait_prompt(monkeypatch):
    seen = {}

    def recording_llm(prompt, temperature=0.3):
        if "rating a project on 9 characteristics" in prompt:
            seen["prompt"] = prompt
        return fake_llm(prompt, temperature)

    monkeypatch.setattr(sdlc_mod, "generate_text", recording_llm)
    return seen


def test_sdlc_scenario_is_optional_and_reaches_trait_prompt(monkeypatch):
    seen = _record_trait_prompt(monkeypatch)
    scenario = "Payroll rules are approved by HR and change only via formal change control."
    d = client.post("/agents/sdlc", json=dict(BODY, scenario=scenario)).json()
    assert scenario in seen["prompt"]
    assert set(d) == SDLC_RESPONSE_KEYS

    d = client.post("/agents/sdlc", json=BODY).json()  # field omitted: still works
    assert "(not provided" in seen["prompt"]
    assert set(d) == SDLC_RESPONSE_KEYS


def test_pipeline_passes_its_scenario_to_trait_rating(monkeypatch):
    seen = _record_trait_prompt(monkeypatch)
    client.post("/agents/pipeline", json={"scenario": "A payroll system for a large company"})
    assert "A payroll system for a large company" in seen["prompt"]


def test_compliance_accepts_and_ignores_scenario_field():
    r = client.post("/agents/compliance", json=dict(BODY, scenario="anything"))
    assert r.status_code == 200 and r.json()["risk_level"] == "High"


def test_tie_is_a_close_call_with_reason_and_unchanged_shape(monkeypatch):
    tied = {
        "clarity": 5, "volatility": 1, "complexity": 2, "criticality": 4,
        "risk_uncertainty": 1, "verification_validation": 4,
        "stakeholder_feedback": 1, "incremental_suitability": 3, "prototyping_need": 1,
    }
    monkeypatch.setattr(sys.modules[__name__], "TRAITS", tied)
    d = client.post("/agents/sdlc", json=BODY).json()
    assert set(d) == SDLC_RESPONSE_KEYS
    assert d["recommended_model"] == "V-Model"
    assert d["is_close_call"] is True
    top, runner = d["scored_models"][:2]
    assert top["score"] == runner["score"] == 84.0
    assert set(top) == set(runner) == {"model", "score", "profile", "why_not"}
    assert runner["model"] == "Waterfall" and "tied with V-Model" in runner["why_not"]


def test_requirement_agent_runs_at_low_temperature(monkeypatch):
    temps = {}

    def recording_llm(prompt, temperature=0.3):
        if "business analyst" in prompt:
            temps["req"] = temperature
        return fake_llm(prompt, temperature)

    monkeypatch.setattr(req_mod, "generate_text", recording_llm)
    client.post("/agents/requirements", json={"scenario": "A payroll system for a large company"})
    assert temps["req"] == 0.1


def test_api_is_branded_requora():
    assert app.title.startswith("Requora AI")
    assert client.get("/openapi.json").json()["info"]["title"].startswith("Requora AI")
