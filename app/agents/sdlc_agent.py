"""
Agent 2: SDLC Recommender (hybrid)

Pipeline:
1. LLM reads the requirements and rates the project on 15 traits (1-5 each),
   constrained by explicit scoring rubrics (TRAIT_RUBRICS below) - NOT asked
   to freely guess numbers. Each trait requires cited evidence and a reason,
   which forces the model to ground its rating in the actual scenario text
   rather than pattern-matching "this sounds serious -> rate everything high".
2. Pure deterministic Python (sdlc_rules.score_models) scores every
   candidate SDLC model against those traits - reproducible, explainable,
   no LLM involved in this step, and no per-model bonus (win share is not
   equal across models, see docs/SCORING_ANALYSIS.md).
3. LLM writes a structured justification for the top pick, grounded in the
   actual measured trait scores AND their evidence - explicitly instructed
   never to contradict a measured score (e.g. cannot call volatility "high"
   if its score is 1 or 2).
4. Deterministic "why not" explanations for every other model, reusing the
   same distance math from step 2.
"""
import re

from app.gemini_client import generate_text
from app.schemas import Justification, TraitRatings, generate_validated
from app.agents.sdlc_rules import (
    score_models, explain_mismatch, is_close_call, top_two_tied, explain_tie_break, TRAIT_LABELS,
)

TRAIT_KEYS = [
    "clarity", "volatility", "complexity", "criticality",
    "risk_uncertainty", "verification_validation",
    "stakeholder_feedback", "incremental_suitability", "prototyping_need",
    # Problem statement section 13 factors not covered by the nine above
    # (mapping in docs/ARCHITECTURE.md, "SDLC decision factors").
    "security_risk", "system_size", "legacy_dependence", "continuous_delivery",
    "stakeholder_availability", "budget_schedule_constraint",
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
REQUIREMENT CLARITY (how settled the stakeholders' requirements are - NOT how neatly the list below is written)
1 = Only a broad goal is known; the core features, users, or scope are not yet identified.
2 = The goal and users are known, but which features to build is still open or is explicitly to be decided from user feedback, experiments, or usage data.
3 = The core features are identified, but their business rules, acceptance criteria, or the scope of later features are left open or expected to be refined as the product evolves.
4 = The core features and most business rules are specified; only minor details remain open.
5 = Requirements are fully specified, with business rules defined by an approved specification, written policy, contract, standard, or regulation.
The requirement list was written by an analyst from a short scenario, so it always looks tidy. Judge clarity from what it says about how settled the needs are (fixed rules and approvals vs. features to be discovered or refined), not from the wording quality, length, or number of requirements.

REQUIREMENT VOLATILITY (how much the requirements are expected to change during development)
1 = Requirements are stated to be fixed, formally approved, or governed by rules/regulations that change rarely and only through formal change control.
2 = Requirements are largely settled; only occasional, planned changes are expected (e.g. periodic rule, rate, or configuration updates).
3 = A fixed core scope with a planned roadmap of further features; change mostly takes the form of NEW features added in later releases, while existing features stay stable.
4 = Existing features are expected to be modified, replaced, or dropped based on user feedback, usage data, or market response, but a recognisable core product is fixed.
5 = The product direction itself is still being discovered (e.g. experiments, MVP to test fit, possible pivots); most features are expected to be reshaped repeatedly throughout development.
Do NOT infer high volatility merely because a project is complex, serious, or has many requirements.
A scenario that explicitly states requirements are stable, formally approved, or rarely changed MUST receive a LOW volatility score (1-2), regardless of how important or complex the project is.
Planned feature ADDITIONS on top of a stable core are a 3; feedback-driven CHANGES to existing features are a 4; an undecided product direction is a 5.

TIE-BREAK RULE FOR CLARITY AND VOLATILITY: each anchor above names the kind of evidence it requires. Choose the HIGHEST anchor whose required evidence is explicitly present in the scenario/domain/requirements; if you are torn between two adjacent anchors and the evidence for the higher one is not explicitly stated, choose the lower one. Never pick a score because projects in this domain "usually" work that way.

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

VERIFICATION & VALIDATION INTENSITY (how rigorous testing, verification and the documentation that evidences them need to be)
1 = Normal testing is sufficient.
5 = Extremely rigorous verification, validation, traceability, documentation or safety-critical / formal verification required.

STAKEHOLDER FEEDBACK NEED (how central continuous user/stakeholder feedback is to development)
1 = Little need for continuous feedback once requirements are set.
5 = Continuous user/stakeholder feedback is central to how the system evolves.

INCREMENTAL DELIVERY SUITABILITY (can functionality be naturally split into usable releases?)
1 = System is best delivered as a single complete release.
5 = Functionality can naturally be delivered and validated in increments.

PROTOTYPING / EXPERIMENTATION NEED (need to build throwaway prototypes to reduce uncertainty)
1 = Little or no need for prototypes.
5 = Substantial uncertainty requires prototyping and experimentation before committing to an approach.

SECURITY RISK (how attractive and exposed the system is to attack, and how much harm a breach would do)
1 = No sensitive data and no external exposure (e.g. an internal tool on public information).
2 = Limited personal data, or access only from inside the organisation.
3 = User accounts or personal data reachable from the internet.
4 = Sensitive personal or financial data, payments, or an obvious target for fraud.
5 = A high-value target where a breach causes severe harm (e.g. core banking, payment rails, safety-control networks, critical infrastructure).

SYSTEM SIZE (scale of the system and of the effort to build it - NOT technical difficulty, which is complexity)
1 = A small tool or single module for one small team.
2 = A small application with a few features.
3 = A medium system: several modules, one team.
4 = A large system: many modules, several teams or a large user base.
5 = Programme scale: many teams or organisations, very large user base.

LEGACY-SYSTEM DEPENDENCE (how much the system depends on existing systems that constrain it)
1 = Greenfield: no existing systems to work with.
2 = A few simple integrations with modern, well-documented services.
3 = Several integrations with existing internal systems.
4 = Core functions depend on legacy systems (e.g. mainframes, core banking, existing databases or hardware) that constrain the design.
5 = It replaces or tightly wraps a legacy system and must reproduce its behaviour.

NEED FOR CONTINUOUS DELIVERY (how often changes must reach production)
1 = A single release, or rare releases months or years apart.
2 = A few planned releases per year.
3 = Regular releases every few weeks.
4 = Frequent releases (weekly or more), feature flags or A/B tests.
5 = Continuous deployment to production is stated or essential.

STAKEHOLDER AVAILABILITY (how available users and business stakeholders are to take part during development - NOT how much feedback is needed)
1 = Available only at the start and at sign-off.
2 = Occasional milestone reviews.
3 = Regular reviews every few weeks.
4 = Frequent access (about weekly) with an engaged product owner.
5 = Continuously available: embedded in the team, or live usage data is available.

BUDGET AND SCHEDULE CONSTRAINT (how fixed and tight the budget and the deadline are)
1 = Flexible budget and timeline.
2 = Mostly flexible, with target dates.
3 = A fixed target date or budget, with some flexibility in scope.
4 = A fixed budget and deadline (e.g. a contract or a regulatory date).
5 = A fixed, tight budget and an immovable deadline with little contingency.
For security risk, size, legacy dependence, continuous delivery, availability and budget/schedule: if the input says nothing, choose the anchor the domain and requirements most plausibly imply, and say in "evidence" that it is inferred.
"""

TRAIT_PROMPT_TEMPLATE = """You are a software engineering process consultant rating project characteristics (15 traits), using ONLY the rubrics provided below. Do not invent your own scale.

{rubrics}

Original scenario (the stakeholder's own words; the best evidence for how settled and how changeable the needs are):
{scenario}

Domain: {domain}

Functional Requirements:
{functional_list}

Non-Functional Requirements:
{non_functional_list}

Rules:
- For each trait, pick the single rubric anchor (1-5) that best matches the evidence actually present in the scenario/requirements/domain above.
- "evidence" must quote or closely paraphrase the specific part of the scenario/requirements/domain that justifies the score. If there is no direct evidence, say so explicitly and explain your inference cautiously.
- "reason" must be one sentence connecting the evidence to the chosen score.
- Do NOT infer high volatility merely because the project is complex, serious, or safety-critical. Stable, formally-controlled requirements get LOW volatility even in high-stakes domains.
- Do NOT infer high risk_uncertainty merely because criticality is high. A mature, well-understood engineering domain can be both high-criticality and low-risk/uncertainty.
- Do NOT infer high complexity merely because there are many requirements - base it on system structure and technical difficulty.
- criticality must be based on consequences of failure, not on how technically difficult the system is.
- Do NOT infer high continuous_delivery merely because requirements change: it is about how often changes must reach production.
- stakeholder_availability is about access to stakeholders, not about how much feedback is needed (that is stakeholder_feedback).

Respond with ONLY valid JSON, no markdown fences, no commentary, in exactly this schema (all 15 traits required):
{{
  "clarity": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "volatility": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "complexity": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "criticality": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "risk_uncertainty": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "verification_validation": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "stakeholder_feedback": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "incremental_suitability": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "prototyping_need": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "security_risk": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "system_size": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "legacy_dependence": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "continuous_delivery": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "stakeholder_availability": {{"score": <1-5>, "evidence": "...", "reason": "..."}},
  "budget_schedule_constraint": {{"score": <1-5>, "evidence": "...", "reason": "..."}}
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

Write a structured justification broken into exactly these 4 sections. Each section should be 1-2 concise sentences, plain prose, no markdown formatting. Refer to traits by their plain-English names exactly as written above (e.g. "risk/uncertainty", "requirement volatility"), never by identifiers with underscores. CRITICAL: you must never describe a trait in a direction that contradicts its measured score - e.g. if requirement volatility's score is 1 or 2, you must describe requirements as stable/low-change, never as "frequently changing" or "high volatility". If project criticality is 5 but risk/uncertainty is low, do not blur the two together - they measure different things.

- requirement_characteristics: what the requirement clarity and requirement volatility scores (and their evidence) imply for how this project should be planned
- project_characteristics: what the project complexity, project criticality and risk/uncertainty scores imply about the project's risk and technical profile
- engineering_justification: why {top_model} specifically fits these measured characteristics, referencing at least one specific requirement ID and at least one specific trait score
- key_tradeoffs: one real trade-off or risk of choosing {top_model} for this project

Respond with ONLY valid JSON, no markdown fences, in exactly this schema:
{{"requirement_characteristics": "...", "project_characteristics": "...", "engineering_justification": "...", "key_tradeoffs": "..."}}
"""


REVIEWER_NOTE = """

A human reviewer asked for this to be regenerated, with this comment. Address it while following every rule above:
{comment}
"""


def _format_requirements(reqs: list[dict]) -> str:
    return "\n".join(f"- {r['id']}: {r['requirement']}" for r in reqs) or "(none)"


def _format_trait_summary(trait_details: dict) -> str:
    # Human labels, not internal keys: whatever the LLM sees here tends to be
    # echoed verbatim into the user-facing justification.
    lines = []
    for key in TRAIT_KEYS:
        d = trait_details[key]
        lines.append(
            f"- {TRAIT_LABELS[key]} = {d['score']}/5 "
            f"(evidence: {_humanize(d['evidence'])} | reason: {_humanize(d['reason'])})"
        )
    return "\n".join(lines)


# Matches internal trait identifiers that contain an underscore (e.g.
# "risk_uncertainty"); single-word keys such as "clarity" are plain English.
_RAW_TRAIT_KEY_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in TRAIT_KEYS if "_" in k) + r")\b"
)


def _humanize(text):
    """Replace internal trait keys in LLM text with their UI labels."""
    if not isinstance(text, str):
        return text
    return _RAW_TRAIT_KEY_RE.sub(lambda m: TRAIT_LABELS[m.group(1)], text)


def _humanize_trait_keys(justification: dict) -> dict:
    """Safety net: the justification is user-facing, so no raw keys survive."""
    return {section: _humanize(text) for section, text in justification.items()}


# Problem statement section 14: "The final choice should be approved by the
# project manager, architect, security team, and compliance officer."
SDLC_APPROVERS = ("Project manager", "Architect", "Security team", "Compliance officer")


def approval_required(model: str) -> dict:
    return {
        "approvers": list(SDLC_APPROVERS),
        "status": "pending",
        "note": (f"Advisory only: adopting {model} must be approved by the project manager, architect, "
                 "security team and compliance officer (problem statement section 14). Nothing in this "
                 "system records that approval yet."),
    }


def analyze_traits(
    domain: str,
    functional_requirements: list[dict],
    non_functional_requirements: list[dict],
    scenario: str | None = None,
    reviewer_comment: str | None = None,
) -> dict:
    """
    Returns trait_details: {trait_key: {"score": int, "evidence": str, "reason": str}}
    for all 15 TRAIT_KEYS, validated by TraitRatings. A missing trait or a
    score that is not an integer from 1 to 5 is retried once and then raises
    LLMOutputError. It is never clamped or replaced with a default: a silent
    3 would push the recommendation toward the model whose profile sits
    nearest the middle of the scale (Incremental).
    """
    prompt = TRAIT_PROMPT_TEMPLATE.format(
        rubrics=TRAIT_RUBRICS,
        scenario=scenario.strip() if scenario and scenario.strip() else "(not provided - rate from the domain and requirements only)",
        domain=domain,
        functional_list=_format_requirements(functional_requirements),
        non_functional_list=_format_requirements(non_functional_requirements),
    )
    if reviewer_comment:  # approval action "regenerate": the reviewer can only influence the ratings
        prompt += REVIEWER_NOTE.format(comment=reviewer_comment.strip())
    # Temperature 0: trait scores feed a deterministic scorer, so the same
    # requirements should produce the same scores as far as the model allows.
    ratings = generate_validated(prompt, TraitRatings, generate=generate_text, temperature=0)
    dumped = ratings.model_dump()
    return {key: dumped[key] for key in TRAIT_KEYS}


def recommend_sdlc(
    domain: str,
    functional_requirements: list[dict],
    non_functional_requirements: list[dict],
    scenario: str | None = None,
    reviewer_comment: str | None = None,
) -> dict:
    """
    Full Agent 2 pipeline. Returns dict with:
    traits (flat scores, for UI bars), trait_details (full evidence/reason,
    for transparency/debugging), scored_models (all 5, ranked, each with a
    why_not explanation for non-top models), recommended_model,
    is_close_call, justification (4 sections)
    """
    trait_details = analyze_traits(domain, functional_requirements, non_functional_requirements, scenario,
                                   reviewer_comment)
    flat_traits = {k: v["score"] for k, v in trait_details.items()}

    scored_models = score_models(flat_traits)
    tied = top_two_tied(flat_traits)
    close_call = tied or is_close_call(scored_models)

    for i, entry in enumerate(scored_models):
        # Additive: 1-based rank and the score as a percentage label (section 14
        # asks for "ranked recommendations" with percentages; the score is a
        # 0-100 compatibility score, not a probability).
        entry["rank"] = i + 1
        entry["percent"] = f"{entry['score']:.1f}%"
        entry["why_not"] = None if i == 0 else explain_mismatch(entry["model"], flat_traits)
    if tied:
        # Same response shape: the runner-up's why_not string carries the tie-break reason.
        scored_models[1]["why_not"] = explain_tie_break(flat_traits) + " " + scored_models[1]["why_not"]

    top = scored_models[0]
    second = scored_models[1]
    key_reqs = (functional_requirements[:2] + non_functional_requirements[:2])

    if tied:
        close_call_note = (
            f"Note: this is a TIE - {second['model']} has the same compatibility score "
            f"({second['score']}/100). {explain_tie_break(flat_traits)} "
            f"State plainly that the two models are tied and {top['model']} was chosen only by this tie-break rule."
        )
    elif close_call:
        close_call_note = (
            f"Note: this is a close comparison - {second['model']} scored {second['score']}/100, "
            f"within {round(top['score'] - second['score'], 1)} points of the top pick. "
            f"Acknowledge this is not an overwhelmingly decisive gap."
        )
    else:
        close_call_note = ""

    justification_prompt = JUSTIFICATION_PROMPT_TEMPLATE.format(
        domain=domain,
        trait_summary=_format_trait_summary(trait_details),
        top_model=top["model"],
        top_score=top["score"],
        close_call_note=close_call_note,
        key_requirements=_format_requirements(key_reqs),
    )
    justification = generate_validated(
        justification_prompt, Justification, generate=generate_text, temperature=0.3,
        context={"requirement_ids": {r["id"] for r in functional_requirements + non_functional_requirements}},
    ).model_dump()
    justification = _humanize_trait_keys(justification)

    return {
        "traits": flat_traits,
        "trait_details": trait_details,
        "scored_models": scored_models,
        "recommended_model": top["model"],
        "is_close_call": close_call,
        "justification": justification,
        "approval_required": approval_required(top["model"]),
    }
