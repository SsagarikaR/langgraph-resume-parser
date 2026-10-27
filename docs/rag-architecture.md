# RAG architecture

How the interview-guideline retrieval pipeline is built, and where its design notes live in code.
This consolidates what was previously scattered across module docstrings; see those modules for
the authoritative, line-level detail — this page is the map.

## Pipeline

```
data/knowledge_base/*.md  →  chunk_markdown()  →  embed (Gemini)  →  Qdrant collection
                                                                          │
generate_questions (graph node)                                         │
        │                                                                │
        ▼                                                                │
  gather_guidelines(topics)  ──tool call──▶  lookup_interview_guideline  ─┘
        │                                         (app/tools/knowledge_tools.py)
        ▼
  LLM summary → "company_interview_guidelines" → QUESTION_GENERATION prompt
```

- **Ingestion & chunking**: `app/repository/vector_repository/vector_store.chunk_markdown`. Each
  bullet or "Red flags:" line in a knowledge-base file becomes one chunk; the embedded `content`
  string includes the file's topic and the chunk's kind, so a query like "Kafka" also matches the
  system-design guideline about event streams.
- **Embeddings & vector store**: `app/repository/vector_repository/vector_store.py`. Qdrant, local
  mode by default (`./qdrant_data`) or a remote server via `QDRANT_URL`. A model-marker file
  (`.qdrant_embedding_model`) forces a rebuild on any embedding-model change, even a same-dimension
  swap that the vector-size check alone would miss. `ingest()` and `search()`/`ensure_index()`
  coordinate through a file lock so a rebuild never serves a half-written index.
- **Retrieval & relevance**: `vector_store.search()` applies two filters: an absolute cosine
  cut-off (`GUIDELINE_MIN_SCORE`) and a relative margin (`GUIDELINE_RELATIVE_MARGIN`) that drops
  hits trailing the query's best match by more than that margin, so a merely-passable tail result
  doesn't dilute the summary alongside a clearly-best one. Query embeddings are cached
  (`_embed_query_cached`) since screenings repeat the same skill names often.
- **Retrieval orchestration**: unlike a typical "embed query → top-k → stuff into prompt" RAG,
  this is tool-calling RAG: `app/tools/guidelines.gather_guidelines` gives the LLM a
  `lookup_interview_guideline` tool and lets it decide what to search for (one call per skill/area)
  and when it has enough to summarize, up to `MAX_TOOL_ROUNDS`/`MAX_TOOL_CALLS` safety limits.
- **Generation & grounding**: the summary becomes `company_interview_guidelines` in the
  `generate_questions` node's context (`app/graph/nodes.py`), feeding the `QUESTION_GENERATION`
  prompt in `app/prompts/screening_prompts.py`.

## Trust boundaries

- Resume and job-description text are **untrusted**: wrapped in `<document>` tags
  (`app/prompts/screening_prompts.as_document`, which also strips any embedded closing tag), and
  every prompt that later sees values copied from them states the `UNTRUSTED_FIELDS` rule.
- Knowledge-base content (`data/knowledge_base/*.md`) is **trusted**: first-party, human-reviewed,
  versioned with the app, so `gather_guidelines` passes search results straight into the
  question-generation prompt without the `<document>`/`UNTRUSTED_FIELDS` wrapping. If the
  knowledge base ever becomes end-user-editable or synced from an external source, it must be
  treated as untrusted input the same as resume/JD text — see
  `app/repository/vector_repository/ingest.py` for this exact caveat at the point it would bite.
- A failed guideline search degrades gracefully (`app/tools/knowledge_tools.py`): the screening
  still completes without guidelines rather than failing the whole workflow.

## Verification against hallucination

Retrieval quality is only half the grounding story; the rest is checking what the LLM does with
what it's given:

- `unverified_skills` (`app/graph/matching.py`) flags any skill the LLM extracted that never
  literally appears in the resume text.
- Interview-question topics are cross-checked the same way in `generate_questions`
  (`app/graph/nodes.py`): a non-behavioral question whose topic doesn't appear in the resume, JD,
  matched/missing skills or project text is marked `topic_verified: false`.
- Durations and match percentages are computed in code, never left to the LLM to (re)calculate.

See also: [threat-model.md](threat-model.md) for the full STRIDE analysis (this page covers only
the RAG-specific slice of it) and [operations.md](operations.md) for running `ingest` and tuning
the retrieval settings in production.
