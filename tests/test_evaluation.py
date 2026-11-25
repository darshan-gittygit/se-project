"""Phase 6: evaluation harness (section 19). Offline, synthetic results; no model calls."""
import copy
import json
import sqlite3
from pathlib import Path

import pytest

import app.agents.requirement_agent as ra
from app.agents.sdlc_rules import SDLC_PROFILES
from app.vocabulary import REQUIREMENT_CATEGORIES
from scripts import eval_human, eval_report, evaluate as ev, run_eval

ROOT = Path(__file__).resolve().parent.parent
CASE_TYPES = {"digital banking", "loan processing", "payment systems", "fraud detection", "insurance", "regulatory reporting"}


# --- synthetic data ---------------------------------------------------------------------

def frozen(obj):
    if isinstance(obj, dict):
        return {k: ("FROZEN" if k == "status" else frozen(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [frozen(x) for x in obj]
    return obj


def make_case(status="FROZEN"):
    g = {"status": status, "requirements": [
        {"id": "G-1", "statement": "Verify identity", "category": "Regulatory", "keyword_groups": [["verif"], ["identity", "kyc"]],
         "expected_sources": ["kyc_aml"], "status": status},
        {"id": "G-2", "statement": "Decision in two minutes", "category": "Performance",
         "keyword_groups": [["decision"], ["minute"]], "expected_sources": [], "status": status},
        {"id": "G-3", "statement": "Credit officer reviews large loans", "category": "Functional",
         "keyword_groups": [["officer"], ["review"]], "expected_sources": ["fair_lending"], "status": status}],
        "ambiguities": [{"id": "A-1", "phrase": "quickly", "keywords": ["quick"], "status": status},
                        {"id": "A-2", "phrase": "simple form", "keywords": ["simple"], "status": status}],
        "conflicts": [{"id": "C-1", "description": "manual review vs two minutes", "sides": [["G-3"], ["G-2"]],
                       "keyword_groups": [["officer", "manual"], ["minute"]], "status": status}],
        "sdlc": {"top": "V-Model", "reason": "regulated", "acceptable": [{"model": "Waterfall", "reason": "stable"}],
                 "status": status}}
    return {"id": "loan", "title": "Loan", "case_type": "loan processing", "answers": [], "documents": [],
            "scenario": "Applicants upload KYC documents. Decisions come quickly. Credit officers review large loans.", "gold": g}


def req(rid, text, source_text="", assumptions=("assumed",), categories=("Functional",), criteria=("checked",)):
    return {"id": rid, "requirement": text, "source_text": source_text, "assumptions": list(assumptions),
            "categories": list(categories), "acceptance_criteria": list(criteria), "business_justification": "why"}


def make_result(frs=None, nfrs=None, flags=(), mappings=None, ranked=("V-Model", "Waterfall", "Agile-V"), rtm=None):
    frs = frs if frs is not None else [req("FR-1", "The system shall verify applicant identity", categories=["Regulatory"]),
                                      req("FR-2", "A credit officer shall review loans above the limit")]
    nfrs = nfrs if nfrs is not None else [req("NFR-1", "Every decision within two minutes", categories=["Performance"])]
    models = list(ranked) + [m for m in SDLC_PROFILES if m not in ranked]
    return {"run_id": "r1", "scenario": "Applicants upload KYC documents. Decisions come quickly.",
            "answers": [{"question": "Who reviews?", "answer": "Credit officers review large loans."}], "documents": [],
            "requirements": {"functional_requirements": frs, "non_functional_requirements": nfrs},
            "quality": {"flags": list(flags)},
            "compliance": {"kb_relevance": "relevant"},
            "compliance_mapping": {"requirements": mappings if mappings is not None else []},
            "sdlc_recommendation": {"recommended_model": models[0],
                                    "scored_models": [{"model": m, "rank": i + 1} for i, m in enumerate(models)]},
            "artefacts": {"traceability_matrix": rtm if rtm is not None else []}}


@pytest.fixture
def case():
    return make_case()


# --- the case files -------------------------------------------------------------------

def test_six_case_files_cover_the_statement_case_types():
    cases = ev.load_cases()
    assert len(cases) == 6 and {c["case_type"] for c in cases} == CASE_TYPES


@pytest.mark.parametrize("path", sorted((ROOT / "eval" / "cases").glob("*.json")), ids=lambda p: p.stem)
def test_case_file_is_well_formed(path):
    c = ev.load_case(path)
    g = c["gold"]
    assert 200 <= len(c["scenario"].split()) <= 400
    assert g["status"] in ("DRAFT", "FROZEN")
    items = g["requirements"] + g["ambiguities"] + g["conflicts"] + [g["sdlc"]]
    assert all(i["status"] in ("DRAFT", "FROZEN") for i in items)
    assert 2 <= len(g["ambiguities"]) <= 3 and 1 <= len(g["conflicts"]) <= 2
    assert all(r["category"] in REQUIREMENT_CATEGORIES and r["keyword_groups"] for r in g["requirements"])
    assert g["sdlc"]["top"] in SDLC_PROFILES and g["sdlc"]["acceptable"]
    assert all(a["model"] in SDLC_PROFILES and a["reason"] for a in g["sdlc"]["acceptable"])
    for a in g["ambiguities"]:          # planted phrases really are in the scenario
        assert any(ev.has_keyword(c["scenario"], k) for k in a["keywords"]), a["id"]
    chk = ev.gold_self_check(c)
    assert chk["own_keywords_fail"] == [] and chk["unknown_sources"] == [] and chk["bad_conflict_sides"] == []
    # scenarios must also pass the app's input guard (masking + injection filter):
    from app.input_guard import Guard
    Guard("business analyst").text("scenario", c["scenario"])   # raises on an injection pattern


def test_not_frozen_lists_every_draft_field(case):
    assert ev.not_frozen(case) == []
    draft = make_case("DRAFT")
    assert ev.not_frozen(draft)[:2] == ["gold", "gold.requirements.G-1"] and "gold.sdlc" in ev.not_frozen(draft)
    one = make_case()
    one["gold"]["conflicts"][0]["status"] = "DRAFT"
    assert ev.not_frozen(one) == ["gold.conflicts.C-1"]
    with pytest.raises(ev.GoldNotFrozen):
        ev.evaluate_case(draft, {"result": make_result()})


# --- matching -----------------------------------------------------------------------

def test_keyword_matching_is_word_start_and_all_groups():
    assert ev.has_keyword("Verification of identity", "verif")
    assert not ev.has_keyword("repayment schedule", "pay")
    assert ev.has_keyword("one-time password", "one time")
    assert ev.matches("verify the applicant's KYC", [["verif"], ["identity", "kyc"]])
    assert not ev.matches("verify the address", [["verif"], ["identity", "kyc"]])
    assert not ev.matches("anything", [])


def test_matching_is_one_to_one_and_maximal():
    gold = [{"id": "G-1", "keyword_groups": [["card"]]}, {"id": "G-2", "keyword_groups": [["card"], ["freeze"]]}]
    system = [{"id": "S-1", "requirement": "freeze a lost card"}, {"id": "S-2", "requirement": "show card limits"}]
    # greedy would give S-1 to G-1 and leave G-2 unmatched; augmenting paths find both
    assert ev.match_requirements(gold, system) == {"G-1": "S-2", "G-2": "S-1"}
    one = [{"id": "S-1", "requirement": "freeze a lost card"}]
    assert len(ev.match_requirements(gold, one)) == 1


# --- extraction and completeness -------------------------------------------------------------

def test_extraction_perfect_match(case):
    r = make_result()
    m = ev.evaluate_case(case, {"result": r}, kb={})
    assert m["extraction"]["precision"] == m["extraction"]["recall"] == m["extraction"]["f1"] == 1.0
    assert m["completeness"]["category_coverage"] == 1.0 and m["completeness"]["category_agreement"] == 1.0


def test_extraction_empty(case):
    m = ev.evaluate_case(case, {"result": make_result(frs=[], nfrs=[])}, kb={})
    e = m["extraction"]
    assert e["precision"] is None and e["recall"] == 0.0 and e["f1"] == 0.0
    assert m["completeness"]["category_coverage"] == 0.0 and m["completeness"]["record_completeness"] is None
    assert m["traceability"]["coverage"] is None and m["hallucination"]["hallucination_rate"] is None


def test_extraction_all_wrong(case):
    m = ev.evaluate_case(case, {"result": make_result(frs=[req("FR-1", "Print a brochure")], nfrs=[req("NFR-1", "Blue logo")])}, kb={})
    e = m["extraction"]
    assert (e["precision"], e["recall"], e["f1"]) == (0.0, 0.0, 0.0)
    assert e["missed_gold_ids"] == ["G-1", "G-2", "G-3"] and e["unmatched_system_ids"] == ["FR-1", "NFR-1"]


def test_partial_extraction_and_completeness(case):
    r = make_result(frs=[req("FR-1", "Verify identity", categories=["Security"]), req("FR-2", "Export a report")], nfrs=[])
    m = ev.evaluate_case(case, {"result": r}, kb={})
    assert m["extraction"]["precision"] == 0.5 and m["extraction"]["recall"] == pytest.approx(1 / 3)
    assert m["extraction"]["f1"] == pytest.approx(0.4)
    c = m["completeness"]
    assert c["category_coverage"] == pytest.approx(1 / 3) and c["missing_categories"] == ["Functional", "Performance"]
    assert c["category_agreement"] == 0.0     # paired, but classified Security instead of Regulatory


def test_record_completeness_counts_missing_fields(case):
    r = make_result(frs=[req("FR-1", "Verify identity", criteria=()), req("FR-2", "Officer review", assumptions=())], nfrs=[])
    assert ev.evaluate_case(case, {"result": r}, kb={})["completeness"]["record_completeness"] == 0.0


# --- ambiguity and conflict --------------------------------------------------------------

def test_ambiguity_detection(case):
    flags = [{"type": "ambiguity", "requirement_ids": ["FR-9"], "reason": "'quickly' is not measurable"},
             {"type": "untestable", "requirement_ids": ["FR-1"], "reason": "no number"},
             {"type": "conflict", "requirement_ids": ["FR-1"], "reason": "simple"}]
    a = ev.ambiguity_detection(case, make_result(flags=flags))
    assert a["detected_ids"] == ["A-1"] and a["missed_ids"] == ["A-2"] and a["detection_rate"] == 0.5
    assert a["flags"] == 2 and a["flags_not_matching_planted"] == 1


def test_ambiguity_found_through_flagged_requirement_text(case):
    r = make_result(frs=[req("FR-1", "The form shall be simple")], flags=[{"type": "ambiguity", "requirement_ids": ["FR-1"],
                                                                         "reason": "vague"}])
    assert ev.ambiguity_detection(case, r)["detected_ids"] == ["A-2"]


def test_ambiguity_none_planted_and_none_found(case):
    case["gold"]["ambiguities"] = []
    assert ev.ambiguity_detection(case, make_result())["detection_rate"] is None
    assert ev.ambiguity_detection(make_case(), make_result())["detection_rate"] == 0.0


def test_conflict_detection_by_ids_by_reason_and_missed(case):
    r = make_result(flags=[{"type": "conflict", "requirement_ids": ["FR-2", "NFR-1"], "reason": "timing"}])
    m = ev.match_requirements(case["gold"]["requirements"], ev.system_requirements(r))
    assert ev.conflict_detection(case, r, m)["detection_rate"] == 1.0
    by_reason = make_result(flags=[{"type": "conflict", "requirement_ids": ["FR-1", "FR-2"],
                                    "reason": "Manual review cannot finish in two minutes"}])
    assert ev.conflict_detection(case, by_reason, m)["detected_ids"] == ["C-1"]
    wrong = make_result(flags=[{"type": "conflict", "requirement_ids": ["FR-1", "FR-2"], "reason": "unrelated"}])
    out = ev.conflict_detection(case, wrong, m)
    assert out["detection_rate"] == 0.0 and out["flags_not_matching_planted"] == 1
    # a side that was never extracted cannot be detected through ids
    only_fr = make_result(nfrs=[], flags=[{"type": "conflict", "requirement_ids": ["FR-2", "FR-1"], "reason": "x"}])
    m2 = ev.match_requirements(case["gold"]["requirements"], ev.system_requirements(only_fr))
    assert ev.conflict_detection(case, only_fr, m2)["detection_rate"] == 0.0


# --- compliance ---------------------------------------------------------------------

KB = {"kyc_aml": ev.norm("Systems should verify the identity of a customer before establishing a relationship."),
      "fair_lending": ev.norm("Borrowers who are declined should receive a reason.")}


def mapping(rid, source, evidence):
    return {"requirement_id": rid, "mappings": [{"source": source, "evidence": evidence}]}


def test_regulatory_coverage(case):
    full = make_result(mappings=[mapping("FR-1", "kyc_aml", "x"), mapping("FR-2", "fair_lending", "x")])
    m = ev.match_requirements(case["gold"]["requirements"], ev.system_requirements(full))
    assert ev.regulatory_coverage(case, full, m)["coverage"] == 1.0
    part = make_result(mappings=[mapping("FR-1", "kyc_aml", "x"), mapping("FR-2", "data_protection", "x")])
    out = ev.regulatory_coverage(case, part, m)
    assert out["coverage"] == 0.5 and out["missed"] == ["G-3/fair_lending"]
    assert ev.regulatory_coverage(case, make_result(), {})["coverage"] == 0.0
    case["gold"]["requirements"] = [dict(g, expected_sources=[]) for g in case["gold"]["requirements"]]
    assert ev.regulatory_coverage(case, full, m)["coverage"] is None


def test_citation_correctness(case):
    r = make_result(mappings=[
        mapping("FR-1", "kyc_aml", "verify the identity of a customer"),        # correct
        mapping("FR-2", "fair_lending", "this sentence is not in the source"),  # right label, evidence not found
        {"requirement_id": "NFR-1", "mappings": [{"source": "kyc_aml", "evidence": "verify the identity"}]},  # wrong label
        mapping("FR-9", "kyc_aml", "verify the identity")])                     # not paired with gold: unjudged
    r["requirements"]["functional_requirements"].append(req("FR-9", "Something else entirely"))
    m = ev.match_requirements(case["gold"]["requirements"], ev.system_requirements(r))
    c = ev.citation_correctness(case, r, m, KB)
    assert c["citations"] == 4 and c["evidence_found_rate"] == 0.75
    assert c["judged"] == 3 and c["unjudged"] == 1
    assert c["label_match_rate"] == pytest.approx(2 / 3) and c["citation_correctness"] == pytest.approx(1 / 3)
    assert c["incorrect"] == ["FR-2/fair_lending", "NFR-1/kyc_aml"]
    assert ev.citation_correctness(case, make_result(), m, KB)["citation_correctness"] is None


def test_citation_evidence_checked_against_real_kb():
    kb = ev.kb_texts()
    assert "kyc_aml" in kb and ev.norm("verify the identity of a customer") in kb["kyc_aml"]


# --- hallucination -------------------------------------------------------------------

def test_hallucination():
    r = make_result(frs=[req("FR-1", "a", source_text="upload KYC documents"),               # in the scenario
                         req("FR-2", "b", source_text="Credit officers review large loans"),  # in an answer
                         req("FR-3", "c", source_text="documents are shredded daily")],       # not in the input
                    nfrs=[req("NFR-1", "d", assumptions=())])                                 # nothing at all
    h = ev.hallucination(r)
    assert h["quote_not_in_input"] == ["FR-3"] and h["no_quote_no_assumption"] == ["NFR-1"]
    assert h["hallucination_rate"] == 0.5 and h["inferred_rate"] == 0.25
    assert ev.hallucination(make_result())["hallucination_rate"] == 0.0


def test_hallucination_uses_uploaded_documents():
    r = make_result(frs=[req("FR-1", "a", source_text="retain records for ten years")], nfrs=[])
    assert ev.hallucination(r)["quote_not_in_input"] == ["FR-1"]
    r["documents"] = [{"filename": "policy.md", "text": "We retain records for ten years."}]
    assert ev.hallucination(r)["quote_not_in_input"] == []


# --- SDLC, corrections, traceability ---------------------------------------------------------------

def test_sdlc_accuracy(case):
    assert ev.sdlc_accuracy(case, make_result()) == {"recommended": "V-Model", "gold_top": "V-Model", "top1": True,
                                                     "top3": True, "acceptable": True, "gold_rank": 1}
    s = ev.sdlc_accuracy(case, make_result(ranked=("Waterfall", "Agile-V", "V-Model")))
    assert (s["top1"], s["top3"], s["acceptable"], s["gold_rank"]) == (False, True, True, 3)
    s = ev.sdlc_accuracy(case, make_result(ranked=("Agile", "Spiral", "DevSecOps")))
    assert (s["top1"], s["top3"], s["acceptable"]) == (False, False, False)
    empty = make_result()
    empty["sdlc_recommendation"] = {}
    assert ev.sdlc_accuracy(case, empty)["top1"] is None


def test_human_correction_rate(tmp_path):
    assert ev.human_correction(None)["rate"] is None and ev.human_correction([])["rate"] is None
    d = [{"action": a} for a in ("accept", "accept", "modify", "reject", "regenerate", "freeze")]
    h = ev.human_correction(d)
    assert h["decisions"] == 5 and h["corrections"] == 2 and h["rate"] == 0.4
    db = tmp_path / "app.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY, run_id TEXT, action TEXT)")
        conn.executemany("INSERT INTO decisions (run_id, action) VALUES (?, ?)", [("r1", "accept"), ("r1", "modify"), ("r2", "reject")])
    assert [x["action"] for x in ev.decisions_from_db(db, "r1")] == ["accept", "modify"]


def test_correction_rate_from_record_decisions(case):
    rec = {"result": make_result(), "decisions": [{"action": "accept"}, {"action": "reject"}]}
    assert ev.evaluate_case(case, rec, kb={})["human_correction"]["rate"] == 0.5


def test_traceability():
    rtm = [{"requirement_id": "FR-1", "user_stories_ids": ["US-1"]},
           {"requirement_id": "FR-2", "use_cases_ids": [], "process_workflows_ids": []},
           {"requirement_id": "NFR-1", "data_requirements_ids": ["DR-1"]}]
    r = make_result(nfrs=[req("NFR-1", "x", criteria=())], rtm=rtm)
    t = ev.traceability(r)
    assert t["coverage"] == pytest.approx(1 / 3) and t["not_fully_traced"] == ["FR-2", "NFR-1"]
    assert t["backward"] == 1.0 and t["forward"] == pytest.approx(2 / 3) and t["verification"] == pytest.approx(2 / 3)


def test_gold_change_is_detected(case):
    rec = {"result": make_result(), "gold_sha256": ev.gold_hash(case), "elapsed_seconds": 12.5}
    assert ev.evaluate_case(case, rec, kb={})["gold_changed_since_run"] is False
    changed = copy.deepcopy(case)
    changed["gold"]["sdlc"]["top"] = "Waterfall"
    m = ev.evaluate_case(changed, rec, kb={})
    assert m["gold_changed_since_run"] is True and m["processing_seconds"] == 12.5


# --- runner ---------------------------------------------------------------------

def ok_body():
    return {"versions": {"models": ["m1"], "app_version": "x", "kb_version": "k"}, "run_id": "r"}


def test_runner_refuses_draft_gold(tmp_path):
    calls = []
    with pytest.raises(ev.GoldNotFrozen):
        run_eval.run_cases([make_case(), make_case("DRAFT")], tmp_path, lambda c: calls.append(c), mode="offline")
    assert calls == [] and not any(tmp_path.iterdir())


def test_runner_cli_refuses_draft_cases(tmp_path, capsys):
    (tmp_path / "loan.json").write_text(json.dumps(make_case("DRAFT")), encoding="utf-8")
    assert run_eval.main(["--cases-dir", str(tmp_path), "--out", str(tmp_path / "out"), "--offline"]) == 2
    assert "DRAFT gold" in capsys.readouterr().err and not (tmp_path / "out").exists()
    assert run_eval.main(["--cases-dir", str(tmp_path), "--check"]) == 0
    assert "NOT FROZEN" in capsys.readouterr().out


def test_runner_saves_resumes_and_pauses(tmp_path):
    a, b = make_case(), dict(make_case(), id="b")
    sleeps, posted = [], []

    def post(c):
        posted.append(c["id"])
        return 200, ok_body()

    s = run_eval.run_cases([a, b], tmp_path, post, mode="offline", pause=5, sleep=sleeps.append, log=lambda *_: None)
    assert s["done"] == ["loan", "b"] and sleeps == [5]
    rec = json.loads((tmp_path / "loan.json").read_text(encoding="utf-8"))
    assert rec["status_code"] == 200 and rec["models_used"] == ["m1"] and rec["gold_sha256"] == ev.gold_hash(a)
    assert rec["elapsed_seconds"] >= 0 and rec["result"]["run_id"] == "r"
    s = run_eval.run_cases([a, b], tmp_path, post, mode="offline", resume=True, pause=5, sleep=sleeps.append, log=lambda *_: None)
    assert s["skipped"] == ["loan", "b"] and posted == ["loan", "b"] and sleeps == [5]


def test_runner_stops_after_repeated_429_without_losing_work(tmp_path):
    cases = [dict(make_case(), id=i) for i in ("c1", "c2", "c3", "c4", "c5")]
    replies = iter([(200, ok_body()), (502, {"detail": "Agent 1 failed: 429 RESOURCE_EXHAUSTED"}),
                    (502, {"detail": "quota exceeded (429)"}), (200, ok_body())])
    s = run_eval.run_cases(cases, tmp_path, lambda c: next(replies), mode="live", pause=0, max_429=2, log=lambda *_: None)
    assert s["stopped_early"] and s["done"] == ["c1"] and s["failed"] == ["c2", "c3"]
    assert (tmp_path / "c1.json").exists() and (tmp_path / "c2.error.json").exists() and not (tmp_path / "c4.json").exists()
    # resume retries the failed cases and keeps the finished one
    again = run_eval.run_cases(cases, tmp_path, lambda c: (200, ok_body()), mode="live", resume=True, pause=0, log=lambda *_: None)
    assert again["skipped"] == ["c1"] and again["done"] == ["c2", "c3", "c4", "c5"]
    assert not (tmp_path / "c2.error.json").exists()


def test_runner_continues_after_other_errors(tmp_path):
    cases = [dict(make_case(), id=i) for i in ("c1", "c2", "c3")]
    replies = iter([(502, {"detail": "invalid output"}), (400, {"detail": "too short"}), (200, ok_body())])
    s = run_eval.run_cases(cases, tmp_path, lambda c: next(replies), mode="live", pause=0, max_429=1, log=lambda *_: None)
    assert not s["stopped_early"] and s["done"] == ["c3"] and s["failed"] == ["c1", "c2"]


def test_offline_mode_end_to_end(tmp_path):
    src = ev.load_case(ROOT / "eval" / "cases" / "loan_processing.json")
    cases_dir, out = tmp_path / "cases", tmp_path / "out"
    cases_dir.mkdir()
    c = dict(src, gold=frozen(src["gold"]))
    (cases_dir / "loan_processing.json").write_text(json.dumps(c), encoding="utf-8")
    original = ra.generate_text
    assert run_eval.main(["--offline", "--cases-dir", str(cases_dir), "--out", str(out)]) == 0
    assert ra.generate_text is original                       # mocks are removed after each call
    rec = json.loads((out / "loan_processing.json").read_text(encoding="utf-8"))
    assert rec["mode"] == "offline" and rec["status_code"] == 200
    m = ev.evaluate_dir(out, cases_dir)[0]
    assert m["extraction"]["recall"] == 1.0                   # offline replies restate the gold, so every item matches
    assert m["hallucination"]["hallucination_rate"] == 0.0
    assert eval_report.main(["--results", str(out), "--cases-dir", str(cases_dir)]) == 0


# --- manual baseline and satisfaction ----------------------------------------------------------

MANUAL = """# Manual baseline: loan

Case: loan
Analyst: DS
Time taken (minutes): 90
Time reviewing the system output (minutes): 20

## Requirements

- [Regulatory] The system shall verify applicant identity.
- A credit officer reviews large loans.
- [Business] Print brochures.

## Ambiguities found

- quickly

## Conflicts found

- manual review vs two minutes

## SDLC choice

Model: Waterfall
Reason: stable
"""


def test_manual_baseline_parse_and_compare(tmp_path, case):
    (tmp_path / "loan.md").write_text(MANUAL, encoding="utf-8")
    (tmp_path / "TEMPLATE.md").write_text((ROOT / "eval" / "manual_baseline" / "TEMPLATE.md").read_text(encoding="utf-8"),
                                          encoding="utf-8")
    manual = eval_human.load_manual(tmp_path)
    assert list(manual) == ["loan"]
    m = manual["loan"]
    assert m["minutes"] == 90 and m["review_minutes"] == 20 and m["sdlc_model"] == "Waterfall"
    assert [r["categories"] for r in m["requirements"]] == [["Regulatory"], [], ["Business"]]
    assert m["ambiguities"] == ["quickly"] and len(m["conflicts"]) == 1
    sysm = {"processing_seconds": 120, "extraction": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "system_count": 3}}
    c = eval_human.compare_manual(case, m, sysm)
    assert c["manual"]["precision"] == pytest.approx(2 / 3) and c["manual"]["recall"] == pytest.approx(2 / 3)
    assert c["system_minutes"] == 2 and c["assisted_minutes"] == 22 and c["time_saved_minutes"] == 68
    assert eval_human.compare_manual(case, m, None)["time_saved_minutes"] is None


def test_template_parses_to_nothing():
    t = eval_human.parse_manual(ROOT / "eval" / "manual_baseline" / "TEMPLATE.md")
    assert t["minutes"] is None and len(t["requirements"]) == 2     # the two example lines


def test_satisfaction(tmp_path):
    (tmp_path / "TEMPLATE.csv").write_text((ROOT / "eval" / "satisfaction" / "TEMPLATE.csv").read_text(encoding="utf-8"),
                                           encoding="utf-8")
    (tmp_path / "round1.csv").write_text("respondent,role,case_id,q1,q2,q3,q4,q5,comment\n"
                                         "R1,business analyst,loan,5,4,4,3,5,good\n"
                                         "R2,product owner,loan,3,2,,4,4,\n\n", encoding="utf-8")
    rows = eval_human.load_satisfaction(tmp_path)
    agg = eval_human.aggregate_satisfaction(rows)
    assert agg["responses"] == 2 and agg["respondents"] == 2
    assert agg["questions"]["q1"]["mean"] == 4 and agg["questions"]["q3"]["n"] == 1
    assert agg["questions"]["q2"]["agree_share"] == 0.5 and agg["questions"]["q5"]["distribution"]["4"] == 1
    assert agg["overall_mean"] == pytest.approx(34 / 9)
    assert eval_human.aggregate_satisfaction([])["overall_mean"] is None


@pytest.mark.parametrize("bad,msg", [("R1,ba,loan,6,4,4,4,4,x\n", "1-5"), ("R1,ba,loan,x,4,4,4,4,x\n", "1-5")])
def test_satisfaction_rejects_bad_values(tmp_path, bad, msg):
    (tmp_path / "r.csv").write_text("respondent,role,case_id,q1,q2,q3,q4,q5,comment\n" + bad, encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        eval_human.load_satisfaction(tmp_path)


def test_satisfaction_rejects_missing_columns(tmp_path):
    (tmp_path / "r.csv").write_text("respondent,q1\nR1,4\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing column"):
        eval_human.load_satisfaction(tmp_path)


# --- report ---------------------------------------------------------------------

def test_report_without_results_lists_cases_and_limitations():
    text = eval_report.build_report(ev.load_cases(), [], None)
    assert "No scored results yet" in text and "## Limitations" in text and "One labeller" in text
    assert text.count("| DRAFT |") + text.count("| FROZEN |") == 6
    assert "No manual baseline recorded yet" in text and "No satisfaction responses yet" in text


def test_report_with_results(case, tmp_path):
    rec = {"case_id": "loan", "mode": "live", "elapsed_seconds": 30.0, "gold_sha256": ev.gold_hash(case),
           "result": make_result(flags=[{"type": "conflict", "requirement_ids": ["FR-2", "NFR-1"], "reason": "x"}])}
    (tmp_path / "loan.md").write_text(MANUAL, encoding="utf-8")
    rows = [{"respondent": "R1", "role": "ba", "case_id": "loan", "q1": 4, "q2": 4, "q3": 5, "q4": 3, "q5": 4, "comment": ""}]
    text = eval_report.build_report([case], [rec], None, manual=eval_human.load_manual(tmp_path), satisfaction=rows)
    assert "## Summary" in text and "| Precision | 1.00 | 1.00 |" in text
    assert "| loan | 1.00 (1/1) |" in text                      # conflict detection detail with counts
    assert "### Processing time" in text
    assert "| loan | 0.67 / 0.67 / 0.67 | 1.00 / 1.00 / 1.00 | 90.00 | 0.50 | 20.00 | 69.50 | Waterfall | V-Model |" in text
    assert "overall mean 4.00 of 5" in text
    assert "Offline harness results" not in text
    offline = eval_report.build_report([case], [dict(rec, mode="offline")], None)
    assert "Offline harness results" in offline


def test_report_skips_draft_cases(case):
    rec = {"case_id": "loan", "mode": "live", "result": make_result()}
    text = eval_report.build_report([make_case("DRAFT")], [rec], None)
    assert "Not scored because their gold is not FROZEN" in text and "No scored results yet" in text
