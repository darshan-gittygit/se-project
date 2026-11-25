# Requora AI

AI-powered requirements engineering: describe a software scenario, and Requora AI generates structured requirements, analyses compliance and risk against a curated knowledge base (RAG), and recommends an SDLC model with an explainable, deterministic scoring trail.

Built by Darshan - NIT Karnataka, Surathkal (CS305 Software Engineering course project).

**Problem statement:** Gathering software requirements and choosing the right SDLC model is manual and subjective, and it often ignores regulatory and risk constraints. Requora AI automates this: it generates structured requirements from a plain-language scenario, checks them against a compliance knowledge base using RAG, and recommends an SDLC model through deterministic, explainable scoring.

## How it works

```
Scenario -> Agent 1: Requirements -> Agent 3: Compliance & Risk (RAG) -> Agent 2: SDLC recommendation
```

1. **Agent 1 (requirements):** functional and non-functional requirements with IDs and priorities.
2. **Agent 3 (compliance and risk):** runs one retrieval query per requirement against the knowledge base in `knowledge_base/` (ChromaDB + Gemini embeddings) and passes only chunks under a tuned distance threshold to the LLM. Whether the knowledge base covers the domain (`kb_relevance`) is computed from those distances using domain-specific sources only; the domain-agnostic security baseline can still be cited, labelled "generic baseline", but never counts as coverage. Any citation of a source that was not retrieved is dropped, so the panel can only cite what was actually retrieved.
3. **Agent 2 (SDLC):** the LLM rates 9 project traits against fixed rubrics and must cite evidence; a deterministic engine scores Waterfall, V-Model, Incremental, Agile and Spiral; the LLM then explains the result. The LLM never picks the model.

Every LLM reply is checked against a schema (`app/schemas.py`): allowed values, known requirement IDs, real knowledge-base sources, all nine traits as integers 1-5. An invalid reply is retried once with the errors, then the API returns a 502 rather than guessing.

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

## Tests

The tests run offline (Gemini and retrieval are mocked), so they need no key or internet:

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

They cover the scoring engine (including that no model gets a per-model bonus), retrieval filtering and `kb_relevance`, schema validation and retries, the Gemini fallback and rate-limit logic, the analysis scripts, and every API route.

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
  agents/
    requirement_agent.py     Agent 1
    compliance_agent.py      Agent 3 (RAG)
    sdlc_agent.py            Agent 2 (trait extraction + justification)
    sdlc_rules.py            deterministic SDLC scoring engine
  rag/                       embeddings, ingest, retriever
  static/                    index.html, style.css, app.js (the dashboard)
knowledge_base/              curated compliance source documents (*.txt)
chroma_db/                   generated vector store (rebuild with the ingest command)
tests/                       offline test suite
scripts/                     threshold tuning (live), win-share and sensitivity analysis (offline)
docs/ARCHITECTURE.md         design notes, diagrams, scoring tables
docs/RAG_TUNING.md           relevance threshold tuning results
docs/SCORING_ANALYSIS.md     win share and sensitivity of the SDLC scoring
docs/EVALUATION_BASELINE.md  evaluation of the project before the critical fixes
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Internal Server Error` or an error panel in the UI | Read the terminal traceback - it names the real cause. |
| `404 NOT_FOUND ... model is no longer available` | Google retired the model name. Put the replacement it names into `GEMINI_MODEL` in `.env`, and add it to `FALLBACK_MODELS` in `app/gemini_client.py`. Restart the server. |
| `503 UNAVAILABLE` | Google-side demand spike. The client already retries and falls back; just try again. |
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
