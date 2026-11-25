"""The win-share and sensitivity scripts must describe the REAL engine. Offline."""
import random

import numpy as np
import pytest

from app.agents import sdlc_rules
from scripts import sensitivity, win_share

TRAITS = list(sdlc_rules.TRAIT_WEIGHTS)


@pytest.fixture(scope="module")
def full_space():
    return win_share.analyse()


def test_vectorised_winner_matches_the_engine_on_random_vectors(full_space):
    rng = random.Random(305)
    grid = np.array([[rng.randint(1, 5) for _ in TRAITS] for _ in range(3000)], dtype=np.int8)
    grid = np.vstack([grid, np.full((1, len(TRAITS)), 3, dtype=np.int8)])
    res = win_share.analyse(grid=grid)
    for row, idx in zip(grid, res["winner_index"]):
        traits = dict(zip(TRAITS, map(int, row)))
        assert res["names"][idx] == sdlc_rules.score_models(traits)[0]["model"]


def test_documented_win_shares(full_space):
    # Pinned so that any change to profiles or weights forces docs/SCORING_ANALYSIS.md
    # (python -m scripts.sensitivity --write) to be regenerated and re-read.
    # Seeded sample of 1,000,000 vectors; standard error about 0.05 points.
    shares = {r["model"]: r["win_share"] for r in full_space["rows"]}
    expected = {"Incremental": 31.1, "Spiral": 18.4, "Agile-V": 16.6, "DevSecOps": 11.8,
                "Agile": 9.1, "Waterfall": 7.6, "V-Model": 5.4}
    assert set(shares) == set(expected)
    assert all(abs(shares[m] - expected[m]) < 0.2 for m in expected), shares
    assert full_space["n"] == win_share.SAMPLE_SIZE and full_space["sampled"]
    assert sum(r["win_share"] for r in full_space["rows"]) == pytest.approx(100.0)


def test_engine_tables_are_restored_even_on_error():
    before = (sdlc_rules.SDLC_PROFILES, sdlc_rules.TRAIT_WEIGHTS)
    with pytest.raises(RuntimeError):
        with sensitivity.engine_tables(profiles={}, weights={}):
            raise RuntimeError
    assert (sdlc_rules.SDLC_PROFILES, sdlc_rules.TRAIT_WEIGHTS) == before


def test_sensitivity_baselines_match_the_live_recommendations():
    res = sensitivity.analyse()
    assert {k: v["winner"] for k, v in res.items()} == {
        "social media": "Agile", "payroll": "V-Model", "railway": "V-Model", "food delivery": "DevSecOps"}
    # every family was actually exercised for every project
    for r in res.values():
        assert all(f["n"] > 0 for f in r["families"].values()) and len(r["families"]) == 4


def test_shipped_profiles_and_weights_are_unchanged_by_the_analysis():
    snapshot = ({k: dict(v) for k, v in sdlc_rules.SDLC_PROFILES.items()}, dict(sdlc_rules.TRAIT_WEIGHTS))
    sensitivity.analyse()
    assert ({k: dict(v) for k, v in sdlc_rules.SDLC_PROFILES.items()}, dict(sdlc_rules.TRAIT_WEIGHTS)) == snapshot
