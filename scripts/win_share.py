"""
Win-share analysis of the SDLC scoring engine: across the space of possible
trait vectors, how often does each model rank first, and what are its mean,
lowest possible and highest compatibility scores?

With 15 traits the space has 5^15 (about 3.1e10) vectors, too many to
enumerate, so the shares come from a seeded uniform random sample of
SAMPLE_SIZE vectors (the standard error is printed; about 0.05 percentage
points). Lowest and highest possible scores are computed exactly.

    python -m scripts.win_share

Why this matters: every model is scored by the same formula with no bonus,
yet win share is not equal. A profile whose values sit near the middle of
the 1-5 scale is, on average, closer to an arbitrary trait vector than a
profile made of 1s and 5s, so it wins a larger share of the space. Uniform
coverage of all vectors is not a realistic prior for real projects; the
table describes the geometry of the profiles, not how often each model
should be recommended.

Offline and deterministic for a given seed (no LLM, no network). Uses numpy for speed; the
winner rule mirrors sdlc_rules._rank exactly (weighted distance, then worst
single-trait gap, then total gap, then name), and tests/test_analysis_scripts.py
checks it against the real engine.
"""
import numpy as np

from app.agents import sdlc_rules


SAMPLE_SIZE = 1_000_000
SEED = 305
CHUNK = 200_000


def _sample(n_traits: int, size: int = SAMPLE_SIZE, seed: int = SEED):
    rng = np.random.default_rng(seed)
    left = size
    while left > 0:
        n = min(CHUNK, left)
        yield rng.integers(1, 6, size=(n, n_traits), dtype=np.int8)
        left -= n


def analyse(profiles=None, weights=None, grid=None) -> dict:
    profiles = profiles or sdlc_rules.SDLC_PROFILES
    weights = weights or sdlc_rules.TRAIT_WEIGHTS
    traits = list(weights)
    names = list(profiles)
    w10 = np.array([round(weights[t] * 10) for t in traits], dtype=np.int32)  # weights are multiples of 0.1
    max_wd = sum(weights[t] * sdlc_rules.MAX_TRAIT_DISTANCE for t in traits)
    rank_of = {n: i for i, n in enumerate(sorted(names))}
    profs = {n: np.array([profiles[n][t] for t in traits], dtype=np.int8) for n in names}

    chunks = [grid] if grid is not None else _sample(len(traits))
    wins = np.zeros(len(names), dtype=np.int64)
    score_sum = np.zeros(len(names))
    winners, n_total = [], 0
    for block in chunks:
        keys = []
        for i, name in enumerate(names):
            gaps = np.abs(block - profs[name]).astype(np.int32)
            wd10 = gaps @ w10                       # weighted distance x 10, exact integer
            worst, total = gaps.max(axis=1), gaps.sum(axis=1)
            score_sum[i] += ((1 - wd10 / 10 / max_wd) * 100).sum()
            # lexicographic key, smaller is better: distance, worst gap, total gap, name
            keys.append(((wd10.astype(np.int64) * 10 + worst) * 100 + total) * 10 + rank_of[name])
        winner = np.argmin(np.stack(keys, axis=1), axis=1)
        wins += np.bincount(winner, minlength=len(names))
        winners.append(winner)
        n_total += len(block)
    rows = []
    for i, n in enumerate(names):
        far = sum(weights[t] * max(profiles[n][t] - 1, 5 - profiles[n][t]) for t in traits)
        share = wins[i] / n_total
        rows.append({"model": n, "win_share": float(share * 100),
                     "std_error": float(np.sqrt(share * (1 - share) / n_total) * 100),
                     "mean": float(score_sum[i] / n_total), "min": float((1 - far / max_wd) * 100),
                     "max": 100.0, "max_distance": far})
    return {"n": n_total, "sampled": grid is None, "rows": rows,
            "winner_index": np.concatenate(winners), "names": names}


def report() -> str:
    res = analyse()
    all3 = sdlc_rules.score_models({t: 3 for t in sdlc_rules.TRAIT_WEIGHTS})
    lines = [
        "## Win share over the trait-vector space", "",
        f"A seeded uniform random sample of {res['n']:,} vectors (each of the {len(sdlc_rules.TRAIT_WEIGHTS)} traits "
        f"in 1-5; standard error at most {max(r['std_error'] for r in res['rows']):.2f} points), scored by the engine's "
        "own formula and tie-break (`python -m scripts.win_share`). Lowest and highest possible scores are exact.", "",
        "| Model | Wins (share of all vectors) | Mean score | Lowest possible | Highest | Largest possible weighted distance from its profile |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(res["rows"], key=lambda r: -r["win_share"]):
        lines.append(f"| {r['model']} | {r['win_share']:.1f}% | {r['mean']:.1f} | {r['min']:.1f} | "
                     f"{r['max']:.1f} | {r['max_distance']:.1f} |")
    most = max(res["rows"], key=lambda r: r["win_share"])
    least = min(res["rows"], key=lambda r: r["win_share"])
    lines += [
        "",
        "A project rated 3 on every trait scores: " + ", ".join(f"{m['model']} {m['score']}" for m in all3) + ".",
        "",
        "Reading: no model gets a bonus (each scores exactly 100 on its own profile, and the formula "
        f"is the same for all), but the profiles are not equally central. {most['model']}'s profile "
        f"sits nearest the middle of the scale, so no project can be far from it (largest possible "
        f"weighted distance {most['max_distance']:.1f}, against {least['max_distance']:.1f} for "
        f"{least['model']}), and it wins the largest share of the uniform space. Real projects "
        "are not uniformly spread over that space, so this is a property of the profile geometry to "
        "be aware of, not a measured recommendation rate. The profiles and weights were deliberately "
        "NOT adjusted to equalise these shares: that would trade documented engineering judgement "
        "for an arbitrary statistical target.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(report())
