# RAGScope

RAGScope is a research platform for observable and reproducible Retrieval-Augmented Generation over scientific documents. Chunks 1–2 implement the scientific knowledge-base lifecycle and a complete, fixed single-query RAG runtime.

The runtime supports no retrieval, lexical, dense, hybrid RRF, and hybrid plus reranking. Adaptive routing, full trace UI, benchmarks, evaluation, experiments, and results dashboards remain out of scope until later chunks.

## Chunk 2 runtime

- Versioned, freeze-only pipeline configurations and a frozen `grounded-answer` prompt snapshot.
- One application orchestrator for normalization/classification, optional rewrite, retrieval, fusion, optional reranking, context selection, grounded structured generation, and persistence.
- Inspectable lexical/dense/fused/reranked ranks that are never overwritten.
- Whole-chunk token budgeting, deterministic deduplication, stable `S1` source IDs, and byte-exact persisted generator context.
- Typed answerable/partial/unanswerable output, raw-response artifacts, claim/citation existence validation, usage and nullable cost records.
- Deterministic offline generation/reranking plus opt-in native Gemini and OpenAI-compatible generation.
- API and `/runtime` UI for pipeline configuration, single-query execution, claims, citations, and basic source inspection.

## Chunk 1 architecture

- `backend/app/corpora`: corpus/version state machine and canonical content hashes.
- `backend/app/artifacts`: content-hashed, random-keyed atomic local artifact storage outside executable source.
- `backend/app/documents`: validation, parser protocol, Docling adapter, text/Markdown normalization, warnings, and chunkers.
- `backend/app/indexing`: version-isolated PostgreSQL FTS/pgvector indexes with portable SQLite fixture implementations.
- `backend/app/providers`: provider-independent embedding/generation/reranker protocols and deterministic fakes.
- `backend/app/jobs`: durable local job state and idempotency contracts.
- `frontend`: accessible Next.js corpus, document, provenance, chunk, and index inspection screens.

Multiple chunker outputs may coexist for comparison. Exactly one chunker snapshot is active on a corpus version; only its chunks enter that version's lexical and dense indexes.

## Quick start with Docker

Requirements: Docker Desktop with Compose.

1. Copy `.env.example` to `.env`. The example contains local-only placeholders, not secrets.
2. Run `docker compose up --build`.
3. Open `http://localhost:3000`; the API documentation is at `http://localhost:8000/docs`.

The API container installs the optional Docling dependency so PDFs use layout-aware parsing. PostgreSQL 16 is supplied with pgvector. Uploaded artifacts live in a Docker volume and are excluded from Git.

## Local development

Backend (Python 3.12+):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,pdf]"
Copy-Item .env.example .env
$env:RAGSCOPE_DATABASE_URL = "sqlite:///./ragscope.db"
$env:RAGSCOPE_ARTIFACT_ROOT = "./var/artifacts"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload
```

Frontend (Node 22+):

```powershell
Set-Location frontend
npm install
npm run dev
```

For PostgreSQL development, keep the database URL from `.env.example` and start the `db` Compose service. Never commit `.env`.

## Fixture workflow

1. Create a corpus and draft version.
2. Upload PDF, Markdown, or UTF-8 text. Duplicate bytes in the same version are rejected.
3. Open a document, parse it, inspect warnings/elements/page provenance, and preview its original PDF.
4. Generate fixed-token and/or structure-aware chunks. The most recently generated strategy becomes the active index snapshot while both remain inspectable.
5. Build indexes. The persisted job reports lexical and dense progress/failures.
6. Verify status/counts, perform fixture search through the lexical/dense API, and freeze the ready version.
7. Confirm any post-freeze upload, parse, metadata, chunk, or state mutation returns `CORPUS_VERSION_IMMUTABLE`.

## Scoring and providers

- PostgreSQL lexical search uses `ts_rank_cd`; it is not called BM25.
- SQLite tests use a documented TF-IDF cosine fallback.
- Dense PostgreSQL search uses pgvector cosine distance; SQLite fixture tests use the same cosine definition in process.
- Ordinary tests use the deterministic fake embedding provider and need no API key.

Exact details are in [docs/INDEXING.md](docs/INDEXING.md).

## Gemini generation

Set `RAGSCOPE_GEMINI_API_KEY` only in the root `.env`, then create a pipeline with
`generation_configuration.provider` set to `gemini`. The runtime UI exposes this as
**Google Gemini** and preselects the stable `gemini-2.5-flash` model. The adapter uses
Gemini's native `generateContent` structured-output contract; fake and OpenAI-compatible
providers remain available and unchanged.

## Engineering checks

```powershell
.\.venv\Scripts\pytest.exe backend/tests -q -p no:cacheprovider
.\.venv\Scripts\ruff.exe check backend
.\.venv\Scripts\mypy.exe backend/app
Set-Location frontend
npm run typecheck
npm run lint
npm run build
```

## Security and limitations

Uploads are size-, extension-, signature/MIME-, binary-, and UTF-8-validated. Client filenames are display metadata only; storage keys are random references and every artifact retains a SHA-256 hash. Keys are checked beneath a dedicated root. Artifact APIs accept UUIDs and never arbitrary filesystem paths.

The optional Docling adapter fails explicitly if unavailable instead of silently degrading to plain PDF text. Real scientific PDF/OCR behavior, live PostgreSQL migrations/ranking, large-corpus performance, and browser-level UI QA still require the focused checks recorded in [docs/GEMINI_QA.md](docs/GEMINI_QA.md).
