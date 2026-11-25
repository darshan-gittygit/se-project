"""
Stakeholder interview step (problem statement sections 2 and 7).

Given the scenario and the answers collected so far, returns the next
follow-up questions:
- follow-ups on answers that are vague, incomplete or inconsistent
  ("follows_up_on" = index of that answer), and
- unasked template questions for topics nobody has covered yet
  ("template_id"; the wording, role and topic are copied from the template
  by code, so templates cannot drift).

One LLM call, validated by FollowUpOutput (retry once, then LLMOutputError).
Which topics are covered is computed in code from the answers, not by the LLM.
"""
from typing import Literal

from pydantic import BaseModel, Field, StrictInt, ValidationInfo, field_validator, model_validator

from app.agents.interview_templates import TEMPLATES, TEMPLATES_BY_ID
from app.gemini_client import generate_text
from app.schemas import _NonEmptyText, _ctx, generate_validated
from app.vocabulary import INTERVIEW_TOPICS, STAKEHOLDER_ROLES, canonical

DEFAULT_MAX_QUESTIONS = 5


class FollowUpQuestion(_NonEmptyText):
    role: Literal[STAKEHOLDER_ROLES]
    topic: Literal[INTERVIEW_TOPICS]
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    template_id: str | None = None
    follows_up_on: StrictInt | None = None

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, v):
        return canonical(v, STAKEHOLDER_ROLES)

    @field_validator("topic", mode="before")
    @classmethod
    def _topic(cls, v):
        return canonical(v, INTERVIEW_TOPICS)

    @field_validator("question")
    @classmethod
    def _is_a_question(cls, v):
        if not v.endswith("?"):
            raise ValueError("question must end with '?'")
        return v

    @field_validator("template_id")
    @classmethod
    def _known_unanswered_template(cls, v, info: ValidationInfo):
        if v is None:
            return v
        if v not in TEMPLATES_BY_ID:
            raise ValueError(f"unknown template_id {v!r}")
        if v in (_ctx(info, "answered_template_ids") or set()):
            raise ValueError(f"template {v} has already been answered")
        return v

    @field_validator("follows_up_on")
    @classmethod
    def _answer_exists(cls, v, info: ValidationInfo):
        n = _ctx(info, "n_answers")
        if v is not None and n is not None and not 0 <= v < n:
            raise ValueError(f"follows_up_on must be an answer index from 0 to {n - 1}" if n else
                             "follows_up_on must be null: there are no answers yet")
        return v

    @model_validator(mode="after")
    def _copy_template(self):
        if self.template_id:
            t = TEMPLATES_BY_ID[self.template_id]
            self.role, self.topic, self.question = t["role"], t["topic"], t["question"]
        elif self.follows_up_on is None:
            raise ValueError("each question needs a template_id or a follows_up_on answer index")
        return self


class FollowUpOutput(BaseModel):
    questions: list[FollowUpQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def _limits_and_duplicates(self, info: ValidationInfo):
        limit = _ctx(info, "max_questions")
        if limit is not None and len(self.questions) > limit:
            raise ValueError(f"return at most {limit} questions, got {len(self.questions)}")
        templates = [q.template_id for q in self.questions if q.template_id]
        texts = [q.question.lower() for q in self.questions]
        if len(set(templates)) != len(templates) or len(set(texts)) != len(texts):
            raise ValueError("questions must not repeat")
        return self


PROMPT_TEMPLATE = """You are a requirements engineer running a stakeholder interview for a new software system.

Scenario:
{scenario}

Answers collected so far (index: [role] question -> answer):
{answers}

Topics no answer has covered yet: {uncovered}

Template questions not yet asked (id | role | topic | question):
{templates}

Choose up to {max_questions} next questions, most important first:
1. For any answer that is vague, incomplete, or inconsistent with the scenario or another answer, write a follow-up question to the same role. Set "follows_up_on" to that answer's index, "template_id" to null, and say in "reason" exactly what is missing or inconsistent.
2. Then pick template questions for uncovered topics that matter most for THIS scenario. Set "template_id" (copy the id exactly) and "follows_up_on" to null; "reason" says why the topic matters here.
Do not ask about something an answer already settles. Use only these roles: {roles}. Use only these topics: {topics}.

Respond with ONLY valid JSON, no markdown fences, in exactly this schema:
{{"questions": [{{"role": "...", "topic": "...", "question": "...?", "reason": "...", "template_id": "BA-1" or null, "follows_up_on": 0 or null}}]}}
"""


def covered_topics(answers: list[dict]) -> list[str]:
    """Topics covered by the answers: the answer's own topic, else its template's topic."""
    found = set()
    for a in answers:
        topic = a.get("topic") or TEMPLATES_BY_ID.get(a.get("question_id") or "", {}).get("topic")
        if topic:
            found.add(topic)
    return [t for t in INTERVIEW_TOPICS if t in found]


def next_questions(scenario: str, answers: list[dict] | None = None,
                   max_questions: int = DEFAULT_MAX_QUESTIONS) -> dict:
    answers = answers or []
    covered = covered_topics(answers)
    uncovered = [t for t in INTERVIEW_TOPICS if t not in covered]
    answered_ids = {a.get("question_id") for a in answers if a.get("question_id")}
    unasked = [t for t in TEMPLATES if t["id"] not in answered_ids]

    prompt = PROMPT_TEMPLATE.format(
        scenario=scenario.strip(),
        answers="\n".join(f"{i}: [{a['role']}] {a['question']} -> {a['answer']}" for i, a in enumerate(answers)) or "(none yet)",
        uncovered=", ".join(uncovered) or "(all topics covered)",
        templates="\n".join(f"{t['id']} | {t['role']} | {t['topic']} | {t['question']}" for t in unasked) or "(all asked)",
        max_questions=max_questions,
        roles=", ".join(STAKEHOLDER_ROLES),
        topics=", ".join(INTERVIEW_TOPICS),
    )
    result = generate_validated(
        prompt, FollowUpOutput, generate=generate_text, temperature=0.2,
        context={"n_answers": len(answers), "answered_template_ids": answered_ids, "max_questions": max_questions},
    )
    return {
        "questions": result.model_dump()["questions"],
        "covered_topics": covered,
        "uncovered_topics": uncovered,
    }
