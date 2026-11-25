"""
Agent 2: SDLC Recommender (hybrid)

Pipeline:
1. LLM reads the requirements and rates the project on 9 traits (1-5 each),
   constrained by explicit scoring rubrics (TRAIT_RUBRICS below) - NOT asked
   to freely guess numbers. Each trait requires cited evidence and a reason,
   which forces the model to ground its rating in the actual scenario text
   rather than pattern-matching "this sounds serious -> rate everything high".
2. Pure deterministic Python (sdlc_rules.score_models) scores every
   candidate SDLC model against those traits - reproducible, explainable,
   no LLM involved in this step, no model has a structural advantage.
3. LLM writes a structured justification for the top pick, grounded in the
   actual measured trait scores AND their evidence - explicitly instructed
   never to contradict a measured score (e.g. cannot call volatility "high"
   if its score is 1 or 2).
4. Deterministic "why not" explanations for every other model, reusing the
   same distance math from step 2.
"""
from app.gemini_client import generate_text
from app.json_utils import parse_json_response
from app.agents.sdlc_rules import score_models, explain_mismatch, is_close_call

TRAIT_KEYS = [
    "clarity", "volatility", "complexity", "criticality",
    "risk_uncertainty", "verification_validation",
    "stakeholder_feedback", "incremental_suitability", "prototyping_need",
]

# Explicit scoring rubrics. The LLM is given these anchors verbatim and
# instructed to pick the closest match, not invent its own scale. This is
# the direct fix for the payroll bug: previously the prompt gave one vague
# sentence per trait with no anchors, so the model free-associated "this
# project sounds serious" into "therefore volatility must be high" even
# when the scenario explicitly described stable, formally-controlled
# requirements. Anchored rubrics plus a required evidence quote close that
# gap.
TRAIT_RUBRICS = """
REQUIREMENT CLARITY (how well-defined and unambiguous the requirements are)
1 = Highly ambiguous, incomplete, or poorly understood.
2 = Major aspects unclear; significant clarification needed.
3 = Core requirements understood but some details uncertain.
4 = Mostly well-defined with only minor ambiguity.
5 = Explicitly defined, complete, and well understood.

REQUIREMENT VOLATILITY (how likely requirements are to change during development)
1 = Expected to remain stable; changes are rare and formally controlled.
2 = Minor changes may occur occasionally.
3 = Moderate requirement changes expected during development.
4 = Expected to change frequently due to feedback, market conditions, regulations, or evolving user needs.
5 = Continuously evolving; substantial changes expected throughout development.
Do NOT infer high volatility merely because a project is complex, serious, or has many requirements.
A scenario that explicitly states requirements are stable, formally approved, or rarely changed MUST receive a LOW volatility score (1-2), regardless of how important or complex the project is.

PROJECT COMPLEXITY (system structure and technical difficulty, NOT the number of requirements)
1 = Small, simple system with few components and dependencies.
2 = Limited integrations/components, relatively straightforward architecture.
3 = Moderate number of components, integrations, or technical dependencies.
4 = Many interacting components, integrations, users, or significant technical challenges.
5 = Highly complex: many interdependent components, difficult technical constraints, distributed/real-time behavior, or major integration challenges.

PROJECT CRITICALITY (consequences of FAILURE, not technical complexity)
1 = Failure has minimal consequences.
2 = Failure causes limited operational or financial impact.
3 = Failure causes significant business/user impact.
4 = Failure can cause major financial, operational, legal, safety, or security consequences.
5 = Failure can cause severe safety, regulatory, financial, security, or mission-critical consequences.

RISK / UNCERTAINTY (how novel or unproven the problem/approach is - NOT the same as criticality)
1 = Very predictable, well-understood problem domain, low uncertainty.
5 = Highly uncertain or novel; requires continuous risk analysis to de-risk the approach.
A project can be extremely high-criticality (catastrophic if it fails) while still being LOW risk/uncertainty, if it uses a mature, well-standardized engineering approach (e.g. established safety-critical domains with existing standards). Risk/uncertainty measures novelty, not stakes.

VERIFICATION & VALIDATION INTENSITY (how rigorous testing/verification needs to be)
1 = Normal testing is sufficient.
5 = Extremely rigorous verification, validation, traceability, or safety-critical testing required.

STAKEHOLDER FEEDBACK NEED (how central continuous user/stakeholder feedback is to development)
1 = Little need for continuous feedback once requirements are set.
5 = Continuous user/stakeholder feedback is central to how the system evolves.

INCREMENTAL DELIVERY SUITABILITY (can functionality be naturally split into usable releases?)
1 = System is best delivered as a single complete release.
5 = Functionality can naturally be delivered and validated in increments.

PROTOTYPING / EXPERIMENTATION NEED (need to build throwaway prototypes to reduce uncertainty)
1 = Little or no need for prototypes.
5 = Substantial uncertainty requires prototyping and experimentation before committing to an approach.
"""

TRAIT_PROMPT_TEMPLATE = """You are a software engineering process consultant rating a project on 9 characteristics, using ONLY the rubrics provided below. Do not invent your own scale.

{rubrics}

Domain: {domain}

Functional Requirements:
{functional_list}

Non-Functional Requirements:
{non_functional_list}

Rules:
- For each trait, pick the single rubric anchor (1-5) that best matches the evidence actually present in the requirements/domain above.
- "evidence" must quote or closely paraphrase the specific part of the requirements/domain that justifies the score. If there is no direct evidence, say so explicitly and explain your inference cautiously.
- "reason" must be one sentence connecting the evidence to the chosen score.
- Do NOT infer high volatility merely because the project is complex, serious, or safety-critical. Stable, formally-controlled requirements get LOW volatility even in high-stakes domains.
- Do NOT infer high risk_uncertainty merely because criticality is high. A mature, well-understood engineering domain can be both high-criticality and low-risk/uncertainty.
- Do NOT infer high complexity merely because there are many requirements - base it on system structure and technical difficulty.
- criticality must be based on consequences of failure, not on how technically difficult the system is.

Respond with ONLY valid JSON, no markdown fences, no commentary, in exactly this schema (all 9 traits required):
{{
  "clarity": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "volatility": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "complexity": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "criticality": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "risk_uncertainty": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "verification_validation": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "stakeholder_feedback": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "incremental_suitability": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "prototyping_need": {{"score": <1-5>, "evidence": "...", "reason": "..."}}
}}
"""

JUSTIFICATION_PROMPT_TEMPLATE = """You are a software engineering process consultant writing a structured recommendation report for a stakeholder.

Domain: {domain}

The project was measured on these traits (1-5 scale), each with the evidence that produced the score - treat these numbers as FACTS, never contradict them:
{trait_summary}

Based on deterministic scoring against documented SDLC model profiles, the recommended model is: {top_model} (compatibility score: {top_score}/100)
{close_call_note}

Key requirements driving this recommendation:
{key_requirements}

Write a structured justification broken into exactly these 4 sections. Each section should be 1-2 concise sentences, plain prose, no markdown formatting. CRITICAL: you must never describe a trait in a direction that contradicts its measured score - e.g. if volatility's score is 1 or 2, you must describe requirements as stable/low-change, never as "frequently changing" or "high volatility". If criticality is 5 but risk_uncertainty is low, do not blur the two together - they measure different things.

- requirement_characteristics: what the clarity/volatility scores (and their evidence) imply for how this project should be planned
- project_characteristics: what the complexity/criticality/risk_uncertainty scores imply about the project's risk and technical profile
- engineering_justification: why {top_model} specifically fits these measured characteristics, referencing at least one specific requirement ID and at least one specific trait score
- key_tradeoffs: one real trade-off or risk of choosing {top_model} for this project

Respond with ONLY valid JSON, no markdown fences, in exactly this schema:
{{"requirement_characteristics": "...", "project_characteristics": "...", "engineering_justification": "...", "key_tradeoffs": "..."}}
"""


def _format_requirements(reqs: list[dict]) -> str:
    return "\n".join(f"- {r['id']}: {r['requirement']}" for r in reqs) or "(none)"


def _format_trait_summary(trait_details: dict) -> str:
    lines = []
    for key in TRAIT_KEYS:
        d = trait_details[key]
        lines.append(f"- {key} = {d['score']}/5 (evidence: {d['evidence']} | reason: {d['reason']})")
    return "\n".join(lines)


def analyze_traits(domain: str, functional_requirements: list[dict], non_functional_requirements: list[dict]) -> dict:
    """
    Returns trait_details: {trait_key: {"score": int, "evidence": str, "reason": str}}
    for all 9 TRAIT_KEYS. Scores are clamped defensively to 1-5.
    """
    prompt = TRAIT_PROMPT_TEMPLATE.format(
        rubrics=TRAIT_RUBRICS,
        domain=domain,
        functional_list=_format_requirements(functional_requirements),
        non_functional_list=_format_requirements(non_functional_requirements),
    )
    raw = generate_text(prompt, temperature=0.15)
    parsed = parse_json_response(raw)

    trait_details = {}
    for key in TRAIT_KEYS:
        entry = parsed.get(key, {})
        score = entry.get("score", 3)
        try:
            score = max(1, min(5, int(score)))
        except (TypeError, ValueError):
            score = 3
        trait_details[key] = {
            "score": score,
            "evidence": entry.get("evidence", ""),
            "reason": entry.get("reason", ""),
        }
    return trait_details


def recommend_sdlc(domain: str, functional_requirements: list[dict], non_functional_requirements: list[dict]) -> dict:
    """
    Full Agent 2 pipeline. Returns dict with:
    traits (flat scores, for UI bars), trait_details (full evidence/reason,
    for transparency/debugging), scored_models (all 5, ranked, each with a
    why_not explanation for non-top models), recommended_model,
    is_close_call, justification (4 sections)
    """
    trait_details = analyze_traits(domain, functional_requirements, non_functional_requirements)
    flat_traits = {k: v["score"] for k, v in trait_details.items()}

    scored_models = score_models(flat_traits)
    close_call = is_close_call(scored_models)

    for i, entry in enumerate(scored_models):
        entry["why_not"] = None if i == 0 else explain_mismatch(entry["model"], flat_traits)

    top = scored_models[0]
    second = scored_models[1]
    key_reqs = (functional_requirements[:2] + non_functional_requirements[:2])

    close_call_note = (
        f"Note: this is a close comparison - {second['model']} scored {second['score']}/100, "
        f"within {round(top['score'] - second['score'], 1)} points of the top pick. "
        f"Acknowledge this is not an overwhelmingly decisive gap."
        if close_call else ""
    )

    justification_prompt = JUSTIFICATION_PROMPT_TEMPLATE.format(
        domain=domain,
        trait_summary=_format_trait_summary(trait_details),
        top_model=top["model"],
        top_score=top["score"],
        close_call_note=close_call_note,
        key_requirements=_format_requirements(key_reqs),
    )
    raw_justification = generate_text(justification_prompt, temperature=0.3)
    justification = parse_json_response(raw_justification)

    return {
        "traits": flat_traits,
        "trait_details": trait_details,
        "scored_models": scored_models,
        "recommended_model": top["model"],
        "is_close_call": close_call,
        "justification": justification,
    }
