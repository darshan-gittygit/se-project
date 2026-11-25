"""
FastAPI entry point.
Run with: python -m uvicorn app.main:app --reload --port 8001 (or run.bat)
Then open http://127.0.0.1:8001/docs for the interactive API,
or http://127.0.0.1:8001/ for the web UI.

Wiring check for the Gemini key and model: python -m app.gemini_client
(the old unauthenticated /test-gemini route was removed - anyone who could
reach the server could spend the API quota with it).
"""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.interview_agent import DEFAULT_MAX_QUESTIONS, next_questions
from app.agents.interview_templates import TEMPLATES
from app.gemini_client import track_models
from app.vocabulary import INTERVIEW_TOPICS, STAKEHOLDER_ROLES
from app.config import STATIC_DIR
from app.agents.requirement_agent import extract_requirements
from app.agents.compliance_agent import analyze_compliance, attach_regulations
from app.agents.quality_agent import analyze_quality
from app.agents.sdlc_agent import recommend_sdlc

app = FastAPI(title="Requora AI - Requirements Engineering & SDLC Recommendation API")

# Input size limits. Scenarios are a paragraph or two; these bound prompt
# size (cost, latency, rate limits) without getting in the way of real use.
MAX_SCENARIO_CHARS = 5000
MAX_DOMAIN_CHARS = 200
MAX_REQUIREMENTS_PER_LIST = 50
MAX_REQUIREMENT_CHARS = 1000
MAX_ANSWERS = 30
MAX_ANSWER_CHARS = 2000


class InterviewAnswer(BaseModel):
    """One stakeholder answer (problem statement sections 2 and 7)."""
    role: Literal[STAKEHOLDER_ROLES]
    question: str
    answer: str
    question_id: str | None = None                  # template id, if the question came from a template
    topic: Literal[INTERVIEW_TOPICS] | None = None


class ScenarioRequest(BaseModel):
    scenario: str
    # Optional interview answers collected so far; requirements can quote them.
    answers: list[InterviewAnswer] = []


class InterviewRequest(ScenarioRequest):
    max_questions: int = Field(DEFAULT_MAX_QUESTIONS, ge=1, le=10)


class RequirementItem(BaseModel):
    id: str
    requirement: str
    priority: str | None = None
    # Optional (additive): used by the quality check's untestable rule.
    acceptance_criteria: list[str] = []


class SdlcRequest(BaseModel):
    domain: str
    functional_requirements: list[RequirementItem]
    non_functional_requirements: list[RequirementItem]
    # Optional original scenario text; /agents/sdlc passes it to trait rating
    # (the compliance endpoint shares this model and ignores it).
    scenario: str | None = None


def _check_scenario(scenario: str | None) -> None:
    # 400 with a plain-string detail, which the UI's error panel can display.
    if not scenario or len(scenario.strip()) < 5:
        raise HTTPException(status_code=400, detail="Scenario description is too short.")
    if len(scenario) > MAX_SCENARIO_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Scenario description is too long ({len(scenario)} characters; the limit is {MAX_SCENARIO_CHARS}).",
        )


def _check_answers(answers: list[InterviewAnswer]) -> list[dict]:
    if len(answers) > MAX_ANSWERS:
        raise HTTPException(status_code=400, detail=f"Too many interview answers (limit {MAX_ANSWERS}).")
    if any(len(a.answer) > MAX_ANSWER_CHARS or len(a.question) > MAX_ANSWER_CHARS for a in answers):
        raise HTTPException(status_code=400, detail=f"An interview question or answer is too long (limit {MAX_ANSWER_CHARS} characters).")
    if any(not a.answer.strip() for a in answers):
        raise HTTPException(status_code=400, detail="Interview answers must not be empty.")
    return [a.model_dump() for a in answers]


def _check_requirement_body(body: SdlcRequest) -> None:
    if len(body.domain) > MAX_DOMAIN_CHARS:
        raise HTTPException(status_code=400, detail=f"Domain is too long (limit {MAX_DOMAIN_CHARS} characters).")
    if body.scenario is not None and len(body.scenario) > MAX_SCENARIO_CHARS:
        raise HTTPException(status_code=400, detail=f"Scenario is too long (limit {MAX_SCENARIO_CHARS} characters).")
    for name in ("functional_requirements", "non_functional_requirements"):
        reqs = getattr(body, name)
        if len(reqs) > MAX_REQUIREMENTS_PER_LIST:
            raise HTTPException(status_code=400, detail=f"Too many {name} (limit {MAX_REQUIREMENTS_PER_LIST}).")
        if any(len(r.requirement) > MAX_REQUIREMENT_CHARS for r in reqs):
            raise HTTPException(status_code=400, detail=f"A requirement is too long (limit {MAX_REQUIREMENT_CHARS} characters).")


def _meta(models_used: list[str]) -> dict:
    """
    meta.model names the model(s) that actually answered, in call order,
    which can differ from GEMINI_MODEL after a fallback. models_used lists
    every call (additive field). None when no model call was made.
    """
    distinct = list(dict.fromkeys(models_used))
    return {"model": ", ".join(distinct) if distinct else None, "models_used": list(models_used)}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/agents/requirements")
def run_requirement_agent(body: ScenarioRequest):
    """Agent 1 alone - scenario (+ optional interview answers) in, requirement records out. Stage 1 of the UI flow."""
    _check_scenario(body.scenario)
    answers = _check_answers(body.answers)
    try:
        with track_models() as used:
            result = extract_requirements(body.scenario, answers)
        result["meta"] = _meta(used)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Agent 1 failed: {e}")


@app.get("/agents/interview/templates")
def interview_templates():
    """The hand-written, role-specific question templates (no LLM call)."""
    return {"templates": TEMPLATES, "roles": list(STAKEHOLDER_ROLES), "topics": list(INTERVIEW_TOPICS)}


@app.post("/agents/interview")
def run_interview_agent(body: InterviewRequest):
    """
    Interview step - scenario + answers so far in, next follow-up questions out
    (one LLM call). Covered/uncovered topics are computed from the answers.
    """
    _check_scenario(body.scenario)
    answers = _check_answers(body.answers)
    try:
        with track_models() as used:
            result = next_questions(body.scenario, answers, body.max_questions)
        result["meta"] = _meta(used)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Interview agent failed: {e}")


@app.post("/agents/quality")
def run_quality_agent(body: SdlcRequest):
    """
    Quality and conflict checks over a requirement list: ambiguity, untestable
    NFRs and near-duplicates by deterministic rules, conflicts by one LLM call.
    """
    _check_requirement_body(body)
    try:
        with track_models() as used:
            result = analyze_quality(
                domain=body.domain,
                functional_requirements=[r.model_dump() for r in body.functional_requirements],
                non_functional_requirements=[r.model_dump() for r in body.non_functional_requirements],
            )
        result["meta"] = _meta(used)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Quality check failed: {e}")


@app.post("/agents/compliance")
def run_compliance_agent(body: SdlcRequest):
    """
    Agent 3 - RAG-grounded compliance/risk analysis. Takes the same
    requirement list shape as Agent 2's endpoint. Stage 2 of the UI flow
    (between requirement generation and SDLC recommendation) - retrieves
    from the curated knowledge base and cites its sources honestly,
    including saying when the knowledge base doesn't cover a domain well.
    """
    _check_requirement_body(body)
    try:
        with track_models() as used:
            result = analyze_compliance(
                domain=body.domain,
                functional_requirements=[r.model_dump() for r in body.functional_requirements],
                non_functional_requirements=[r.model_dump() for r in body.non_functional_requirements],
            )
        result["meta"] = _meta(used)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Agent 3 (Compliance) failed: {e}")


@app.post("/agents/sdlc")
def run_sdlc_agent(body: SdlcRequest):
    """
    Agent 2 alone - takes an already-generated requirement list and
    recommends an SDLC model. Stage 3 of the UI flow, called separately
    from Stage 1 so the frontend can show a genuine multi-step workflow
    rather than one combined black-box call.
    """
    _check_requirement_body(body)
    try:
        with track_models() as used:
            result = recommend_sdlc(
                domain=body.domain,
                functional_requirements=[r.model_dump() for r in body.functional_requirements],
                non_functional_requirements=[r.model_dump() for r in body.non_functional_requirements],
                scenario=body.scenario,
            )
        result["meta"] = _meta(used)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Agent 2 failed: {e}")


@app.post("/agents/pipeline")
def run_full_pipeline(body: ScenarioRequest):
    """
    Convenience endpoint: all agents in one call. Kept for API testing
    via /docs - the web UI itself calls /agents/requirements,
    /agents/compliance, and /agents/sdlc separately for a real
    multi-stage workflow.

    Order: requirements -> compliance (then each requirement's
    applicable_regulations is filled from the domain-specific sources cited
    for it) -> quality checks -> SDLC recommendation.
    """
    _check_scenario(body.scenario)
    answers = _check_answers(body.answers)

    with track_models() as used:
        try:
            requirements = extract_requirements(body.scenario, answers)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Agent 1 failed: {e}")

        try:
            compliance_result = analyze_compliance(
                domain=requirements.get("domain_inferred", "Unknown"),
                functional_requirements=requirements.get("functional_requirements", []),
                non_functional_requirements=requirements.get("non_functional_requirements", []),
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Agent 3 (Compliance) failed: {e}")

        attach_regulations(requirements, compliance_result)

        try:
            quality_result = analyze_quality(
                domain=requirements.get("domain_inferred", "Unknown"),
                functional_requirements=requirements.get("functional_requirements", []),
                non_functional_requirements=requirements.get("non_functional_requirements", []),
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Quality check failed: {e}")

        try:
            sdlc_result = recommend_sdlc(
                domain=requirements.get("domain_inferred", "Unknown"),
                functional_requirements=requirements.get("functional_requirements", []),
                non_functional_requirements=requirements.get("non_functional_requirements", []),
                scenario=body.scenario,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Agent 2 failed: {e}")

    return {
        "scenario": body.scenario,
        "requirements": requirements,
        "compliance": compliance_result,
        "quality": quality_result,
        "sdlc_recommendation": sdlc_result,
        "meta": _meta(used),
    }


# Serve the demo web UI
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_ui():
    return FileResponse(STATIC_DIR / "index.html")
