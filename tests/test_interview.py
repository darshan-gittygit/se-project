"""Phase 1 step 2: role-based templates and the follow-up question endpoint. Offline."""
import json

import pytest
from fastapi.testclient import TestClient

import app.agents.interview_agent as iv
from app.agents.interview_templates import TEMPLATES, TEMPLATES_BY_ID
from app.main import app
from app.vocabulary import INTERVIEW_TOPICS, STAKEHOLDER_ROLES

client = TestClient(app)
SCENARIO = "A digital loan origination system for a bank, with KYC checks and credit scoring."
ANSWERS = [
    {"role": "Risk-management teams", "question": "What limits apply?", "answer": "Large loans need extra checks.",
     "question_id": "RSK-1"},
    {"role": "Product owners", "question": "Target dates?", "answer": "Launch in Q3.", "topic": "Expected project schedule and budget"},
]


def scripted(*replies):
    calls = []

    def fake(prompt, temperature=0.3):
        calls.append(prompt)
        r = replies[min(len(calls), len(replies)) - 1]
        return r if isinstance(r, str) else json.dumps(r)
    return fake, calls


def q(**kw):
    base = {"role": "Risk-management teams", "topic": "Financial transaction limits",
            "question": "What amount counts as a large loan?", "reason": "'Large' is not defined.",
            "template_id": None, "follows_up_on": 0}
    base.update(kw)
    return base


GOOD = {"questions": [q(), q(template_id="CMP-1", follows_up_on=None, role="Product owners",
                            topic="Users and roles", question="anything?", reason="Regulation drives scope.")]}


# --- templates (plain data) --------------------------------------------------

def test_every_stakeholder_role_has_templates_and_every_topic_is_covered():
    assert {t["role"] for t in TEMPLATES} == set(STAKEHOLDER_ROLES)
    assert {t["topic"] for t in TEMPLATES} == set(INTERVIEW_TOPICS)


def test_template_ids_are_unique_and_questions_are_questions():
    assert len(TEMPLATES_BY_ID) == len(TEMPLATES)
    assert all(t["question"].endswith("?") for t in TEMPLATES)


def test_templates_endpoint_needs_no_llm(monkeypatch):
    monkeypatch.setattr(iv, "generate_text", lambda *a, **k: pytest.fail("no LLM call expected"))
    d = client.get("/agents/interview/templates").json()
    assert len(d["templates"]) == len(TEMPLATES) and d["roles"] == list(STAKEHOLDER_ROLES)


# --- coverage is computed, not generated -----------------------------------

def test_covered_topics_come_from_answer_topics_or_template_ids():
    assert iv.covered_topics(ANSWERS) == ["Financial transaction limits", "Expected project schedule and budget"]


# --- endpoint ----------------------------------------------------------------

def test_follow_up_and_template_questions(monkeypatch):
    fake, calls = scripted(GOOD)
    monkeypatch.setattr(iv, "generate_text", fake)
    r = client.post("/agents/interview", json={"scenario": SCENARIO, "answers": ANSWERS})
    assert r.status_code == 200 and len(calls) == 1
    d = r.json()
    follow, templ = d["questions"]
    assert follow["follows_up_on"] == 0 and follow["template_id"] is None
    # template wording, role and topic are copied by code, whatever the LLM wrote
    assert templ == {"role": "Compliance and legal officers", "topic": "Regulatory and security constraints",
                     "question": TEMPLATES_BY_ID["CMP-1"]["question"], "reason": "Regulation drives scope.",
                     "template_id": "CMP-1", "follows_up_on": None}
    assert "Financial transaction limits" not in d["uncovered_topics"]
    assert len(d["covered_topics"]) + len(d["uncovered_topics"]) == len(INTERVIEW_TOPICS)
    assert "0: [Risk-management teams] What limits apply? -> Large loans need extra checks." in calls[0]
    assert "RSK-1 |" not in calls[0]  # answered templates are not offered again


def test_first_round_without_answers(monkeypatch):
    fake, _ = scripted({"questions": [q(template_id="BA-1", follows_up_on=None)]})
    monkeypatch.setattr(iv, "generate_text", fake)
    d = client.post("/agents/interview", json={"scenario": SCENARIO}).json()
    assert d["questions"][0]["template_id"] == "BA-1" and d["covered_topics"] == []


@pytest.mark.parametrize("bad", [
    {"questions": []},
    {"questions": [q(role="The CEO")]},
    {"questions": [q(topic="Vibes")]},
    {"questions": [q(question="Tell me about limits.")]},              # not a question
    {"questions": [q(follows_up_on=7)]},                                # no such answer
    {"questions": [q(follows_up_on=None)]},                             # neither template nor follow-up
    {"questions": [q(template_id="XYZ-9", follows_up_on=None)]},        # unknown template
    {"questions": [q(template_id="RSK-1", follows_up_on=None)]},        # already answered
    {"questions": [q(template_id="BA-1", follows_up_on=None)] * 2},     # repeated
    {"questions": [q(question=f"Question {i}?") for i in range(6)]},    # over max_questions (5)
])
def test_invalid_output_is_retried_then_502(monkeypatch, bad):
    fake, calls = scripted(bad)
    monkeypatch.setattr(iv, "generate_text", fake)
    r = client.post("/agents/interview", json={"scenario": SCENARIO, "answers": ANSWERS})
    assert r.status_code == 502 and len(calls) == 2
    assert r.json()["detail"].startswith("Interview agent failed: model output failed validation")


def test_invalid_then_valid_recovers(monkeypatch):
    fake, calls = scripted("not json", GOOD)
    monkeypatch.setattr(iv, "generate_text", fake)
    assert client.post("/agents/interview", json={"scenario": SCENARIO, "answers": ANSWERS}).status_code == 200
    assert len(calls) == 2


def test_max_questions_is_bounded_and_passed_to_the_prompt(monkeypatch):
    assert client.post("/agents/interview", json={"scenario": SCENARIO, "max_questions": 11}).status_code == 422
    fake, calls = scripted({"questions": [q(template_id="BA-1", follows_up_on=None)]})
    monkeypatch.setattr(iv, "generate_text", fake)
    client.post("/agents/interview", json={"scenario": SCENARIO, "max_questions": 2})
    assert "Choose up to 2 next questions" in calls[0]
