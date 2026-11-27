"""
Phase 7: the web UI (app/static). Offline: no browser, no model calls.

Checks that the static files are served, that the recorded sample parses,
holds every key the UI reads and no secrets or unmasked personal data, that
the constants hard-coded in app.js match the backend, that every API path the
JS calls exists, and that the JS parses (`node --check`, skipped without Node).
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import input_guard, roles
from app.agents.sdlc_rules import SDLC_PROFILES, TRAIT_WEIGHTS
from app.main import MAX_SCENARIO_CHARS, app
from app.masking import detect

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
client = TestClient(app)


def js_array(name: str) -> list[str]:
    m = re.search(rf"\b{name} = \[(.*?)\]", APP_JS, re.S)
    assert m, name
    return re.findall(r'"([^"]+)"', m.group(1))


def js_number(name: str) -> int:
    m = re.search(rf"\b{name} = (\d+)", APP_JS)
    assert m, name
    return int(m.group(1))


@pytest.fixture(scope="module")
def sample() -> dict:
    return json.loads((STATIC / "sample_run.json").read_text(encoding="utf-8"))


# --- static files -----------------------------------------------------------------

def test_index_and_assets_are_served():
    r = client.get("/")
    assert r.status_code == 200 and "REQUORA" in r.text and "by Darshan" in r.text
    for f, ctype in (("app.js", "javascript"), ("charts.js", "javascript"), ("style.css", "text/css"),
                     ("sample_run.json", "application/json")):
        assert f"/static/{f}" in r.text or f == "sample_run.json"
        res = client.get(f"/static/{f}")
        assert res.status_code == 200 and ctype in res.headers["content-type"], f


def test_no_framework_or_external_script():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.findall(r'<script src="([^"]+)"', html) == ["/static/charts.js", "/static/app.js"]
    # only stylesheet from outside is Google Fonts; the CSS falls back to system fonts offline
    external = [h for h in re.findall(r'href="(https?://[^"]+)"', html) if "fonts.g" not in h]
    assert external == []
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert '"Poppins", "Segoe UI", system-ui' in css and '"Plus Jakarta Sans", "Segoe UI", system-ui' in css


def test_palette_and_print_stylesheet():
    css = (STATIC / "style.css").read_text(encoding="utf-8").upper()
    for colour in ("#000000", "#0B0B0D", "#151518", "#26262B", "#F2F2F5", "#9A9AA4", "#B26CFF", "#C79CFF",
                   "#1F1F26", "#3B2470", "#5B21B6", "#7C3AED", "#9333EA"):
        assert colour in css, colour
    assert "@MEDIA PRINT" in css and ":FOCUS-VISIBLE" in css


def test_role_dropdown_lists_every_role():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.findall(r'<option value="([^"]+)"', html) == list(roles.ROLES)


# --- constants in app.js match the backend ---------------------------------------------

def test_js_constants_match_backend():
    assert js_array("TRAIT_KEYS") == list(TRAIT_WEIGHTS)
    assert set(js_array("SDLC_MODELS")) == set(SDLC_PROFILES)
    assert js_array("ROLES") == list(roles.ROLES)
    assert set(js_array("SDLC_APPROVERS")) == roles.PERMISSIONS["review_sdlc"]
    assert set(js_array("AUDIT_ROLES")) == roles.PERMISSIONS["read_audit"]
    assert tuple(js_array("DOC_EXT")) == input_guard.ALLOWED_EXTENSIONS
    assert js_number("MAX_DOCS") == input_guard.MAX_DOCUMENTS
    assert js_number("MAX_DOC_CHARS") == input_guard.MAX_DOCUMENT_CHARS
    assert js_number("MAX_SCENARIO") == MAX_SCENARIO_CHARS


def test_every_api_path_in_the_js_exists():
    literals = set(re.findall(r'"(/[a-z_][a-z_/.-]*)', APP_JS))
    routes = [getattr(r, "path", "") for r in app.routes]
    for lit in literals:
        if lit.startswith("/static/"):
            assert (STATIC / lit[len("/static/"):]).exists(), lit
            continue
        ok = any(p == lit or p.split("{")[0] == lit or (lit.startswith("/") and p.endswith(lit) and "{" in p)
                 for p in routes)
        assert ok, f"app.js calls {lit}, which is not an API route"
    for needed in ("/agents/pipeline", "/agents/interview", "/agents/interview/templates", "/agents/srs", "/runs/", "/audit"):
        assert f'"{needed}' in APP_JS, needed
    assert '"X-Role": S.role' in APP_JS


def test_every_request_sends_the_role_so_require_role_works():
    """With REQUIRE_ROLE=1 a request without X-Role gets 400, and a browser link navigation sends no headers.
    So every fetch must carry the header, and the SRS buttons must fetch the file, not link to the API."""
    calls = re.findall(r"\bfetch\(([^;]*)", APP_JS)
    assert len(calls) == 3   # api(), fetchText() and the interview templates; a new fetch must send X-Role too
    for call in calls:
        assert "opts" in call or '"X-Role": S.role' in call, call
    assert APP_JS.count('var opts = { method: method, headers: { "X-Role": S.role } };') == 2
    for api_path in ("/agents/", "/runs/", "/audit"):   # no <a href>, window.open or location pointing at the API
        assert not re.search(r"(href\s*=|window\.open\(|location(\.href)?\s*=)\s*[\"']" + re.escape(api_path), APP_JS), api_path
    assert 'fetchText("GET", "/agents/srs/"' in APP_JS and "URL.createObjectURL(blob)" in APP_JS
    assert '<option value="">' not in (STATIC / "index.html").read_text(encoding="utf-8")   # the role is never empty


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed; JS syntax not checked")
@pytest.mark.parametrize("name", ["app.js", "charts.js"])
def test_js_parses(name):
    out = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr


# --- the recorded sample ---------------------------------------------------------

def test_sample_is_labelled(sample):
    assert sample["_sample"]["label"] == "Recorded sample, not a live run"


def test_sample_has_the_keys_the_ui_reads(sample):
    for k in ("scenario", "answers", "documents", "requirements", "quality", "compliance", "compliance_mapping",
              "risk_register", "sdlc_recommendation", "sdlc_workflow", "artefacts", "masking", "versions", "run_id"):
        assert k in sample, k
    reqs = sample["requirements"]["functional_requirements"] + sample["requirements"]["non_functional_requirements"]
    for r in reqs:
        for f in ("id", "requirement", "priority", "type", "categories", "source_stakeholder", "source_text", "source_document",
                  "business_justification", "dependencies", "assumptions", "acceptance_criteria", "applicable_regulations",
                  "risk_level", "confidence", "approval_status"):
            assert f in r, f
    for m in sample["compliance_mapping"]["requirements"]:
        assert {"requirement_id", "mappings", "needs_human_review", "review_reasons"} <= set(m)
        for mp in m["mappings"]:
            assert {"source", "regulation", "scope", "jurisdiction", "version", "chunk_id", "distance", "status",
                    "required_control", "evidence", "evidence_verified", "confidence"} <= set(mp)
    assert sample["compliance_mapping"]["notice"].startswith("Not a legal determination")
    for c in sample["compliance"]["retrieved_chunks"]:
        assert {"id", "source", "scope", "distance", "matched_requirement_ids", "applies_to_domain"} <= set(c)
    for r in sample["risk_register"]["risks"]:
        assert {"id", "rank", "description", "category", "likelihood", "impact", "score", "level", "mitigation",
                "linked_requirement_ids"} <= set(r)
        assert 1 <= r["likelihood"] <= 5 and 1 <= r["impact"] <= 5
    s = sample["sdlc_recommendation"]
    assert set(s["traits"]) == set(TRAIT_WEIGHTS) and set(s["trait_details"]) == set(TRAIT_WEIGHTS)
    assert len(s["scored_models"]) == len(SDLC_PROFILES) == 7
    for m in s["scored_models"]:
        assert {"model", "score", "rank", "percent", "why_not", "profile"} <= set(m) and set(m["profile"]) == set(TRAIT_WEIGHTS)
    assert s["recommended_model"] == s["scored_models"][0]["model"]
    assert {"is_close_call", "justification", "approval_required"} <= set(s)
    for p in sample["sdlc_workflow"]["phases"]:
        for f in ("activities", "responsible_roles", "deliverables", "security_activities", "testing_requirements",
                  "compliance_checkpoints", "approval_gates", "entry_criteria", "exit_criteria"):
            assert all({"text", "source"} <= set(x) for x in p[f]), f
    a = sample["artefacts"]
    for k in ("user_stories", "use_cases", "process_workflows", "data_requirements", "interface_requirements",
              "traceability_matrix", "compliance_control_matrix", "assumptions_register", "open_issues"):
        assert k in a, k
    assert {"counts", "total", "classification", "fields"} <= set(sample["masking"])


def test_sample_answer_roles_are_valid(sample):
    valid = set(client.get("/agents/interview/templates").json()["roles"])
    assert all(a["role"] in valid for a in sample["answers"])


def test_sample_has_no_secrets_or_unmasked_personal_data(sample):
    text = (STATIC / "sample_run.json").read_text(encoding="utf-8")
    assert detect(text) == {}                                   # no emails, cards, phones, ids left
    assert "AIza" not in text and "BEGIN PRIVATE KEY" not in text
    key = os.environ.get("GEMINI_API_KEY", "")
    assert len(key) < 8 or key not in text
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, value = line.split("=", 1)
                value = value.strip().strip('"\'')
                # secret-looking settings only: a recorded live run legitimately names the model (GEMINI_MODEL)
                if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name.upper()) and len(value) >= 8:
                    assert value not in text, f"{name.strip()} value found in the sample"
    assert sample["masking"]["total"] >= 1 and "[EMAIL_1]" in sample["scenario"]


def test_srs_can_be_built_from_the_sample(sample):
    body = {k: v for k, v in sample.items() if k != "_sample"}
    for fmt, ctype in (("markdown", "text/markdown"), ("html", "text/html")):
        r = client.post(f"/agents/srs?format={fmt}", json=body, headers={"X-Role": "business analyst"})
        assert r.status_code == 200 and ctype in r.headers["content-type"], r.text[:300]
        assert "FR-1" in r.text


# --- label, step and count helpers, run in Node ------------------------------------------

HELPERS = ("cap", "typeLabel", "itemLabel", "itemName", "stepText", "reviewCounts", "reviewText")


def _helper_source() -> str:
    lines = []
    for name in HELPERS:
        m = re.search(rf"^  function {name}\(.*$", APP_JS, re.M)
        assert m, f"{name} must stay a one-line helper in app.js"
        lines.append(m.group(0).strip())
    return "\n".join(lines)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed; helpers not run")
def test_ui_label_step_and_count_helpers():
    script = _helper_source() + """
var cm = {requirements: [{needs_human_review: true, mappings: [1, 2]}, {needs_human_review: false, mappings: [1]},
                         {needs_human_review: true, mappings: []}]};
console.log(JSON.stringify({
  sdlc: itemLabel("sdlc", "sdlc"), req: itemLabel("requirement", "FR-1"), comp: itemLabel("compliance", "FR-1/kyc_aml"),
  type: typeLabel("sdlc"), name: itemName("sdlc", "sdlc"), nameReq: itemName("requirement", "FR-2"),
  steps: ["1. Officer declines", "Step 2: Send", "3) Tell", "1.5 seconds later"].map(stepText),
  counts: reviewCounts(cm), text: reviewText(reviewCounts(cm)), one: reviewText({reqs: 1, maps: 1})
}));
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["sdlc"] == "SDLC selection" and r["type"] == "SDLC" and r["name"] == "SDLC selection"
    assert r["req"] == "Requirement FR-1" and r["comp"] == "Compliance FR-1/kyc_aml" and r["nameReq"] == "FR-2"
    assert r["steps"] == ["Officer declines", "Send", "Tell", "1.5 seconds later"]
    assert r["counts"] == {"reqs": 2, "maps": 2} and r["text"] == "2 requirements (2 mappings)"
    assert r["one"] == "1 requirement (1 mapping)"


def test_running_timer_updates_text_only_so_the_spinner_keeps_turning():
    """The 500 ms timer must not rebuild the sidebar: that recreates the spinner and restarts its animation."""
    body = re.search(r"S\.timer = setInterval\(function \(\) \{(.*?)\}, 500\);", APP_JS, re.S).group(1)
    assert not re.search(r"\brender(Sidebar|Panel)?\(|innerHTML", body), body
    assert '.stage-btn[data-tab="scenario"] .stage-sum' in body and 'textContent = stageInfo("scenario").sum' in body


def test_no_raw_type_and_id_labels_left():
    # "sdlc sdlc" came from joining item_type and item_id directly
    assert 'item_type + " " + ' not in APP_JS and 'esc(type + " " + id)' not in APP_JS
    assert "cap(x.item_type)" not in APP_JS
    assert "esc(stepText(s))" in APP_JS
