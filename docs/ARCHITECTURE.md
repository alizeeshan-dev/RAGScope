# RAGScope architecture

## System boundary

RAGScope is a local research application with three persistence boundaries:

1. relational state in SQLAlchemy-managed PostgreSQL or SQLite;
2. immutable, content-hashed large payloads in the artifact store; and
3. frozen configuration/version records that identify research conditions.

FastAPI routes validate transport data and call application/domain services. They
do not own retrieval or experiment orchestration. The Next.js application calls
the typed API and never receives provider credentials.

## Components and responsibilities

| Component | Responsibility | Important boundary |
| --- | --- | --- |
| `corpora` | Corpus/version lifecycle and canonical snapshot hashes | Frozen corpus versions reject mutation |
| `documents` | Upload validation, parsing, elements, chunks, provenance | Uploaded bytes and parsed text are untrusted |
| `artifacts` | Atomic content-addressed storage for exact/large outputs | UUID lookup and root containment; no arbitrary paths |
| `indexing` | Version-isolated lexical/dense index construction and integrity | An index cannot mix corpus or chunker snapshots |
| `providers` | Embedding, generation, reranking protocols and adapters | Domain code does not depend on a vendor SDK |
| `pipelines` / `prompts` | Versioned fixed/adaptive settings and prompt snapshots | Frozen records are immutable |
| `query_processing` / `adaptive` | Normalization, classification, rewriting, deterministic routing | Router selects allowed capabilities only |
| `retrieval` / `reranking` / `context` | Candidate retrieval, RRF, reorder-only reranking, evidence selection | Earlier ranks are preserved; rerankers cannot add chunks |
| `generation` / `citations` | Grounded typed output, raw response, claim/source resolution | Invented source IDs invalidate the output |
| `tracing` | Ordered observable execution spans and redacted export | No hidden chain-of-thought is stored or displayed |
| `datasets` | Dataset-field suggestions, evidence, review history, catalog | Model values cannot silently become human ground truth |
| `benchmarks` | Question/evidence authoring and frozen versions | Frozen questions/evidence are immutable |
| `evaluation` | Versioned metrics, citation verification, failure attribution | Missing data stays missing; human/automatic labels coexist |
| `experiments` | Frozen run matrix, cost ceiling, resume/retry semantics | Completed valid cells are not duplicated |
| `analysis` | Explicit-denominator aggregation and deterministic exports | Raw runs and derived aggregates remain distinct |
| `jobs` | PostgreSQL queue, leases, heartbeats, cancellation, and attempt history | Workers claim with `FOR UPDATE SKIP LOCKED`; expired leases are recoverable |
| `research` + `scripts/research` | Exact-selector report figures from versioned exports | Missing values become audited placeholders, never synthetic zeroes |

## Observable data flow

```text
user question
  -> normalize and optionally classify/rewrite
  -> fixed configuration OR deterministic adaptive route decision
  -> lexical and/or dense retrieval
  -> optional Reciprocal Rank Fusion
  -> optional reorder-only reranking
  -> deterministic deduplication and token-budget context selection
  -> exact context artifact with stable S1..Sn source identifiers
  -> provider-independent structured generation
  -> schema and citation-ID validation
  -> claims/citations and QueryRun finalization
  -> versioned metrics and observable failure attribution
```

The `QueryOrchestrator` is the single execution implementation for fixed and
adaptive runs. Adaptive mode changes the persisted runtime route; it does not
fork a second RAG engine or mutate the frozen pipeline configuration.

## Pipeline interfaces

- Every retriever accepts a corpus version, query, top-k/candidate count, validated
  filters, and retriever configuration.
- Retrieval records retain retriever type, original rank/score, normalized score,
  fused rank/score, reranked rank/score, timing, and context disposition.
- Rerankers receive only retrieved candidates and may only reorder/subselect them.
- Generation providers return provider/model identity, raw and parsed content,
  usage, latency, request ID, and nullable cost where available.
- Metrics consume stored artifacts, human annotations, and observable database
  state—not provider internals or hidden reasoning.
- Experiment analysis requires exact metric name, version, scope, method, and an
  explicit denominator policy before metrics can be combined.

## Versioning and immutability

| Research object | Mutable state | Frozen consequence |
| --- | --- | --- |
| Corpus version | documents, metadata, parse/chunk/index configuration | content hash identifies the document/index snapshot |
| Pipeline configuration | retrieval through generation settings | changes require a new version |
| Prompt | template and declared variables | QueryRun identifies exact prompt snapshot |
| Router configuration | deterministic rules and allowed capabilities | adaptive route remains reproducible |
| Dataset extraction | model suggestion plus review history | original model output is preserved after edits |
| Benchmark version | questions, answer criteria, evidence sets | questions and evidence become read-only |
| Experiment | dependencies, pipelines, repetitions, retry policy, code commit | run matrix and configuration hash are fixed |
| Metric/judge/taxonomy | implementation or prompt/rule version | recomputation adds a new result identity rather than silently replacing old data |

An experiment freeze re-resolves its dependencies and rejects mutable/mismatched
corpus, benchmark, pipeline, prompt, or adaptive-router dependencies.

## Trace model

Each `TraceSpan` belongs to one QueryRun and records parent, sequence, type, name,
status, start/finish, latency, concise redacted input/output summaries, the actual
configuration snapshot, stable error code, and artifact IDs. Large exact payloads
remain in artifacts and are loaded only on demand.

The observable hierarchy includes query processing/classification/rewriting,
routing, lexical/dense retrieval, fusion, reranking, context construction,
generation, claim processing, and citation validation. A failed stage preserves
earlier successful spans and does not fabricate downstream spans.

## Error boundaries

- Domain validation errors have stable public codes.
- Provider, embedding, database, timeout, and structured-output failures remain
  infrastructure failures rather than answer-quality outcomes.
- Evaluation excludes documented infrastructure failures only when the persisted
  metric denominator policy says so.
- Trace instrumentation is observational; it must not change retrieval/generation
  behavior or leave QueryRun state inconsistent.
- Experiment attempts distinguish retryable infrastructure failures from terminal
  cells. Resume uses stable matrix/idempotency identities.

## Security and privacy boundaries

- Secrets are read through environment-backed settings (`SecretStr` where used).
- Redaction traverses nested trace/configuration/error structures before persistence.
- Provider errors are sanitized; authorization headers and keys are never persisted.
- Retrieved and uploaded text is delimited as untrusted data in prompts and cannot
  override system instructions or request external actions.
- Uploads are bounded and validated by extension, signature/MIME, binary/text rules,
  and UTF-8 requirements.
- Client filenames are display metadata; artifact paths use random/internal keys.
- Human evidence references must resolve to the same stable document/chunk/element
  provenance and corpus version.
- Exported records contain research state and identifiers, not provider credentials.

Authentication, multi-tenant isolation, cloud deployment, and autonomous tool use
are outside the implemented local research boundary.

## Frontend views

- `/` — Corpus Studio
- `/documents/{id}` — Document Inspector and provenance drill-down
- `/runtime` — pipeline and router configuration
- `/laboratory` and `/laboratory/{run_id}` — query execution and observable trace
- `/comparisons` — aligned 2–4 pipeline comparison
- `/datasets` — dataset extraction/catalog/review
- `/benchmarks` — benchmark/version/evidence authoring
- `/experiments` and `/experiments/{id}` — experiment configuration and execution
- `/results/{experiment_id}` — denominator-aware analysis and run drill-down

## Deployment notes

The Docker path uses FastAPI, a separate PostgreSQL-backed worker, Next.js,
PostgreSQL 16, and pgvector. API routes enqueue and commit long operations before
returning a typed `202` receipt. Worker handlers use fresh database sessions,
lease/heartbeat jobs, retain sanitized attempts, and share the content-hashed
artifact volume with the API. Experiment pause is cooperative between matrix
cells; resume preserves completed cells and retries only allowed infrastructure
failures.

SQLite exists
for deterministic development and tests, not as evidence that PostgreSQL-specific
ranking/migration behavior has been validated. Artifact bytes must remain outside
executable application directories in every deployment.
