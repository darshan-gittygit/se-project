"""
Writes docs/EVALUATION.md from saved evaluation results (problem statement section 19).

    python -m scripts.eval_report                          # latest live results folder, print to the terminal
    python -m scripts.eval_report --write                  # ... and write docs/EVALUATION.md
    python -m scripts.eval_report --results eval/results/2026-12-01 --db data/app.db --write

Inputs: eval/cases/*.json (gold, must be FROZEN to be scored), the results
folder written by scripts/run_eval.py, eval/manual_baseline/<case>.md and
eval/satisfaction/*.csv. Offline (mocked) results are labelled as such: their
numbers only show that the harness works.
"""
import argparse
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_human import (MANUAL_DIR, SATISFACTION_DIR, aggregate_satisfaction, compare_manual,  # noqa: E402
                                load_manual, load_satisfaction)
from scripts.evaluate import (CASES_DIR, decisions_from_db, evaluate_case, fmt, kb_texts, load_cases,  # noqa: E402
                              load_results, not_frozen)

OUT = ROOT / "docs" / "EVALUATION.md"
RESULTS_DIR = ROOT / "eval" / "results"

QUESTION_TEXT = {
    "q1": "Requirements correct and relevant",
    "q2": "Requirements complete enough to start design",
    "q3": "Compliance and risk findings useful and clearly advisory",
    "q4": "SDLC recommendation convincing",
    "q5": "Would save time versus the usual process",
}

# (section title, [(column label, metric group, value key, numerator key, denominator key)])
DETAIL = [
    ("Requirement extraction (precision, recall, F1)", [
        ("Precision", "extraction", "precision", "true_positives", "system_count"),
        ("Recall", "extraction", "recall", "true_positives", "gold_count"),
        ("F1", "extraction", "f1", None, None)]),
    ("Completeness", [
        ("Category coverage", "completeness", "category_coverage", None, None),
        ("Category agreement", "completeness", "category_agreement", None, None),
        ("Record completeness", "completeness", "record_completeness", None, None)]),
    ("Ambiguity detection", [
        ("Detection rate", "ambiguity", "detection_rate", "detected", "planted"),
        ("Other ambiguity flags", "ambiguity", "flags_not_matching_planted", None, None)]),
    ("Conflict detection", [
        ("Detection rate", "conflict", "detection_rate", "detected", "planted"),
        ("Other conflict flags", "conflict", "flags_not_matching_planted", None, None)]),
    ("Regulatory-control coverage", [
        ("Coverage", "regulatory_coverage", "coverage", "covered_pairs", "expected_pairs"),
        ("KB relevance (system)", None, "kb_relevance", None, None)]),
    ("Hallucination", [
        ("Hallucination rate", "hallucination", "hallucination_rate", None, None),
        ("Quote not in input", "hallucination", "quote_not_in_input_rate", None, None),
        ("No quote and no assumption", "hallucination", "no_quote_no_assumption_rate", None, None),
        ("Inferred (no quote)", "hallucination", "inferred_rate", None, None)]),
    ("Citation correctness", [
        ("Citations", "citation", "citations", None, None),
        ("Evidence found in source", "citation", "evidence_found_rate", None, None),
        ("Source label matches gold", "citation", "label_match_rate", None, None),
        ("Both (correct)", "citation", "citation_correctness", None, None)]),
    ("SDLC recommendation", [
        ("Recommended", "sdlc", "recommended", None, None), ("Gold top", "sdlc", "gold_top", None, None),
        ("Top-1", "sdlc", "top1", None, None), ("Top-3", "sdlc", "top3", None, None),
        ("Acceptable", "sdlc", "acceptable", None, None), ("Gold model's rank", "sdlc", "gold_rank", None, None)]),
    ("Human correction rate", [
        ("Rate", "human_correction", "rate", "corrections", "decisions")]),
    ("Traceability coverage", [
        ("Fully traced", "traceability", "coverage", None, None), ("Backward (source)", "traceability", "backward", None, None),
        ("Forward (artefact)", "traceability", "forward", None, None),
        ("Verification (acceptance criteria)", "traceability", "verification", None, None)]),
    ("Processing time", [
        ("Seconds", None, "processing_seconds", None, None)]),
]


def value(m: dict, group, key, num=None, den=None) -> str:
    src = m if group is None else m[group]
    v = src.get(key)
    out = fmt(v)
    if num and den and src.get(den):
        out += f" ({src[num]}/{src[den]})"
    return out


def mean_of(metrics: list[dict], group, key):
    vals = [(m if group is None else m[group]).get(key) for m in metrics]
    nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, str)]
    return fmt(statistics.mean(nums)) if nums else "n/a"


def table(head: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(r) + " |" for r in rows]


def latest_live(results_dir: Path = RESULTS_DIR) -> Path | None:
    dirs = sorted(p for p in results_dir.glob("*") if p.is_dir() and not p.name.endswith("-offline")) if results_dir.exists() else []
    return dirs[-1] if dirs else None


def build_report(cases: list[dict], records: list[dict], results_dir: Path | None, db: Path | None = None,
                 manual: dict | None = None, satisfaction: list[dict] | None = None) -> str:
    by_case = {c["id"]: c for c in cases}
    kb = kb_texts()
    metrics, skipped = [], []
    for rec in records:
        case = by_case.get(rec["case_id"])
        if case is None or not_frozen(case):
            skipped.append(rec["case_id"])
            continue
        decisions = decisions_from_db(db, rec["result"]["run_id"]) if db and rec["result"].get("run_id") else None
        metrics.append(evaluate_case(case, rec, decisions, kb))
    modes = {r.get("mode") for r in records}
    L = ["# Requora AI - Evaluation (problem statement section 19)", "",
         f"Generated by `python -m scripts.eval_report` on {date.today().isoformat()}; do not edit by hand. "
         "Metric definitions are in `scripts/evaluate.py` and in the last section.", ""]
    if "offline" in modes:
        L += ["> **Offline harness results.** These runs used mocked model replies built from the gold itself, so the "
              "numbers below only show that the harness works. They say nothing about the system's quality.", ""]

    L += ["## Case studies", ""]
    run_ids = {r["case_id"] for r in records}
    L += table(["Case", "Type", "Scenario words", "Gold requirements", "Planted ambiguities", "Planted conflicts",
                "Gold SDLC", "Gold status", "Result"],
               [[c["id"], c.get("case_type", ""), str(len(c["scenario"].split())), str(len(c["gold"]["requirements"])),
                 str(len(c["gold"].get("ambiguities", []))), str(len(c["gold"].get("conflicts", []))),
                 c["gold"]["sdlc"]["top"], "FROZEN" if not not_frozen(c) else "DRAFT",
                 "yes" if c["id"] in run_ids else "-"] for c in cases])
    L.append("")
    if results_dir:
        L += [f"Results folder: `{results_dir.relative_to(ROOT) if results_dir.is_relative_to(ROOT) else results_dir}`"
              + (f"; human corrections read from `{db}`" if db else "") + ".", ""]
    if skipped:
        L += [f"Not scored because their gold is not FROZEN (or the case file is missing): {', '.join(skipped)}.", ""]

    if not metrics:
        L += ["## Results", "", "No scored results yet. The gold of every case must be reviewed and FROZEN, then "
              "`python -m scripts.run_eval` runs the cases live and this report is regenerated.", ""]
    else:
        ids = [m["case_id"] for m in metrics]
        L += ["## Summary", ""]
        from scripts.evaluate import SUMMARY
        rows = [[label] + [fmt(m[g][k]) for m in metrics] + [mean_of(metrics, g, k)] for label, g, k in SUMMARY]
        rows.append(["Processing time (s)"] + [fmt(m["processing_seconds"]) for m in metrics]
                    + [mean_of(metrics, None, "processing_seconds")])
        L += table(["Metric"] + ids + ["Mean"], rows) + [""]
        L += ["Means are over the cases where the metric is defined (n/a = no denominator, e.g. no decisions recorded).", ""]
        for title, cols in DETAIL:
            L += [f"### {title}", ""]
            L += table(["Case"] + [c[0] for c in cols],
                       [[m["case_id"]] + [value(m, g, k, n, d) for _, g, k, n, d in cols] for m in metrics])
            L.append("")
        L += ["### What was missed", ""]
        L += table(["Case", "Gold requirements not extracted", "Ambiguities missed", "Conflicts missed", "Source pairs missed"],
                   [[m["case_id"], ", ".join(m["extraction"]["missed_gold_ids"]) or "-",
                     ", ".join(m["ambiguity"]["missed_ids"]) or "-", ", ".join(m["conflict"]["missed_ids"]) or "-",
                     ", ".join(m["regulatory_coverage"]["missed"]) or "-"] for m in metrics])
        L.append("")
        changed = [m["case_id"] for m in metrics if m["gold_changed_since_run"]]
        if changed:
            L += [f"> Gold changed after these runs were made: {', '.join(changed)}. Re-run them or explain the change.", ""]

    L += ["## Manual baseline", ""]
    manual = manual or {}
    comps = []
    for cid, man in manual.items():
        case = by_case.get(cid)
        if case is None or not_frozen(case):
            continue
        comps.append(compare_manual(case, man, next((m for m in metrics if m["case_id"] == cid), None)))
    if comps:
        L += table(["Case", "Manual P / R / F1", "System P / R / F1", "Manual minutes", "System minutes",
                    "Review minutes", "Time saved (minutes)", "Manual SDLC", "Gold SDLC"],
                   [[c["case_id"], " / ".join(fmt(c["manual"][k]) for k in ("precision", "recall", "f1")),
                     " / ".join(fmt(c["system"][k]) for k in ("precision", "recall", "f1")),
                     fmt(c["manual_minutes"]), fmt(c["system_minutes"]), fmt(c["review_minutes"]),
                     fmt(c["time_saved_minutes"]), c["manual_sdlc"] or "-", c["gold_sdlc"]] for c in comps])
        L += ["", "Time saved = manual minutes - (system processing + the analyst's review of the system output). "
              "Both sets of requirements are scored against the same gold with the same keyword matching.", ""]
    else:
        L += ["No manual baseline recorded yet (copy `eval/manual_baseline/TEMPLATE.md` to `<case>.md` for two cases).", ""]

    L += ["## Stakeholder satisfaction", ""]
    agg = aggregate_satisfaction(satisfaction or [])
    if agg["responses"]:
        L += [f"{agg['responses']} response(s) from {agg['respondents']} respondent(s)"
              + (f" ({', '.join(agg['roles'])})" if agg["roles"] else "") + f"; overall mean {fmt(agg['overall_mean'])} of 5.", ""]
        L += table(["Question", "n", "Mean", "Median", "Agree (4-5)", "1 / 2 / 3 / 4 / 5"],
                   [[f"{q}: {QUESTION_TEXT[q]}", str(v["n"]), fmt(v["mean"]), fmt(v["median"]), fmt(v["agree_share"]),
                     " / ".join(str(v["distribution"][str(k)]) for k in range(1, 6))] for q, v in agg["questions"].items()])
        L.append("")
    else:
        L += ["No satisfaction responses yet (form: `eval/satisfaction_form.md`; answers: `eval/satisfaction/*.csv`).", ""]

    L += ["## How each metric is computed", "",
          "- **Extraction precision / recall / F1:** gold and system requirements are paired one-to-one when every keyword "
          "group of the gold requirement has a word-start match in the system requirement's text. Precision = pairs / "
          "system requirements (n/a when the system returned none); recall = pairs / gold requirements.",
          "- **Completeness:** share of the gold's requirement categories with at least one extracted requirement. Also "
          "shown: category agreement (paired requirements whose system categories include the gold category) and record "
          "completeness (system records with acceptance criteria, justification, categories and a quote or assumption).",
          "- **Ambiguity detection:** planted vague phrases named by an ambiguity or untestable flag (in its reason or in a "
          "flagged requirement's text) over all planted phrases. Flags that match nothing planted are counted separately; "
          "they may be real findings.",
          "- **Conflict detection:** planted conflicts for which a conflict flag links requirements paired with both sides, "
          "or whose reason matches the conflict's keyword groups, over all planted conflicts.",
          "- **Regulatory-control coverage:** (gold requirement, expected knowledge-base source) pairs present in the paired "
          "system requirement's compliance mapping, over all expected pairs.",
          "- **Hallucination rate:** requirements whose quote is not in the (masked) input, or that have neither a quote nor "
          "an assumption, over all requirements. The app already rejects such records, so this mainly confirms the guard.",
          "- **Citation correctness:** compliance mappings whose evidence quote occurs in that source's knowledge-base text "
          "and whose source is one the gold expects for the requirement, over the mappings of paired requirements.",
          "- **SDLC accuracy:** top-1 = the recommended model is the gold choice; top-3 = the gold choice is in the system's "
          "top three; acceptable = the recommendation is the gold choice or one of its listed alternatives.",
          "- **Human correction rate:** (modify + reject) over all review decisions (accept, reject, modify, regenerate) "
          "recorded for the run in the app database; n/a until someone reviews the run in the UI.",
          "- **Traceability coverage:** requirements with a backward link (quote or assumption), a forward link (a story, "
          "use case, process workflow, data or interface requirement) and acceptance criteria, over all requirements.",
          "- **Processing time:** wall-clock seconds of the `/agents/pipeline` request, including retries and back-off.", ""]

    L += ["## Limitations", "",
          "- **Small sample:** six case studies, one run each; differences of one requirement move a rate by 6-7 points. "
          "No confidence intervals are claimed.",
          "- **One labeller:** the gold, the planted problems and the manual baseline were written by the same person, so "
          "they share that person's view of the scenarios; there is no inter-rater agreement figure. The gold was frozen "
          "before the runs (its hash is stored with each result) to avoid tuning it to the output.",
          "- **Keyword matching:** a consistent but approximate stand-in for human judgement: unlisted synonyms count as "
          "misses, broad keywords can over-match, and merged requirements pair with only one gold item.",
          "- **Planted problems:** detection rates are recall over planted items only; extra flags are reported but not "
          "judged, so there is no false-positive rate.",
          "- **Synthetic scenarios:** realistic but written for this evaluation, not real or anonymised bank documents.",
          "- **Free-tier models:** results depend on whichever Gemini model answered (listed per run), on rate limits and "
          "retries (which inflate processing time), and on run-to-run variation in trait ratings.",
          "- **Knowledge base:** a small set of paraphrased principles; insurance and regulatory reporting have no "
          "domain-specific source, so their regulatory coverage tests mostly the generic baseline.",
          "- **Satisfaction:** a handful of respondents (classmates or the author) is not a stakeholder survey.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write docs/EVALUATION.md from saved evaluation results.")
    ap.add_argument("--results", type=Path, help="results folder (default: latest live folder in eval/results)")
    ap.add_argument("--cases-dir", type=Path, default=CASES_DIR)
    ap.add_argument("--db", type=Path, help="app database with review decisions (human correction rate)")
    ap.add_argument("--write", action="store_true", help=f"write {OUT.relative_to(ROOT)}")
    args = ap.parse_args(argv)
    results_dir = args.results or latest_live()
    records = load_results(results_dir) if results_dir and results_dir.exists() else []
    try:
        text = build_report(load_cases(args.cases_dir), records, results_dir, args.db, load_manual(MANUAL_DIR),
                            load_satisfaction(SATISFACTION_DIR))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"Wrote {OUT}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
