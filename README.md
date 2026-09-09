# RAGScope

**An observable and adaptive retrieval-augmented generation evaluation platform for scientific dataset discovery.**

RAGScope is a local research system for studying how evidence moves through a retrieval-augmented generation (RAG) pipeline. It ingests scientific documents, executes controlled retrieval and generation configurations, preserves the observable execution path, and evaluates where evidence or answer quality is lost.

This repository contains the research platform and deterministic engineering fixtures. It does **not** contain a completed main scientific experiment, and it makes no claim that one pipeline is universally superior.

## Research problem

Most document-question-answering systems expose a final response but provide insufficient evidence to determine whether an error originated in parsing, retrieval, reranking, context selection, generation, or citation handling. This makes pipeline comparison difficult and can hide trade-offs between answer quality, latency, and cost.

RAGScope addresses the primary research question defined in the project specification:

> How do retrieval strategy, reranking, context construction, and adaptive routing affect evidence retrieval, answer faithfulness, citation quality, latency, and cost in scientific-document RAG?

The platform separates retrieval quality from generation quality, records exact research conditions, and evaluates results against human-authored benchmark evidence rather than hidden model state.

## What the system does

```text
scientific files
  -> validation and content-hashed storage
  -> parsing, elements, chunks, lexical/dense indexes
  -> frozen corpus version

question
  -> normalization, classification, optional rewriting
  -> fixed pipeline or deterministic adaptive route
  -> lexical/dense retrieval -> optional RRF -> optional reranking
  -> deduplicated, token-budgeted context with stable source IDs
  -> structured grounded generation -> claims and citations
  -> observable trace -> metrics -> failure attribution

frozen corpus + benchmark + pipelines
  -> experiment run matrix -> immutable raw runs
  -> denominator-aware aggregation -> CSV/JSON exports and figures
```

The same query orchestrator executes fixed and adaptive routes. Adaptive routing selects a persisted runtime route without modifying frozen pipeline configurations or creating a second RAG implementation.

## Core capabilities

| Area | Implemented capability |
| --- | --- |
| Corpus and documents | Versioned corpora; bounded PDF/Markdown/text upload; content hashes; Docling-backed parsing; page/element/chunk provenance; fixed and structure-aware chunking |
| Indexing and retrieval | PostgreSQL full-text retrieval using `ts_rank_cd`; dense cosine search with pgvector; document/year filters; corpus isolation; configurable Reciprocal Rank Fusion |
| Pipelines | No retrieval, lexical, dense, hybrid, hybrid plus reranking, and deterministic adaptive routing; immutable configuration and prompt snapshots |
| Reranking and context | Reorder-only reranking; preserved lexical/dense/fused/reranked ranks; deterministic deduplication; token budgets; selected/excluded evidence tracking |
| Generation | Provider-independent structured output; deterministic fake provider; opt-in Gemini and OpenAI-compatible adapters; answerable, partially answerable, and unanswerable outcomes |
| Evidence and tracing | Exact context and raw-response artifacts; ordered observable spans; claim-level citations; source resolution; redacted, versioned trace exports |
| Research authoring | Dataset-metadata extraction with field evidence and correction history; benchmark questions, acceptable evidence sets, leakage warnings, review, and immutable versions |
| Evaluation | Versioned retrieval, context, generation, citation, answerability, operational, and routing metrics; human and automatic labels remain distinct |
| Failure analysis | Observable-stage taxonomy covering parsing, retrieval, reranking, context, generation, citation, and infrastructure failures; human overrides preserve automatic labels |
| Experiments | Frozen dependency snapshots; deterministic question × pipeline × repetition matrix; PostgreSQL job queue; pause/resume; bounded retries; immutable raw results |
| Analysis | Explicit denominators and missingness; evidence-survival and adaptive comparisons; run-level and aggregate CSV; versioned JSON; eight reproducible SVG/JSON figures |
| Interface | Corpus Studio, Document Inspector, Pipeline Builder, Query Laboratory, Pipeline Comparison, Dataset Intelligence, Benchmark Authoring, Experiment Manager, and Results Dashboard |

PostgreSQL lexical scores are not described as BM25. The portable fake embedding, generator, reranker, and judge implementations are deterministic test components, not substitutes for research-quality models.

## Architecture and technology stack

RAGScope has three persistence boundaries:

1. SQLAlchemy-managed relational state in PostgreSQL/pgvector (SQLite is supported for portable development tests).
2. Immutable, content-addressed artifact files for large or exact payloads.
3. Frozen version records for corpora, prompts, pipelines, routers, benchmarks, metrics, and experiments.

| Layer | Technology |
| --- | --- |
| API and services | Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16, pgvector; SQLite for deterministic portable tests |
| Background execution | PostgreSQL-backed worker queue with leases, attempts, idempotency, cancellation, and `FOR UPDATE SKIP LOCKED` claiming |
| Parsing | Docling, with deterministic text/Markdown fixture paths |
| Model providers | Deterministic fake providers; Gemini generation and embeddings; OpenAI-compatible generation; optional local Sentence Transformers CrossEncoder |
| Frontend | Next.js 16, React 19, TypeScript 5, CSS Modules |
| Verification | pytest, Ruff, mypy, ESLint, Next.js type/build checks, Playwright |
| Local deployment | Docker Compose services: `db`, `api`, `worker`, and `web` |

Routes validate transport data and delegate to application services. Long operations return a typed HTTP `202` job receipt and execute in the worker with a fresh database session. Provider credentials remain environment-only and are not returned to the browser.

See [Architecture](docs/ARCHITECTURE.md) for component responsibilities, versioning rules, trace semantics, and security boundaries.

## Experimental methodology

The intended controlled study compares these frozen conditions:

| Condition | Retrieval and routing |
| --- | --- |
| P0 — No RAG | Generator receives no corpus evidence |
| P1 — Lexical | PostgreSQL full-text retrieval, no reranker |
| P2 — Dense | pgvector cosine retrieval, no reranker |
| P3 — Hybrid | Lexical and dense retrieval with Reciprocal Rank Fusion |
| P4 — Hybrid + reranking | Hybrid candidates followed by a configured reranker |
| P5 — Adaptive | Versioned deterministic router selects only allowed, available settings |

A valid experiment references one frozen corpus version, one frozen human-reviewed benchmark version, frozen pipelines, exact prompt/model/index/router/metric versions, repetitions, pricing, retry policy, and a code commit. Every benchmark question × pipeline × repetition cell creates an independent `QueryRun` with a deterministic identity.

Human benchmark evidence is primary ground truth. Answerable questions require at least one acceptable evidence set; unanswerable questions require a reviewed explanation. Alternative evidence sets are OR alternatives, while evidence items within a set are jointly required.

Implemented measurements include:

- retrieval Recall@k, Precision@k, reciprocal rank, nDCG@k, evidence-set completeness, and required-document recall;
- context precision/recall, required-evidence retention, redundancy, source diversity, and token count;
- human-labelled correctness, completeness, abstention, unsupported claims, and contradictions;
- citation existence, precision, recall, support, and completeness;
- latency, token use, configured cost, provider calls, and infrastructure failures; and
- adaptive-route distribution and quality/resource deltas against an explicitly selected fixed baseline.

Missing annotations or prices remain null; they are never converted to zero. Aggregates record their numerator or median input, denominator/sample size, missing count, infrastructure-excluded count, and contributing run identifiers. Research-quality failures remain results and are not retried or excluded as infrastructure failures.

The exact metric definitions and study controls are documented in [Methodology](docs/METHODOLOGY.md). Export-to-report rules are in [Report inputs](docs/REPORT_INPUTS.md).

## Results status

No final corpus, frozen 50–100-question human benchmark, real-provider pilot, or main experiment is present in the tracked research documentation. Consequently, there are no valid pipeline rankings, effect sizes, latency/cost comparisons, or scientific conclusions to report.

The repository does include a deterministic engineering workflow with verified dimensions:

- 2 synthetic Markdown documents containing a table, missing fact, contradiction, distractor, and prompt-injection text;
- 6 frozen fake-provider conditions (P0–P5);
- 5 reviewed fixture questions;
- 30 experiment cells (5 questions × 6 pipelines × 1 repetition); and
- 8 generated figure specifications/figures plus a manifest.

These outputs validate orchestration and reproducibility mechanics only. They are not empirical findings. Generated figures are content-addressed or written beneath ignored artifact directories; no main-result screenshots or charts are committed for inclusion in this README.

On 2026-09-09, the portable backend suite completed with **232 tests passed and 2 PostgreSQL-specific tests skipped** because `RAGSCOPE_POSTGRES_TEST_URL` was not configured for that invocation. The maintained QA record documents a separate PostgreSQL-backed run in which all 234 tests passed. See [Gemini QA handoff](docs/GEMINI_QA.md) for the environment and remaining independent checks.

## Setup

### Docker Compose

Requirements: Docker Desktop with Compose.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open:

- UI: `http://localhost:3000`
- OpenAPI documentation: `http://localhost:8000/docs`
- API health: `http://localhost:8000/health`

Verify the services and queue:

```powershell
docker compose ps
docker compose logs worker --tail 50
docker compose exec db psql -U ragscope -d ragscope `
  -c "select status, count(*) from jobs group by status order by status;"
```

PostgreSQL is exposed on host port `5433`; a host `psql` installation is not required. Database and artifact data are kept in named Docker volumes. Do not remove those volumes before exporting or backing up a study.

The defaults use deterministic fake providers and require no API key. For opt-in Gemini generation and embeddings, edit only the untracked root `.env`:

```dotenv
RAGSCOPE_GENERATION_PROVIDER=gemini
RAGSCOPE_EMBEDDING_PROVIDER=gemini
RAGSCOPE_GEMINI_API_KEY=<local secret>
RAGSCOPE_GEMINI_MODEL=gemini-2.5-flash
RAGSCOPE_GEMINI_EMBEDDING_MODEL=gemini-embedding-001
```

Never commit `.env` or place credentials in prompts, benchmark notes, frontend forms, or experiment metadata. Token pricing is configured separately as decimal currency units per one million tokens; blank means unavailable, not zero. See [Reproducibility](docs/REPRODUCIBILITY.md) for the complete environment contract.

### Local development

Requirements: Python 3.12+, Node.js 22+, and PowerShell for the commands below.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,pdf]"
Copy-Item .env.example .env
$env:RAGSCOPE_DATABASE_URL = "sqlite:///./ragscope.db"
$env:RAGSCOPE_ARTIFACT_ROOT = (New-Item -ItemType Directory -Force ".\var\artifacts").FullName
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload
```

In a second terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

SQLite is suitable for deterministic development, not for claims about PostgreSQL full-text or pgvector behavior.

## Usage

The normal UI workflow is:

1. In **Corpus Studio**, create a corpus/version, upload documents, parse them, generate chunks, build the required indexes, review warnings, and freeze the ready version.
2. In **Pipeline Builder**, create and freeze fixed pipeline configurations and, if needed, a frozen router plus adaptive pipeline.
3. In **Query Laboratory**, run individual questions and inspect rankings, context selection, exact context, traces, generation, claims, citations, metrics, and failure attribution.
4. Use **Pipeline Comparison** to align two to four frozen configurations on the same corpus and original question.
5. Use **Dataset Intelligence** for evidence-backed dataset extraction and field-level human review.
6. Use **Benchmark Authoring** to create reviewed questions, stable acceptable evidence sets, and an immutable benchmark version.
7. In **Experiment Manager**, create an experiment, inspect its cost estimate and dependencies, freeze it, start the worker-backed matrix, pause/resume if necessary, review human labels, and export results.
8. Use the **Results Dashboard** to filter aggregates, inspect denominators, and drill into contributing QueryRuns.

Long-running API operations return `202`. Poll `GET /api/v1/jobs/{job_id}` or use the UI’s job progress views. A bounded `wait=true&timeout_seconds=<1–60>` mode is available for compatibility and tests.

### Deterministic PostgreSQL fixture

With the Docker database running and a local development environment installed:

```powershell
$env:RAGSCOPE_DATABASE_URL = "postgresql+psycopg://ragscope:ragscope@localhost:5433/ragscope"
$env:RAGSCOPE_ARTIFACT_ROOT = (New-Item -ItemType Directory -Force ".\var\fixture-artifacts").FullName
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m backend.app.fixture_workflow
```

The command is idempotent: rerunning it reuses the deterministic experiment identity instead of duplicating completed valid runs.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider
.\.venv\Scripts\ruff.exe check backend
.\.venv\Scripts\mypy.exe backend/app
.\.venv\Scripts\alembic.exe upgrade head

Set-Location frontend
npm run typecheck
npm run lint
npm run build
npm run e2e
```

Ordinary tests use fake providers and do not perform paid or network model calls. PostgreSQL-specific tests require `RAGSCOPE_POSTGRES_TEST_URL` and a migrated pgvector database.

## Repository structure

```text
backend/
  alembic/                 database migrations
  app/                     API, domain services, providers, worker, analysis
  tests/                   unit, API, integration, metric-gold, and PostgreSQL tests
benchmark/
  fixtures/synthetic/      tracked deterministic evidence fixtures
  fixtures/representative/ ignored local scientific papers for manual validation
docs/                      architecture, methodology, QA, threats, report runbooks
frontend/
  app/                     Next.js routes and research interfaces
  components/              shared navigation and document/evidence components
  lib/                     typed API client and frontend contracts
  tests/e2e/               Playwright critical workflows
scripts/
  fixtures/                synthetic PDF generation
  research/                deterministic research-figure generation
ragscope-project-specification.md
docker-compose.yml
.env.example
```

The documentation index is [docs/README.md](docs/README.md). The complete clean-checkout and experiment workflow is [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md).

## Limitations and future work

- The final scientific corpus, human-reviewed benchmark, real-provider pilot, main experiment, case studies, and report findings still require human research input and execution.
- The deterministic citation verifier uses lexical overlap; it is not full natural-language entailment. Model-assisted judgments are secondary and require separate validation against human labels.
- Some automatic failure-taxonomy signals are not yet derived from stored evidence, so defined categories can remain unassigned without human review.
- Long non-experiment worker handlers do not renew their lease periodically during one large operation; work exceeding the configured lease can be reclaimed and should be addressed before large production runs.
- The cost guard rejects complete estimates above the configured ceiling, but execution is not mechanically conditioned on a separately stored human approval record; paid studies require a documented manual gate.
- PostgreSQL dense search is exact cosine search. No dimension-specific HNSW or IVFFlat index is created.
- PDF parsing quality depends on Docling and source layout; OCR, tables, reading order, and unusual encodings require document-level review.
- Authentication, multi-tenancy, cloud deployment, GraphRAG, corpus-poisoning studies, and multi-turn RAG are outside the implemented scope.
- The local CrossEncoder dependency is optional and disabled in the default Docker build; its model revision must be pinned and cached for a reproducible reranking study.
- Secret redaction is applied before persistence, but final research packages should also be scanned independently across tracked and generated files.

Appropriate next work is to close the worker-lease and paid-execution gates, validate citation/failure judgments, freeze the real corpus and benchmark, execute an approved pilot, and only then run the main experiment. Study-specific threats are maintained in [Threats to validity](docs/THREATS_TO_VALIDITY.md).
