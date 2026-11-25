"""
Evaluation metrics (problem statement section 19), computed from saved
pipeline results and hand-labelled gold data. Offline: no model calls.

    python -m scripts.evaluate eval/results/2026-12-01            # table per case
    python -m scripts.evaluate eval/results/2026-12-01 --json     # full metric details
    python -m scripts.evaluate eval/results/2026-12-01 --db data/app.db   # + human correction rate

Case files live in eval/cases/<case>.json; their "gold" section is written by
hand and must be FROZEN (every gold item's "status") before it is used, so the
gold cannot be tuned after seeing the system's output. Results are written by
scripts/run_eval.py.

How requirements are matched (and the limits of it)
---------------------------------------------------
Each gold requirement carries keyword groups, e.g. [["mfa", "two factor"],
["login", "sign in"]]. A system requirement matches when EVERY group has at
least one keyword in its requirement text. Keywords are word-start prefixes
("verif" matches "verify" and "verification") after the text is lower-cased
and reduced to letters, digits and "%" (the same normalisation the app uses
for quotes). Gold and system requirements are then paired one-to-one with a
maximum bipartite matching, so one system requirement never counts twice.
Limits: synonyms the labeller did not list are missed (lower recall than a
human judge would give), a very broad keyword can match the wrong requirement
(higher precision than deserved), and a system requirement that merges two
gold requirements can only be paired with one of them. Results are a
consistent, repeatable approximation of a human judgement, not a substitute.
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "eval" / "cases"
KB_DIR = ROOT / "knowledge_base"
FROZEN, DRAFT = "FROZEN", "DRAFT"
AMBIGUITY_FLAG_TYPES = ("ambiguity", "untestable")
REVIEW_ACTIONS = ("accept", "reject", "modify", "regenerate")


class GoldNotFrozen(Exception):
    """A case's gold section still has DRAFT (or missing) status fields."""


# --- cases and gold ----------------------------------------------------------------

def load_case(path: Path) -> dict:
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("id", "scenario", "gold"):
        if key not in case:
            raise ValueError(f"{path}: missing '{key}'")
    return case


def load_cases(cases_dir: Path = CASES_DIR, ids: list[str] | None = None) -> list[dict]:
    cases = {p.stem: load_case(p) for p in sorted(Path(cases_dir).glob("*.json"))}
    if ids:
        unknown = [i for i in ids if i not in cases]
        if unknown:
            raise ValueError(f"Unknown case(s): {', '.join(unknown)}. Known: {', '.join(cases)}.")
        return [cases[i] for i in ids]
    return list(cases.values())


def not_frozen(case: dict) -> list[str]:
    """Paths of every gold field whose status is not FROZEN (empty list = usable)."""
    g = case["gold"]
    out = [] if g.get("status") == FROZEN else ["gold"]
    for key in ("requirements", "ambiguities", "conflicts"):
        out += [f"gold.{key}.{item.get('id', i)}" for i, item in enumerate(g.get(key, []))
                if item.get("status") != FROZEN]
    if g.get("sdlc", {}).get("status") != FROZEN:
        out.append("gold.sdlc")
    return out


def require_frozen(case: dict) -> None:
    bad = not_frozen(case)
    if bad:
        raise GoldNotFrozen(f"Case {case['id']}: gold is not FROZEN ({len(bad)} field(s), e.g. {', '.join(bad[:3])}). "
                            "Review the gold, then set every status to FROZEN.")


def gold_hash(case: dict) -> str:
    """Short SHA-256 of the gold section, stored with each result so later gold edits are visible."""
    return hashlib.sha256(json.dumps(case["gold"], sort_keys=True).encode()).hexdigest()[:12]


# --- text matching ------------------------------------------------------------------

def norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9%]+", " ", str(text or "").lower()).split())


def has_keyword(text: str, keyword: str) -> bool:
    k = norm(keyword)
    return bool(k) and re.search(r"(?<![a-z0-9%])" + re.escape(k), norm(text)) is not None


def matches(text: str, groups: list[list[str]]) -> bool:
    return bool(groups) and all(any(has_keyword(text, k) for k in g) for g in groups)


def ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None:
        return None if r is None else 0.0
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


# --- requirements --------------------------------------------------------------------

def system_requirements(result: dict) -> list[dict]:
    req = result.get("requirements") or {}
    return list(req.get("functional_requirements") or []) + list(req.get("non_functional_requirements") or [])


def match_requirements(gold: list[dict], system: list[dict]) -> dict[str, str]:
    """{gold id: system id}, a maximum one-to-one matching (Kuhn's augmenting paths)."""
    edges = {g["id"]: [s["id"] for s in system if matches(s.get("requirement", ""), g["keyword_groups"])] for g in gold}
    owner: dict[str, str] = {}

    def assign(gid: str, seen: set) -> bool:
        for sid in edges[gid]:
            if sid in seen:
                continue
            seen.add(sid)
            if sid not in owner or assign(owner[sid], seen):
                owner[sid] = gid
                return True
        return False

    for g in gold:
        assign(g["id"], set())
    return {gid: sid for sid, gid in owner.items()}


def gold_self_check(case: dict) -> dict:
    """
    Sanity check for the labeller: each gold statement should match its own
    keyword groups, and ideally no other gold requirement's groups (overlaps
    are allowed - the one-to-one matching resolves them - but worth a look).
    Also checks expected sources exist in the knowledge base and conflict sides name real gold ids.
    """
    gold = case["gold"]["requirements"]
    ids = {g["id"] for g in gold}
    kb = {p.stem for p in KB_DIR.glob("*.txt")}
    return {
        "own_keywords_fail": [g["id"] for g in gold if not matches(g["statement"], g["keyword_groups"])],
        "overlaps": {g["id"]: [o["id"] for o in gold if o is not g and matches(g["statement"], o["keyword_groups"])]
                     for g in gold if any(o is not g and matches(g["statement"], o["keyword_groups"]) for o in gold)},
        "unknown_sources": sorted({s for g in gold for s in g.get("expected_sources", []) if s not in kb}),
        "bad_conflict_sides": [c["id"] for c in case["gold"].get("conflicts", [])
                               if not all(gid in ids for side in c["sides"] for gid in side)],
    }


def extraction(gold: list[dict], system: list[dict], matching: dict) -> dict:
    tp = len(matching)
    p, r = ratio(tp, len(system)), ratio(tp, len(gold))
    return {"true_positives": tp, "system_count": len(system), "gold_count": len(gold),
            "precision": p, "recall": r, "f1": f1(p, r),
            "missed_gold_ids": [g["id"] for g in gold if g["id"] not in matching],
            "unmatched_system_ids": [s["id"] for s in system if s["id"] not in matching.values()]}


def completeness(gold: list[dict], system: list[dict], matching: dict) -> dict:
    """Share of gold categories with at least one matched requirement, plus classification agreement and record fields."""
    cats = sorted({g["category"] for g in gold})
    covered = sorted({g["category"] for g in gold if g["id"] in matching})
    by_id = {s["id"]: s for s in system}
    agree = sum(g["category"] in (by_id[matching[g["id"]]].get("categories") or []) for g in gold if g["id"] in matching)
    fields_ok = sum(bool(s.get("acceptance_criteria")) and bool(s.get("business_justification")) and bool(s.get("categories"))
                    and (bool(s.get("source_text")) or bool(s.get("assumptions"))) for s in system)
    return {"category_coverage": ratio(len(covered), len(cats)), "categories": cats,
            "missing_categories": [c for c in cats if c not in covered],
            "category_agreement": ratio(agree, len(matching)),
            "record_completeness": ratio(fields_ok, len(system))}


# --- quality checks --------------------------------------------------------------------

def _flags(result: dict, types: tuple) -> list[dict]:
    return [f for f in (result.get("quality") or {}).get("flags", []) if f.get("type") in types]


def ambiguity_detection(case: dict, result: dict) -> dict:
    """Planted ambiguities found by an ambiguity/untestable flag (its reason or a flagged requirement names them)."""
    texts = {s["id"]: s.get("requirement", "") for s in system_requirements(result)}
    flags = _flags(result, AMBIGUITY_FLAG_TYPES)
    flag_texts = [" ".join([f.get("reason", "")] + [texts.get(i, "") for i in f.get("requirement_ids", [])]) for f in flags]
    planted = case["gold"].get("ambiguities", [])
    found, used = [], set()
    for a in planted:
        hit = [i for i, t in enumerate(flag_texts) if any(has_keyword(t, k) for k in a["keywords"])]
        if hit:
            found.append(a["id"])
            used.update(hit)
    return {"planted": len(planted), "detected": len(found), "detection_rate": ratio(len(found), len(planted)),
            "detected_ids": found, "missed_ids": [a["id"] for a in planted if a["id"] not in found],
            "flags": len(flags), "flags_not_matching_planted": len(flags) - len(used)}


def conflict_detection(case: dict, result: dict, matching: dict) -> dict:
    """A planted conflict counts when a conflict flag links requirements matched to both sides, or its reason names both."""
    flags = _flags(result, ("conflict",))
    planted = case["gold"].get("conflicts", [])
    found, used = [], set()
    for c in planted:
        sides = [{matching[g] for g in side if g in matching} for side in c["sides"]]
        for i, f in enumerate(flags):
            ids = set(f.get("requirement_ids", []))
            by_ids = all(side & ids for side in sides) and len(ids) >= 2
            if by_ids or matches(f.get("reason", ""), c.get("keyword_groups", [])):
                found.append(c["id"])
                used.add(i)
                break
    return {"planted": len(planted), "detected": len(found), "detection_rate": ratio(len(found), len(planted)),
            "detected_ids": found, "missed_ids": [c["id"] for c in planted if c["id"] not in found],
            "flags": len(flags), "flags_not_matching_planted": len(flags) - len(used)}


# --- compliance --------------------------------------------------------------------

def _mappings(result: dict) -> dict[str, list[dict]]:
    return {m["requirement_id"]: m.get("mappings", [])
            for m in (result.get("compliance_mapping") or {}).get("requirements", [])}


def regulatory_coverage(case: dict, result: dict, matching: dict) -> dict:
    """Expected (gold requirement, KB source) pairs found in the matched requirement's compliance mapping."""
    maps = _mappings(result)
    pairs = [(g["id"], s) for g in case["gold"]["requirements"] for s in g.get("expected_sources", [])]
    covered = [(gid, s) for gid, s in pairs
               if gid in matching and s in {m["source"] for m in maps.get(matching[gid], [])}]
    return {"expected_pairs": len(pairs), "covered_pairs": len(covered), "coverage": ratio(len(covered), len(pairs)),
            "missed": [f"{gid}/{s}" for gid, s in pairs if (gid, s) not in covered]}


def kb_texts(kb_dir: Path = KB_DIR) -> dict[str, str]:
    return {p.stem: norm(p.read_text(encoding="utf-8")) for p in Path(kb_dir).glob("*.txt")}


def citation_correctness(case: dict, result: dict, matching: dict, kb: dict[str, str]) -> dict:
    """
    Every cited mapping: is its evidence quote really in that source's text, and
    is the source one the gold expects for the requirement? Label agreement is
    judged only for requirements matched to a gold requirement.
    """
    gold_sources = {matching[g["id"]]: set(g.get("expected_sources", [])) for g in case["gold"]["requirements"]
                    if g["id"] in matching}
    total = found = judged = label_ok = both = 0
    wrong = []
    for rid, maps in _mappings(result).items():
        for m in maps:
            total += 1
            ev = norm(m.get("evidence", ""))
            in_source = bool(ev) and ev in kb.get(m.get("source"), "")
            found += in_source
            if rid in gold_sources:
                judged += 1
                ok = m.get("source") in gold_sources[rid]
                label_ok += ok
                both += ok and in_source
                if not (ok and in_source):
                    wrong.append(f"{rid}/{m.get('source')}")
    return {"citations": total, "evidence_found_rate": ratio(found, total), "judged": judged, "unjudged": total - judged,
            "label_match_rate": ratio(label_ok, judged), "citation_correctness": ratio(both, judged), "incorrect": wrong}


# --- grounding -------------------------------------------------------------------------

def input_text(result: dict) -> str:
    parts = [result.get("scenario", "")]
    parts += [f"{a.get('question', '')} {a.get('answer', '')}" for a in result.get("answers") or []]
    parts += [d.get("text", "") for d in result.get("documents") or []]
    return norm(" ".join(parts))


def hallucination(result: dict) -> dict:
    """Requirements whose quote is not in the input, and requirements with neither a quote nor an assumption."""
    reqs, text = system_requirements(result), input_text(result)
    bad_quote = [r["id"] for r in reqs if r.get("source_text") and norm(r["source_text"]) not in text]
    ungrounded = [r["id"] for r in reqs if not r.get("source_text") and not r.get("assumptions")]
    either = sorted(set(bad_quote) | set(ungrounded))
    return {"requirements": len(reqs), "quote_not_in_input": bad_quote, "no_quote_no_assumption": ungrounded,
            "quote_not_in_input_rate": ratio(len(bad_quote), len(reqs)),
            "no_quote_no_assumption_rate": ratio(len(ungrounded), len(reqs)),
            "hallucination_rate": ratio(len(either), len(reqs)),
            "inferred_rate": ratio(sum(not r.get("source_text") for r in reqs), len(reqs))}


# --- SDLC, human corrections, traceability ------------------------------------------------

def sdlc_accuracy(case: dict, result: dict) -> dict:
    s = result.get("sdlc_recommendation") or {}
    gold = case["gold"]["sdlc"]
    ranked = [m["model"] for m in sorted(s.get("scored_models") or [], key=lambda m: m.get("rank", 99))]
    chosen = s.get("recommended_model")
    acceptable = {gold["top"]} | {a["model"] for a in gold.get("acceptable", [])}
    return {"recommended": chosen, "gold_top": gold["top"], "top1": chosen == gold["top"] if chosen else None,
            "top3": gold["top"] in ranked[:3] if ranked else None,
            "acceptable": chosen in acceptable if chosen else None, "gold_rank": ranked.index(gold["top"]) + 1
            if gold["top"] in ranked else None}


def human_correction(decisions: list[dict] | None) -> dict:
    """(modify + reject) / all review decisions (accept, reject, modify, regenerate) on the run."""
    if not decisions:
        return {"decisions": 0, "corrections": 0, "rate": None}
    reviews = [d for d in decisions if d.get("action") in REVIEW_ACTIONS]
    corr = [d for d in reviews if d["action"] in ("modify", "reject")]
    by_action = {a: sum(d["action"] == a for d in reviews) for a in REVIEW_ACTIONS}
    return {"decisions": len(reviews), "corrections": len(corr), "rate": ratio(len(corr), len(reviews)), "by_action": by_action}


def decisions_from_db(db_path: Path, run_id: str) -> list[dict]:
    """Decision rows of one run, read-only (the evaluation never writes to the app database)."""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM decisions WHERE run_id = ? ORDER BY id", (run_id,))]
    finally:
        conn.close()


ARTEFACT_LINKS = ("user_stories_ids", "use_cases_ids", "process_workflows_ids", "data_requirements_ids",
                  "interface_requirements_ids")


def traceability(result: dict) -> dict:
    """
    A requirement is fully traced when it has a backward link (source quote or
    assumption), a forward link (at least one story, use case, process workflow,
    data or interface requirement) and a verification link (acceptance criteria).
    """
    reqs = {r["id"]: r for r in system_requirements(result)}
    rows = {r["requirement_id"]: r for r in (result.get("artefacts") or {}).get("traceability_matrix", [])}
    back = fwd = ver = full = 0
    gaps = []
    for rid, r in reqs.items():
        row = rows.get(rid, {})
        src = row.get("source") or {}
        b = bool(r.get("source_text") or r.get("assumptions") or src.get("statement") or src.get("assumptions"))
        f = any(row.get(k) for k in ARTEFACT_LINKS)
        v = bool(r.get("acceptance_criteria"))
        back, fwd, ver = back + b, fwd + f, ver + v
        full += b and f and v
        if not (b and f and v):
            gaps.append(rid)
    n = len(reqs)
    return {"requirements": n, "coverage": ratio(full, n), "backward": ratio(back, n), "forward": ratio(fwd, n),
            "verification": ratio(ver, n), "not_fully_traced": gaps}


# --- one case --------------------------------------------------------------------------

def evaluate_case(case: dict, record: dict, decisions: list[dict] | None = None, kb: dict | None = None) -> dict:
    """All section-19 metrics for one saved run record (see scripts/run_eval.py for its shape)."""
    require_frozen(case)
    result = record["result"]
    gold, system = case["gold"]["requirements"], system_requirements(result)
    matching = match_requirements(gold, system)
    decisions = decisions if decisions is not None else record.get("decisions")
    return {
        "case_id": case["id"], "matching": matching,
        "gold_changed_since_run": bool(record.get("gold_sha256")) and record["gold_sha256"] != gold_hash(case),
        "extraction": extraction(gold, system, matching),
        "completeness": completeness(gold, system, matching),
        "ambiguity": ambiguity_detection(case, result),
        "conflict": conflict_detection(case, result, matching),
        "regulatory_coverage": regulatory_coverage(case, result, matching),
        "kb_relevance": (result.get("compliance") or {}).get("kb_relevance"),
        "hallucination": hallucination(result),
        "citation": citation_correctness(case, result, matching, kb if kb is not None else kb_texts()),
        "sdlc": sdlc_accuracy(case, result),
        "human_correction": human_correction(decisions),
        "traceability": traceability(result),
        "processing_seconds": record.get("elapsed_seconds"),
        "models_used": record.get("models_used", []),
    }


# --- results on disk -------------------------------------------------------------------

def load_results(results_dir: Path) -> list[dict]:
    """Saved run records (<case>.json) in a results folder; error files are skipped."""
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(results_dir).glob("*.json"))
            if not p.name.endswith(".error.json")]


def evaluate_dir(results_dir: Path, cases_dir: Path = CASES_DIR, db: Path | None = None) -> list[dict]:
    cases = {c["id"]: c for c in load_cases(cases_dir)}
    kb, out = kb_texts(), []
    for rec in load_results(results_dir):
        case = cases.get(rec["case_id"])
        if case is None:
            raise ValueError(f"Result for unknown case {rec['case_id']}")
        run_id = rec["result"].get("run_id")
        decisions = decisions_from_db(db, run_id) if db and run_id else None
        out.append(evaluate_case(case, rec, decisions, kb))
    return out


def fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


SUMMARY = (("Precision", "extraction", "precision"), ("Recall", "extraction", "recall"), ("F1", "extraction", "f1"),
           ("Category coverage", "completeness", "category_coverage"),
           ("Ambiguity detection", "ambiguity", "detection_rate"), ("Conflict detection", "conflict", "detection_rate"),
           ("Regulatory coverage", "regulatory_coverage", "coverage"), ("Hallucination", "hallucination", "hallucination_rate"),
           ("Citation correctness", "citation", "citation_correctness"), ("SDLC top-1", "sdlc", "top1"),
           ("SDLC top-3", "sdlc", "top3"), ("Human correction", "human_correction", "rate"),
           ("Traceability", "traceability", "coverage"))


def summary_rows(metrics: list[dict]) -> list[list[str]]:
    rows = [[label] + [fmt(m[group][key]) for m in metrics] for label, group, key in SUMMARY]
    rows.append(["Processing time (s)"] + [fmt(m["processing_seconds"]) for m in metrics])
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compute section-19 metrics from saved evaluation results.")
    ap.add_argument("results", type=Path, help="folder written by scripts/run_eval.py, e.g. eval/results/2026-12-01")
    ap.add_argument("--cases-dir", type=Path, default=CASES_DIR)
    ap.add_argument("--db", type=Path, help="app database to read review decisions from (human correction rate)")
    ap.add_argument("--json", action="store_true", help="print every metric as JSON")
    args = ap.parse_args(argv)
    try:
        metrics = evaluate_dir(args.results, args.cases_dir, args.db)
    except (GoldNotFrozen, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not metrics:
        print(f"No results in {args.results}.")
        return 1
    if args.json:
        print(json.dumps(metrics, indent=2))
        return 0
    head = ["Metric"] + [m["case_id"] for m in metrics]
    rows = summary_rows(metrics)
    widths = [max(len(r[i]) for r in [head] + rows) for i in range(len(head))]
    for r in [head] + rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    for m in metrics:
        if m["gold_changed_since_run"]:
            print(f"warning: gold of {m['case_id']} changed after this run was made")
    return 0


if __name__ == "__main__":
    sys.exit(main())
