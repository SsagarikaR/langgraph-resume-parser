# Resume Screening (LangGraph)

Screens a resume against a job description with a LangGraph workflow on Gemini:
it extracts structured data, matches skills, analyses gaps, looks up company interview
guidelines (Qdrant vector search), drafts interview questions, then pauses for human
approval before writing a JSON report.

```
validate_input → parse_resume → extract_resume → analyze_job → match_requirements
   ├─ strong match ─────────────────────────┐
   └─ weak match → analyze_gaps ────────────┤
                                            ▼
              generate_questions ⇄ human_review (interrupt) → generate_report
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"       # or: pip install -r requirements-dev.txt
cp .env.example .env          # then set GEMINI_API_KEY
```

## Run

```bash
uvicorn app.api:app --reload              # API on :8000, docs at /docs
streamlit run ui/streamlit_app.py         # UI
resume-screener start data/resumes/resume.pdf data/jds/jd.txt   # CLI (or python -m app.cli); prints the screening id
resume-screener review <id>                                       # approve or revise the questions
resume-screener-ingest                    # rebuild the guideline index (stop the API first in local Qdrant mode)
```

## Live progress

`POST /screenings`, `/screenings/{id}/review` and `/screenings/{id}/continue` accept `?stream=true`.
The response is then NDJSON (one JSON object per line), produced from LangGraph's
`stream(stream_mode="tasks")` while the graph runs:

```
{"type": "step", "node": "extract_resume", "event": "started"}
{"type": "step", "node": "extract_resume", "event": "finished"}
...
{"type": "step", "node": "human_review", "event": "interrupted"}   # paused for review
{"type": "result", "screening": {...}}                              # same body as without stream
```

Input errors are rejected with a normal status code before streaming starts; a failure after that
arrives as a final `{"type": "error", "status": 500, "detail": ...}` event. The Streamlit UI uses this
to show each step as it runs. Without `?stream`, the same run returns one JSON response.

## API protection

Configured in `.env` (see `.env.example`):

- **Rate limits** per client IP: `RATE_LIMIT_PER_MINUTE` (default 60) for any `/screenings`
  request, plus `WORKFLOW_RATE_LIMIT_PER_MINUTE` (default 5) for the requests that run LLM calls
  (start, review, continue). Over the limit returns `429` with `Retry-After`. `/health` is exempt.
  Counts are in memory per worker process. Behind a reverse proxy, run uvicorn with `--proxy-headers`
  so limits apply to real client IPs. All Streamlit users share the UI server's IP.
- **CORS**: `CORS_ORIGINS` is a comma-separated list of browser origins. It is empty by default, so no
  cross-origin browser access. The Streamlit UI calls the API server-side and needs no entry.
- **Request size**: bodies over twice `MAX_UPLOAD_MB` are rejected with `413` before parsing.
- `thread_id` path values are validated, and responses carry basic security headers.

## Test

```bash
pytest
ruff check . && ruff format --check .   # lint + formatting
```

Tests need no API key or network: the LLM and vector store are never called.

## Layout

| Path | Role |
|------|------|
| `pyproject.toml` | Package metadata, dependencies, entry points, pytest and ruff config |
| `app/cli.py` | Command-line runner (`resume-screener`) |
| `app/core/` | Settings (`config.py`, from env/.env), logging and the lazily created chat model (`llm.py`) |
| `app/graph/` | State, nodes, routing, prompts and the compiled graph; `matching.py` (skill and experience scoring) and `guidelines.py` (tool-calling loop) hold the logic the nodes use |
| `app/schemas/` | Pydantic models the LLM fills (structured output) |
| `app/knowledge/`, `app/tools/` | Qdrant guideline store and the LLM tool that searches it |
| `app/utils/` | Small helpers with no LLM: PDF text extraction, safe report filenames |
| `app/repositories/` | Checkpointer and upload access |
| `app/services/` | Business logic, no HTTP |
| `app/api/` | FastAPI app factory, routes, request/response schemas and rate limiting |
| `ui/` | Streamlit client of the API |
