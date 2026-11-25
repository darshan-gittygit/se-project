"""Fix 3: a missing or invalid trait is an error - never a silent default of 3. Offline."""
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.sdlc_agent as sdlc_mod
from app.main import app
from app.schemas import LLMOutputError, TraitRatings

client = TestClient(app)
GOOD = {k: {"score": 2, "evidence": f"evidence {k}", "reason": f"reason {k}"} for k in sdlc_mod.TRAIT_KEYS}
JUSTIFICATION = {"requirement_characteristics": "a", "project_characteristics": "b",
                 "engineering_justification": "c", "key_tradeoffs": "d"}
BODY = {"domain": "X",
        "functional_requirements": [{"id": "FR-1", "requirement": "r", "priority": "High"}],
        "non_functional_requirements": [{"id": "NFR-1", "requirement": "r", "priority": "Low"}]}


def with_trait(key, value):
    d = json.loads(json.dumps(GOOD))
    if value is ...:
        del d[key]
    else:
        d[key]["score"] = value
    return d


@pytest.mark.parametrize("bad_score", [0, 6, -1, 3.5, 4.0, "4", "high", True, None])
def test_invalid_scores_are_rejected(bad_score):
    with pytest.raises(ValueError):
        TraitRatings.model_validate(with_trait("volatility", bad_score))


@pytest.mark.parametrize("key", sdlc_mod.TRAIT_KEYS)
def test_each_missing_trait_is_rejected(key):
    with pytest.raises(ValueError, match=key):
        TraitRatings.model_validate(with_trait(key, ...))


def test_empty_evidence_is_rejected():
    d = json.loads(json.dumps(GOOD))
    d["clarity"]["evidence"] = "   "
    with pytest.raises(ValueError):
        TraitRatings.model_validate(d)


def test_valid_ratings_pass_through_unchanged():
    out = TraitRatings.model_validate(GOOD).model_dump()
    assert out == GOOD


def test_analyze_traits_raises_instead_of_defaulting(monkeypatch):
    calls = []
    monkeypatch.setattr(sdlc_mod, "generate_text",
                        lambda p, temperature=0: calls.append(p) or json.dumps(with_trait("criticality", ...)))
    with pytest.raises(LLMOutputError, match="criticality"):
        sdlc_mod.analyze_traits("X", BODY["functional_requirements"], BODY["non_functional_requirements"])
    assert len(calls) == 2  # one retry


def test_analyze_traits_recovers_when_retry_is_valid(monkeypatch):
    replies = iter([json.dumps(with_trait("volatility", 9)), json.dumps(GOOD)])
    monkeypatch.setattr(sdlc_mod, "generate_text", lambda p, temperature=0: next(replies))
    out = sdlc_mod.analyze_traits("X", BODY["functional_requirements"], BODY["non_functional_requirements"])
    assert out["volatility"]["score"] == 2


def test_sdlc_endpoint_returns_502_not_an_incremental_default(monkeypatch):
    # Before this fix every missing trait became 3, and an all-3 project
    # recommends Incremental (83.2) - so a broken reply looked like a result.
    def llm(prompt, temperature=0.3):
        if "9 characteristics" in prompt:
            return json.dumps({"clarity": GOOD["clarity"]})
        return json.dumps(JUSTIFICATION)
    monkeypatch.setattr(sdlc_mod, "generate_text", llm)
    r = client.post("/agents/sdlc", json=BODY)
    assert r.status_code == 502
    assert "Agent 2 failed: model output failed validation" in r.json()["detail"]
