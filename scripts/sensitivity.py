"""
Sensitivity analysis of the SDLC scoring engine. For each reference project
it changes ONE thing at a time and records whether the recommended model
changes:

  A. each trait weight +/-0.2
  B. each trait weight x0.5 and x1.5
  C. each cell of each model's profile +/-1 (kept within 1-5)
  D. each of the project's trait ratings +/-1 (kept within 1-5) - this is the
     size of error an LLM rating can plausibly make

    python -m scripts.sensitivity            # print the report
    python -m scripts.sensitivity --write    # save docs/SCORING_ANALYSIS.md (with the win-share table)

The real engine (sdlc_rules.score_models) is used for every variant; the
module's profile and weight tables are swapped temporarily and restored.
Offline and deterministic. Nothing here changes the shipped profiles or
weights.
"""
import sys
from contextlib import contextmanager
from pathlib import Path

from app.agents import sdlc_rules

OUT = Path(__file__).resolve().parent.parent / "docs" / "SCORING_ANALYSIS.md"

# The first nine traits of each vector are what Agent 2 produced live for the
# four evaluation scenarios (docs/EVALUATION_BASELINE.md, 2026-11-25, first run
# of each). The six Phase 3 traits did not exist then; NEW_TRAITS are hand
# ratings from the scenario texts against the new rubrics (unstated budget or
# schedule = 2, "target dates"), not live LLM output.
NEW_TRAITS = {
    "social media": {"security_risk": 3, "system_size": 3, "legacy_dependence": 1, "continuous_delivery": 5,
                     "stakeholder_availability": 4, "budget_schedule_constraint": 2},
    "payroll": {"security_risk": 4, "system_size": 3, "legacy_dependence": 3, "continuous_delivery": 1,
                "stakeholder_availability": 2, "budget_schedule_constraint": 2},
    "railway": {"security_risk": 5, "system_size": 4, "legacy_dependence": 4, "continuous_delivery": 1,
                "stakeholder_availability": 2, "budget_schedule_constraint": 2},
    "food delivery": {"security_risk": 4, "system_size": 4, "legacy_dependence": 2, "continuous_delivery": 4,
                      "stakeholder_availability": 4, "budget_schedule_constraint": 2},
}
REFERENCES = {
    "social media": {"clarity": 2, "volatility": 5, "complexity": 4, "criticality": 1, "risk_uncertainty": 2,
                     "verification_validation": 2, "stakeholder_feedback": 5, "incremental_suitability": 5,
                     "prototyping_need": 3},
    "payroll": {"clarity": 5, "volatility": 1, "complexity": 3, "criticality": 4, "risk_uncertainty": 1,
                "verification_validation": 4, "stakeholder_feedback": 1, "incremental_suitability": 3,
                "prototyping_need": 1},
    "railway": {"clarity": 5, "volatility": 1, "complexity": 5, "criticality": 5, "risk_uncertainty": 1,
                "verification_validation": 5, "stakeholder_feedback": 1, "incremental_suitability": 2,
                "prototyping_need": 1},
    "food delivery": {"clarity": 3, "volatility": 3, "complexity": 4, "criticality": 2, "risk_uncertainty": 2,
                      "verification_validation": 3, "stakeholder_feedback": 4, "incremental_suitability": 5,
                      "prototyping_need": 2},
}
REFERENCES = {name: {**traits, **NEW_TRAITS[name]} for name, traits in REFERENCES.items()}


@contextmanager
def engine_tables(profiles=None, weights=None):
    """Temporarily replaces sdlc_rules' profile/weight tables; always restores them."""
    old_p, old_w = sdlc_rules.SDLC_PROFILES, sdlc_rules.TRAIT_WEIGHTS
    try:
        if profiles is not None:
            sdlc_rules.SDLC_PROFILES = profiles
        if weights is not None:
            sdlc_rules.TRAIT_WEIGHTS = weights
        yield
    finally:
        sdlc_rules.SDLC_PROFILES, sdlc_rules.TRAIT_WEIGHTS = old_p, old_w


def winner(traits, profiles=None, weights=None) -> str:
    with engine_tables(profiles, weights):
        return sdlc_rules.score_models(traits)[0]["model"]


def variants():
    """Yields (family, label, profiles or None, weights or None, trait_change or None)."""
    base_w, base_p = dict(sdlc_rules.TRAIT_WEIGHTS), sdlc_rules.SDLC_PROFILES
    for t in base_w:
        for d in (-0.2, +0.2):
            yield "A. weight +/-0.2", f"weight {t} {d:+.1f}", None, dict(base_w, **{t: round(base_w[t] + d, 2)}), None
    for t in base_w:
        for f in (0.5, 1.5):
            yield "B. weight x0.5 / x1.5", f"weight {t} x{f}", None, dict(base_w, **{t: round(base_w[t] * f, 3)}), None
    for m, prof in base_p.items():
        for t in prof:
            for d in (-1, +1):
                if 1 <= prof[t] + d <= 5:
                    p = {k: dict(v) for k, v in base_p.items()}
                    p[m][t] += d
                    yield "C. profile cell +/-1", f"{m} {t} {prof[t]}->{prof[t] + d}", p, None, None
    for t in base_w:
        for d in (-1, +1):
            yield "D. project rating +/-1", f"rating {t} {d:+d}", None, None, (t, d)


def analyse() -> dict:
    out = {}
    for ref, traits in REFERENCES.items():
        ranked = sdlc_rules.score_models(traits)
        base = ranked[0]["model"]
        fam = {}
        for family, label, profiles, weights, change in variants():
            t = dict(traits)
            if change:
                key, d = change
                if not 1 <= t[key] + d <= 5:
                    continue
                t[key] += d
            w = winner(t, profiles, weights)
            rec = fam.setdefault(family, {"n": 0, "flips": []})
            rec["n"] += 1
            if w != base:
                rec["flips"].append(f"{label} -> {w}")
        out[ref] = {"winner": base, "runner_up": ranked[1]["model"],
                    "margin": round(ranked[0]["score"] - ranked[1]["score"], 1), "families": fam}
    return out


def report() -> str:
    res = analyse()
    families = sorted({f for r in res.values() for f in r["families"]})
    lines = ["## Sensitivity: does one change flip the recommendation?", "",
             "Reference projects: the nine traits Agent 2 produced live for the four evaluation scenarios, plus "
             "hand ratings of the six Phase 3 traits (`NEW_TRAITS`). Each cell is flips / variants tried (`python -m scripts.sensitivity`).", "",
             "| Project | Winner (margin over runner-up) | " + " | ".join(families) + " |",
             "|---|---|" + "---|" * len(families)]
    for ref, r in res.items():
        cells = [f"{len(r['families'][f]['flips'])} / {r['families'][f]['n']}" for f in families]
        lines.append(f"| {ref} | {r['winner']} (+{r['margin']} over {r['runner_up']}) | " + " | ".join(cells) + " |")
    lines += ["", "### Every flip", ""]
    for ref, r in res.items():
        flips = [f for fam in families for f in r["families"][fam]["flips"]]
        lines.append(f"- **{ref}**: " + ("; ".join(flips) if flips else "none"))
    lines += ["", "Reading: weights and profiles are expert judgement, so the question is whether the "
              "recommendations depend on the exact numbers chosen. Flips in A-C mean that winner rests "
              "on a specific weight or profile value; flips in D show which single rating error by the "
              "LLM would change the answer. Close calls (small margins) are where flips concentrate, "
              "which is why the UI labels results within 5 points as close calls."]
    return "\n".join(lines) + "\n"


def main(write=False):
    from scripts.win_share import report as win_share_report
    doc = ("# Scoring engine analysis\n\nGenerated by `python -m scripts.sensitivity --write`. "
           "Offline and deterministic.\n\n" + win_share_report() + "\n" + report())
    print(doc)
    if write:
        OUT.write_text(doc, encoding="utf-8")
        print(f"Wrote {OUT}")


if __name__ == "__main__":
    main(write="--write" in sys.argv)
