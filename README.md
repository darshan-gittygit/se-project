# Requora AI

AI-powered requirements engineering: describe a software scenario, and Requora AI generates structured requirements, analyses compliance and risk against a curated knowledge base (RAG), and recommends an SDLC model with an explainable, deterministic scoring trail.

Built by Darshan - NIT Karnataka, Surathkal (CS305 Software Engineering course project).

**Problem statement:** Gathering software requirements and choosing the right SDLC model is manual and subjective, and it often ignores regulatory and risk constraints. Requora AI automates this: it generates structured requirements from a plain-language scenario, checks them against a compliance knowledge base using RAG, and recommends an SDLC model through deterministic, explainable scoring.

## How it works

```
(optional) Interview -> Scenario + answers -> Agent 1: Requirement records -> Agent 3: Compliance & Risk (RAG)
                                                                   -> Quality check -> Agent 2: SDLC recommendation
```

0. **Interview (optional):** role-specific question templates for the 9 stakeholder roles in the problem statement, and an endpoint that returns the next follow-up questions from the answers collected so far.
1. **Agent 1 (requirements):** functional and non-functional requirement records with the problem statement's fields: categories, source stakeholder, the exact source text, business justification, priority, dependencies, assumptions, acceptance criteria, risk level, confidence (computed) and approval status (`proposed`).
2. **Agent 3 (compliance and risk):** runs one retrieval query per requirement against the knowledge base in `knowledge_base/` (ChromaDB + Gemini embeddings) and passes only chunks under a tuned distance threshold to the LLM. Whether the knowledge base covers the domain (`kb_relevance`) is computed from those distances using domain-specific sources only; the domain-agnostic security baseline can still be cited, labelled "generic baseline", but never counts as coverage. Any citation of a source that was not retrieved is dropped, so the panel can only cite what was actually retrieved.
3. **Agent 2 (SDLC):** the LLM rates 15 project traits (covering the problem statement's 13 decision factors) against fixed rubrics and must cite evidence; a deterministic engine ranks Waterfall, V-Model, Incremental, Agile, Spiral, DevSecOps and the Agile-V hybrid with percentage scores; the LLM then explains the result. The LLM never picks the model, and the choice is marked as needing approval by the project manager, architect, security team and compliance officer.
4. **Workflow (pipeline):** the recommended model's base phases (plain data, with activities, roles, deliverables, security activities, testing, compliance checkpoints, approval gates, entry/exit criteria and traceability) plus scenario-specific items one LLM call may add, linked to requirement IDs.
5. **Artefacts and SRS (pipeline):** user stories with acceptance criteria, use cases, process workflows, data and interface requirements (one LLM call), plus a traceability matrix, compliance-control matrix, assumptions and dependency register and open-issues list built by code. Everything is assembled into one SRS you can download as Markdown or open as a print-friendly HTML page (Print -> Save as PDF).

**Compliance mapping and risk register (pipeline):** each requirement is mapped to the knowledge-base sources that apply to the scenario's domain, with jurisdiction, version, required control, verified evidence, satisfied/gap, graded confidence and a human-review flag; a threat and risk register scores likelihood x impact in code.

**Quality check (API and pipeline):** flags ambiguity, untestable wording and near-duplicates by deterministic rules, and conflicts between requirements with one LLM call; each flag has requirement IDs, a reason and a suggested fix.

Every LLM reply is checked against a schema (`app/schemas.py`): allowed values, known requirement IDs, real knowledge-base sources, all nine traits as integers 1-5. An invalid reply is retried once with the errors, then the API returns a 502 rather than guessing.

**Input protection:** sensitive values (emails, phones, card numbers, Aadhaar/PAN-style ids, IBANs, account and SSN-style numbers) are replaced with placeholders such as `[EMAIL_1]` before any model call or storage, user text is fenced off as data in every prompt, and obvious injection attempts ("ignore previous instructions", "reveal your system prompt" ...) are rejected with 400. You can attach `.txt`, `.md` or `.csv` documents as JSON (`documents: [{"filename", "text"}]`, up to 20,000 characters each).

**Approvals and audit (UI and API):** each pipeline run is stored in `data/app.db` with its requirements, regulatory interpretations and SDLC selection as approval items. Send your role in an `X-Role` header (business analyst, product owner, security officer, compliance officer, project manager, architect, auditor) to accept, reject, modify or regenerate items (`POST /runs/<run_id>/approvals`) and to freeze the baseline (`POST /runs/<run_id>/baseline/freeze`). Every stage, action and artefact goes into a tamper-evident audit log the auditor and compliance officer can read at `GET /audit`. The role header is a stand-in for real sign-in; set `REQUIRE_ROLE=1` to reject requests without it. `RETENTION_DAYS` with `python -m app.retention` deletes old runs (audit history is kept). `/agents/pipeline?artefacts=false` skips the artefact LLM call.

Download the SRS of a pipeline run from the links in its `srs` key, e.g. `GET /agents/srs/<id>?format=html`, or `POST /agents/srs?format=markdown` with a saved pipeline result.

Details, the class diagram and the full scoring tables are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Setup (once)

Windows PowerShell, from the project folder:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
# open .env and put your real Gemini key after GEMINI_API_KEY=

python -m app.rag.ingest      # builds chroma_db/ from knowledge_base/ (needs internet + key)
```

Get a free key at <https://aistudio.google.com/app/apikey>.

## Run

```powershell
python -m uvicorn app.main:app --reload --port 8001
```

or double-click `run.bat`. Then open <http://127.0.0.1:8001/>. API docs are at `/docs`.

## Using the web UI

Plain HTML, CSS and JavaScript in `app/static/` (no framework, no build step; charts are inline SVG drawn by `charts.js`).

- **Top bar:** pick your role; it is sent as the `X-Role` header on every request and decides which review actions the server accepts. "New analysis" clears the page.
- **Scenario:** type the scenario, optionally answer role-specific template questions or ask for model follow-up questions, and attach `.txt`/`.md`/`.csv` files (read in the browser, sent as JSON). "Run full analysis" makes one `/agents/pipeline` request; the page shows the elapsed time while it runs (the server reports no per-stage progress, so none is shown). A banner reports how many sensitive values were masked (counts only).
- **Requirements, Compliance and risk, SDLC, Artefacts:** every field the API returns: requirement records with quotes and quality flags, the compliance mapping (with the "Not a legal determination" notice and a "generic baseline" / "domain-specific" label), retrieved chunks, the risk register with a likelihood x impact grid, the SDLC result (radar of the 15 traits against the winner's profile, trait-by-model heat matrix, ranked bars, close-call banner, why-not text, workflow table, four approvers), and all artefacts with "Download SRS (Markdown)" and "Open printable SRS".
- **Approvals:** accept / reject / modify / regenerate buttons on each item, blocking items, decision history and "Freeze baseline". 403 and 409 answers are shown as plain messages.
- **Audit log** (auditor and compliance officer): filter by run, role and action; shows whether the hash chain verifies.
- **Reloading** keeps a live run: its id is in the address (`#run=<id>`) and is reopened from the database.
- **Load recorded sample:** shows `app/static/sample_run.json`, labelled "Recorded sample, not a live run", with no model calls (useful when the quota is used up). It was produced offline by `python -m scripts.make_sample_run` from mocked model replies and real knowledge-base paragraphs; replace it with a recorded live run before a demo. Approvals are disabled in sample mode because nothing is stored.

## Tests

The tests run offline (Gemini and retrieval are mocked), so they need no key or internet:

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

They cover the scoring engine (including that no model gets a per-model bonus), retrieval filtering and `kb_relevance`, schema validation and retries, the Gemini fallback and rate-limit logic, the analysis scripts, every API route, and the UI's static files (the recorded sample's keys and that it holds no secrets, JS constants matching the backend, `node --check` when Node.js is installed).

Analysis scripts (not part of the test run):

```powershell
python -m scripts.sensitivity --write              # offline: win share + sensitivity -> docs/SCORING_ANALYSIS.md
python -m scripts.tune_relevance_threshold --write # live: re-tunes the RAG threshold -> docs/RAG_TUNING.md
```

## Project layout

```
app/
  main.py                    FastAPI routes and static UI
  config.py                  .env loading and project paths
  gemini_client.py           Gemini calls: backoff on 429, retry on 503, fallback across models
  json_utils.py              tolerant JSON parsing of LLM output
  schemas.py                 Pydantic schemas for every LLM reply + validate/retry helper
  vocabulary.py              roles, topics, categories and statuses quoted from the problem statement
  agents/
    requirement_agent.py     Agent 1
    interview_agent.py       interview follow-up questions (+ interview_templates.py)
    quality_agent.py         quality and conflict checks
    compliance_mapping.py    per-requirement compliance mapping, confidence, review flags
    risk_agent.py            threat and risk register
    sdlc_workflows.py        base SDLC workflow phases per model (plain data)
    workflow_agent.py        project-specific workflow additions
    artefact_agent.py        user stories, use cases, process workflows, data and interface requirements
  artefacts.py               traceability matrix, compliance-control matrix, registers, open issues (no LLM)
  srs.py                     SRS as Markdown and print-friendly HTML
  db.py, runs.py             SQLite storage: runs, approval items, decisions, audit events
  roles.py                   roles (X-Role header) and the permission table
  approvals.py               accept / reject / modify / regenerate, versions, baseline freeze
  audit.py                   append-only, hash-chained audit log and its verifier
  masking.py                 sensitive-data detection and placeholders
  prompt_safety.py           prompt delimiters, injection filter, output checks
  input_guard.py             per-request masking + filtering, document upload checks
  retention.py               purge old runs (python -m app.retention)
    compliance_agent.py      Agent 3 (RAG)
    sdlc_agent.py            Agent 2 (trait extraction + justification)
    sdlc_rules.py            deterministic SDLC scoring engine
  rag/                       embeddings, ingest, retriever
  static/                    index.html, style.css, app.js (the UI), charts.js (SVG charts), sample_run.json
knowledge_base/              curated compliance source documents (*.txt) + sources.json metadata
chroma_db/                   generated vector store (rebuild with the ingest command)
data/app.db                  local database (created on startup, gitignored)
tests/                       offline test suite
scripts/                     threshold tuning (live), win-share and sensitivity analysis (offline), make_sample_run.py
docs/ARCHITECTURE.md         design notes, diagrams, scoring tables
docs/RAG_TUNING.md           relevance threshold tuning results
docs/SCORING_ANALYSIS.md     win share and sensitivity of the SDLC scoring
docs/EVALUATION_BASELINE.md  evaluation of the project before the critical fixes
docs/COVERAGE.md             problem-statement coverage plan: deliverable, phase and status per section
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Internal Server Error` or an error panel in the UI | Read the terminal traceback - it names the real cause. |
| `404 NOT_FOUND ... model is no longer available` | Google retired the model name. Put the replacement it names into `GEMINI_MODEL` in `.env`, and add it to `FALLBACK_MODELS` in `app/gemini_client.py`. Restart the server. |
| `503 UNAVAILABLE` | Google-side demand spike. The client retries the same model up to 4 times (2, 4, 8 s waits) and then falls back; if every model fails, the error lists each attempt, so you can see whether it was demand (503) or quota (429). |
| `429 RESOURCE_EXHAUSTED` | Free-tier rate limit. The client waits and retries (up to 10 s per wait), then tries the next model; a daily-quota error ("retry in 21h") skips straight to the next model. If every model is exhausted, try again later or use another key. |
| Check that the key and model work | `python -m app.gemini_client` sends one tiny prompt and prints which model answered. |
| `Knowledge base not found` | Run `python -m app.rag.ingest`. |
| UI looks old or a stage is missing | Hard refresh the browser (Ctrl+Shift+R). |
| Changes do not show up / odd stale behaviour | An old server process may still hold the port. Check with `netstat -ano | findstr :8001`, then `taskkill /PID <pid> /F`, or use another `--port`. |
| `.env` changes ignored | Restart the server; `--reload` only watches Python files. |

## Honest limitations

- The knowledge base is a small curated set of paraphrased regulatory principles (31 chunks, 6 documents), not verbatim legal text.
- Trait scores come from an LLM and can vary slightly between runs; everything after that step is deterministic.
- Free-tier Gemini has rate limits and model names change; the client is built to tolerate this.
- Never share or submit your `.env` file - it contains your API key. The `.gitignore` already excludes it.
