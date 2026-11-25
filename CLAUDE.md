# Requora AI - guide for Claude Code

Course project (CS305 Software Engineering, NITK, solo, by Darshan). A FastAPI app: scenario -> requirements -> compliance/risk (RAG) -> SDLC recommendation. Pure Python, calls Gemini through the `google-genai` SDK. No LangChain/LangGraph; agents are plain functions chained in `app/main.py`.

## Commands

- Run: `python -m uvicorn app.main:app --reload --port 8001` (or `run.bat`), UI at http://127.0.0.1:8001/
- Build knowledge base (needs `.env` key + internet): `python -m app.rag.ingest`
- Tests (offline, mocked): `python -m pytest -q`
- Windows + PowerShell; use the project's `venv`.

## Rules for changes

- The UI is being rebuilt in Phase 7; after that phase, keep UI changes small.
- SDLC scoring stays deterministic: the LLM extracts traits (with evidence) and writes explanations, but never chooses the model. No hidden bonus for any model, no hardcoded domain -> model mapping. `tests/test_scoring.py` enforces fairness; keep it passing.
- Explanations must never contradict measured trait scores.
- Compliance stays honest: cite only retrieved knowledge-base content; say when the KB does not cover a domain. Never fabricate citations or confidence percentages.
- The knowledge base is paraphrased principles, not verbatim law - describe it that way.
- Keep the JSON response shapes of `/agents/*` stable; `app/static/app.js` depends on them.
- Never print, commit or ship `.env` (it holds the Gemini API key). `chroma_db/` and `venv/` are not committed.

## Gotchas

- Google retires free-tier Gemini model names often. The error message names the replacement; update `GEMINI_MODEL` in `.env` and `FALLBACK_MODELS` in `app/gemini_client.py`.
- `.env` and Python changes need a server restart if `--reload` misses them; a stale process on the port causes confusing "old code" behaviour.
- ChromaDB distances are the default squared-L2 metric; the 0.62 relevance threshold in `compliance_agent.py` was tuned for that and for one query per requirement (`python -m scripts.tune_relevance_threshold`, results in `docs/RAG_TUNING.md`). Re-tune it if the metric, embedding model or query format changes.
- `google-genai==0.3.0` is pinned (it is what the working venv uses) but is marked yanked on PyPI for Pillow vulnerability issues. Upgrading is a sensible task, but test live afterwards: `generate_content`, `embed_content` and the error classes are used in `app/gemini_client.py` and `app/rag/embeddings.py`.

## Layout

See `README.md` (project layout) and `docs/ARCHITECTURE.md` (design, diagrams, scoring tables).

## Not done yet / ideas

- Live verification of the 9-trait extraction on the four test scenarios (social media, payroll, railway, food delivery); payroll volatility should come out low (1-2).
- Documentation generator (auto SRS from the three agents' output), audit log, evaluation metrics.
- Feed compliance findings into SDLC criticality/verification scoring.


Keep it simple: standard library first, no new dependencies or frameworks without asking, small modules with tests.