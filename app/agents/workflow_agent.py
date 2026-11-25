"""
Project-specific SDLC workflow (problem statement section 15).

The base phases and items for the selected model come from plain data
(sdlc_workflows.BASE_WORKFLOWS). One LLM call may only ADD scenario-specific
items to existing phases - for example threat modelling in design or
transaction-integrity testing in validation - each linked to requirement ids
where possible. The schema rejects any phase name that is not a base phase,
so phases cannot be renamed or invented, and the output format has no way to
remove anything. Validated by WorkflowAdditions (retry once, then 502).

Every item in the result is {"text", "source": "base" | "scenario",
"requirement_ids"}; scenario items also carry the model's "reason".
"""
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from app.agents.sdlc_workflows import BASE_WORKFLOWS, WORKFLOW_FIELDS
from app.gemini_client import generate_text
from app.schemas import _NonEmptyText, _ctx, _normalise, generate_validated

MAX_ADDITIONS = 25


class Addition(_NonEmptyText):
    phase: str
    field: Literal[WORKFLOW_FIELDS]
    item: str = Field(min_length=1)
    requirement_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @field_validator("phase")
    @classmethod
    def _existing_phase(cls, v, info: ValidationInfo):
        phases = _ctx(info, "phases")
        if phases is not None and v not in phases:
            raise ValueError(f"phase {v!r} is not a base phase; use one of {phases}")
        return v

    @field_validator("requirement_ids")
    @classmethod
    def _known_ids(cls, ids, info: ValidationInfo):
        known = _ctx(info, "requirement_ids")
        unknown = [i for i in ids if known is not None and i not in known]
        if unknown:
            raise ValueError(f"unknown requirement ids {unknown}")
        return list(dict.fromkeys(ids))


class WorkflowAdditions(BaseModel):
    additions: list[Addition] = Field(default_factory=list, max_length=MAX_ADDITIONS)

    @model_validator(mode="after")
    def _new_and_unique(self, info: ValidationInfo):
        base = _ctx(info, "base_items") or {}
        seen = set()
        for a in self.additions:
            key = (a.phase, a.field, _normalise(a.item))
            if key in seen:
                raise ValueError(f"repeated addition {a.item!r} in {a.phase}/{a.field}")
            if _normalise(a.item) in base.get((a.phase, a.field), set()):
                raise ValueError(f"{a.item!r} is already a base item of {a.phase}/{a.field}")
            seen.add(key)
        return self


PROMPT_TEMPLATE = """You are tailoring the {model} software development workflow to one project.

Domain: {domain}

Requirements (id [categories]: statement):
{requirements}

Compliance findings (gaps, checkpoints, retention and reporting obligations):
{compliance}

Top risks from the risk register:
{risks}

Base workflow for {model} (fixed; you cannot remove or rename anything):
{base}

Add only scenario-specific items that this project needs and the base workflow does not already cover, for example threat modelling during design, static security testing during implementation, transaction-integrity testing during validation, or compliance approval before deployment. For each addition give:
- "phase": a phase name copied exactly from the base workflow;
- "field": one of {fields};
- "item": one short, concrete sentence;
- "requirement_ids": the requirement ids it serves (from the list above; [] only if it serves none directly);
- "reason": why this project needs it.
Add at most {max_additions} items. Do not repeat base items. Do not name laws or standards that the input does not mention. An empty list is allowed if nothing is needed.

Respond with ONLY valid JSON, no markdown fences, in exactly this schema:
{{"additions": [{{"phase": "...", "field": "...", "item": "...", "requirement_ids": ["FR-1"], "reason": "..."}}]}}
"""


def _base_listing(phases: list[dict]) -> str:
    lines = []
    for i, p in enumerate(phases, start=1):
        lines.append(f"{i}. {p['name']}")
        for f in WORKFLOW_FIELDS:
            if p[f]:
                lines.append(f"   - {f}: {'; '.join(p[f])}")
    return "\n".join(lines)


def _compliance_lines(mapping: dict | None) -> str:
    lines = []
    for e in (mapping or {}).get("requirements", []):
        for it in e["mappings"]:
            if it["status"] == "gap":
                lines.append(f"- {e['requirement_id']}: gap against {it['source']}: {it['compliance_gap']}")
            for c in it.get("checkpoints", []):
                lines.append(f"- {e['requirement_id']}: checkpoint: {c}")
            for c in it.get("retention_and_reporting", []):
                lines.append(f"- {e['requirement_id']}: retention/reporting: {c}")
    return "\n".join(dict.fromkeys(lines)) or "(none)"


def _risk_lines(register: dict | None, top: int = 5) -> str:
    return "\n".join(f"- {r['id']} ({r['level']}, {r['category']}): {r['description']} [{', '.join(r['linked_requirement_ids'])}]"
                     for r in (register or {}).get("risks", [])[:top]) or "(none)"


def generate_workflow(model: str, domain: str, requirements: list[dict],
                      compliance_mapping: dict | None = None, risk_register: dict | None = None) -> dict:
    base_phases = BASE_WORKFLOWS[model]
    names = [p["name"] for p in base_phases]
    base_items = {(p["name"], f): {_normalise(x) for x in p[f]} for p in base_phases for f in WORKFLOW_FIELDS}
    prompt = PROMPT_TEMPLATE.format(
        model=model, domain=domain,
        requirements="\n".join(f"- {r['id']} [{', '.join(r.get('categories') or [])}]: {r['requirement']}" for r in requirements),
        compliance=_compliance_lines(compliance_mapping), risks=_risk_lines(risk_register),
        base=_base_listing(base_phases), fields=", ".join(WORKFLOW_FIELDS), max_additions=MAX_ADDITIONS,
    )
    out = generate_validated(
        prompt, WorkflowAdditions, generate=generate_text, temperature=0.2,
        context={"phases": names, "requirement_ids": {r["id"] for r in requirements}, "base_items": base_items},
    )
    phases = []
    for order, p in enumerate(base_phases, start=1):
        row = {"order": order, "name": p["name"]}
        for f in WORKFLOW_FIELDS:
            row[f] = [{"text": x, "source": "base", "requirement_ids": []} for x in p[f]]
        phases.append(row)
    by_name = {p["name"]: p for p in phases}
    for a in out.additions:
        by_name[a.phase][a.field].append({"text": a.item, "source": "scenario",
                                          "requirement_ids": a.requirement_ids, "reason": a.reason})
    linked = sorted({rid for a in out.additions for rid in a.requirement_ids})
    return {
        "model": model,
        "phases": phases,
        "added_items": len(out.additions),
        "requirements_linked": linked,
        "notice": (f"Base phases and items are the fixed {model} template; items with source \"scenario\" were "
                   "added for this project by the LLM and cannot remove or rename anything. The model choice "
                   "itself still needs the approvals listed in sdlc_recommendation.approval_required."),
    }
