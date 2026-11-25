"""
Deterministic SDLC scoring engine.

Takes numeric trait ratings (1-5 scale, produced by the LLM trait-analysis
step in sdlc_agent.py, which is itself constrained by explicit rubrics - see
TRAIT_RUBRICS in sdlc_agent.py) and scores each candidate SDLC model using a
fixed, explainable weight matrix. This is pure Python - no LLM involved -
which is what makes the final recommendation reproducible and defensible
rather than just "the AI said so".

NINE traits are used, not four. The original four (clarity, volatility,
complexity, criticality) could not reliably distinguish Spiral from V-Model:
both profiles are "high complexity, high criticality", so any safety-critical
or otherwise serious scenario pushed both models' scores up together, with
Spiral usually edging ahead regardless of whether the project actually needed
Spiral's defining characteristic - risk-driven, prototype-heavy development
for genuinely uncertain problems. Five traits were added specifically to
restore that missing resolution:

- risk_uncertainty: separates Spiral (built for high-uncertainty problems)
  from V-Model (built for well-understood, high-stakes problems with LOW
  uncertainty) even when both score high on criticality/complexity.
- verification_validation: separates V-Model (extreme V&V rigor) from
  Waterfall (normal V&V) even though both favor high clarity/low volatility.
- stakeholder_feedback: separates Agile (continuous feedback is central)
  from Incremental (feedback matters less than phased delivery).
- incremental_suitability: separates Incremental/Agile (naturally
  decomposable into releases) from Waterfall/V-Model/Spiral (not).
- prototyping_need: separates Spiral (explicitly prototype/experiment-driven)
  from everything else, especially V-Model/Waterfall.

Every trait that was added earns its place by actually breaking a specific
tie that the original 4-trait system could not break - this was checked
deliberately rather than padding the model with arbitrary extra dimensions.

Phase 3 added six traits for problem statement section 13 factors that the
nine did not cover (security risk, system size, legacy-system dependence,
need for continuous delivery, stakeholder availability, budget/schedule
constraint) and two models from the section 14 table: DevSecOps
("continuous secure deployment") and the Agile-V hybrid ("high regulation
with evolving requirements"). The factor-to-trait mapping and the reasoning
behind every new profile value are in docs/ARCHITECTURE.md. Profiles and
weights were fixed before computing any results and were not tuned to
equalise win share.
"""

# Each model's "ideal" trait profile on the same 1-5 scales. These are
# documented engineering judgments about what each SDLC methodology is
# actually built for - not tuned to produce any particular test result.
SDLC_PROFILES = {
    "Waterfall": {
        "clarity": 5, "volatility": 1, "complexity": 2, "criticality": 2,
        "risk_uncertainty": 1, "verification_validation": 2,
        "stakeholder_feedback": 1, "incremental_suitability": 1, "prototyping_need": 1,
        "security_risk": 3, "system_size": 2, "legacy_dependence": 3, "continuous_delivery": 1,
        "stakeholder_availability": 2, "budget_schedule_constraint": 4,
    },
    "V-Model": {
        "clarity": 5, "volatility": 1, "complexity": 3, "criticality": 5,
        "risk_uncertainty": 2, "verification_validation": 5,
        "stakeholder_feedback": 1, "incremental_suitability": 1, "prototyping_need": 1,
        "security_risk": 3, "system_size": 4, "legacy_dependence": 4, "continuous_delivery": 1,
        "stakeholder_availability": 2, "budget_schedule_constraint": 3,
    },
    "Incremental": {
        "clarity": 3, "volatility": 3, "complexity": 3, "criticality": 2,
        "risk_uncertainty": 2, "verification_validation": 2,
        "stakeholder_feedback": 3, "incremental_suitability": 5, "prototyping_need": 2,
        "security_risk": 3, "system_size": 4, "legacy_dependence": 4, "continuous_delivery": 3,
        "stakeholder_availability": 3, "budget_schedule_constraint": 4,
    },
    "Agile": {
        "clarity": 2, "volatility": 5, "complexity": 3, "criticality": 2,
        "risk_uncertainty": 2, "verification_validation": 1,
        "stakeholder_feedback": 5, "incremental_suitability": 5, "prototyping_need": 2,
        "security_risk": 3, "system_size": 2, "legacy_dependence": 2, "continuous_delivery": 4,
        "stakeholder_availability": 5, "budget_schedule_constraint": 3,
    },
    "Spiral": {
        "clarity": 3, "volatility": 3, "complexity": 5, "criticality": 5,
        "risk_uncertainty": 5, "verification_validation": 3,
        "stakeholder_feedback": 2, "incremental_suitability": 2, "prototyping_need": 5,
        "security_risk": 3, "system_size": 5, "legacy_dependence": 4, "continuous_delivery": 2,
        "stakeholder_availability": 3, "budget_schedule_constraint": 1,
    },
    # Section 14: "Continuous secure deployment -> DevSecOps".
    "DevSecOps": {
        "clarity": 3, "volatility": 4, "complexity": 4, "criticality": 4,
        "risk_uncertainty": 2, "verification_validation": 4,
        "stakeholder_feedback": 4, "incremental_suitability": 5, "prototyping_need": 2,
        "security_risk": 5, "system_size": 3, "legacy_dependence": 2, "continuous_delivery": 5,
        "stakeholder_availability": 4, "budget_schedule_constraint": 3,
    },
    # Section 14: "High regulation with evolving requirements -> Agile-V-Model hybrid".
    "Agile-V": {
        "clarity": 3, "volatility": 4, "complexity": 3, "criticality": 5,
        "risk_uncertainty": 2, "verification_validation": 5,
        "stakeholder_feedback": 4, "incremental_suitability": 4, "prototyping_need": 2,
        "security_risk": 4, "system_size": 4, "legacy_dependence": 3, "continuous_delivery": 3,
        "stakeholder_availability": 4, "budget_schedule_constraint": 2,
    },
}

# Relative importance of each trait when computing fit. Fixed and documented
# here rather than hidden inside a prompt. Traits that do the most work
# separating otherwise-similar models (risk_uncertainty for Spiral vs
# V-Model; verification_validation for V-Model vs Waterfall; stakeholder_
# feedback for Agile vs Incremental) are weighted slightly higher than
# traits that mostly just confirm an existing signal.
TRAIT_WEIGHTS = {
    "clarity": 1.0,
    "volatility": 1.2,
    "complexity": 0.8,
    "criticality": 1.0,
    "risk_uncertainty": 1.3,
    "verification_validation": 1.1,
    "stakeholder_feedback": 1.2,
    "incremental_suitability": 1.0,
    "prototyping_need": 1.1,
    # Section 13 factors added in Phase 3. continuous_delivery does the most
    # separating work (DevSecOps/Agile vs plan-driven models), like volatility;
    # size and budget/schedule have the weakest link to model choice.
    "security_risk": 1.0,
    "system_size": 0.6,
    "legacy_dependence": 0.8,
    "continuous_delivery": 1.2,
    "stakeholder_availability": 0.8,
    "budget_schedule_constraint": 0.6,
}

MAX_TRAIT_DISTANCE = 4  # on a 1-5 scale, max possible difference per trait

# If the top two models' scores are within this many points, the result is
# reported as a close call rather than a decisive winner (Part 5).
CLOSE_CALL_MARGIN = 5.0


# Two UNROUNDED scores this close are a tie. With the current weights every
# weighted distance is a multiple of 0.1, so distinct scores differ by at
# least 100 * 0.1 / 58.8 = 0.17 points: in practice a tie means an identical
# weighted distance, which also shows as identical 1-decimal scores in the UI
# (tests/test_scoring.py checks this granularity assumption).
TIE_TOLERANCE = 0.05


def _rank(traits: dict) -> list[dict]:
    """
    Internal ranking with everything the tie-break needs. Never depends on
    the order of SDLC_PROFILES:
    1. Sort by unrounded score (desc), model name as a stable total order.
    2. Models within TIE_TOLERANCE of the leading score of their group form a
       tie group, ordered by: smaller worst single-trait gap, then smaller
       total unweighted gap, then alphabetical name.
    """
    max_weighted_distance = sum(
        TRAIT_WEIGHTS[t] * MAX_TRAIT_DISTANCE for t in TRAIT_WEIGHTS
    )
    records = []
    for model_name, profile in SDLC_PROFILES.items():
        gaps = {t: abs(traits[t] - profile[t]) for t in TRAIT_WEIGHTS}
        weighted_distance = sum(TRAIT_WEIGHTS[t] * gaps[t] for t in TRAIT_WEIGHTS)
        records.append({
            "model": model_name,
            "raw_score": (1 - weighted_distance / max_weighted_distance) * 100,
            "worst_gap": max(gaps.values()),
            "total_gap": sum(gaps.values()),
            "profile": profile,
        })

    records.sort(key=lambda r: (-r["raw_score"], r["model"]))
    ranked, i = [], 0
    while i < len(records):
        leader = records[i]["raw_score"]
        j = i
        while j < len(records) and leader - records[j]["raw_score"] <= TIE_TOLERANCE:
            j += 1
        group = records[i:j]
        group.sort(key=lambda r: (r["worst_gap"], r["total_gap"], r["model"]))
        ranked.extend(group)
        i = j
    return ranked


def score_models(traits: dict) -> list[dict]:
    """
    traits: dict with all 15 keys in TRAIT_WEIGHTS, each an int 1-5.
    Returns a list of {"model": str, "score": float (0-100), "profile": dict},
    sorted best-fit first (ties broken as described in _rank). Score is a
    weighted similarity to each model's ideal profile, normalized to 0-100
    by the same formula for every model, with no per-model bonus or offset
    (each model scores exactly 100 on its own profile).

    Equal treatment is not equal win share: a profile near the middle of the
    1-5 scale is closer on average to an arbitrary trait vector, so over all
    5^9 possible vectors Incremental ranks first most often (39.7%) and
    Waterfall least (8.5%). See scripts/win_share.py and
    docs/SCORING_ANALYSIS.md. The profiles encode what each methodology is
    built for and were deliberately not tuned to equalise those shares.
    """
    return [
        {"model": r["model"], "score": round(r["raw_score"], 1), "profile": r["profile"]}
        for r in _rank(traits)
    ]


def top_two_tied(traits: dict) -> bool:
    """True if the top two models' unrounded scores are within TIE_TOLERANCE."""
    ranked = _rank(traits)
    return len(ranked) > 1 and abs(ranked[0]["raw_score"] - ranked[1]["raw_score"]) <= TIE_TOLERANCE


def explain_tie_break(traits: dict) -> str | None:
    """Deterministic sentence on why the top model won a tie, or None if no tie."""
    if not top_two_tied(traits):
        return None
    winner, runner = _rank(traits)[:2]
    w, r = winner["model"], runner["model"]
    score = round(winner["raw_score"], 1)
    if winner["worst_gap"] != runner["worst_gap"]:
        rule = (f"{w}'s worst single-trait gap is {winner['worst_gap']} point(s), "
                f"smaller than {r}'s {runner['worst_gap']}")
    elif winner["total_gap"] != runner["total_gap"]:
        rule = (f"both have a worst single-trait gap of {winner['worst_gap']}, but {w}'s total "
                f"gap across all traits is {winner['total_gap']}, smaller than {r}'s {runner['total_gap']}")
    else:
        rule = (f"both have a worst single-trait gap of {winner['worst_gap']} and a total gap of "
                f"{winner['total_gap']}, so the final rule (alphabetical order) applies")
    return (f"{r} tied with {w} on compatibility score ({score}/100). The tie was broken "
            f"deterministically: {rule}.")


def is_close_call(scored_models: list[dict]) -> bool:
    """True if the top two models are within CLOSE_CALL_MARGIN points (a tie included)."""
    if len(scored_models) < 2:
        return False
    return (scored_models[0]["score"] - scored_models[1]["score"]) < CLOSE_CALL_MARGIN


TRAIT_LABELS = {
    "clarity": "requirement clarity",
    "volatility": "requirement volatility",
    "complexity": "project complexity",
    "criticality": "project criticality",
    "risk_uncertainty": "risk/uncertainty",
    "verification_validation": "verification & validation intensity",
    "stakeholder_feedback": "stakeholder feedback need",
    "incremental_suitability": "incremental delivery suitability",
    "prototyping_need": "prototyping/experimentation need",
    "security_risk": "security risk",
    "system_size": "system size",
    "legacy_dependence": "legacy-system dependence",
    "continuous_delivery": "need for continuous delivery",
    "stakeholder_availability": "stakeholder availability",
    "budget_schedule_constraint": "budget and schedule constraint",
}


def explain_mismatch(model_name: str, traits: dict) -> str:
    """
    Deterministic, rule-based explanation of why a given model is a WEAKER
    fit than the top choice, based on the same distance math used for
    scoring. No LLM call - this narrates the existing numbers, which keeps
    it honest, reproducible, and guaranteed consistent with the displayed
    scores for an academic audience.
    """
    profile = SDLC_PROFILES[model_name]
    diffs = [
        (trait, abs(traits[trait] - profile[trait]), traits[trait], profile[trait])
        for trait in TRAIT_WEIGHTS
    ]
    diffs.sort(key=lambda d: d[1], reverse=True)
    top_trait, gap, actual, ideal = diffs[0]
    label = TRAIT_LABELS[top_trait]

    direction = "higher" if actual > ideal else "lower"
    ideal_direction = "lower" if actual > ideal else "higher"

    return (
        f"{model_name} typically fits projects with {ideal_direction} {label} "
        f"(around {ideal}/5) than this project's rated {actual}/5 - a gap of {gap} points, "
        f"the largest mismatch among the traits considered. This project's {direction} "
        f"{label} makes {model_name} a weaker structural fit here."
    )
