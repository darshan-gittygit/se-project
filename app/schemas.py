"""
Pydantic schemas for every LLM output, plus generate_validated(), which asks
the model, validates the reply and retries once with the validation errors
before giving up.

Checks that need request-specific facts (which requirement IDs exist, which
knowledge-base sources exist) read them from the validation context:
    context={"requirement_ids": {...}, "kb_sources": {...}}

The response shapes these produce are exactly the keys the API already
returned; validation only rejects bad values, it never adds or renames keys.
"""
import re
from typing import Callable, Literal

from pydantic import BaseModel, Field, StrictInt, ValidationError, ValidationInfo, field_validator, model_validator

from app.json_utils import parse_json_response

_REQ_ID_IN_TEXT = re.compile(r"\b(?:FR|NFR)-\d+\b")


def _ctx(info: ValidationInfo, key: str):
    return (info.context or {}).get(key)


class _NonEmptyText(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, v):
        return v.strip() if isinstance(v, str) else v


# --- Agent 1: requirements -------------------------------------------------

class Requirement(_NonEmptyText):
    id: str
    requirement: str = Field(min_length=1)
    priority: Literal["High", "Medium", "Low"]

    @field_validator("priority", mode="before")
    @classmethod
    def _normalise_case(cls, v):
        return v.strip().capitalize() if isinstance(v, str) else v


class RequirementsOutput(_NonEmptyText):
    domain_inferred: str = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    functional_requirements: list[Requirement] = Field(min_length=1)
    non_functional_requirements: list[Requirement] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_are_well_formed_and_unique(self):
        for prefix, reqs in (("FR", self.functional_requirements), ("NFR", self.non_functional_requirements)):
            for r in reqs:
                if not re.fullmatch(rf"{prefix}-\d+", r.id):
                    raise ValueError(f"requirement id {r.id!r} must look like {prefix}-<number>")
        ids = [r.id for r in self.functional_requirements + self.non_functional_requirements]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate requirement ids: {dupes}")
        return self


# --- Agent 3: compliance ---------------------------------------------------

class Consideration(_NonEmptyText):
    related_requirement_ids: list[str] = Field(min_length=1)
    note: str = Field(min_length=1)
    source: str = Field(min_length=1)

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
    """All nine traits are required; a missing or invalid one is an error."""
    clarity: TraitRating
    volatility: TraitRating
    complexity: TraitRating
    criticality: TraitRating
    risk_uncertainty: TraitRating
    verification_validation: TraitRating
    stakeholder_feedback: TraitRating
    incremental_suitability: TraitRating
    prototyping_need: TraitRating


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


def describe_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for e in exc.errors()[:5]:
            loc = ".".join(str(p) for p in e["loc"]) or "response"
            parts.append(f"{loc}: {e['msg']}")
        return "; ".join(parts)
    return "the response was not valid JSON"


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
    for _ in range(MAX_ATTEMPTS):
        raw = generate(current, temperature=temperature)
        try:
            return schema.model_validate(parse_json_response(raw), context=context)
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            error = describe_error(exc)
            current = prompt + RETRY_NOTE.format(error=error)
    raise LLMOutputError(f"model output failed validation after {MAX_ATTEMPTS} attempts: {error}")
