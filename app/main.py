"""
FastAPI entry point.
Run with: python -m uvicorn app.main:app --reload --port 8001 (or run.bat)
Then open http://127.0.0.1:8001/docs for the interactive API,
or http://127.0.0.1:8001/ for the web UI.

Wiring check for the Gemini key and model: python -m app.gemini_client
(the old unauthenticated /test-gemini route was removed - anyone who could
reach the server could spend the API quota with it).
"""
import json
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.interview_agent import DEFAULT_MAX_QUESTIONS, next_questions
from app.agents.interview_templates import TEMPLATES
from app.gemini_client import track_calls, track_models
from app.vocabulary import INTERVIEW_TOPICS, STAKEHOLDER_ROLES
from app import config
from app.config import STATIC_DIR
from app.agents.requirement_agent import extract_requirements
from app.agents.compliance_agent import analyze_compliance, attach_regulations
from app.agents.quality_agent import analyze_quality
from app.agents.risk_agent import build_risk_register
from app.agents.workflow_agent import generate_workflow
from app.agents.sdlc_agent import recommend_sdlc
from app.agents.artefact_agent import generate_artefacts
from app.artefacts import GENERATED_KINDS, build_code_artefacts
from app.srs import build_blocks, render_html, render_markdown
from app import approvals, roles
from app.audit import append_event, query_events, sha256_text, verify_chain
from app.input_guard import DocumentRejected, Guard, InjectionBlocked
from app.rag.embeddings import EMBEDDING_MODEL
from app.rag.kb_metadata import kb_version
from app.db import connect
from app.runs import current_items, decisions as run_decisions, get_run, new_run_id, save_run, with_current_state

@asynccontextmanager
async def lifespan(app):
    with connect():  # creates data/app.db and its tables on startup
        pass
    yield


app = FastAPI(title="Requora AI - Requirements Engineering & SDLC Recommendation API", lifespan=lifespan)

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


class DocumentInput(BaseModel):
    """An uploaded text document (section 3): plain JSON, no multipart. .txt, .md or .csv; checked in app/input_guard.py."""
    filename: str
    text: str


class ScenarioRequest(BaseModel):
    scenario: str
    # Optional interview answers collected so far; requirements can quote them.
    answers: list[InterviewAnswer] = []
    # Optional uploaded documents (additive); requirements can quote them by file name.
    documents: list[DocumentInput] = []


class InterviewRequest(ScenarioRequest):
    max_questions: int = Field(DEFAULT_MAX_QUESTIONS, ge=1, le=10)


class RequirementItem(BaseModel):
    id: str
    requirement: str
    priority: str | None = None
    # Optional (additive): used by the quality check's untestable rule.
    acceptance_criteria: list[str] = []
    categories: list[str] = []


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
    return {"model": ", ".join(distinct) if distinct else None, "models_used": list(models_used),
            **_versions(distinct)}


def _versions(models: list[str] | None = None) -> dict:
    """Section 17 model and knowledge-base versioning: recorded with every response, run and audit event."""
    out = {"app_version": config.APP_VERSION, "kb_version": kb_version(), "embedding_model": EMBEDDING_MODEL}
    if models is not None:
        out["models"] = list(models)
    return out


@app.get("/health")
def health():
    return {"status": "ok"}


# --- roles, errors and audit helpers ----------------------------------------------

def role_header(x_role: str | None = Header(None, alias="X-Role")) -> str:
    """The caller's role from the X-Role header (a stand-in for real authentication; see app/roles.py)."""
    try:
        role = roles.parse_role(x_role)
    except roles.RoleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if role == roles.UNSPECIFIED and config.require_role():
        raise HTTPException(status_code=400, detail="An X-Role header is required (REQUIRE_ROLE is on). Use one of: "
                                                    + ", ".join(roles.ROLES) + ".")
    return role


@app.exception_handler(roles.PermissionDenied)
def _denied(request, exc):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(approvals.NotFound)
def _not_found(request, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(approvals.Conflict)
def _conflict(request, exc):
    detail = exc.args[0] if exc.args else "Conflict"
    return JSONResponse(status_code=409, content={"detail": detail})


@app.exception_handler(InjectionBlocked)
def _injection(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc), "field": exc.field, "pattern": exc.pattern})


@app.exception_handler(DocumentRejected)
def _document(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(approvals.RegenerationFailed)
def _regen_failed(request, exc):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(approvals.InvalidInput)
def _invalid(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _audit_stage(stage: str, role: str, calls: list[dict], output, guard: Guard, run_id: str | None = None,
                 extra: dict | None = None):
    """One audit event per stage run: prompt hashes, models, retrieved chunk ids (if any) and the masked output."""
    append_event("stage_run", role, run_id=run_id, stage=stage,
                 model=", ".join(dict.fromkeys(c["model"] for c in calls)) or None,
                 details={"prompts": calls, "output": guard.output(output), **(extra or {})})


def _chunk_ids(compliance_result: dict) -> list[str]:
    return [c["id"] for c in compliance_result.get("retrieved_chunks", [])]


def _single_stage(stage: str, role: str, error_prefix: str, fn, guard: Guard, extra=None):
    """Runs one stage for a single-stage endpoint (inputs already masked by `guard`), with meta and an audit event."""
    try:
        with track_models() as used, track_calls() as calls:
            result = fn()
    except Exception as e:
        append_event("stage_failed", role, stage=stage, details={"error": guard.output(str(e))[:2000]})
        raise HTTPException(status_code=502, detail=f"{error_prefix}: {e}")
    _audit_stage(stage, role, calls, result, guard, extra=extra(result) if extra else None)
    result = guard.output(result)
    result["meta"] = _meta(used)
    result["masking"] = guard.report()
    return result


def _guarded_body(body: "SdlcRequest", guard: Guard) -> tuple[str, list[dict], list[dict], str | None]:
    """Masks and filters a requirement-list request body."""
    return (guard.text("domain", body.domain),
            guard.obj("functional_requirements", [r.model_dump() for r in body.functional_requirements]),
            guard.obj("non_functional_requirements", [r.model_dump() for r in body.non_functional_requirements]),
            guard.text("scenario", body.scenario))


@app.post("/agents/requirements")
def run_requirement_agent(body: ScenarioRequest, role: str = Depends(role_header)):
    """Agent 1 alone - scenario (+ optional interview answers) in, requirement records out. Stage 1 of the UI flow."""
    roles.require("run_pipeline", role, allow_unspecified=True)
    _check_scenario(body.scenario)
    guard = Guard(role, endpoint="/agents/requirements")
    scenario = guard.text("scenario", body.scenario)
    answers = guard.answers(_check_answers(body.answers))
    documents = guard.documents([d.model_dump() for d in body.documents])
    return _single_stage("requirements", role, "Agent 1 failed",
                         lambda: extract_requirements(scenario, answers, documents), guard)


@app.get("/agents/interview/templates")
def interview_templates():
    """The hand-written, role-specific question templates (no LLM call)."""
    return {"templates": TEMPLATES, "roles": list(STAKEHOLDER_ROLES), "topics": list(INTERVIEW_TOPICS)}


@app.post("/agents/interview")
def run_interview_agent(body: InterviewRequest, role: str = Depends(role_header)):
    """
    Interview step - scenario + answers so far in, next follow-up questions out
    (one LLM call). Covered/uncovered topics are computed from the answers.
    """
    roles.require("run_pipeline", role, allow_unspecified=True)
    _check_scenario(body.scenario)
    guard = Guard(role, endpoint="/agents/interview")
    scenario = guard.text("scenario", body.scenario)
    answers = guard.answers(_check_answers(body.answers))
    return _single_stage("interview", role, "Interview agent failed",
                         lambda: next_questions(scenario, answers, body.max_questions), guard)


@app.post("/agents/quality")
def run_quality_agent(body: SdlcRequest, role: str = Depends(role_header)):
    """
    Quality and conflict checks over a requirement list: ambiguity, untestable
    NFRs and near-duplicates by deterministic rules, conflicts by one LLM call.
    """
    roles.require("run_pipeline", role, allow_unspecified=True)
    _check_requirement_body(body)
    guard = Guard(role, endpoint="/agents/quality")
    domain, frs, nfrs, _ = _guarded_body(body, guard)
    return _single_stage("quality", role, "Quality check failed", lambda: analyze_quality(
        domain=domain, functional_requirements=frs, non_functional_requirements=nfrs), guard)


@app.post("/agents/compliance")
def run_compliance_agent(body: SdlcRequest, role: str = Depends(role_header)):
    """
    Agent 3 - RAG-grounded compliance/risk analysis. Takes the same
    requirement list shape as Agent 2's endpoint. Stage 2 of the UI flow
    (between requirement generation and SDLC recommendation) - retrieves
    from the curated knowledge base and cites its sources honestly,
    including saying when the knowledge base doesn't cover a domain well.
    """
    roles.require("run_pipeline", role, allow_unspecified=True)
    _check_requirement_body(body)
    guard = Guard(role, endpoint="/agents/compliance")
    domain, frs, nfrs, scenario = _guarded_body(body, guard)
    return _single_stage("compliance", role, "Agent 3 (Compliance) failed", lambda: analyze_compliance(
        domain=domain, functional_requirements=frs, non_functional_requirements=nfrs, scenario=scenario), guard,
        extra=lambda r: {"retrieved_chunk_ids": _chunk_ids(r), "embedding_model": EMBEDDING_MODEL})


@app.post("/agents/sdlc")
def run_sdlc_agent(body: SdlcRequest, role: str = Depends(role_header)):
    """
    Agent 2 alone - takes an already-generated requirement list and
    recommends an SDLC model. Stage 3 of the UI flow, called separately
    from Stage 1 so the frontend can show a genuine multi-step workflow
    rather than one combined black-box call.
    """
    roles.require("run_pipeline", role, allow_unspecified=True)
    _check_requirement_body(body)
    guard = Guard(role, endpoint="/agents/sdlc")
    domain, frs, nfrs, scenario = _guarded_body(body, guard)
    return _single_stage("sdlc", role, "Agent 2 failed", lambda: recommend_sdlc(
        domain=domain, functional_requirements=frs, non_functional_requirements=nfrs, scenario=scenario), guard)


@app.post("/agents/pipeline")
def run_full_pipeline(body: ScenarioRequest, artefacts: bool = Query(True, description="false skips the artefact LLM call"),
                      role: str = Depends(role_header)):
    """
    Convenience endpoint: all agents in one call. Kept for API testing
    via /docs - the web UI itself calls /agents/requirements,
    /agents/compliance, and /agents/sdlc separately for a real
    multi-stage workflow.

    Order: requirements -> compliance + per-requirement compliance mapping
    (then each requirement's applicable_regulations is filled from the
    domain-specific sources cited for it) -> quality checks -> risk register
    -> SDLC recommendation -> workflow for the recommended model -> section 12
    artefacts (1 LLM call, 2 for more than 20 requirements; skipped with
    artefacts=false) and code-built matrices/registers. 8 LLM calls.

    Every stage is recorded in the audit log as it completes. The finished run
    is stored in SQLite (run_id) with its approval items, so it, its approvals
    and its SRS survive a restart.
    """
    roles.require("run_pipeline", role, allow_unspecified=True)
    _check_scenario(body.scenario)
    run_id = new_run_id()
    guard = Guard(role, run_id=run_id, endpoint="/agents/pipeline")
    scenario = guard.text("scenario", body.scenario)
    answers = guard.answers(_check_answers(body.answers))
    documents = guard.documents([d.model_dump() for d in body.documents])
    append_event("pipeline_started", role, run_id=run_id,
                 details={"scenario_sha256": sha256_text(scenario), "scenario_chars": len(scenario),
                          "answers": len(answers), "documents": [d["filename"] for d in documents],
                          "artefacts": artefacts, "masking": guard.report(), **_versions()})

    with track_models() as used, track_calls() as calls:
        def stage(name, error_prefix, fn, extra=None):
            before = len(calls)
            try:
                out = fn()
            except Exception as e:
                append_event("pipeline_failed", role, run_id=run_id, stage=name,
                             details={"error": guard.output(str(e))[:2000]})
                raise HTTPException(status_code=502, detail=f"{error_prefix}: {e}")
            _audit_stage(name, role, calls[before:], out, guard, run_id=run_id, extra=extra(out) if extra else None)
            return out

        requirements = stage("requirements", "Agent 1 failed", lambda: extract_requirements(scenario, answers, documents))
        domain = requirements.get("domain_inferred", "Unknown")
        all_reqs = requirements.get("functional_requirements", []) + requirements.get("non_functional_requirements", [])
        compliance_result = stage("compliance", "Agent 3 (Compliance) failed", lambda: analyze_compliance(
            domain=domain, functional_requirements=requirements.get("functional_requirements", []),
            non_functional_requirements=requirements.get("non_functional_requirements", []), scenario=scenario),
            extra=lambda r: {"retrieved_chunk_ids": _chunk_ids(r), "embedding_model": EMBEDDING_MODEL})
        attach_regulations(requirements, compliance_result)
        compliance_mapping = compliance_result.pop("compliance_mapping")
        quality_result = stage("quality", "Quality check failed", lambda: analyze_quality(
            domain=domain, functional_requirements=requirements.get("functional_requirements", []),
            non_functional_requirements=requirements.get("non_functional_requirements", [])))
        risk_register = stage("risk_register", "Risk register failed", lambda: build_risk_register(
            domain=domain, requirements=all_reqs, scenario=scenario,
            compliance_mapping=compliance_mapping, quality=quality_result))
        sdlc_result = stage("sdlc", "Agent 2 failed", lambda: recommend_sdlc(
            domain=domain, functional_requirements=requirements.get("functional_requirements", []),
            non_functional_requirements=requirements.get("non_functional_requirements", []), scenario=scenario))
        sdlc_workflow = stage("workflow", "Workflow generator failed", lambda: generate_workflow(
            model=sdlc_result["recommended_model"], domain=domain, requirements=all_reqs,
            compliance_mapping=compliance_mapping, risk_register=risk_register))
        if artefacts:
            generated = stage("artefacts", "Artefact generator failed", lambda: generate_artefacts(requirements, scenario))
        else:
            generated = {**{k: [] for k in GENERATED_KINDS},
                         "generation": {"llm_calls": 0, "split": False, "skipped": True,
                                        "note": "Skipped (artefacts=false): no stories, use cases, workflows, data or "
                                                "interface requirements were generated."}}

    generation = generated.pop("generation")
    code_built = build_code_artefacts(requirements, compliance_mapping, risk_register, quality_result,
                                      sdlc_result, sdlc_workflow, generated)
    result = {
        "scenario": scenario,
        "answers": answers,
        "documents": documents,
        "requirements": requirements,
        "compliance": compliance_result,
        "compliance_mapping": compliance_mapping,
        "quality": quality_result,
        "risk_register": risk_register,
        "sdlc_recommendation": sdlc_result,
        "sdlc_workflow": sdlc_workflow,
        "artefacts": {**generated, **code_built, "generation": generation},
        "meta": _meta(used),
    }
    # Belt and braces: model outputs were already checked for unmasked data; mask again before anything is stored.
    result = guard.output(result)
    result["masking"] = guard.report()
    result["versions"] = _versions(list(dict.fromkeys(used)))
    for kind in (*GENERATED_KINDS, *code_built):
        value = result["artefacts"][kind]
        append_event("artefact_generated", role, run_id=run_id, details={
            "artefact": kind, "by": "llm" if kind in GENERATED_KINDS else "code",
            "ids": [x.get("id") for x in value if isinstance(x, dict) and x.get("id")] if isinstance(value, list) else [],
            "sha256": sha256_text(json.dumps(value, sort_keys=True, ensure_ascii=False))})
    save_run(run_id, result, role)
    append_event("pipeline_completed", role, run_id=run_id, details={"llm_calls": len(calls)})
    result["run_id"] = run_id
    result["srs"] = {
        "id": run_id,
        "markdown_url": f"/agents/srs/{run_id}?format=markdown",
        "html_url": f"/agents/srs/{run_id}?format=html",
        "note": "Stored in the local database with the run; the SRS shows the current approval state.",
    }
    return result


# --- SRS download (no LLM call) -------------------------------------------------

SrsFormat = Literal["markdown", "html"]


def _srs_response(result: dict, fmt: str, name: str):
    blocks = build_blocks(result)
    if fmt == "html":
        return HTMLResponse(render_html(blocks))
    return Response(render_markdown(blocks), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="requora-srs-{name}.md"'})


def _stored_srs(run_id: str, fmt: str, role: str):
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run id. Run /agents/pipeline again, "
                                                    "or POST a saved pipeline result to /agents/srs.")
    append_event("srs_downloaded", role, run_id=run_id, details={"format": fmt})
    return _srs_response(with_current_state(run), fmt, run_id)


@app.get("/agents/srs/{srs_id}")
def get_srs(srs_id: str, format: SrsFormat = Query("markdown"), role: str = Depends(role_header)):
    """The SRS of a stored /agents/pipeline run (with its current approval state), as Markdown or print-friendly HTML."""
    return _stored_srs(srs_id, format, role)


@app.post("/agents/srs")
def post_srs(result: dict = Body(...), format: SrsFormat = Query("markdown"), role: str = Depends(role_header)):
    """Builds the SRS from a /agents/pipeline result sent in the body (no LLM call)."""
    req = result.get("requirements")
    if not isinstance(req, dict) or not all(isinstance(req.get(k), list) for k in
                                            ("functional_requirements", "non_functional_requirements")):
        raise HTTPException(status_code=400, detail="Body must be a /agents/pipeline result with "
                                                    "requirements.functional_requirements and non_functional_requirements.")
    try:
        return _srs_response(result, format, "document")
    except (KeyError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"Pipeline result is missing or has malformed fields: {e}")


# --- runs, approvals and baseline (section 16) ----------------------------------

class ApprovalRequest(BaseModel):
    item_type: Literal["requirement", "compliance", "sdlc"]
    item_id: str
    action: Literal["accept", "reject", "modify", "regenerate"]
    comment: str = ""
    content: dict | None = None   # changed fields, for "modify"


def _run_or_404(run_id: str) -> dict:
    run = get_run(run_id)
    if run is None:
        raise approvals.NotFound(f"Unknown run {run_id}.")
    return run


@app.get("/runs/{run_id}")
def get_run_summary(run_id: str, role: str = Depends(role_header)):
    """A stored run: versions, baseline state and the current state of every approval item (any role)."""
    run = _run_or_404(run_id)
    items = [approvals.state(run_id, it["item_type"], it["item_id"]) for it in current_items(run_id)]
    return {"run_id": run_id, "created_at": run["created_at"], "created_by_role": run["role"],
            "app_version": run["app_version"], "kb_version": run["kb_version"],
            "baseline": {"status": run["baseline_status"], "frozen_at": run["baseline_frozen_at"],
                         "frozen_by": run["baseline_frozen_by"]},
            "items": items, "blockers": approvals.blockers(run_id), "decisions": run_decisions(run_id)}


@app.get("/runs/{run_id}/result")
def get_run_result(run_id: str, role: str = Depends(role_header)):
    """The stored pipeline result with the current approval state applied."""
    return with_current_state(_run_or_404(run_id))


@app.post("/runs/{run_id}/approvals")
def post_approval(run_id: str, body: ApprovalRequest, role: str = Depends(role_header)):
    """Accept, reject, modify or regenerate one item. Needs the role the permission table names for that item."""
    if role == roles.AUDITOR:
        raise roles.PermissionDenied("The auditor role is read-only.")
    guard = Guard(role, run_id=run_id, endpoint="/runs/approvals")
    comment = guard.text("comment", body.comment)
    content = guard.obj("content", body.content) if body.content else body.content
    out = approvals.act(run_id, body.item_type, body.item_id, body.action, role, comment, content)
    out["masking"] = guard.report()
    return out


@app.post("/runs/{run_id}/baseline/freeze")
def post_freeze(run_id: str, role: str = Depends(role_header)):
    """Freezes the requirement baseline; 409 with the blocking items unless every mandatory item is approved."""
    return approvals.freeze_baseline(run_id, role)


# --- audit log (section 20); read-only by design ---------------------------------

@app.get("/audit")
def get_audit(run_id: str | None = None, role_filter: str | None = Query(None, alias="role"),
              action: str | None = None, limit: int = Query(500, ge=1, le=5000), role: str = Depends(role_header)):
    """
    Audit events filtered by run, role and action, plus a verification of the
    whole hash chain. Auditor and compliance officer only. There are no update or delete routes.
    """
    roles.require("read_audit", role)
    events = query_events(run_id=run_id, role=role_filter, action=action, limit=limit)
    chain = verify_chain()
    append_event("audit_viewed", role, run_id=run_id, details={"filters": {"run_id": run_id, "role": role_filter,
                                                                            "action": action}, "returned": len(events)})
    return {"events": events, "chain": chain}


# Serve the demo web UI
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_ui():
    return FileResponse(STATIC_DIR / "index.html")
