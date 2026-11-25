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
"""

# Each model's "ideal" trait profile on the same 1-5 scales. These are
# documented engineering judgments about what each SDLC methodology is
# actually built for - not tuned to produce any particular test result.
SDLC_PROFILES = {
    "Waterfall": {
        "clarity": 5, "volatility": 1, "complexity": 2, "criticality": 2,
        "risk_uncertainty": 1, "verification_validation": 2,
        "stakeholder_feedback": 1, "incremental_suitability": 1, "prototyping_need": 1,
    },
    "V-Model": {
        "clarity": 5, "volatility": 1, "complexity": 3, "criticality": 5,
        "risk_uncertainty": 2, "verification_validation": 5,
        "stakeholder_feedback": 1, "incremental_suitability": 1, "prototyping_need": 1,
    },
    "Incremental": {
        "clarity": 3, "volatility": 3, "complexity": 3, "criticality": 2,
        "risk_uncertainty": 2, "verification_validation": 2,
        "stakeholder_feedback": 3, "incremental_suitability": 5, "prototyping_need": 2,
    },
    "Agile": {
        "clarity": 2, "volatility": 5, "complexity": 3, "criticality": 2,
        "risk_uncertainty": 2, "verification_validation": 1,
        "stakeholder_feedback": 5, "incremental_suitability": 5, "prototyping_need": 2,
    },
    "Spiral": {
        "clarity": 3, "volatility": 3, "complexity": 5, "criticality": 5,
        "risk_uncertainty": 5, "verification_validation": 3,
        "stakeholder_feedback": 2, "incremental_suitability": 2, "prototyping_need": 5,
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
}

MAX_TRAIT_DISTANCE = 4  # on a 1-5 scale, max possible difference per trait

# If the top two models' scores are within this many points, the result is
# reported as a close call rather than a decisive winner (Part 5).
CLOSE_CALL_MARGIN = 5.0


def score_models(traits: dict) -> list[dict]:
    """
    traits: dict with all 9 keys in TRAIT_WEIGHTS, each an int 1-5.
    Returns a list of {"model": str, "score": float (0-100), "profile": dict},
    sorted best-fit first. Score is a weighted similarity to each model's
    ideal profile, normalized to 0-100 identically for every model - no
    model has a structural numerical advantage; a model only wins because
    its profile best matches the extracted characteristics.
    """
    results = []
    max_weighted_distance = sum(
        TRAIT_WEIGHTS[t] * MAX_TRAIT_DISTANCE for t in TRAIT_WEIGHTS
    )

    for model_name, profile in SDLC_PROFILES.items():
        weighted_distance = sum(
            TRAIT_WEIGHTS[t] * abs(traits[t] - profile[t]) for t in TRAIT_WEIGHTS
        )
        similarity = 1 - (weighted_distance / max_weighted_distance)
        score = round(similarity * 100, 1)
        results.append({"model": model_name, "score": score, "profile": profile})

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def is_close_call(scored_models: list[dict]) -> bool:
    """True if the top two models are within CLOSE_CALL_MARGIN points."""
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
