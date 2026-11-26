"""
Threat and risk register (problem statement section 12 "Threat and risk
register"; the risk-analysis agent "assesses business, technical and
compliance risks", section 4).

One LLM call proposes risks and rates likelihood and impact against the
anchored 1-5 rubrics below, with a reason for each rating. Code does the
rest: score = likelihood x impact, level bands, ranking and RISK-n ids.
Validated by RiskOutput (retry once, then LLMOutputError).

Categories: the statement's business, technical and compliance risks, plus
"Security" because the register is a threat register (section 12) and
section 11 pairs compliance with security analysis, and "Operational"
(running the system: support, monitoring, data quality, manual steps).

5-8 risks per project. With 3-10 allowed, live loan runs returned 3-5 risks,
all or mostly High: the model listed only the most serious threats. Asking
for 5-8 and naming operational risks makes room for lower-severity risks
that genuinely exist; the rubrics are unchanged, and every risk must still
link to the requirements it concerns.
"""
from typing import Literal

from pydantic import BaseModel, Field, StrictInt, ValidationInfo, field_validator, model_validator

from app.gemini_client import generate_text
from app.prompt_safety import DATA_RULE, untrusted
from app.schemas import _NonEmptyText, _ctx, generate_validated
from app.vocabulary import canonical

RISK_CATEGORIES = ("Business", "Technical", "Compliance", "Security", "Operational")
MIN_RISKS, MAX_RISKS = 5, 8

# Score bands on the 1-25 likelihood x impact scale.
LEVEL_BANDS = (("High", 10), ("Medium", 5), ("Low", 1))

LIKELIHOOD_RUBRIC = """LIKELIHOOD (how likely the risk is to occur during development or operation)
1 = Rare: would need several unusual failures at once; nothing in the input points to it.
2 = Unlikely: possible, but the requirements already include a specific control against it.
3 = Possible: plausible in normal operation, and no specific control is in the requirements yet.
4 = Likely: the input itself points to it (a stated constraint or dependency, a listed compliance gap, or a listed quality flag) and no control is specified.
5 = Almost certain: it will happen unless something changes (e.g. two requirements conflict, or a stated requirement cannot be met as written)."""

IMPACT_RUBRIC = """IMPACT (consequence if the risk occurs)
1 = Negligible: minor inconvenience; no financial, legal, safety or data effect.
2 = Minor: limited, recoverable effect on a few users or a small amount of money.
3 = Moderate: noticeable service disruption, financial loss or a reportable incident.
4 = Major: large financial loss, regulatory breach or penalty, or harm to many customers or their data.
5 = Severe: risk to life or safety, systemic failure, or loss of licence / criminal liability."""


def level_for(score: int) -> str:
    return next(level for level, minimum in LEVEL_BANDS if score >= minimum)


class Rating(_NonEmptyText):
    score: StrictInt = Field(ge=1, le=5)
    reason: str = Field(min_length=1)


class Risk(_NonEmptyText):
    description: str = Field(min_length=1)
    category: Literal[RISK_CATEGORIES]
    likelihood: Rating
    impact: Rating
    mitigation: str = Field(min_length=1)
    linked_requirement_ids: list[str] = Field(min_length=1)

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        return canonical(v, RISK_CATEGORIES)

    @field_validator("linked_requirement_ids")
    @classmethod
    def _known_ids(cls, ids, info: ValidationInfo):
        known = _ctx(info, "requirement_ids")
        unknown = [i for i in ids if known is not None and i not in known]
        if unknown:
            raise ValueError(f"unknown requirement ids {unknown}")
        return list(dict.fromkeys(ids))


class RiskOutput(BaseModel):
    risks: list[Risk] = Field(min_length=MIN_RISKS, max_length=MAX_RISKS)

    @model_validator(mode="after")
    def _no_repeats(self):
        texts = [r.description.lower() for r in self.risks]
        if len(set(texts)) != len(texts):
            raise ValueError("risks must not repeat")
        return self


PROMPT_TEMPLATE = """You are a risk analyst building the threat and risk register for a software project.

{data_rule}

Domain: {domain}
Scenario: {scenario}

Requirements (id [risk level]: statement):
{requirements}

Compliance gaps (controls the requirements do not yet meet):
{compliance}

Quality flags (ambiguity, untestable, duplicate, conflict):
{quality}

List {min_risks}-{max_risks} distinct risks to this project. Cover business, technical, compliance and security risks, and include operational risks of running the system (support, monitoring, data quality, manual steps) where they genuinely exist; these are often of lower severity, which is fine. Every risk must come from the requirements, compliance gaps or quality flags above and be linked to the requirement ids it threatens or depends on: do not invent risks unrelated to the requirements to reach the count, and do not repeat one risk in different words. Rate likelihood and impact separately with these anchors, and give a one-sentence reason for each rating that points to the input:

{likelihood}

{impact}

Use the full scales: most projects have risks at several different levels, and a regulated domain is not by itself a reason for the highest scores. Do not name laws or regulations that the input does not mention. Give one concrete mitigation per risk.

Respond with ONLY valid JSON, no markdown fences, in exactly this schema:
{{"risks": [{{"description": "...", "category": "Business" | "Technical" | "Compliance" | "Security" | "Operational",
  "likelihood": {{"score": 1-5, "reason": "..."}}, "impact": {{"score": 1-5, "reason": "..."}},
  "mitigation": "...", "linked_requirement_ids": ["FR-1"]}}]}}
"""


def _compliance_lines(mapping: dict | None) -> str:
    lines = []
    for e in (mapping or {}).get("requirements", []):
        for it in e["mappings"]:
            if it["status"] == "gap":
                lines.append(f"- {e['requirement_id']}: gap against {it['source']}: {it['compliance_gap']}")
    # Review flags are deliberately NOT passed: most say the knowledge-base match
    # is uncertain, not that a control is missing, and in the live loan run they
    # pushed every likelihood to 4-5.
    return "\n".join(lines) or "(none)"


def _quality_lines(quality: dict | None) -> str:
    return "\n".join(f"- {f['type']} ({', '.join(f['requirement_ids'])}): {f['reason']}"
                     for f in (quality or {}).get("flags", [])) or "(none)"


def build_risk_register(domain: str, requirements: list[dict], scenario: str | None = None,
                        compliance_mapping: dict | None = None, quality: dict | None = None) -> dict:
    prompt = PROMPT_TEMPLATE.format(
        domain=untrusted("DOMAIN", domain), scenario=untrusted("SCENARIO", (scenario or "(not provided)").strip()),
        requirements=untrusted("REQUIREMENTS", "\n".join(
            f"- {r['id']} [{r.get('risk_level', '-')}]: {r['requirement']}" for r in requirements)),
        compliance=untrusted("COMPLIANCE GAPS", _compliance_lines(compliance_mapping)),
        quality=untrusted("QUALITY FLAGS", _quality_lines(quality)), data_rule=DATA_RULE,
        min_risks=MIN_RISKS, max_risks=MAX_RISKS, likelihood=LIKELIHOOD_RUBRIC, impact=IMPACT_RUBRIC,
    )
    out = generate_validated(prompt, RiskOutput, generate=generate_text, temperature=0.2,
                             context={"requirement_ids": {r["id"] for r in requirements}})
    rows = []
    for r in out.risks:
        score = r.likelihood.score * r.impact.score
        rows.append({
            "description": r.description, "category": r.category,
            "likelihood": r.likelihood.score, "likelihood_reason": r.likelihood.reason,
            "impact": r.impact.score, "impact_reason": r.impact.reason,
            "score": score, "level": level_for(score),
            "mitigation": r.mitigation, "linked_requirement_ids": r.linked_requirement_ids,
        })
    rows.sort(key=lambda x: (-x["score"], -x["impact"], -x["likelihood"], x["description"]))
    for i, row in enumerate(rows, start=1):
        row["id"], row["rank"] = f"RISK-{i}", i
    risks = [{"id": r.pop("id"), "rank": r.pop("rank"), **r} for r in rows]
    return {
        "risks": risks,
        "scoring": "score = likelihood x impact (1-25); High >= 10, Medium 5-9, Low 1-4",
        "summary": {
            "count": len(risks),
            "by_level": {lvl: sum(r["level"] == lvl for r in risks) for lvl, _ in LEVEL_BANDS},
            "likelihood_distribution": {str(n): sum(r["likelihood"] == n for r in risks) for n in range(1, 6)},
            "impact_distribution": {str(n): sum(r["impact"] == n for r in risks) for n in range(1, 6)},
        },
    }
