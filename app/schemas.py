"""
Pydantic schemas for every LLM output, plus generate_validated(), which asks
the model, validates the reply and retries once with the validation errors
before giving up.

Checks that need request-specific facts (which requirement IDs exist, which
knowledge-base sources exist, which texts a requirement may quote) read them
from the validation context:
    context={"requirement_ids": {...}, "kb_sources": {...},
             "source_texts": [{"text": str, "role": str | None}, ...]}

Existing response keys are never renamed or removed; new keys are additive.
"""
import contextvars
import re
from contextlib import contextmanager
from typing import Callable, Literal

from pydantic import BaseModel, Field, StrictInt, ValidationError, ValidationInfo, field_validator, model_validator

from app.json_utils import parse_json_response
from app.prompt_safety import output_problem
from app.vocabulary import (
    APPROVAL_STATUSES, LEVELS, REQUIREMENT_CATEGORIES, STAKEHOLDER_ROLES, canonical,
)

_REQ_ID_IN_TEXT = re.compile(r"\b(?:FR|NFR)-\d+\b")


def _ctx(info: ValidationInfo, key: str):
    return (info.context or {}).get(key)


class _NonEmptyText(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, v):
        return v.strip() if isinstance(v, str) else v


# --- Agent 1: requirements -------------------------------------------------
#
# The requirement record follows docs/PROBLEM_STATEMENT.md section 8 (ID,
# statement, category, source stakeholder, business justification, priority,
# dependencies, assumptions, acceptance criteria, applicable regulations,
# risk level, confidence score, approval status), with section 9's
# categories (multi-label) and section 12's link to the original stakeholder
# statement (source_text). Fields marked "set by code" ignore whatever the
# LLM sends: the LLM cannot approve its own output, cite regulations (those
# come from the compliance agent's retrieval) or grade its own confidence.

def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9%]+", " ", text.lower()).split())


MIN_SOURCE_WORDS = 2


class Requirement(_NonEmptyText):
    id: str
    requirement: str = Field(min_length=1)
    priority: Literal[LEVELS]
    type: Literal["FR", "NFR"] | None = None                      # set by code from the list it is in
    categories: list[Literal[REQUIREMENT_CATEGORIES]] = Field(min_length=1)
    source_stakeholder: Literal[STAKEHOLDER_ROLES]
    source_text: str = ""                                          # exact quote it derives from, or "" if assumed
    source_document: str | None = None                             # set by code: uploaded file the quote is from
    business_justification: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)          # other requirement ids
    assumptions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    applicable_regulations: list[str] = Field(default_factory=list)  # set by code (compliance agent)
    risk_level: Literal[LEVELS]
    confidence: Literal[LEVELS] | None = None                      # set by code, see _confidence
    approval_status: Literal[APPROVAL_STATUSES] = "proposed"       # set by code

    @field_validator("priority", "risk_level", mode="before")
    @classmethod
    def _normalise_case(cls, v):
        return v.strip().capitalize() if isinstance(v, str) else v

    @field_validator("categories", mode="before")
    @classmethod
    def _canonical_categories(cls, v):
        return [canonical(x, REQUIREMENT_CATEGORIES) for x in v] if isinstance(v, list) else v

    @field_validator("source_stakeholder", mode="before")
    @classmethod
    def _canonical_role(cls, v):
        return canonical(v, STAKEHOLDER_ROLES)

    @field_validator("categories")
    @classmethod
    def _unique_categories(cls, v):
        return list(dict.fromkeys(v))

    @field_validator("acceptance_criteria", "assumptions")
    @classmethod
    def _no_blank_items(cls, v):
        if any(not str(x).strip() for x in v):
            raise ValueError("items must not be blank")
        return [str(x).strip() for x in v]


def _confidence(req: Requirement) -> str:
    """
    Deterministic, not model-reported (no invented confidence figures):
    High   - quotes the scenario/answers and needs no assumptions
    Medium - quotes the scenario/answers but also relies on assumptions
    Low    - no quote; derived from assumptions only
    """
    if not req.source_text:
        return "Low"
    return "Medium" if req.assumptions else "High"


class RequirementsOutput(_NonEmptyText):
    domain_inferred: str = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    functional_requirements: list[Requirement] = Field(min_length=1)
    non_functional_requirements: list[Requirement] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_and_complete_records(self, info: ValidationInfo):
        for prefix, reqs in (("FR", self.functional_requirements), ("NFR", self.non_functional_requirements)):
            for r in reqs:
                if not re.fullmatch(rf"{prefix}-\d+", r.id):
                    raise ValueError(f"requirement id {r.id!r} must look like {prefix}-<number>")
        all_reqs = self.functional_requirements + self.non_functional_requirements
        ids = [r.id for r in all_reqs]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate requirement ids: {dupes}")

        sources = _ctx(info, "source_texts")
        for prefix, reqs in (("FR", self.functional_requirements), ("NFR", self.non_functional_requirements)):
            for r in reqs:
                complete_requirement(r, prefix, ids, sources)
        return self


def complete_requirement(r: Requirement, prefix: str, ids: list[str], sources: list[dict] | None,
                         status: str = "proposed") -> Requirement:
    """
    Checks one record against the rest of the list and the input texts, then
    sets the code-owned fields. Shared by Agent 1, reviewer edits and
    regeneration (app/approvals.py), so every version passes the same rules.
    """
    bad_deps = [d for d in r.dependencies if d not in ids or d == r.id]
    if bad_deps:
        raise ValueError(f"{r.id}: dependencies must be other requirement ids from this list, got {bad_deps}")
    document = None
    if r.source_text:
        if len(r.source_text.split()) < MIN_SOURCE_WORDS:
            raise ValueError(f"{r.id}: source_text must quote at least {MIN_SOURCE_WORDS} words")
        if sources is not None:
            quote = _normalise(r.source_text)
            origin = next((s for s in sources if quote in _normalise(s["text"])), None)
            if origin is None:
                raise ValueError(f"{r.id}: source_text is not an exact quote from the scenario, "
                                 f"interview answers or uploaded documents: {r.source_text!r}")
            if origin.get("role"):
                r.source_stakeholder = origin["role"]  # the person who actually said it
            document = origin.get("document")
    elif not r.assumptions:
        raise ValueError(f"{r.id}: give a source_text quote or list the assumptions it relies on")
    r.source_document = document  # only ever set from a verified quote, never taken from the model
    r.type = prefix
    r.confidence = _confidence(r)
    r.approval_status = status
    r.applicable_regulations = []
    return r


class RegeneratedRequirement(BaseModel):
    """One requirement rewritten after a reviewer's comment (approval action "regenerate")."""
    requirement: Requirement

    @model_validator(mode="after")
    def _same_id_and_grounded(self, info: ValidationInfo):
        target = _ctx(info, "requirement_id")
        if target and self.requirement.id != target:
            raise ValueError(f"keep the requirement id {target!r}")
        prefix = "NFR" if self.requirement.id.startswith("NFR") else "FR"
        complete_requirement(self.requirement, prefix, _ctx(info, "all_ids") or [self.requirement.id],
                             _ctx(info, "source_texts"))
        return self


# --- Agent 3: compliance ---------------------------------------------------

COMPLIANCE_STATUSES = ("satisfied", "gap", "not_applicable")


class Consideration(_NonEmptyText):
    """
    One compliance finding. `note` is the required control the source expects
    (problem statement section 11). The section-11 analysis fields are
    optional so a partial answer is not a hard failure: a missing status is
    reported as "not_assessed" and an unverifiable evidence quote lowers the
    confidence; both send the requirement to human review (compliance_mapping.py).
    """
    related_requirement_ids: list[str] = Field(min_length=1)
    note: str = Field(min_length=1)
    source: str = Field(min_length=1)
    status: Literal[COMPLIANCE_STATUSES] | None = None
    evidence: str = ""                       # exact quote from the retrieved source text
    compliance_gap: str | None = None        # required when status is "gap", else null
    checkpoints: list[str] = Field(default_factory=list)              # mandatory approval or audit checkpoints
    retention_and_reporting: list[str] = Field(default_factory=list)  # data-retention and reporting obligations

    @field_validator("status", mode="before")
    @classmethod
    def _status_case(cls, v):
        return v.strip().lower().replace(" ", "_").replace("-", "_") if isinstance(v, str) else v

    @field_validator("compliance_gap", mode="before")
    @classmethod
    def _blank_gap_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("checkpoints", "retention_and_reporting")
    @classmethod
    def _no_blank_items(cls, v):
        if any(not str(x).strip() for x in v):
            raise ValueError("items must not be blank")
        return [str(x).strip() for x in v]

    @model_validator(mode="after")
    def _gap_text_matches_status(self):
        if self.status == "gap" and not self.compliance_gap:
            raise ValueError("compliance_gap must describe the gap when status is 'gap'")
        if self.status != "gap" and self.compliance_gap:
            raise ValueError("compliance_gap must be null unless status is 'gap'")
        return self

    @field_validator("related_requirement_ids")
    @classmethod
    def _known_requirement_ids(cls, ids, info: ValidationInfo):
        known = _ctx(info, "requirement_ids")
        unknown = [i for i in ids if known is not None and i not in known]
        if unknown:
            raise ValueError(f"unknown requirement ids {unknown}")
        return ids

    @field_validator("source")
    @classmethod
    def _known_source(cls, source, info: ValidationInfo):
        known = _ctx(info, "kb_sources")
        if known is not None and source not in known:
            raise ValueError(f"unknown knowledge-base source {source!r}; allowed: {sorted(known)}")
        return source


class ComplianceOutput(_NonEmptyText):
    risk_level: Literal["Low", "Medium", "High"]
    considerations: list[Consideration] = Field(default_factory=list)
    summary: str = Field(min_length=1)


# --- Agent 2: SDLC trait ratings -------------------------------------------

class TraitRating(_NonEmptyText):
    # StrictInt: "4", 4.0, true and null are all rejected - the scorer needs
    # an integer on the rubric's 1-5 scale, and there is no safe default.
    score: StrictInt = Field(ge=1, le=5)
    evidence: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class TraitRatings(BaseModel):
    """All fifteen traits are required; a missing or invalid one is an error."""
    clarity: TraitRating
    volatility: TraitRating
    complexity: TraitRating
    criticality: TraitRating
    risk_uncertainty: TraitRating
    verification_validation: TraitRating
    stakeholder_feedback: TraitRating
    incremental_suitability: TraitRating
    prototyping_need: TraitRating
    security_risk: TraitRating
    system_size: TraitRating
    legacy_dependence: TraitRating
    continuous_delivery: TraitRating
    stakeholder_availability: TraitRating
    budget_schedule_constraint: TraitRating


# --- Agent 2: SDLC justification -------------------------------------------

class Justification(_NonEmptyText):
    requirement_characteristics: str = Field(min_length=1)
    project_characteristics: str = Field(min_length=1)
    engineering_justification: str = Field(min_length=1)
    key_tradeoffs: str = Field(min_length=1)

    @model_validator(mode="after")
    def _mentions_only_known_requirements(self, info: ValidationInfo):
        known = _ctx(info, "requirement_ids")
        if known is None:
            return self
        mentioned = {m for text in self.model_dump().values() for m in _REQ_ID_IN_TEXT.findall(text)}
        unknown = sorted(mentioned - set(known))
        if unknown:
            raise ValueError(f"justification mentions unknown requirement ids {unknown}")
        return self


# --- validate + retry ------------------------------------------------------

class LLMOutputError(RuntimeError):
    """The model's output failed validation on every attempt."""


MAX_ATTEMPTS = 2  # the first answer plus one retry

RETRY_NOTE = (
    "\n\nYour previous response was rejected because: {error}\n"
    "Respond again with ONLY the corrected JSON object, following every rule above."
)


class OutputRejected(ValueError):
    """Schema-valid output that failed the output safety check."""


def describe_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for e in exc.errors()[:5]:
            loc = ".".join(str(p) for p in e["loc"]) or "response"
            parts.append(f"{loc}: {e['msg']}")
        return "; ".join(parts)
    if isinstance(exc, OutputRejected):
        return str(exc)
    return "the response was not valid JSON"


_retry_log: contextvars.ContextVar[list | None] = contextvars.ContextVar("requora_retry_log", default=None)


@contextmanager
def track_retries():
    """
    Collects one record per rejected model answer inside the block:
    {"schema", "attempt", "reason"} - the validation reason text only, never the
    answer itself. main.py masks it and adds it to the stage's audit event.
    """
    log: list[dict] = []
    token = _retry_log.set(log)
    try:
        yield log
    finally:
        _retry_log.reset(token)


def generate_validated(
    prompt: str,
    schema: type[BaseModel],
    *,
    generate: Callable[..., str],
    temperature: float,
    context: dict | None = None,
) -> BaseModel:
    """
    Calls `generate(prompt, temperature=...)`, parses JSON and validates it
    against `schema`. On failure it retries once, telling the model what was
    wrong; if that also fails it raises LLMOutputError (the API turns that
    into a 502). `generate` is passed in so tests can mock each agent's call.
    """
    current, error = prompt, ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = generate(current, temperature=temperature)
        try:
            parsed = schema.model_validate(parse_json_response(raw), context=context)
            problem = output_problem(raw)   # secrets or unmasked sensitive data (app/prompt_safety.py)
            if problem:
                raise OutputRejected(problem)
            return parsed
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            error = describe_error(exc)
            log = _retry_log.get()
            if log is not None:
                log.append({"schema": schema.__name__, "attempt": attempt, "reason": error[:1000]})
            current = prompt + RETRY_NOTE.format(error=error)
    raise LLMOutputError(f"model output failed validation after {MAX_ATTEMPTS} attempts: {error}")
