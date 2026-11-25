"""
FastAPI entry point.
Run with: uvicorn app.main:app --reload
Then open http://127.0.0.1:8000/docs for the interactive API,
or http://127.0.0.1:8000/ for the demo web UI.
"""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.gemini_client import generate_text
from app.config import GEMINI_MODEL, STATIC_DIR
from app.agents.requirement_agent import extract_requirements
from app.agents.compliance_agent import analyze_compliance
from app.agents.sdlc_agent import recommend_sdlc

app = FastAPI(title="SE Project - Requirement & SDLC Agent System")


class ScenarioRequest(BaseModel):
    scenario: str


class RequirementItem(BaseModel):
    id: str
    requirement: str
    priority: str | None = None


class SdlcRequest(BaseModel):
    domain: str
    functional_requirements: list[RequirementItem]
    non_functional_requirements: list[RequirementItem]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/test-gemini")
def test_gemini():
    """Wiring check - confirms the API key/SDK/model setup works end-to-end."""
    reply = generate_text("Reply with exactly one word: Working")
    return {"gemini_says": reply}


@app.post("/agents/requirements")
def run_requirement_agent(body: ScenarioRequest):
    """Agent 1 alone - scenario in, requirements out. Stage 1 of the UI flow."""
    if not body.scenario or len(body.scenario.strip()) < 5:
        raise HTTPException(status_code=400, detail="Scenario description is too short.")
    try:
        result = extract_requirements(body.scenario)
        result["meta"] = {"model": GEMINI_MODEL}
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Agent 1 failed: {e}")


@app.post("/agents/compliance")
def run_compliance_agent(body: SdlcRequest):
    """
    Agent 3 - RAG-grounded compliance/risk analysis. Takes the same
    requirement list shape as Agent 2's endpoint. Stage 2 of the UI flow
    (between requirement generation and SDLC recommendation) - retrieves
    from the curated knowledge base and cites its sources honestly,
    including saying when the knowledge base doesn't cover a domain well.
    """
    try:
        result = analyze_compliance(
            domain=body.domain,
            functional_requirements=[r.model_dump() for r in body.functional_requirements],
            non_functional_requirements=[r.model_dump() for r in body.non_functional_requirements],
        )
        result["meta"] = {"model": GEMINI_MODEL}
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
    try:
        result = recommend_sdlc(
            domain=body.domain,
            functional_requirements=[r.model_dump() for r in body.functional_requirements],
            non_functional_requirements=[r.model_dump() for r in body.non_functional_requirements],
        )
        result["meta"] = {"model": GEMINI_MODEL}
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
    """
    if not body.scenario or len(body.scenario.strip()) < 5:
        raise HTTPException(status_code=400, detail="Scenario description is too short.")

    try:
        requirements = extract_requirements(body.scenario)
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

    try:
        sdlc_result = recommend_sdlc(
            domain=requirements.get("domain_inferred", "Unknown"),
            functional_requirements=requirements.get("functional_requirements", []),
            non_functional_requirements=requirements.get("non_functional_requirements", []),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Agent 2 failed: {e}")

    return {
        "scenario": body.scenario,
        "requirements": requirements,
        "compliance": compliance_result,
        "sdlc_recommendation": sdlc_result,
    }


# Serve the demo web UI
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_ui():
    return FileResponse(STATIC_DIR / "index.html")