"""
Runs evaluation case studies through the real /agents/pipeline (in-process,
no server needed) and saves each full result for scripts/evaluate.py.

    python -m scripts.run_eval --check                         # gold status of every case, runs nothing
    python -m scripts.run_eval --cases loan_processing,insurance
    python -m scripts.run_eval --resume                        # skip cases already saved today
    python -m scripts.run_eval --offline                       # mocked model replies (plumbing test, no quota)

Refuses any case whose gold is not FROZEN (scripts/evaluate.py: not_frozen).
Results go to eval/results/<YYYY-MM-DD>/<case>.json, written as soon as each
case finishes (atomically), so stopping never loses finished work. A failed
case writes <case>.error.json instead and is retried by --resume. After
--max-429 rate-limit failures in a row the run stops cleanly.

Live runs use the Gemini key in .env and store each run in the app database
(data/app.db), like the UI does, so the runs can be reviewed in the UI
(#run=<id>) and the review decisions feed the human correction rate.
Offline runs use a throwaway database and replies built from the case's
gold, so their metrics are meaningless: they only test the harness.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.evaluate import CASES_DIR, GoldNotFrozen, gold_hash, gold_self_check, load_cases, not_frozen  # noqa: E402

RESULTS_DIR = ROOT / "eval" / "results"
RATE_LIMIT = re.compile(r"\b429\b|RESOURCE_EXHAUSTED|rate.?limit|quota", re.IGNORECASE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def is_done(out_dir: Path, case_id: str) -> bool:
    p = out_dir / f"{case_id}.json"
    if not p.exists():
        return False
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("status_code") == 200
    except (ValueError, OSError):
        return False


def run_cases(cases: list[dict], out_dir: Path, post, *, mode: str, resume: bool = False, pause: float = 20.0,
              max_429: int = 3, artefacts: bool = True, sleep=time.sleep, log=print) -> dict:
    """
    post(case) -> (status_code, body). Runs the cases in order and returns a
    summary {done, skipped, failed, stopped_early}. Never runs a case whose gold is not FROZEN.
    """
    for c in cases:
        if not_frozen(c):
            raise GoldNotFrozen(f"Case {c['id']} has DRAFT gold; nothing was run.")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"done": [], "skipped": [], "failed": [], "stopped_early": False}
    rate_limited = 0
    ran_one = False
    for c in cases:
        if resume and is_done(out_dir, c["id"]):
            summary["skipped"].append(c["id"])
            log(f"skip {c['id']} (already saved)")
            continue
        if ran_one and pause > 0:
            sleep(pause)
        ran_one = True
        log(f"run  {c['id']} ...")
        started, t0 = now(), time.perf_counter()
        status, body = post(c)
        elapsed = round(time.perf_counter() - t0, 2)
        error_path = out_dir / f"{c['id']}.error.json"
        if status == 200:
            versions = body.get("versions") or {}
            write_json(out_dir / f"{c['id']}.json", {
                "case_id": c["id"], "case_title": c.get("title", ""), "mode": mode, "status_code": 200,
                "started_at": started, "finished_at": now(), "elapsed_seconds": elapsed,
                "models_used": versions.get("models", []), "app_version": versions.get("app_version"),
                "kb_version": versions.get("kb_version"), "gold_sha256": gold_hash(c), "artefacts": artefacts,
                "result": body})
            if error_path.exists():
                error_path.unlink()
            summary["done"].append(c["id"])
            rate_limited = 0
            log(f"     saved in {elapsed:.1f}s")
            continue
        detail = body.get("detail") if isinstance(body, dict) else body
        write_json(error_path, {"case_id": c["id"], "mode": mode, "status_code": status, "started_at": started,
                                "elapsed_seconds": elapsed, "detail": detail})
        summary["failed"].append(c["id"])
        log(f"     failed with {status}: {str(detail)[:200]}")
        rate_limited = rate_limited + 1 if RATE_LIMIT.search(json.dumps(detail)) else 0
        if rate_limited >= max_429:
            summary["stopped_early"] = True
            log(f"stopping: {rate_limited} rate-limit failures in a row; finished cases are saved. "
                "Wait for the quota to reset and use --resume.")
            break
    return summary


# --- offline replies (harness test only) ------------------------------------------------

_NFR_CATEGORIES = {"Security", "Privacy", "Performance", "Availability and reliability", "Usability",
                   "Operational and maintenance"}
_TRAITS = {"clarity": 3, "volatility": 3, "complexity": 3, "criticality": 3, "risk_uncertainty": 3,
           "verification_validation": 3, "stakeholder_feedback": 3, "incremental_suitability": 3, "prototyping_need": 3,
           "security_risk": 3, "system_size": 3, "legacy_dependence": 3, "continuous_delivery": 3,
           "stakeholder_availability": 3, "budget_schedule_constraint": 3}


def offline_router(case: dict):
    """Mocked LLM replies for one case: its gold requirements as records (no quotes, so they rely on an assumption)."""
    frs, nfrs = [], []
    for g in case["gold"]["requirements"]:
        target = nfrs if g["category"] in _NFR_CATEGORIES else frs
        target.append(g)
    if not frs:
        frs, nfrs = nfrs[:1], nfrs[1:]
    if not nfrs:
        frs, nfrs = frs[:-1], frs[-1:]

    def rec(prefix, i, g):
        return {"id": f"{prefix}-{i}", "requirement": f"The system shall support: {g['statement']}.", "priority": "Medium",
                "categories": [g["category"]], "source_stakeholder": "Business analysts", "source_text": "",
                "business_justification": "Offline harness reply.", "dependencies": [],
                "assumptions": ["Offline mock reply, not model output"], "acceptance_criteria": ["Checked in a test"],
                "risk_level": "Medium"}

    reqs = {"domain_inferred": case.get("title", case["id"]), "assumptions": ["Offline harness run"],
            "functional_requirements": [rec("FR", i + 1, g) for i, g in enumerate(frs)],
            "non_functional_requirements": [rec("NFR", i + 1, g) for i, g in enumerate(nfrs)]}
    first = "FR-1"
    replies = [
        ("senior business analyst", reqs),
        ("compliance and risk analyst", {"risk_level": "Medium", "summary": "Offline harness reply.", "considerations": []}),
        ("requirements list for CONFLICTS", {"conflicts": []}),
        ("threat and risk register", {"risks": [{"description": f"Offline harness risk {n}", "category": cat,
                                                 "likelihood": {"score": 2, "reason": "offline"},
                                                 "impact": {"score": n + 1, "reason": "offline"}, "mitigation": "None needed.",
                                                 "linked_requirement_ids": [first]}
                                                for n, cat in ((1, "Technical"), (2, "Business"), (3, "Security"))]}),
        ("rating project characteristics", {k: {"score": v, "evidence": "Offline harness reply.",
                                                "reason": f"Rated {v} offline."} for k, v in _TRAITS.items()}),
        ("structured recommendation report", {"requirement_characteristics": "Offline harness reply (all traits rated 3/5).",
                                              "project_characteristics": "Offline harness reply.",
                                              "engineering_justification": "Offline harness reply.",
                                              "key_tradeoffs": "Offline harness reply."}),
        ("software development workflow to one project", {"additions": []}),
        ("writing the artefacts of a Software Requirements Specification",
         {"user_stories": [{"role": "user", "goal": f"to use {r['id']}", "benefit": "the harness is tested",
                            "acceptance_criteria": ["Given x, when y, then z"], "requirement_ids": [r["id"]]}
                           for r in reqs["functional_requirements"]],
          "use_cases": [{"name": "Main flow", "primary_actor": "User", "preconditions": [], "main_flow": ["Start", "Finish"],
                         "alternative_flows": [], "postconditions": [], "requirement_ids": [first]}],
          "process_workflows": [], "data_requirements": [], "interface_requirements": []}),
    ]

    def router(prompt, temperature=0.3):
        for marker, reply in replies:
            if marker in prompt:
                return json.dumps(reply)
        raise AssertionError("offline router: unexpected prompt " + prompt[:80])

    return router


def make_post(offline: bool, role: str, artefacts: bool):
    """A post(case) function calling /agents/pipeline in-process."""
    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    path = "/agents/pipeline" + ("" if artefacts else "?artefacts=false")

    def call(case):
        r = client.post(path, headers={"X-Role": role}, json={"scenario": case["scenario"], "answers": case.get("answers", []),
                                                               "documents": case.get("documents", [])})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"detail": r.text[:2000]}

    def post(case):
        if not offline:
            return call(case)
        import app.agents.artefact_agent as aa
        import app.agents.compliance_agent as ca
        import app.agents.quality_agent as qa
        import app.agents.requirement_agent as ra
        import app.agents.risk_agent as rk
        import app.agents.sdlc_agent as sa
        import app.agents.workflow_agent as wa
        mods = (ra, ca, qa, rk, sa, wa, aa)
        saved = [m.generate_text for m in mods], ca.retrieve_many
        router = offline_router(case)
        try:   # patched only for this call, then restored
            for m in mods:
                m.generate_text = router
            ca.retrieve_many = lambda queries, top_k=3: [[] for _ in queries]
            return call(case)
        finally:
            for m, fn in zip(mods, saved[0]):
                m.generate_text = fn
            ca.retrieve_many = saved[1]

    return post


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run evaluation case studies through /agents/pipeline.")
    ap.add_argument("--cases", help="comma-separated case ids (default: all)")
    ap.add_argument("--cases-dir", type=Path, default=CASES_DIR)
    ap.add_argument("--out", type=Path, help="results folder (default: eval/results/<today>)")
    ap.add_argument("--resume", action="store_true", help="skip cases already saved in the results folder")
    ap.add_argument("--pause", type=float, default=20.0, help="seconds to wait between cases (default 20)")
    ap.add_argument("--max-429", type=int, default=3, help="stop after this many rate-limit failures in a row")
    ap.add_argument("--role", default="business analyst", help="X-Role sent with each request")
    ap.add_argument("--no-artefacts", action="store_true", help="skip the artefact LLM call (?artefacts=false)")
    ap.add_argument("--offline", action="store_true", help="mocked replies and a throwaway database (harness test)")
    ap.add_argument("--check", action="store_true", help="list each case's gold status and self-check, then exit")
    args = ap.parse_args(argv)

    try:
        cases = load_cases(args.cases_dir, [c.strip() for c in args.cases.split(",")] if args.cases else None)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    draft = {c["id"]: not_frozen(c) for c in cases}
    if args.check:
        problems = 0
        for c in cases:
            bad, chk = draft[c["id"]], gold_self_check(c)
            print(f"{c['id']:24} {'FROZEN' if not bad else f'NOT FROZEN ({len(bad)} field(s))'}")
            for label, key in (("statement does not match its own keywords", "own_keywords_fail"),
                               ("expected source not in knowledge_base/", "unknown_sources"),
                               ("conflict side names an unknown gold id", "bad_conflict_sides")):
                if chk[key]:
                    problems += 1
                    print(f"    ERROR {label}: {', '.join(chk[key])}")
            for gid, others in chk["overlaps"].items():
                print(f"    note  {gid}'s statement also matches the keywords of {', '.join(others)}")
        return 1 if problems else 0
    if any(draft.values()):
        print("error: these cases still have DRAFT gold, so nothing was run: "
              + ", ".join(cid for cid, bad in draft.items() if bad)
              + ". Review the gold in eval/cases/, set every status to FROZEN, then run again.", file=sys.stderr)
        return 2
    if args.offline and not os.environ.get("REQUORA_DB_PATH"):
        os.environ["REQUORA_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="requora-eval-"), "eval.db")
    out = args.out or RESULTS_DIR / (date.today().isoformat() + ("-offline" if args.offline else ""))
    post = make_post(args.offline, args.role, not args.no_artefacts)
    summary = run_cases(cases, out, post, mode="offline" if args.offline else "live", resume=args.resume,
                        pause=0 if args.offline else args.pause, max_429=args.max_429, artefacts=not args.no_artefacts)
    print(f"done {len(summary['done'])}, skipped {len(summary['skipped'])}, failed {len(summary['failed'])}"
          f"{', stopped early (rate limit)' if summary['stopped_early'] else ''}; results in {out}")
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
