# Gemini QA handoff

## Implementation summary

Chunk 1 establishes a FastAPI/SQLAlchemy corpus lifecycle, safe artifact-backed ingestion, normalized scientific document elements, deterministic fixed and structure-aware chunking, lexical and fake-provider dense indexing, persisted parse/chunk/index jobs, integrity-controlled freezing, and a Next.js inspection UI. Both chunk strategies can remain inspectable, while indexes are constrained to the active chunker configuration.

## Critical workflows Gemini should test

- Create a corpus, create a configured draft version, upload one PDF and one Markdown source, parse both, inspect provenance, generate each chunking strategy, build both indexes, and freeze.
- Upload the same bytes under a different filename and confirm `DUPLICATE_DOCUMENT`.
- Interrupt or repeat parse/chunk/index calls and confirm idempotent results rather than duplicate valid records.
- Attempt upload, metadata change, parse, chunk, and index mutations after freeze; each must return `CORPUS_VERSION_IMMUTABLE`.
- Force one embedding failure and confirm the version cannot become `ready`.

## Important invariants

- Frozen versions are immutable in service/domain checks, not only hidden in the UI.
- A ready index contains only chunks belonging to its corpus version and configuration snapshot.
- Content hashes exclude UUIDs and timestamps; identical source hashes plus canonical configuration yield identical version hashes.
- Original artifacts are random-keyed with retained content hashes, never overwritten, and separate from normalized JSON/chunks.
- Parser warnings remain attached to the document and visible to clients.
- Dense tests use deterministic fake embeddings and never call an external provider.

## Edge cases

- Empty, encrypted, malformed, scanned/OCR-only, unusually large, table-heavy, multi-column, and non-ASCII PDFs.
- Markdown with repeated headings, deep heading levels, pipe tables, lists, very long paragraphs, or no final newline.
- Plain text with invalid UTF-8, broken words, empty sections, and repeated page-like headers.
- Chunk overlap equal to/exceeding target size, zero/negative limits, a table larger than the target, and a document with no elements.
- Two versions of the same corpus with overlapping documents and different chunk/embedding configurations.

## Security checks

- Try `../`, absolute paths, reserved names, Unicode separators, double extensions, mismatched MIME/extension, NUL-like names, and symlink-based artifact access.
- Stream a body beyond the configured limit and confirm no partial artifact/document remains.
- Confirm artifact endpoints accept stable IDs only and never disclose host paths.
- Probe paging/filter values with invalid ranges and SQL-like strings; ORM queries must remain parameterized and bounded.
- Confirm `.env`, API keys, database credentials, and artifact paths are absent from API payloads, logs, and frontend bundles.

## UI checks

- Verify keyboard navigation, focus visibility, status text independent of colour, narrow layouts, long hashes/configurations, empty states, and error announcements.
- Confirm parsed table elements remain visually distinct and provenance is readable.
- Confirm PDF preview/artifact behavior on each supported browser; the initial UI may rely on browser-native PDF viewing.
- Compare fixed and structure-aware chunk boundaries on the same scientific source.

## Integration checks

- Run migrations on a clean PostgreSQL/pgvector database, not only SQLite.
- Validate Docling output mapping and bounding boxes against several real layout-heavy papers.
- Confirm worker/job status and result artifacts survive API restart.
- Confirm PostgreSQL lexical ranking and pgvector cosine ordering match service contracts and stay version-isolated.
- Confirm the frontend's upload, parse, chunk, index, status, and freeze calls match deployed API response shapes.

## Known limitations

- Base automated tests use SQLite and a portable embedding representation; PostgreSQL FTS/pgvector behavior needs dedicated integration QA.
- Docling is an optional/heavier installation and machine-specific PDF/OCR behavior is not exhaustively verified.
- Fake embeddings validate determinism and isolation, not semantic quality.
- The UI is an engineering interface; browser-level end-to-end coverage and polished PDF overlays remain future QA work.
- Local jobs are durable and idempotent but execute inline in the API process; a continuously polling/separate worker and stale-job leasing are not yet implemented.
- Draft document removal deletes database references but deliberately leaves immutable artifact bytes for a future safe orphan-reclamation command.
- Only the fake embedding provider is functional in Chunk 1; real-provider protocols exist, but no external embedding adapter is bundled.

## Assumptions made by Codex

- Whitespace-delimited token counting is used for deterministic base chunk budgets and is identified in configuration.
- Lexical fallback scoring is documented by its real method and is never called BM25.
- Manual document metadata changes are audit metadata only and do not rewrite original or normalized source artifacts.
- A controlled successful index build transitions a version to `ready`; freezing then records the stable hash/time without inventing another status.

## Areas Codex could not fully verify

- Representative real scientific PDF behavior across Docling versions, OCR engines, and operating systems.
- Real external embedding providers, because Chunk 1 requires no paid API and ordinary tests must remain offline.
- Performance and restart behavior on large corpora.
- Browser-native PDF behavior and accessibility across all target browsers.
- Docker Compose startup/build on a clean host; Docker was not installed in Codex's verification environment.

## Regression checklist

- Frozen corpus versions reject every future mutation path.
- Corpus content hashes stay stable under identical documents/config and change for material inputs.
- Source and parsed artifacts retain hashes, media types, producer/config metadata, and safe references.
- Tables stay typed and provenance IDs/page ranges survive chunking.
- Both retrievers remain deterministic under fixtures and isolated by corpus version.
- Partial/foreign/mismatched index entries can never produce a ready version.
- Later query orchestration must reject versions that are not ready/frozen as required by its contract.
- Later provider integrations must remain selectable through configuration and keep fake providers available.

---

# Chunk 2 QA: complete fixed RAG runtime

Preserve every Chunk 1 regression above while checking this section. Codex performed normal deterministic engineering verification; this is the focused manual/integration checklist, not an instruction to run exhaustive benchmarking.

## Implemented functionality and critical workflows

- Create a pipeline for each mode (`none`, `lexical`, `dense`, `hybrid`) and a hybrid pipeline with reranking enabled; freeze it, run one question, and retrieve the persisted QueryRun, retrieval rows, claims, citations, and context-source decisions.
- Confirm normalization preserves `query_text`, stores `normalized_query`, and stores an optional `rewritten_query` separately. Disable classification and confirm the bypass reason/confidence is recorded while the configured fixed route remains unchanged.
- For lexical/dense/hybrid/hybrid-rerank, confirm corpus isolation, top-k, publication-year/document filters, and empty valid filter results. PostgreSQL FTS must be described as `ts_rank_cd`, never BM25.
- Hand-check RRF with configured rank constant and weights. Lexical/dense rank and score rows must remain unchanged after fusion and reranking; a reranker may only return input candidates.
- Confirm whole chunks are selected in final rank order, duplicates and over-budget candidates have distinct exclusion reasons, selected sources receive deterministic `S1`, `S2`, … IDs, and the context artifact bytes exactly equal the evidence context used for generation.
- Confirm the run identifies a frozen prompt ID/version/hash, raw response is a separate artifact, structured fields populate QueryRun/claims/citations, and tokens/provider/model/request ID/latency are retained. Unknown pricing must remain null.

## Answer and citation cases

- Exercise `answerable`, `partially_answerable`, and `unanswerable`; partial output requires limitations and unanswerable output requires an abstention reason.
- Test no-RAG (`none`) and no-relevant-evidence behavior. They must record retrieval disabled or empty evidence and must not treat a low score as proof that a fact is absent.
- Ask about an absent fact, a false premise, conflicting sources, and differing dataset versions. Confirm explicit insufficient/partial/conflicting-evidence wording and no infrastructure failure code for an answer-quality outcome.
- Return malformed JSON and schema-invalid JSON from a fake provider. Confirm `INVALID_STRUCTURED_OUTPUT`, no valid research claims, and preservation of the raw response artifact.
- Return `[S999]` in either answer text or claim citations. Confirm `INVALID_CITATION`, no claim persistence, and no silent acceptance. For valid IDs, verify exact chunk/document/page resolution; entailment remains `not_evaluated` in Chunk 2.

## Prompt-injection and security tests

- Put role changes, “ignore previous instructions,” fake system messages, tool/web/code execution requests, secret requests, and delimiter-looking text inside a retrieved chunk. Confirm it is JSON-escaped inside an explicitly untrusted source block and never executed or followed.
- Put closing delimiter text in the user question and source text. Confirm it cannot escape the question/evidence data boundary or create a new citation source.
- Confirm API keys and authorization headers never appear in pipeline snapshots, artifacts, raw error messages, logs, prompts, API/frontend payloads, or this file. Provider failures must be sanitized.
- Confirm only environment-backed real calls are possible; ordinary tests and default UI flows use fake providers and make no network call.

## UI and integration checks

- On `/runtime`, create/save/freeze configurations, verify invalid combinations are rejected, choose a ready corpus and frozen pipeline, run a question, and inspect answerability, answer, limitations, claims/citations, source inclusion/exclusion, ranks, usage, and nullable cost.
- Check empty/loading/error states, keyboard access, narrow layout, long answers/source text, a failed run, no sources, and many candidates. The UI must not imply that citation presence is entailment.
- Run the Chunk 2 migration up/down/up against clean PostgreSQL with pgvector, then execute all five modes using real PostgreSQL FTS/vector indexes. Confirm API response shapes match the frontend.
- Opt in to native Gemini with `RAGSCOPE_GEMINI_API_KEY` in a local `.env`; verify the `generateContent` structured JSON schema, `x-goog-api-key` secrecy, timeout/sanitized failure behavior, model/request ID and usage capture, nullable pricing, and that the key is absent from persisted data. Repeat the existing OpenAI-compatible check only if that provider is used.

## Known limitations and assumptions

- Citation existence/resolution is implemented; relevance, entailment, contradiction scoring, and general answer evaluation are intentionally deferred.
- The only bundled reranker is the deterministic overlap fake. There is no local cross-encoder download requirement.
- Real generation supports native Gemini and OpenAI-compatible endpoints and is opt-in. Real embedding is not bundled yet; dense runtime uses the corpus version's deterministic fake embedding snapshot in ordinary development/tests.
- Context uses whole chunks and deterministic overlap removal; compression/summarization is deferred. Classification/rewrite are deterministic and do not perform adaptive routing.
- Automated verification used SQLite portable retrieval behavior; live PostgreSQL ranking and real-provider smoke tests were not performed in this pass.

## Chunk 2 regression checklist

- Every run uses the one orchestrator; API routes contain no stage orchestration.
- Only ready corpus versions and frozen pipeline snapshots run.
- Frozen pipeline/prompt rows reject mutation; changing configuration requires a new version.
- No/lexical/dense/hybrid/hybrid-rerank all complete with deterministic fakes.
- Earlier ranks/scores survive fusion/reranking; filters are allow-listed and version-isolated.
- Context budget/dedup decisions and stable IDs are persisted, and exact evidence context/raw provider output remain separate immutable artifacts.
- All three answerability states validate; invented citations and invalid structured output fail explicitly.
- QueryRun timing, tokens, nullable cost, failure code, route/classification, prompt and provider metadata remain recoverable.
- Chunk 1 ingestion, parsing, chunking, indexing, readiness and freeze tests remain green.

## Areas not fully verified

- Live PostgreSQL/pgvector migration and ranking behavior, real Gemini/OpenAI-compatible credentials, high-volume latency/cost behavior, and browser-level end-to-end accessibility.
- Semantic embedding quality, cross-encoder reranking, full trace spans, benchmark/evaluation workflows, and automated citation entailment are later-chunk work.

---

# Chunk 3 QA: observable execution and controlled comparison

Preserve every Chunk 1–2 regression above. Chunk 3 exposes observable application stages and artifacts; it never exposes or labels hidden model reasoning or chain-of-thought.

## Implementation summary and critical trace workflows

- Every executed fixed pipeline records ordered spans for query processing, classification, rewriting, configured routing, retrieval and its lexical/dense/fusion children, optional reranking, context construction, generation, claim processing, and citation validation. Successful stages are checkpointed so a later failure retains a partial trace.
- `GET /query-runs/{id}/trace` returns `ragscope.observable-trace.v1` with the run, frozen corpus/pipeline/prompt identity, ordered hierarchy, compact summaries, artifact IDs/hashes, and derived latency/token/cost/evidence metrics. It does not open artifact bodies.
- Context, raw-response, and parsed-response artifacts link to the QueryRun and producing span. The exact context body remains lazy-loaded through the existing artifact endpoint.
- `POST /query-comparisons` executes 2–4 distinct frozen configurations for one exact original question and ready corpus version. `GET /query-comparisons/{id}` returns persisted run links, interpreted configuration differences, aligned rank/evidence/context/outcome columns, pairwise overlap, timing/cost, and failures.

## High-priority Gemini checks

- Run no-RAG, lexical, dense, hybrid, and hybrid-rerank. Confirm span `sequence_number` matches actual execution order, classification/rewriting are children of query processing, lexical/dense/fusion are children of retrieval, and citation validation is a child of claim processing.
- Confirm lexical/dense original ranks are never overwritten by fused or reranked ranks in the trace, Query Laboratory, or comparison matrix. Move candidates up/down and verify all rank columns and movement direction.
- Compare the expanded exact context in Query Laboratory byte-for-byte with the referenced context artifact. Verify selected/excluded rows, `S1` IDs, token budget, deduplication exclusions, and budget exclusions agree.
- Force retrieval and provider failures. Prior spans must remain succeeded, the failing span/root must carry the stable code, no downstream spans may be fabricated, and the failed run must remain navigable in Query Laboratory and alongside successful comparison columns.
- Attempt to compare duplicate/unfrozen configurations, fewer than two or more than four configurations, an unready corpus, or a runner that changes the corpus/question. Confirm explicit rejection and no silently uncontrolled comparison.
- Put API keys, bearer headers, cookies, token/password fields, credential URLs, nested provider errors, and configured secret values in trace inputs/configurations. Confirm `[REDACTED]` appears and the original secret is absent from span rows, export JSON, artifact metadata, and UI.
- Confirm each citation opens the exact cited passage and the existing Document Inspector with the correct document, page, and chunk hint. Missing/invented citation IDs must still fail rather than create broken provenance.

## Query Laboratory QA

- From `/laboratory`, select a ready corpus/frozen pipeline, run a question, and inspect original/normalized/rewritten query, classification reason/confidence, fixed route, timeline status/latency/failure, and stage configuration summaries.
- Inspect retrieval at realistic top-k values. Confirm document title, page, section, retriever-specific score/rank, fused/reranked rank, selected state, and readable horizontal overflow.
- Expand exact context only on demand. Check loading/error/empty behavior, long source text, no-RAG empty context, deduplication and token-budget reason labels.
- Inspect answerability, limitations/abstention, provider/model, generation latency, tokens, nullable cost, claims, support state, citation passage, and source link. Unknown cost must say unavailable, never zero.
- Check keyboard navigation, visible focus, text status labels independent of colour, screen-reader labels, narrow layouts, and failed/partial loading states.

## Pipeline Comparison QA

- Select one corpus, one question, and 2, 3, then 4 frozen configurations. Confirm every column's QueryRun has the identical `query_text` and `corpus_version_id`, and every trace link opens the correct Query Laboratory run.
- Verify the interpreted difference table for retrieval mode/top-k, embedding model, RRF settings, reranker/model/counts, context budget/deduplication, generation provider/model/temperature/output limit, and prompt version. Audit the raw frozen snapshot separately.
- Confirm evidence overlap/unique sets and Jaccard values by hand for a small fixture. Verify chunks missing from one pipeline, rank changes, context inclusion differences, answerability/citation differences, failures, latency, tokens, and nullable cost remain aligned.
- A single-question comparison must not label a universal winner. One failed column must not crash or hide successful columns.

## Secret redaction, latency, and artifact invariants

- Redaction is recursive and applied at span persistence and export. Provider exception messages are reduced to stable codes/type summaries; authorization payloads and secrets never enter observable metadata.
- Span latency is milliseconds. Parent/child times may overlap; total QueryRun latency may include orchestration/database overhead. UI formatting must not silently change units.
- Cost uses the configured currency/value when available. Unknown price remains null/unavailable. Token counts agree between QueryRun, trace summary, Laboratory, and comparison.
- Trace list/timeline rendering uses structured rows and batched metadata queries. Artifact content is read only when a user expands an exact payload; corpus/query pages must not scan artifact storage.

## Prompt-injection, edge, and integration checks

- Repeat Chunk 2 injection sources containing role changes, tool/web/code requests, fake delimiters, and secrets. The observable trace must identify retrieved text as untrusted document content, never as an application/system instruction or action.
- Test no candidates, all candidates excluded, partial/unanswerable output, conflicting evidence, invalid citations, invalid structured output, long queries, large top-k, nullable pages/titles, and deleted/unavailable artifact content.
- Run Chunk 3 migration up/down/up on clean PostgreSQL/pgvector. Exercise all fixed modes and one mixed success/failure comparison; verify no N+1 metadata pattern and that API/frontend response contracts match.
- With opt-in Gemini, verify request IDs/usage/model/latency where available, sanitized provider failures, partial failed traces, and absence of `RAGSCOPE_GEMINI_API_KEY` from database rows, exports, artifacts, browser payloads, and logs.

## Assumptions and known limitations

- Trace spans represent observable application operations, inputs/outputs, timing, configuration, and artifacts—not hidden model reasoning.
- Benchmark-required evidence-loss labels are absent until human evidence annotations exist. Current evidence flow reports only observable retrieval/reranking/context movement.
- Claim support remains mostly `not_evaluated`; citation entailment, contradiction scoring, evaluation metrics, failure attribution, experiments, and aggregate dashboards remain deferred.
- Comparison context bodies are referenced/lazy-loaded to avoid repeatedly reading large artifacts. The aligned API includes structured context sources and the immutable context artifact ID.
- The frontend has no component-test harness; critical views passed TypeScript, ESLint, and production build checks but browser-level end-to-end and screen-reader QA remain manual.

## Areas not fully verified

- Live PostgreSQL/pgvector timing/query plans, large-candidate performance, real Gemini network behavior, multi-browser source navigation, and browser-level accessibility automation.
- Benchmark evidence annotations, human relevance labels, citation entailment, and universal pipeline-quality conclusions are intentionally outside Chunk 3.

## Chunk 3 regression checklist

- Trace order/hierarchy follows real stage execution; prior spans survive later failure and downstream stages are not invented.
- Every span has status/timing/configuration/compact summaries; large exact outputs remain linked, hashed artifacts.
- Trace export is versioned, reconstructible, payload-light, and secret-free.
- Original, fused, and reranked ranks remain distinct across APIs and both Chunk 3 screens.
- Exact context and citations resolve to immutable artifacts/chunks/documents/pages through the existing inspector.
- Comparisons contain 2–4 frozen pipelines with one exact question/corpus and persist links to each independent QueryRun.
- Comparison configuration/evidence/context/answer/citation/token/cost/latency/failure differences remain aligned without declaring a winner.
- All Chunk 1 ingestion/indexing and Chunk 2 fixed-runtime, prompt, generation, citation, and Gemini protections remain green.

---

# Chunk 4 QA: dataset intelligence and human ground truth

Preserve every Chunk 1–3 regression above. Chunk 4 adds human-reviewed research data; model extraction is always a suggestion, never ground truth by itself.

## Dataset extraction and evidence workflow

- From `/datasets`, run both `baseline` and `retrieval_assisted` extraction on the same ready corpus documents. Confirm the record retains strategy, schema/prompt/provider/model/retrieval snapshots, hashes, job identity, raw response artifact, and parsed artifact.
- Exercise stated values, JSON `null`, and explicit `not_stated`. These states must remain distinct from invalid output, provider failure, an unreviewed field, reviewer rejection, and reviewer clearing.
- Return malformed JSON, extra schema fields, invalid URLs, bad field state/value combinations, and unknown evidence IDs. Confirm explicit rejection and preservation of the safely redacted raw response; unsupported fields must never enter an approved catalog record.
- For every non-null model field, verify at least one evidence row resolves to the same corpus/document and an actual chunk or parsed element, with correct page and a supporting-text substring. Arbitrary model citation IDs must fail.
- Review fields with accept, edit, reject, clear, and mark-not-stated. Confirm original model values/evidence never change, current reviewed values change separately, and every action appends a timestamped revision with note and evidence-backed/manual status.
- Attach a human-selected passage to an edited field and confirm its document/page/element/chunk/text provenance. Approval must fail for a non-null field without evidence, while a manual unsupported assertion remains visibly distinct from an evidence-backed correction.
- Search/filter the catalog by text, domain, modality, task, language, license, and review status. Compare 2–4 records, inspect evidence/history, export JSON, and verify source links open the existing Document Inspector at the correct page/element/chunk.

## Benchmark authoring and immutable versioning

- Create a benchmark, create a draft version against a ready corpus, and author each supported question type with controlled difficulty. Confirm human fields cannot be populated through `model_suggestion` inputs.
- For answerable questions, require a reference answer or criteria and at least one non-empty acceptable evidence set before `reviewed` or freeze. For unanswerable questions, require an explanation and do not require fake evidence IDs.
- Select exact passages from the existing inspector model. Verify every reference resolves within the version corpus to document/page/chunk or element, matches selected-text offsets, and retains required/alternative/contradiction/distractor role and evidence-set membership.
- Create multiple acceptable evidence sets and confirm they remain distinct. Required document/chunk summaries must be derived from stable required/alternative references rather than unvalidated free text.
- Edit a reviewed draft label or add/remove evidence and confirm it returns to `in_review`. Freeze only when all questions satisfy review invariants; after freeze, question/evidence/answerability/reference mutations must fail through both API and UI and ORM-level writes.
- Exercise the deterministic leakage warning with a question that copies a source passage and a paraphrased control. It is advisory only and must not make a model call or block saving.

## Shared annotation UI and source drill-down

- In dataset field review and benchmark evidence selection, navigate documents/pages, choose a parsed element/chunk, select an exact contiguous passage, add/remove it, and inspect the pending/saved provenance. Reload and confirm the same source resolves.
- Confirm dataset original/current values, null/not-stated states, evidence-backed status, notes, and correction history are all visible. Frozen benchmark screens must be read-only and explain why actions are unavailable.
- Check empty/loading/error states, keyboard access, visible focus, long tables/passages, narrow layouts, nullable page/metadata, unsafe URLs, and 2–4-column dataset comparison. Status must not rely on colour alone.

## Security and provider checks

- Place prompt-injection text, fake evidence IDs, role changes, tool/web/code requests, credentials, and delimiter-looking text in a paper. Confirm the extraction prompt treats it as untrusted data and triggers no external action.
- Confirm API keys, authorization headers, sensitive environment values, database credentials, and provider error payloads are absent from jobs, record configuration, artifacts, exports, frontend payloads, logs, and this file.
- Try cross-corpus/cross-document chunk or element references, mismatched pages, non-substring passages, unsafe display URLs, path-like export names, and unrestricted artifact IDs. Confirm bounded validation and no arbitrary file access.
- With opt-in Gemini, confirm dataset extraction sends the dataset-specific JSON schema, captures provider/model/usage safely, and keeps the key environment-only. Ordinary tests must remain deterministic and offline.

## Assumptions, limitations, and unverified areas

- Baseline extraction uses deterministic whole-document/relevant ordered chunks; retrieval-assisted extraction uses deterministic field-oriented source selection. They share the same typed provider contract and can run on the same document set.
- Extraction produces one candidate dataset record per document/run in this development workflow; consolidating duplicate mentions across papers and automated factual verification are deferred.
- Human passage selection uses existing chunk/element text and contiguous ranges; PDF-coordinate drawing and OCR-region annotation are not implemented.
- Benchmark authoring is human-led. Final benchmark construction, evaluation metrics, citation entailment, failure attribution, experiments, and aggregate dashboards are later work.
- Automated verification used SQLite and fake providers. Live PostgreSQL/pgvector, real Gemini network calls, large-catalog performance, browser-level end-to-end, and full accessibility automation remain manual/unverified.

## Chunk 4 regression checklist

- Strict extraction syntax/state validation is separate from factual review and provider/job failure.
- Non-null approved fields always have valid source evidence; explicit not-stated values do not require fabricated evidence.
- Original extraction output/evidence survives every human correction and revisions are append-only.
- Model suggestions never silently become approved dataset values or human benchmark labels.
- Catalog filters/comparison/export retain evidence/configuration provenance and expose no secrets.
- Answerable questions require acceptable evidence; unanswerable questions require explanations without fake evidence.
- Alternative evidence sets remain distinct and every selected passage resolves to the exact source.
- Frozen benchmark versions reject all question/evidence/label mutation in API, service, ORM, and UI.
- Leakage warnings are deterministic, cheap, advisory, and do not replace human review.
- Chunk 1 document/indexing, Chunk 2 fixed runtime/Gemini, and Chunk 3 trace/comparison contracts remain green.

---

# Chunk 5 QA: evaluation, failure attribution, and adaptive RAG

Preserve every Chunk 1–4 invariant above. Chunk 5 measures stored observable outputs and human annotations; it never uses hidden model state or replaces human ground truth with an automated judgment.

## Metric correctness and evidence-set checks

- Recalculate the hand fixtures for Recall@k, Precision@k, reciprocal rank, nDCG@k, evidence-set completeness, required-document recall, context precision/recall, and citation precision/recall. Confirm exact expected values and the documented denominator in each result's details.
- Create two distinct acceptable evidence sets and retrieve only one complete set. Completeness/recall must be complete; the other valid alternative must not be treated as missing evidence. Retrieve only part of every set and confirm a fractional partial score, not a binary total miss.
- Compare retrieval, reranked, and selected-context chunk sets. Confirm required evidence retained, redundancy, source diversity, and token count use the persisted rank/context rows and preserve stage-specific loss.
- Evaluate an unlinked run. Human-grounded retrieval/context/generation fields must be `null`/Missing, never zero. Operational metrics may still be present from QueryRun/TraceSpan data.
- Confirm human correctness/completeness/claim labels and automated verifier/judge values use distinct `evaluation_method`, version, and input hashes. Recomputing a new metric version must append rather than overwrite the earlier result.

## Generation, answerability, and citation verification

- Exercise human `answerable`, `partially_answerable`, and `unanswerable` labels against each generated answerability decision. Verify correct/incorrect abstention, unsupported-answer, partial-answer, and false-premise applicability denominators.
- Mark an infrastructure/provider failure and confirm answer-quality/citation values are missing and excluded from denominators; the failed run is not counted as an abstention.
- Verify citation levels independently: ID existence/resolution, deterministic lexical relevance/support, per-claim collective support, and human override. Human correction must preserve the automatic label, score, method, and verifier version.
- Exercise valid support, no citation, invalid ID, irrelevant evidence, partial support, contradiction, and multiple citations that collectively support a claim. Inspect the exact cited passage and source provenance.
- If an opt-in model judge is configured later, confirm provider/model/prompt/judge versions and structured details are stored as secondary `model_judge` metrics. Primary conclusions must remain available without that judge.

## Failure attribution boundaries

- Required evidence absent from retrieved top-k must attribute retrieval; retrieved then absent after enabled reranking must attribute reranking; reranked then omitted from final context must attribute context.
- When complete required evidence is in context but the answer is wrong, incomplete, unsupported, contradicted, or fails answerability behavior, attribute generation. Appropriate evidence with missing/incorrect citation must attribute citation.
- Exercise explicit parsing signals and each infrastructure code. Infrastructure must be primary and separate from answer quality; low quality must never be relabeled as infrastructure.
- Confirm one primary and multiple secondary labels retain taxonomy/rules versions, observable evidence, and input hash. A human correction must coexist with—not delete—the automatic attribution.

## Adaptive routing and reproducibility

- Freeze a router configuration, then create a frozen adaptive pipeline that references it. Repeat identical query/classification/filter/capability inputs and confirm byte-equivalent mode, rewrite/rerank flags, candidate count, context budget, reason code, and version/hash.
- Exercise deterministic no-retrieval, lexical, dense, hybrid, and hybrid-rerank selections. Confirm QueryRun and routing span persist the chosen runtime route separately from the immutable pipeline/router snapshots.
- Remove dense-index or reranker availability and request a route that needs it. Confirm `ROUTE_UNAVAILABLE` and no silent fallback. Also test allowed-route, candidate-count, and context-budget bounds.
- Confirm adaptive execution invokes the same retrieval, fusion, reranking, context, generation, claim, citation, trace, and evaluation services used by fixed pipelines; there must be no second RAG implementation.
- Compare one adaptive observation with explicit fixed observations using a versioned best-observed criterion. Check calls/tokens/cost/latency reductions and quality loss; missing pricing or quality must remain missing.

## Query Laboratory, API, and UI checks

- Trigger/read `/query-runs/{id}/evaluation`; inspect scope, value/Missing, method, version, details, input snapshot/hash, evidence survival, citation metrics, operational values, and linked benchmark ID.
- In Query Laboratory confirm the classifier and adaptive reason/version, automatic-vs-human status, benchmark-ground-truth notice, versioned metric table, primary/secondary failure evidence, and preserved human correction.
- Check successful, unanswerable, partially answerable, failed-provider, no-benchmark, no-retrieval, and nullable-cost runs. UI must display unavailable rather than zero and remain inspectable after partial failure.
- Verify router configuration create/list/read/freeze endpoints and frozen immutability. Existing fixed pipeline API payloads and screens must remain compatible.

## Security, assumptions, and limitations

- Repeat all prior secret/prompt-injection checks. Metric inputs, router snapshots/reasons, judge/verifier details, failure evidence, API payloads, and UI must not expose Gemini keys, headers, credentials, or unredacted provider failures.
- The default citation verifier is deterministic lexical overlap. It is versioned and useful for development but is not a full semantic entailment model. Human review remains primary.
- Automated semantic answer similarity is deterministic token cosine. The structured judge boundary and fake judge exist, but no new paid provider or mandatory real judge call is introduced.
- Router decisions are rule-based and reproducible. “Best observed” is an explicit per-run analysis concept, not an oracle production route or a claim of universal pipeline superiority.
- Batch experiments, aggregate dashboards, final benchmark construction, and broad real-provider QA remain Chunk 6 or manual work. Live PostgreSQL/pgvector and real Gemini smoke tests remain unverified in this pass.

## Chunk 5 regression checklist

- Metric definitions match hand calculations; missing labels remain null and human/automatic/model-judge methods remain distinct.
- Alternative evidence sets score correctly and evidence survival maps loss to the earliest responsible observable stage.
- Context-present-but-wrong-answer is a generation failure; citation faults remain citation failures; infrastructure is excluded from quality denominators.
- Automatic citation/failure judgments survive every human override.
- Classifier, router, metrics, judge prompts, verifier, taxonomy, and attribution rules are versioned or snapshotted.
- Adaptive routes are deterministic, capability-safe, reasoned, persisted/traced, and executed through the existing pipeline.
- Fixed pipelines, Gemini generation, human benchmark annotations, observable traces, comparisons, dataset extraction/review, and all earlier migrations remain intact.

# GEMINI QA ROUND 1 — CHUNKS 1–5

## Environment Tested
- **OS**: Windows host (No Docker installed)
- **Database**: SQLite (in-memory/local for tests)
- **PostgreSQL/pgvector**: BLOCKED (Docker unavailable in the environment)
- **Frontend**: Node.js, Next.js (Turbopack)
- **Backend**: Python 3.13, FastAPI, SQLAlchemy, pytest, mypy, ruff

## Commands Run
- `npm install && npm run build` (Frontend build)
- `npm run lint` (Frontend linting)
- `pytest backend/tests --basetemp=...` (Backend tests)
- `mypy backend` (Backend type checking)
- `ruff check backend` (Backend linting)
- `alembic upgrade head` (Blocked due to missing PostgreSQL DB container)

## Automated Test Summary
- **Backend Pytest**: 185 passed, 0 failed, 1 warning (deprecation). (PASS)
- **Backend Ruff**: 0 issues found. (PASS)
- **Backend Mypy**: Initially 33 errors in 12 files. Fixed by Gemini during QA. Currently 0 errors. (CONDITIONAL PASS -> PASS)
- **Frontend Build**: Compiled successfully, statically generated pages successfully. (PASS)
- **Frontend Lint**: 0 errors. (PASS)

## Manual Test Summary
- Manual API/UI exploratory testing could not be completed because `docker compose` is missing on the QA host, blocking the launch of the PostgreSQL + pgvector `db` container which the backend requires. Therefore, the application could not be served end-to-end.
- Evaluated backend correctness purely through the exhaustive automated test suite written by Codex, and static analysis tools.

## Defects

### Defect 1: Extensive Mypy Type Inconsistencies
- **Severity**: Medium
- **Component**: Backend (`test_evaluation_generation_citation.py`, `test_evaluation_context_operational.py`, `test_reranking_context.py`, `test_document_service.py`, `test_gemini_provider.py`, `test_retrieval.py`, `test_jobs.py`, `test_comparisons.py`, `alembic/versions`)
- **Reproduction**: Run `mypy backend`
- **Expected Result**: 0 type errors.
- **Actual Result**: 33 type errors related to invariant sequences (`list` vs `Sequence`), missing return types, missing generic parameters on `sa.Column`, unmatched kwargs, and incomplete Mock objects (like `httpx.HTTPStatusError`).
- **Likely Root Cause**: Strict typing rules not strictly enforced during initial rapid iteration by Codex, specifically around covariance of sequences and `typing.Any`.
- **Recommended Fix**: Update type signatures to use `typing.Sequence[typing.Any]` where `list[object]` was used, explicitly pass keyword arguments instead of `**kwargs` unpacking in tests, supply missing dummy `request`/`response` arguments to `HTTPStatusError` mocks. *(Note: Fixed directly by Gemini during QA to unblock strict type verification)*.

## Blockers
- **Docker Compose Missing**: Cannot spin up PostgreSQL/pgvector database.
- **E2E Manual Testing**: Blocked by the above.

## Regressions
- No regressions detected in existing automated test suite (Chunk 1-5 tests are green).

## Security Findings
- No leaked API keys or secrets detected in the repository source code, frontend payloads, or environment examples.

## Real-Provider Tests
- **Not Performed**: Prevented by the lack of local environment launchability and restricted scope on paid API tests.

## Remaining Unverified Risks
- Real PostgreSQL/pgvector integration (SQLite does not perfectly mimic pgvector behavior or PostgreSQL full-text search `ts_rank_cd`).
- UI browser-level accessibility and interaction flows.
- Real Gemini provider network interactions and latency.

---

## Final QA Report

### Overall Verdict
**CONDITIONAL PASS — proceed after listed fixes**

### Test Summary
- Automated tests run/passed/failed: 185 / 185 / 0
- Static analysis (mypy): 33 issues found and resolved. 0 remaining.
- Manual flows tested: 0 (Blocked)
- Real-provider tests: 0
- Security tests: Static code scan passed.

### Research-Validity Risks
- **Vector Search Fidelity**: Since all automated tests run against SQLite using a fake/portable embedding representation, the actual `pgvector` recall and precision behavior remains entirely unverified. If the schema mappings or cosine similarities are slightly off in Postgres, adaptive routing and benchmarking will give incorrect conclusions.
- **Postgres FTS (`ts_rank_cd`) Fidelity**: Similar to the above, SQLite full-text search behaves differently.
- **Database Migrations**: Alembic migrations have not been run against a real Postgres database to verify index creation and constraint enforcement.

### Remaining QA Gaps
- End-to-end integration with PostgreSQL/pgvector.
- Browser-native UI testing and human-in-the-loop workflow verification.
- Real API provider limits, timeouts, and structure parsing for native Gemini models.

### Codex Fix List
1. **[RESOLVED BY QA]** Merge the mypy strict typing fixes implemented during QA (Sequence vs List covariance, HTTPStatusError kwargs, Generator return types).
2. **[MUST DO]** Verify Alembic migrations and application boot manually against a real Postgres instance before starting Chunk 6.
3. **[MUST DO]** Add automated CI integration tests that run against a real `postgres` docker service to ensure `pgvector` and `ts_rank_cd` behavior is locked in, preventing SQLite-only false confidence.

# Chunk 6 — Experiment System and Results Dashboard

## Implemented experiment workflow

- Create, inspect, estimate, freeze, start, and resume experiments through the typed experiment API and Experiment Manager.
- Freeze validates a ready/frozen corpus, frozen benchmark, reviewed questions, frozen pipelines/router, prompt snapshots, provider availability, and required indexes.
- The dependency snapshot records actual corpus, benchmark, question, pipeline, prompt, model, index, router, metric, citation-verifier, taxonomy, attribution-rule, pricing, repetition, deterministic-seed, and code-commit data without secrets.
- The run matrix has a deterministic SHA-256 idempotency key and UUID for every question × pipeline × repetition cell. Attempts are retained separately, and QueryRuns link to the exact cell/attempt before provider work starts.
- Resume preserves successful cells, reconciles terminal QueryRuns after interrupted linking, and retries only configured infrastructure failure codes. Invalid answers and other research outcomes are not retries.
- Completed experiment raw QueryRuns, retrieval/context, traces, artifacts, claims/citations, evaluations, and attributions have an ORM immutability backstop.
- Results aggregation uses exact metric identity, explicit denominator policies, null-preserving missingness, infrastructure exclusion accounting, evidence survival, deterministic CSV, and versioned JSON.
- The Results Dashboard provides the eight required views with sample sizes, filters, missing-value labels, and Query Laboratory drill-down.

## High-priority Chunk 6 QA

- Interrupt an experiment between QueryRun completion and attempt linking; resume must reconcile the existing `(experiment_run_id, experiment_attempt_number)` and create zero duplicate valid runs.
- Try to mutate every research-affecting field after freeze. Expect `EXPERIMENT_VERSION_CONFLICT`; lifecycle/progress/cost fields may change only through the service.
- Try to add/change/delete raw results after completion. Expect `EXPERIMENT_RAW_RESULTS_IMMUTABLE`; derived exports may be regenerated.
- Recalculate dashboard aggregates from the exported run-level CSV/JSON. Numerators, denominators, missing counts, infrastructure exclusions, medians, and contributing run IDs must match.
- Verify filters visibly change the sample population and never turn missing metrics or unknown cost into zero.
- Verify a failed infrastructure attempt remains in attempt history while wrong answers, unsupported claims, and abstention errors are final research outcomes rather than retry triggers.
- Confirm required evidence survival respects alternative acceptable evidence sets at retrieval, reranking, and context.
- Search exports for configured secret values and sensitive keys. Values must be redacted and arbitrary artifact filesystem paths must be absent.
- Verify all eight dashboard figures, keyboard navigation, accessible labels, small-sample cautions, and trace drill-down.

## Chunk 6 known limitations and unverified areas

- The current Job abstraction is persisted and queue-compatible but executes inline; no external worker/lease system exists.
- PostgreSQL/pgvector execution is still unverified on this host because Docker and `psql` are unavailable. A dedicated `postgres-integration.yml` workflow and marked real-PostgreSQL test were added but were not executed locally.
- Do not authorize a paid experiment from an incomplete cost estimate. Missing pricing intentionally leaves total cost unavailable and does not compare unknown values with the budget guard.

# Chunk 7 — Final Research Audit and Reproducibility

## Final preflight verdict

**BLOCKED — do not run the paid pilot or main research experiment yet.**

- Ten scientific PDFs are present only in the Git-ignored representative fixture directory. Their ingestion, parsing warnings, license/source metadata, indexes, READY/frozen corpus state, and content hash cannot be certified without the project PostgreSQL database.
- No accessible frozen benchmark with at least 50 human-reviewed questions can be certified. Human ground truth must not be generated or inferred by Codex.
- P0–P5 frozen conditions are not present in a reviewable export/database on this host.
- Gemini generation and embedding adapters exist and share `RAGSCOPE_GEMINI_API_KEY`, but no paid smoke call was authorized or performed. The optional local CrossEncoder requires a pinned cached model revision and the reranker dependency.
- Real generation/embedding pricing and `RAGSCOPE_EXPERIMENT_COST_LIMIT` are unset, so a complete paid estimate is unavailable.
- The current working tree is not represented by the recorded Git HEAD. A clean commit is required before its hash can identify an experiment faithfully.
- The latest independent Gemini result covers Chunks 1–5 only and remains conditional on real PostgreSQL, browser, migration, and provider checks.

## Reproducibility assets to verify

- From a clean checkout, follow `docs/REPRODUCIBILITY.md` to migrate, load fixtures, execute a deterministic fake-provider experiment, export results, and regenerate all eight SVG/JSON figures.
- Confirm the figure manifest hashes the exact versioned analysis export and selector configuration; no manual numeric edits are permitted.
- Confirm incomplete exports generate explicit placeholder figures instead of invented values.
- Confirm methodology, threats-to-validity, report-input, research-report, and demo documents contain no unsupported result claims or unresolved identifiers presented as real data.

## Highest-priority final Gemini QA

- Record the final corpus ID/hash, parser/chunker/embedding/index snapshots, source/license metadata, and all parsing warnings.
- Audit the frozen benchmark count and category distribution. Every answerable question needs a reviewed acceptable evidence set; every unanswerable question needs a reviewed explanation; leakage warnings need human disposition.
- Compare P0–P5 snapshots field by field and confirm controlled generator, prompt, temperature, output limit, context budget, evaluation versions, and any intended differences.
- Run the ≥5-question × ≥3-pipeline pilot with the same real providers intended for the main study. Inspect traces, rank preservation, evidence alignment, citations, metrics, cost, latency, and resume behavior.
- Stop on any research-invalidating pilot defect, add a regression test, and rerun affected pilot cases before freezing the main experiment.
- Reproduce every aggregate and figure from immutable exports, including denominators, exclusions, repetitions, retries, route distributions, and evidence survival.
- Confirm case studies include both successful and harmful mechanisms and link to exact QueryRun/trace identifiers.
- Run the real PostgreSQL workflow/CI, frontend critical flows, one explicitly authorized Gemini smoke test, secret scan, and documentation accuracy audit.

## Chunk 7 items Codex could not independently verify

- Final corpus selection and licenses.
- Human review of at least 50 benchmark questions/evidence annotations.
- Frozen P0–P5 research configurations.
- Paid-provider behavior, current provider pricing, and experiment-budget authorization.
- Pilot or main experiment results, exclusions, aggregate findings, ablations, qualitative cases, or demonstration recording.
- Final independent Gemini QA after the repository is committed and the real PostgreSQL environment is available.

# Completion follow-up — PostgreSQL worker, fixtures, and browser coverage

This section supersedes older statements above that Docker/PostgreSQL, an external
worker, or browser automation were unavailable. Those statements remain as the
historical Gemini QA record.

## Implemented closure work

- Long ingestion, indexing, dataset extraction, evaluation, experiment execution,
  export, analysis, and figure operations use typed PostgreSQL jobs. Workers claim
  with `FOR UPDATE SKIP LOCKED`, retain attempts, heartbeat leases, reclaim expiry,
  and honor cooperative cancellation/pause between safe units of work.
- Default long-operation responses are `202` receipts. Explicit waits are bounded
  to 1–60 seconds and production requests poll the worker instead of executing an
  unbounded handler in the API process.
- Artifact roots are absolute/configured; artifacts can link to experiments and
  carry hashes, producer/configuration versions, media types, and safe identifiers.
  Trace-export backfill and configured-secret scanning are explicit maintenance
  commands.
- Experiment analysis now computes headlines and all figure datasets over the full
  filtered population, independently of paginated contributing-run rows. Every
  applicable headline exposes denominator, missing, and excluded counts.
- The shared source inspector resolves page/element/chunk URL focus and is reused by
  trace, dataset, and benchmark evidence workflows.
- The deterministic PostgreSQL fixture creates frozen P0–P5 conditions, five
  reviewed questions, a 30-cell completed experiment, exports, and eight figures;
  repeated execution reuses the same experiment identity.
- Playwright covers source focus, pipeline freeze/query/trace/comparison, dataset
  review, and full-population result drill-down. CI includes fresh PostgreSQL
  migrations, pgvector/FTS tests, worker concurrency, fixture reproduction,
  frontend checks, browser flows, and secret scanning.

## Highest-priority independent follow-up

- Re-run the complete CI workflow from a clean committed checkout and confirm zero
  non-opt-in skips, including worker crash/lease recovery and duplicate-free resume.
- Compare every persisted dashboard value with the run-level export under multiple
  answerability, fixed/adaptive, failure-stage/category/code, status, question-type,
  difficulty, pipeline, and infrastructure-inclusion filters.
- Interrupt a worker during a real pilot matrix, wait for lease expiry, resume, and
  confirm completed valid QueryRuns and raw artifacts are unchanged.
- Resolve several citations/evidence selections from UI URL parameters to the exact
  PDF page, element/chunk, and displayed passage.
- Scan tracked files plus generated exports/artifacts for configured secret values;
  the scanner must report identifiers only and must never print the secret.

## Research gates still intentionally open

- The candidate PDFs are not a final human-approved corpus and their source/license
  metadata and parser warnings still require review.
- The five-question fixture is not the required 75-question human benchmark.
- No paid Gemini smoke, 45-run pilot, or 450-run main matrix has been authorized or
  executed. Real current pricing, a cost limit, a clean commit, pinned local
  CrossEncoder revision (if P4 uses it), and explicit budget approval are required.
- Consequently no main-study rankings, hypothesis outcomes, qualitative cases, or
  demonstration claims may be reported yet.

## Completion verification — 2026-08-28

- Docker Compose now builds the CPU-only API/worker image from a clean dependency
  layer and runs healthy PostgreSQL/pgvector, API, worker, and production frontend
  services. Alembic reports `b7d2f8a4c901 (head)` and no model/migration drift.
- The PostgreSQL-backed suite passes 234 tests with no non-opt-in skips; this includes
  pgvector similarity, PostgreSQL FTS, `SKIP LOCKED` worker concurrency, leases,
  resume/idempotency, metrics, immutable artifacts, filters, exports, and figures.
- The production frontend passes lint, TypeScript, build, and seven Playwright flows:
  source focus/frozen evidence, pipeline/query/trace/comparison, dataset review,
  full-population dashboard drill-down, experiment human-review navigation, full
  corpus upload/parse/chunk/index/freeze, and reviewed unanswerable benchmark freeze.
- Browser authoring exposed and regression-tested an async React form-lifecycle bug.
  Corpus, benchmark, dataset, pipeline, and router forms now retain their form node
  before awaiting the API, so successful mutations refresh without a null reset.
- Human review queue labels are now contract-complete: correctness, completeness,
  appropriate abstention, false-premise recognition, claim support, and citation
  precision can all be stored as distinct `human-review.v1` observations.
- Completed experiments continue to reject raw QueryRun/retrieval/context/trace/
  claim/citation mutation. They permit append-only versioned derived evaluation and
  narrowly scoped human override fields so the required post-run review is possible.
- Repeated metric computation preserves every input-hashed row. Analysis selects the
  newest stored input deterministically per metric identity, preventing duplicate
  history from corrupting dashboard denominators.
- The deterministic fixture reruns successfully, regenerates all eight JSON/SVG
  figures plus run/aggregate/raw/adaptive exports, and the configured-value secret
  scanner reports `secret_findings=0`.

### Independent checks still recommended

- From a clean commit, repeat CI and independently reproduce selected dashboard
  denominators from the exported run-level CSV/JSON.
- During the authorized real-provider pilot, interrupt one worker, allow its lease to
  expire, resume, and confirm zero duplicate valid QueryRuns and preserved attempts.
- Review exact PDF passage focus for multiple real documents and run one explicitly
  authorized Gemini smoke request through P0–P5 before the paid 45-run pilot.

# Frontend redesign v2 — independent QA checklist

The Figma redesign uses one floating top navigation across Overview, Corpus Studio,
Query Laboratory, Pipeline Comparison, Dataset Intelligence, Benchmark Authoring,
Experiment Manager, and Pipeline Builder. The redesign intentionally reuses the
existing backend contracts; it does not introduce a parallel data or mock layer.

## Visual and navigation checks

- Compare all eight landing screens against Figma file
  `tZwxwNHJAHdnxMpEOCq0Ji`, frames `2:9`, `2:95`, `2:177`, `2:282`, `2:370`,
  `2:467`, `2:554`, and `2:648` at 1440px and responsive widths.
- Confirm the cream canvas, dark-green floating navigation, serif display hierarchy,
  card borders/radii, compact research forms, and evidence-oriented visual hierarchy
  remain consistent. The old left/sidebar or duplicated page header must never return.
- Keyboard-test all eight navigation links, visible focus, horizontal navigation at
  narrow widths, skip-to-content, form labels, status text, tables, and error states.
- Confirm `/` is the live Overview and `/corpora` is Corpus Studio; corpus, version,
  and document detail routes remain reachable from the new navigation and cards.

## Live integration checks

- Overview counts must come from stored corpora, indexes, frozen pipelines,
  experiments, benchmarks, and full-population analysis. Missing evidence/pricing
  values must display unavailable, never a fabricated zero.
- Corpus Studio must create a real corpus and preserve version creation, upload,
  parse, fixed/structure-aware chunking, indexing, freeze, and Document Inspector.
- Query Laboratory must execute a real frozen pipeline and retain stage order,
  rank history, context exclusions, exact-context artifact, generation metadata,
  evaluation, failure attribution, human labels, and citation/source drill-down.
- Pipeline Comparison must enforce one corpus/question and 2–4 frozen pipelines;
  stored comparison URLs, configuration differences, evidence overlap, failed
  columns, rank movement, and QueryRun links must remain functional.
- Dataset Intelligence must use live extraction/catalog/filter/comparison endpoints;
  verify original model output, field evidence, not-stated state, corrections, human
  approval rules, history, export, and exact source focus.
- Benchmark Authoring must preserve draft editing, stable evidence selection,
  alternative evidence sets, leakage warnings, unanswerable explanations, human
  annotation state, freeze immutability, and read-only frozen detail screens.
- Experiment Manager must preserve dependency selection, cost estimate, freeze,
  start/pause/resume, progress, review queue, immutable export generation, dashboard
  filters, all eight full-population figures, denominators, and trace drill-down.
- Pipeline Builder must preserve fixed/adaptive pipeline and router creation/freeze,
  complete retrieval/fusion/reranker/context/generation settings, safe provider
  capability reporting, and the persisted smoke-query path. Credentials must never
  appear in browser payloads or snapshots.

## States, regression, and security

- Exercise loading, empty, validation, success, partial-failure, infrastructure
  failure, and backend-unavailable states on every landing page. No screen should
  crash because a list is empty or an optional metric/cost is null.
- Verify PDF/source links retain `page`, `element`, and `chunk` focus and highlight
  the exact provenance-backed passage from datasets, benchmarks, claims, context,
  retrieval, and comparison views.
- Confirm charts and overview summaries do not derive research claims from paginated
  UI rows. Sample sizes and denominator changes must stay explicit.
- Inspect browser requests, rendered DOM, client errors, traces, and downloaded
  exports for API keys, authorization headers, cookies, database URLs, provider
  errors, prompts, or unsafe artifact paths.
- Repeat production Docker Compose health, frontend lint/type/build, the Playwright
  critical flows, API health, and selected backend regression tests after the final
  independent visual review.
