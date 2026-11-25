"""Phase 4 step 3: SRS assembly and rendering. Offline, no LLM."""
import re

from app.srs import NOTICE, SrsStore, build_blocks, render_html, render_markdown
from tests.artefact_fixtures import REQUIREMENTS, pipeline_result

WHEN = "2026-11-26 10:00 UTC"
SECTIONS = ["1. Introduction", "2. Stakeholders and sources", "3. Functional requirements", "4. Non-functional requirements",
            "5. User stories", "6. Use cases", "7. Process workflows", "8. Data requirements", "9. Interface requirements",
            "10. Compliance-control matrix", "11. Threat and risk register", "12. SDLC recommendation",
            "13. Development workflow (V-Model)", "14. Requirements traceability matrix", "15. Assumptions and dependencies",
            "16. Open issues", "17. Approval status"]


def md(result=None):
    return render_markdown(build_blocks(result or pipeline_result(), generated_at=WHEN))


def test_markdown_has_every_section_in_order():
    headings = [l[3:] for l in md().splitlines() if l.startswith("## ")]
    assert headings == SECTIONS


def test_notice_appears_at_the_top_and_with_the_approvals():
    text = md()
    assert text.count(f"> **{NOTICE}**") == 2
    assert text.index(NOTICE) < text.index("## 1. Introduction")
    assert "> **Not a legal determination." in text.split("## 10. Compliance-control matrix")[1]


def test_approval_table_is_all_proposed_or_pending():
    section = md().split("## 17. Approval status")[1]
    rows = [l for l in section.splitlines() if l.startswith("| ") and not l.startswith("| Item")]
    assert len(rows) == 3 + 4  # three requirements, SDLC model, compliance review, artefacts, workflow
    assert all(("| proposed |" in r) or ("| pending |" in r) for r in rows)
    assert "| SDLC model: V-Model | pending | Project manager, Architect, Security team, Compliance officer |" in section


def test_requirement_rows_and_generated_items_keep_their_stakeholder_statement():
    text = md()
    assert '| FR-1 | The system shall verify applicant identity documents. |' in text
    assert 'Compliance and legal officers: "upload identity and address documents for KYC verification"' in text
    assert "Software architects and developers: no stakeholder statement; assumed: 5 seconds is acceptable \\| to applicants" in text
    story_row = next(l for l in text.splitlines() if l.startswith("| US-1 |"))
    assert "As an applicant, I want to upload my documents" in story_row
    # the fixture's generated items carry no source_statements; the RTM still links them back
    rtm = text.split("## 14. Requirements traceability matrix")[1].split("## 15.")[0]
    assert '| FR-1 | Compliance and legal officers: "upload identity and address documents for KYC verification" |' in rtm
    assert "| FR-1 |" in rtm and "US-1" in rtm and "RISK-2" in rtm


def test_table_cells_escape_pipes_and_flatten_newlines():
    result = pipeline_result()
    result["requirements"]["functional_requirements"][0]["requirement"] = "Line one\nline | two"
    text = md(result)
    assert "| FR-1 | Line one line \\| two |" in text


def test_workflow_scenario_items_are_marked():
    assert "Decline-reason tests (added for FR-2)" in md()


def test_missing_parts_render_without_errors():
    minimal = {"scenario": "x", "requirements": REQUIREMENTS}
    text = md(minimal)
    assert [l[3:] for l in text.splitlines() if l.startswith("## ")][:5] == SECTIONS[:5]
    assert "None generated." in text and "No register." in text and "## 12. SDLC recommendation\n\nNot available." in text


def test_html_is_escaped_self_contained_and_print_friendly():
    result = pipeline_result()
    result["scenario"] = "<script>alert(1)</script> & loans"
    page = render_html(build_blocks(result, generated_at=WHEN))
    assert page.startswith("<!DOCTYPE html>") and "<title>Software Requirements Specification: Digital Loan Origination</title>" in page
    assert "<script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; loans" in page
    assert "@media print" in page and 'onclick="window.print()"' in page
    assert not re.search(r'(src|href)=["\']https?://', page)  # no external resources
    assert page.count("<h2>") == len(SECTIONS) and "<table>" in page and '<div class="note">' in page


def test_markdown_and_html_come_from_the_same_blocks():
    blocks = build_blocks(pipeline_result(), generated_at=WHEN)
    md_rows = render_markdown(blocks).count("\n| ") - sum(1 for b in blocks if b[0] == "table" and b[2])
    html_rows = render_html(blocks).count("<tr>") - sum(1 for b in blocks if b[0] == "table" and b[2])
    assert md_rows == html_rows


def test_store_keeps_the_most_recent_results():
    store = SrsStore(capacity=2)
    a, b, c = store.put({"n": 1}), store.put({"n": 2}), store.put({"n": 3})
    assert store.get(a) is None and store.get(b) == {"n": 2} and store.get(c) == {"n": 3}
    assert len(b) == 12 and store.get("nope") is None
