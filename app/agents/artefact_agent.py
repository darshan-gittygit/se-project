"""
Generated requirement artefacts (problem statement section 12): user stories
with acceptance criteria, use-case descriptions, process workflows, data
requirements and interface requirements.

One LLM call for normal-sized inputs. With more than SPLIT_THRESHOLD
requirements the output could outgrow one response, so it is split into two
calls (stories + use cases; workflows + data + interfaces); the result's
"generation" field says which happened. Every call is schema-validated:
unknown fields are rejected (extra="forbid"), every item must link to
requirement ids that exist, and every functional requirement needs at least
one user story. Retry once, then LLMOutputError (502).

Code assigns the ids (US-1, UC-1, PW-1, DR-1, IR-1), builds the user-story
sentence, and attaches each item's source stakeholder statements
(section 12: "each generated item should remain linked to its original
stakeholder statement").
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.artefacts import all_requirements, source_statements
from app.gemini_client import generate_text
from app.prompt_safety import DATA_RULE, untrusted
from app.schemas import _NonEmptyText, _ctx, generate_validated

SPLIT_THRESHOLD = 20
ID_PREFIX = {"user_stories": "US", "use_cases": "UC", "process_workflows": "PW",
             "data_requirements": "DR", "interface_requirements": "IR"}
# Interface kinds from the classic SRS structure (IEEE 830 section 3.1.1).
INTERFACE_TYPES = ("User interface", "Hardware interface", "Software interface", "Communications interface")


def _clean_list(v):
    if any(not str(x).strip() for x in v):
        raise ValueError("items must not be blank")
    return [str(x).strip() for x in v]


class _Strict(_NonEmptyText):
    model_config = ConfigDict(extra="forbid")


class _Linked(_Strict):
    requirement_ids: list[str] = Field(min_length=1)

    @field_validator("requirement_ids")
    @classmethod
    def _known_ids(cls, ids, info: ValidationInfo):
        known = _ctx(info, "requirement_ids")
        unknown = [i for i in ids if known is not None and i not in known]
        if unknown:
            raise ValueError(f"unknown requirement ids {unknown}")
        return list(dict.fromkeys(ids))


class UserStory(_Linked):
    role: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    benefit: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    _ac = field_validator("acceptance_criteria")(classmethod(lambda cls, v: _clean_list(v)))


class UseCase(_Linked):
    name: str = Field(min_length=1)
    primary_actor: str = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    main_flow: list[str] = Field(min_length=2)
    alternative_flows: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    _lists = field_validator("preconditions", "main_flow", "alternative_flows", "postconditions")(
        classmethod(lambda cls, v: _clean_list(v)))


class ProcessStep(_Strict):
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)


class ProcessWorkflow(_Linked):
    name: str = Field(min_length=1)
    steps: list[ProcessStep] = Field(min_length=2)


class DataRequirement(_Linked):
    entity: str = Field(min_length=1)
    description: str = Field(min_length=1)
    attributes: list[str] = Field(min_length=1)
    retention: str = ""   # "" when the input does not state a retention period
    _attrs = field_validator("attributes")(classmethod(lambda cls, v: _clean_list(v)))


class InterfaceRequirement(_Linked):
    name: str = Field(min_length=1)
    interface_type: Literal[INTERFACE_TYPES]
    description: str = Field(min_length=1)


def _stories_cover_functional(model, info: ValidationInfo):
    frs = _ctx(info, "functional_ids") or set()
    covered = {i for s in model.user_stories for i in s.requirement_ids}
    missing = sorted(frs - covered)
    if missing:
        raise ValueError(f"every functional requirement needs a user story; missing {missing}")
    return model


class PartStories(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_stories: list[UserStory] = Field(min_length=1)
    use_cases: list[UseCase] = Field(min_length=1)
    _cover = model_validator(mode="after")(_stories_cover_functional)


class PartModels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process_workflows: list[ProcessWorkflow] = Field(default_factory=list)
    data_requirements: list[DataRequirement] = Field(default_factory=list)
    interface_requirements: list[InterfaceRequirement] = Field(default_factory=list)


class AllArtefacts(PartStories, PartModels):
    pass


SECTION_RULES = {
    "user_stories": '- "user_stories": at least one per functional requirement. Fields: "role" (who), "goal" (what they want, '
                    'phrased to follow "I want", e.g. "to upload my documents"), "benefit" (so that ...), '
                    '"acceptance_criteria" (1-3, Given/When/Then), "requirement_ids".',
    "use_cases": '- "use_cases": the main interactions. Fields: "name", "primary_actor", "preconditions", "main_flow" '
                 '(at least 2 numbered steps as strings), "alternative_flows", "postconditions", "requirement_ids".',
    "process_workflows": '- "process_workflows": end-to-end business processes. Fields: "name", "steps" (at least 2, each '
                         '{"actor", "action"}), "requirement_ids".',
    "data_requirements": '- "data_requirements": data the system stores. Fields: "entity", "description", "attributes", '
                         '"retention" ("" if the input does not state one), "requirement_ids".',
    "interface_requirements": '- "interface_requirements": interfaces to users, hardware, other software or networks. Fields: '
                              '"name", "interface_type" (one of: ' + ", ".join(INTERFACE_TYPES) + '), "description", "requirement_ids".',
}

PROMPT_TEMPLATE = """You are a requirements engineer writing the artefacts of a Software Requirements Specification.

{data_rule}

Domain: {domain}
Scenario: {scenario}

Requirements (id [type; categories] stakeholder: statement | acceptance criteria):
{requirements}

Write these artefacts, using only what the requirements and scenario support:
{rules}

Rules: every item must list the requirement ids it comes from (only ids from the list above). Do not add fields that are not listed. Do not name laws, regulations or standards that the input does not mention. Do not invent features the requirements do not imply.

Respond with ONLY valid JSON, no markdown fences, with exactly these keys: {keys}.
"""


def _listing(requirements: dict) -> str:
    return "\n".join(
        f"- {r['id']} [{r.get('type', '')}; {', '.join(r.get('categories') or [])}] {r.get('source_stakeholder', '')}: "
        f"{r['requirement']} | {'; '.join(r.get('acceptance_criteria') or [])}"
        for r in all_requirements(requirements))


def _call(schema, kinds, requirements, scenario, context):
    prompt = PROMPT_TEMPLATE.format(
        domain=untrusted("DOMAIN", requirements.get("domain_inferred", "")),
        scenario=untrusted("SCENARIO", (scenario or "(not provided)").strip()),
        requirements=untrusted("REQUIREMENTS", _listing(requirements)), data_rule=DATA_RULE, rules="\n".join(SECTION_RULES[k] for k in kinds),
        keys=", ".join(f'"{k}"' for k in kinds))
    return generate_validated(prompt, schema, generate=generate_text, temperature=0.2, context=context).model_dump()


def _finish(kind: str, items: list[dict], requirements: dict) -> list[dict]:
    out = []
    for i, item in enumerate(items, start=1):
        row = {"id": f"{ID_PREFIX[kind]}-{i}", **item}
        if kind == "user_stories":
            article = "an" if item["role"][:1].lower() in "aeiou" else "a"
            row["story"] = f"As {article} {item['role']}, I want {item['goal']}, so that {item['benefit']}."
        row["source_statements"] = source_statements(item["requirement_ids"], requirements)
        out.append(row)
    return out


def generate_artefacts(requirements: dict, scenario: str | None = None) -> dict:
    reqs = all_requirements(requirements)
    context = {"requirement_ids": {r["id"] for r in reqs},
               "functional_ids": {r["id"] for r in requirements.get("functional_requirements", [])}}
    split = len(reqs) > SPLIT_THRESHOLD
    if split:
        raw = {**_call(PartStories, ["user_stories", "use_cases"], requirements, scenario, context),
               **_call(PartModels, ["process_workflows", "data_requirements", "interface_requirements"],
                       requirements, scenario, context)}
    else:
        raw = _call(AllArtefacts, list(ID_PREFIX), requirements, scenario, context)
    result = {kind: _finish(kind, raw[kind], requirements) for kind in ID_PREFIX}
    result["generation"] = {"llm_calls": 2 if split else 1, "split": split,
                            "note": (f"More than {SPLIT_THRESHOLD} requirements: generated in two calls to keep each "
                                     "response small." if split else "Generated in one call.")}
    return result
