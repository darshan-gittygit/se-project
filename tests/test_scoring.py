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


# --- Tie-break: unrounded scores, never list order -------------------------
import itertools

from app.agents import sdlc_rules
from app.agents.sdlc_rules import top_two_tied, explain_tie_break, TIE_TOLERANCE

# Live payroll run that produced an exact 84.0 / 84.0 Waterfall vs V-Model tie.
# Both have worst gap 2 and total gap 6, so the alphabetical rule decides.
PAYROLL_TIE = {
    "clarity": 5, "volatility": 1, "complexity": 2, "criticality": 4,
    "risk_uncertainty": 1, "verification_validation": 4,
    "stakeholder_feedback": 1, "incremental_suitability": 3, "prototyping_need": 1,
}


def _rankings_for_every_profile_order(monkeypatch, traits, profiles=None):
    profiles = profiles or dict(SDLC_PROFILES)
    seen = set()
    for order in itertools.permutations(profiles):
        monkeypatch.setattr(sdlc_rules, "SDLC_PROFILES", {m: profiles[m] for m in order})
        seen.add(tuple(r["model"] for r in score_models(traits)))
    return seen


def test_payroll_tie_is_detected_and_broken_alphabetically():
    ranked = score_models(PAYROLL_TIE)
    assert [r["model"] for r in ranked[:2]] == ["V-Model", "Waterfall"]
    assert ranked[0]["score"] == ranked[1]["score"] == 84.0
    assert top_two_tied(PAYROLL_TIE)
    assert "alphabetical" in explain_tie_break(PAYROLL_TIE)


@pytest.mark.parametrize("traits", [PAYROLL_TIE] + [t for t, _ in SCENARIOS.values()])
def test_same_ranking_regardless_of_profile_order(monkeypatch, traits):
    # All 120 orderings of SDLC_PROFILES must give one identical ranking.
    assert len(_rankings_for_every_profile_order(monkeypatch, traits)) == 1


def test_tie_broken_by_smaller_worst_single_trait_gap(monkeypatch):
    # Same weighted distance (2 x 1.0 vs 1 + 1 at weight 1.0), different worst gap.
    base = {t: 3 for t in TRAITS}
    spiky = dict(base, clarity=5)               # one gap of 2 -> worst gap 2
    even = dict(base, clarity=4, criticality=4)  # two gaps of 1 -> worst gap 1
    profiles = {"Aaa-Spiky": spiky, "Zzz-Even": even}
    assert _rankings_for_every_profile_order(monkeypatch, base, profiles) == {("Zzz-Even", "Aaa-Spiky")}
    assert top_two_tied(base)
    assert "worst single-trait gap is 1" in explain_tie_break(base)


def test_tie_broken_by_smaller_total_unweighted_gap(monkeypatch):
    # Weighted distance 2.6 for both (risk 1.3 x 2  vs  clarity 1.0 x 1 + complexity 0.8 x 2),
    # worst gap 2 for both, total unweighted gap 2 vs 3. Names chosen so that
    # alphabetical order alone would pick the wrong one.
    base = {t: 3 for t in TRAITS}
    lower_total = dict(base, risk_uncertainty=5)
    higher_total = dict(base, clarity=4, complexity=5)
    profiles = {"Aaa-HigherTotal": higher_total, "Zzz-LowerTotal": lower_total}
    assert _rankings_for_every_profile_order(monkeypatch, base, profiles) == {("Zzz-LowerTotal", "Aaa-HigherTotal")}
    assert top_two_tied(base)
    assert "total gap across all traits is 2" in explain_tie_break(base)


def test_scores_further_apart_than_tolerance_are_not_ties():
    traits, _ = SCENARIOS["social_media"]
    assert not top_two_tied(traits)
    assert explain_tie_break(traits) is None


def test_ties_always_show_as_equal_rounded_scores():
    # The UI detects a tie as equal 1-decimal scores (response shape has no raw
    # score). That is exact only while distinct weighted distances map to scores
    # more than 0.1 + TIE_TOLERANCE apart: weights must be multiples of 0.1.
    assert all(abs(w * 10 - round(w * 10)) < 1e-9 for w in TRAIT_WEIGHTS.values())
    max_distance = sum(w * 4 for w in TRAIT_WEIGHTS.values())
    smallest_step = 100 * 0.1 / max_distance
    assert smallest_step > 0.1 + TIE_TOLERANCE
