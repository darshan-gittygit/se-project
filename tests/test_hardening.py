"""Fix 5: real model reported in meta, 429 backoff, input limits, no /test-gemini. Offline."""
import json
import types

import pytest
import requests
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

import app.gemini_client as gc
from app.main import MAX_SCENARIO_CHARS, MAX_REQUIREMENTS_PER_LIST, app
from tests.helpers import record

client = TestClient(app)
PRIMARY, FALLBACK = gc._candidate_models()[0], gc._candidate_models()[1]

REQS = {"domain_inferred": "Payroll", "assumptions": [],
        "functional_requirements": [record("FR-1", "Calculate salaries.")],
        "non_functional_requirements": [record("NFR-1", "Audit trail.")]}
TRAITS = {k: {"score": 3, "evidence": "e", "reason": "r"} for k in (
    "clarity", "volatility", "complexity", "criticality", "risk_uncertainty",
    "verification_validation", "stakeholder_feedback", "incremental_suitability", "prototyping_need")}
JUSTIFICATION = {"requirement_characteristics": "a", "project_characteristics": "b",
                 "engineering_justification": "c", "key_tradeoffs": "d"}
BODY = {"domain": "Payroll", "functional_requirements": REQS["functional_requirements"],
        "non_functional_requirements": REQS["non_functional_requirements"]}


def api_error(cls, code, status, details=None):
    r = requests.Response()
    r.status_code = code
    body = {"error": {"code": code, "message": "x", "status": status}}
    if details:
        body["error"]["details"] = details
    r._content = json.dumps(body).encode()
    r.headers["Content-Type"] = "application/json"
    return cls(code, r)


def reply_for(prompt):
    if "business analyst" in prompt:
        return json.dumps(REQS)
    if "9 characteristics" in prompt:
        return json.dumps(TRAITS)
    return json.dumps(JUSTIFICATION)


@pytest.fixture
def fake_sdk(monkeypatch):
    """behaviour(model, prompt, call_no) -> text or raises. Records (model) per call and sleeps."""
    state = {"tried": [], "sleeps": [], "behaviour": None}

    def generate_content(model, contents, config):
        state["tried"].append(model)
        return types.SimpleNamespace(text=state["behaviour"](model, contents, len(state["tried"])))

    monkeypatch.setattr(gc, "_client", types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content)))
    monkeypatch.setattr(gc.time, "sleep", lambda s: state["sleeps"].append(s))
    return state


# --- meta.model is the model that answered ---------------------------------

def test_meta_reports_fallback_model_not_configured_one(fake_sdk):
    def behaviour(model, prompt, n):
        if model == PRIMARY:
            raise api_error(genai_errors.ClientError, 404, "NOT_FOUND")
        return reply_for(prompt)
    fake_sdk["behaviour"] = behaviour
    d = client.post("/agents/requirements", json={"scenario": "A payroll system for a company"}).json()
    assert d["meta"] == {"model": FALLBACK, "models_used": [FALLBACK]}


def test_meta_lists_every_model_when_calls_were_served_by_different_models(fake_sdk):
    # SDLC makes two calls: traits served by the primary, justification by the fallback.
    def behaviour(model, prompt, n):
        if model == PRIMARY and "recommendation report" in prompt:
            raise api_error(genai_errors.ServerError, 503, "UNAVAILABLE")
        return reply_for(prompt)
    fake_sdk["behaviour"] = behaviour
    d = client.post("/agents/sdlc", json=BODY).json()
    assert d["meta"]["models_used"] == [PRIMARY, FALLBACK]
    assert d["meta"]["model"] == f"{PRIMARY}, {FALLBACK}"


def test_pipeline_has_meta_for_all_calls(fake_sdk, monkeypatch):
    import app.agents.compliance_agent as comp
    monkeypatch.setattr(comp, "retrieve_many", lambda q, top_k=3: [[] for _ in q])
    fake_sdk["behaviour"] = lambda model, prompt, n: (
        json.dumps({"risk_level": "Low", "considerations": [], "summary": "s"})
        if "compliance and risk analyst" in prompt else reply_for(prompt))
    d = client.post("/agents/pipeline", json={"scenario": "A payroll system for a company"}).json()
    assert d["meta"]["models_used"] == [PRIMARY] * 5  # requirements, compliance, conflicts, traits, justification
    assert {"scenario", "requirements", "compliance", "quality", "sdlc_recommendation"} <= set(d)


def test_tracking_does_not_leak_between_requests(fake_sdk):
    fake_sdk["behaviour"] = lambda model, prompt, n: reply_for(prompt)
    client.post("/agents/requirements", json={"scenario": "A payroll system for a company"})
    d = client.post("/agents/requirements", json={"scenario": "A payroll system for a company"}).json()
    assert d["meta"]["models_used"] == [PRIMARY]
    assert gc._models_used.get() is None


# --- 429 backoff -------------------------------------------------------------

def test_429_is_retried_on_the_same_model_with_backoff(fake_sdk):
    def behaviour(model, prompt, n):
        if n <= 2:
            raise api_error(genai_errors.ClientError, 429, "RESOURCE_EXHAUSTED")
        return "ok"
    fake_sdk["behaviour"] = behaviour
    assert gc.generate_text("hi") == "ok"
    assert fake_sdk["tried"] == [PRIMARY] * 3
    assert fake_sdk["sleeps"] == [2, 4]


def test_429_honours_retry_delay_hint_but_caps_it(fake_sdk):
    hints = iter(["3s", "45s"])

    def behaviour(model, prompt, n):
        if n <= 2:
            raise api_error(genai_errors.ClientError, 429, "RESOURCE_EXHAUSTED", details=[
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": next(hints)}])
        return "ok"
    fake_sdk["behaviour"] = behaviour
    gc.generate_text("hi")
    assert fake_sdk["sleeps"] == [3.0, gc.RATE_LIMIT_MAX_DELAY]


def test_429_with_a_daily_quota_hint_skips_to_the_next_model_without_waiting(fake_sdk):
    # Seen live: "Generate content requests per day limit exceeded ... retry in 21h31m".
    def behaviour(model, prompt, n):
        if model == PRIMARY:
            raise api_error(genai_errors.ClientError, 429, "RESOURCE_EXHAUSTED", details=[
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "77486s"}])
        return "ok"
    fake_sdk["behaviour"] = behaviour
    assert gc.generate_text("hi") == "ok"
    assert fake_sdk["tried"] == [PRIMARY, FALLBACK] and fake_sdk["sleeps"] == []


def test_429_that_persists_moves_to_the_next_model(fake_sdk):
    def behaviour(model, prompt, n):
        if model == PRIMARY:
            raise api_error(genai_errors.ClientError, 429, "RESOURCE_EXHAUSTED")
        return "ok"
    fake_sdk["behaviour"] = behaviour
    assert gc.generate_text("hi") == "ok"
    assert fake_sdk["tried"] == [PRIMARY] * gc.RATE_LIMIT_ATTEMPTS + [FALLBACK]


def test_404_is_not_retried_on_the_same_model(fake_sdk):
    def behaviour(model, prompt, n):
        if model == PRIMARY:
            raise api_error(genai_errors.ClientError, 404, "NOT_FOUND")
        return "ok"
    fake_sdk["behaviour"] = behaviour
    gc.generate_text("hi")
    assert fake_sdk["tried"] == [PRIMARY, FALLBACK] and fake_sdk["sleeps"] == []


# --- input limits ------------------------------------------------------------

@pytest.mark.parametrize("path", ["/agents/requirements", "/agents/pipeline"])
def test_overlong_scenario_is_rejected_before_any_model_call(fake_sdk, path):
    fake_sdk["behaviour"] = lambda *a: pytest.fail("model must not be called")
    r = client.post(path, json={"scenario": "x" * (MAX_SCENARIO_CHARS + 1)})
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)
    assert "too long" in r.json()["detail"]


def test_scenario_at_the_limit_is_accepted(fake_sdk):
    fake_sdk["behaviour"] = lambda model, prompt, n: reply_for(prompt)
    r = client.post("/agents/requirements", json={"scenario": "x" * MAX_SCENARIO_CHARS})
    assert r.status_code == 200


@pytest.mark.parametrize("path", ["/agents/sdlc", "/agents/compliance"])
@pytest.mark.parametrize("change", [
    {"scenario": "x" * (MAX_SCENARIO_CHARS + 1)},
    {"domain": "d" * 201},
    {"functional_requirements": [{"id": f"FR-{i}", "requirement": "r"} for i in range(MAX_REQUIREMENTS_PER_LIST + 1)]},
    {"non_functional_requirements": [{"id": "NFR-1", "requirement": "r" * 1001}]},
])
def test_oversized_requirement_bodies_are_rejected(fake_sdk, path, change):
    fake_sdk["behaviour"] = lambda *a: pytest.fail("model must not be called")
    r = client.post(path, json=dict(BODY, **change))
    assert r.status_code == 400 and isinstance(r.json()["detail"], str)


# --- /test-gemini ------------------------------------------------------------

def test_test_gemini_route_is_gone():
    assert client.get("/test-gemini").status_code == 404
    assert "/test-gemini" not in client.get("/openapi.json").json()["paths"]
