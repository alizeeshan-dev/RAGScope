# RAGScope

RAGScope is an observable research platform for studying Retrieval-Augmented
Generation over scientific documents. It preserves the path from an original
question through retrieval, reranking, context selection, structured generation,
claims, citations, evaluation, failure attribution, and controlled experiments.

## Problem and research contribution

RAG systems often expose a final answer without enough evidence to determine
whether a failure came from parsing, retrieval, reranking, context budgeting,
generation, citation handling, or infrastructure. RAGScope makes those observable
boundaries persistent and inspectable. Its contribution is an evidence-centric
instrument—not a claim that one retrieval strategy is universally best.

The platform provides:

- immutable corpus, prompt, pipeline, router, benchmark, and experiment snapshots;
- no-retrieval, lexical, dense, hybrid RRF, hybrid-reranked, and adaptive routes;
- rank history that is never overwritten between retrieval stages;
- exact content-hashed generator context and raw-provider-response artifacts;
- human-reviewed dataset extraction and benchmark evidence annotations;
- versioned retrieval, context, answerability, citation, operational, and failure metrics;
- resumable question × pipeline × repetition experiments; and
- denominator-aware analysis with run-level CSV and versioned JSON exports.

## Architecture at a glance

```text
Scientific files -> parse -> elements -> chunks -> lexical/dense indexes
                                                |
Question -> classify/rewrite -> fixed/adaptive route -> retrieve/fuse/rerank
                                                |
                      exact context -> generation -> claims/citations
                                                |
                observable trace -> evaluation -> failure attribution
                                                |
       frozen benchmark + pipelines -> experiment matrix -> analysis/export
```

The FastAPI application owns domain services and persistence. PostgreSQL/pgvector
is the production-oriented database path; deterministic SQLite implementations
support ordinary engineering tests. Large exact payloads live in the content-hashed
artifact store. The Next.js interface provides the Corpus Studio, Document
Inspector, Pipeline Builder, Query Laboratory, Pipeline Comparison, Dataset
Catalog, Benchmark Editor, Experiment Manager, and Results Dashboard.

See [Architecture](docs/ARCHITECTURE.md) for component, trace, versioning, error,
and security boundaries.

## Supported pipelines

| Condition | Retrieval | Optional rewrite | Optional rerank | Intended role |
| --- | --- | --- | --- | --- |
| No RAG | None | Yes | No | Generation baseline without corpus evidence |
| Lexical | PostgreSQL FTS or deterministic fixture TF-IDF | Yes | Yes | Exact-term baseline |
| Dense | pgvector cosine or deterministic fake embeddings | Yes | Yes | Embedding baseline |
| Hybrid | Lexical + dense with configurable RRF | Yes | Yes | Fused evidence baseline |
| Adaptive | Deterministic versioned router selects allowed settings | Router decision | Router decision | Cost/quality routing analysis |

PostgreSQL lexical ranking uses `ts_rank_cd`; RAGScope does not mislabel it as
BM25. The fake embedding and judge implementations are deterministic test tools,
not research-quality semantic models.

## Quick start with Docker

Requirements: Docker Desktop with Compose.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open:

- application: `http://localhost:3000`
- API documentation: `http://localhost:8000/docs`
- health check: `http://localhost:8000/health`

Docker uses PostgreSQL 16 with pgvector and persists database/artifact volumes.
The checked-in defaults use fake providers and require no external API key.

## Local development

Requirements: Python 3.12+, Node.js 22+, and PowerShell for these examples.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,pdf]"
Copy-Item .env.example .env
$env:RAGSCOPE_DATABASE_URL = "sqlite:///./ragscope.db"
$env:RAGSCOPE_ARTIFACT_ROOT = "./var/artifacts"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload
```

In a second terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

Never commit `.env`. For optional Gemini calls, place
`RAGSCOPE_GEMINI_API_KEY` only in the root `.env`; never paste it into chat,
source, frozen snapshots, prompts, or frontend payloads. The default Gemini
model is `gemini-2.5-flash`.

## Reproducible fixture experiment

The complete setup, UI sequence, API checks, export checks, and clean verification
commands are in [Reproducibility](docs/REPRODUCIBILITY.md). The short path is:

1. Ingest and parse a small local document set.
2. Generate chunks, build lexical/dense indexes, and freeze the ready corpus version.
3. Create and freeze at least two pipeline configurations.
4. Author a small reviewed benchmark with stable source evidence and freeze it.
5. Create an experiment, calculate the cost ceiling, freeze it, and start the matrix.
6. Resume interrupted/retryable cells; completed valid QueryRuns are not duplicated.
7. Inspect contributing runs in Query Laboratory and export tidy CSV/versioned JSON.

Representative local papers belong in the Git-ignored directory
`benchmark/fixtures/representative/`. Their presence on disk does **not** mean
they have been ingested, reviewed, or included in a frozen research corpus.

For a complete no-network engineering proof on PostgreSQL, run:

```powershell
$env:RAGSCOPE_DATABASE_URL = "postgresql+psycopg://ragscope:ragscope@localhost:5433/ragscope"
$env:RAGSCOPE_ARTIFACT_ROOT = (Resolve-Path ".\var\artifacts").Path
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m backend.app.fixture_workflow
```

The idempotent loader creates a synthetic two-document corpus, fixed and
structure-aware chunks, fake lexical/dense indexes, frozen P0–P5 configurations,
a five-question reviewed benchmark, a 30-cell experiment, immutable exports, and
all eight figure artifacts. Repeating the command reuses the same identities.
These are engineering fixtures, not scientific findings.

Long operations return a `202` job receipt by default and are executed by the
PostgreSQL-backed worker. Use `GET /api/v1/jobs/{id}` to poll. `wait=true` is
bounded by `timeout_seconds` (1–60); it never turns a production request into an
unbounded inline task.

## Engineering checks

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\alembic.exe upgrade head
Set-Location frontend
npm run typecheck
npm run lint
npm run build
npm run e2e
```

Ordinary automated tests use deterministic fake providers and do not require
network access or a paid API.

## Results and demonstration status

> **Main-results placeholder:** no main frozen 50–100-question benchmark or main
> research experiment is claimed by this repository documentation. Do not insert
> pipeline rankings, effect sizes, costs, or conclusions here until they are backed
> by a completed frozen experiment and its exported run/aggregate tables.

> **Demo-media placeholder:** no demonstration screenshots or video are claimed as
> checked-in assets. Follow [Demo workflow](docs/DEMO_WORKFLOW.md) to record them
> from an actual local run. Do not substitute mock values for missing experiment data.

The report-input mapping and explicit result placeholders are in
[Report inputs](docs/REPORT_INPUTS.md). A fillable report structure is in
[Research report](docs/RESEARCH_REPORT.md). Reproducible report figures are
generated from a versioned analysis JSON export with
`scripts/research/generate_figures.py`; missing inputs produce labelled
placeholders rather than fabricated points.

## Safety and limitations

- Uploaded documents are untrusted data and cannot trigger browsing, tools, code
  execution, or external actions.
- API keys come only from environment/local secret files and are redacted from
  traces, provider errors, artifacts, and exports.
- Artifact endpoints resolve UUID-backed records beneath a configured root; they
  do not accept arbitrary file paths.
- Human benchmark labels remain separate from model suggestions and automated judges.
- Missing labels and unknown prices remain null/missing, never invented zeroes.
- LLM-judge metrics are secondary; primary conclusions must use human evidence and labels.
- Small corpora, author-created questions, parser errors, model drift, and single-run
  generation can limit validity.
- Real PDF/OCR behavior, live-provider behavior, browser flows, and large-corpus
  performance require focused verification in the target environment.

Read [Methodology](docs/METHODOLOGY.md),
[Threats to validity](docs/THREATS_TO_VALIDITY.md), and the maintained manual QA
checklist in [docs/GEMINI_QA.md](docs/GEMINI_QA.md) before reporting findings.
The complete documentation map is in [docs/README.md](docs/README.md).
