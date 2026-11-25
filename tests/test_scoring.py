"""Tests for the deterministic SDLC scoring engine (no LLM involved)."""
import pytest

from app.agents.sdlc_rules import (
    SDLC_PROFILES, TRAIT_WEIGHTS, score_models, is_close_call, explain_mismatch,
)

TRAITS = list(TRAIT_WEIGHTS)

# Hand-written trait vectors that follow the rubrics for each scenario.
# They test the scoring maths, not live LLM extraction.
SCENARIOS = {
    "payroll": ({
        "clarity": 5, "volatility": 1, "complexity": 3, "criticality": 5,
        "risk_uncertainty": 1, "verification_validation": 5,
        "stakeholder_feedback": 1, "incremental_suitability": 1, "prototyping_need": 1,
    }, "V-Model"),
    "railway": ({
        "clarity": 4, "volatility": 2, "complexity": 5, "criticality": 5,
        "risk_uncertainty": 3, "verification_validation": 5,
        "stakeholder_feedback": 1, "incremental_suitability": 1, "prototyping_need": 2,
    }, "V-Model"),
    "social_media": ({
        "clarity": 3, "volatility": 5, "complexity": 3, "criticality": 2,
        "risk_uncertainty": 2, "verification_validation": 1,
        "stakeholder_feedback": 5, "incremental_suitability": 5, "prototyping_need": 3,
    }, "Agile"),
    "food_delivery": ({
        "clarity": 3, "volatility": 4, "complexity": 3, "criticality": 3,
        "risk_uncertainty": 2, "verification_validation": 2,
        "stakeholder_feedback": 4, "incremental_suitability": 4, "prototyping_need": 2,
    }, "Incremental"),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario_winner(name):
    traits, expected = SCENARIOS[name]
    assert score_models(traits)[0]["model"] == expected


def test_payroll_stable_requirements_do_not_pick_spiral():
    traits, _ = SCENARIOS["payroll"]
    ranked = [r["model"] for r in score_models(traits)]
    assert ranked[0] == "V-Model"
    assert ranked.index("Spiral") > ranked.index("Waterfall")


def test_all_profiles_cover_exactly_the_weighted_traits():
    for model, profile in SDLC_PROFILES.items():
        assert set(profile) == set(TRAITS), model
        assert all(1 <= v <= 5 for v in profile.values()), model


def test_no_hidden_advantage_each_model_wins_on_its_own_profile():
    # Feeding a model its own ideal profile must score exactly 100 and win.
    # If any model had a built-in bonus or penalty this would fail.
    for model, profile in SDLC_PROFILES.items():
        ranked = score_models(dict(profile))
        assert ranked[0]["model"] == model
        assert ranked[0]["score"] == 100.0


def test_scores_are_normalised_0_to_100():
    for traits, _ in SCENARIOS.values():
        for r in score_models(traits):
            assert 0.0 <= r["score"] <= 100.0


def test_results_sorted_best_first():
    traits, _ = SCENARIOS["food_delivery"]
    scores = [r["score"] for r in score_models(traits)]
    assert scores == sorted(scores, reverse=True)


def test_close_call_detection():
    assert is_close_call([{"score": 75.0}, {"score": 73.0}]) is True
    assert is_close_call([{"score": 90.0}, {"score": 60.0}]) is False
    assert is_close_call([{"score": 90.0}]) is False


def test_explain_mismatch_mentions_actual_values():
    traits, _ = SCENARIOS["payroll"]
    text = explain_mismatch("Agile", traits)
    assert "Agile" in text and "/5" in text
