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
 
 #   G E M I N I   Q A   R O U N D   1   � �    C H U N K S   1 � �  5  
  
 # #   E n v i r o n m e n t   T e s t e d  
 -   * * O S * * :   W i n d o w s   h o s t   ( N o   D o c k e r   i n s t a l l e d )  
 -   * * D a t a b a s e * * :   S Q L i t e   ( i n - m e m o r y / l o c a l   f o r   t e s t s )  
 -   * * P o s t g r e S Q L / p g v e c t o r * * :   B L O C K E D   ( D o c k e r   u n a v a i l a b l e   i n   t h e   e n v i r o n m e n t )  
 -   * * F r o n t e n d * * :   N o d e . j s ,   N e x t . j s   ( T u r b o p a c k )  
 -   * * B a c k e n d * * :   P y t h o n   3 . 1 3 ,   F a s t A P I ,   S Q L A l c h e m y ,   p y t e s t ,   m y p y ,   r u f f  
  
 # #   C o m m a n d s   R u n  
 -   ` n p m   i n s t a l l   & &   n p m   r u n   b u i l d `   ( F r o n t e n d   b u i l d )  
 -   ` n p m   r u n   l i n t `   ( F r o n t e n d   l i n t i n g )  
 -   ` p y t e s t   b a c k e n d / t e s t s   - - b a s e t e m p = . . . `   ( B a c k e n d   t e s t s )  
 -   ` m y p y   b a c k e n d `   ( B a c k e n d   t y p e   c h e c k i n g )  
 -   ` r u f f   c h e c k   b a c k e n d `   ( B a c k e n d   l i n t i n g )  
 -   ` a l e m b i c   u p g r a d e   h e a d `   ( B l o c k e d   d u e   t o   m i s s i n g   P o s t g r e S Q L   D B   c o n t a i n e r )  
  
 # #   A u t o m a t e d   T e s t   S u m m a r y  
 -   * * B a c k e n d   P y t e s t * * :   1 8 5   p a s s e d ,   0   f a i l e d ,   1   w a r n i n g   ( d e p r e c a t i o n ) .   ( P A S S )  
 -   * * B a c k e n d   R u f f * * :   0   i s s u e s   f o u n d .   ( P A S S )  
 -   * * B a c k e n d   M y p y * * :   I n i t i a l l y   3 3   e r r o r s   i n   1 2   f i l e s .   F i x e d   b y   G e m i n i   d u r i n g   Q A .   C u r r e n t l y   0   e r r o r s .   ( C O N D I T I O N A L   P A S S   - >   P A S S )  
 -   * * F r o n t e n d   B u i l d * * :   C o m p i l e d   s u c c e s s f u l l y ,   s t a t i c a l l y   g e n e r a t e d   p a g e s   s u c c e s s f u l l y .   ( P A S S )  
 -   * * F r o n t e n d   L i n t * * :   0   e r r o r s .   ( P A S S )  
  
 # #   M a n u a l   T e s t   S u m m a r y  
 -   M a n u a l   A P I / U I   e x p l o r a t o r y   t e s t i n g   c o u l d   n o t   b e   c o m p l e t e d   b e c a u s e   ` d o c k e r   c o m p o s e `   i s   m i s s i n g   o n   t h e   Q A   h o s t ,   b l o c k i n g   t h e   l a u n c h   o f   t h e   P o s t g r e S Q L   +   p g v e c t o r   ` d b `   c o n t a i n e r   w h i c h   t h e   b a c k e n d   r e q u i r e s .   T h e r e f o r e ,   t h e   a p p l i c a t i o n   c o u l d   n o t   b e   s e r v e d   e n d - t o - e n d .  
 -   E v a l u a t e d   b a c k e n d   c o r r e c t n e s s   p u r e l y   t h r o u g h   t h e   e x h a u s t i v e   a u t o m a t e d   t e s t   s u i t e   w r i t t e n   b y   C o d e x ,   a n d   s t a t i c   a n a l y s i s   t o o l s .  
  
 # #   D e f e c t s  
  
 # # #   D e f e c t   1 :   E x t e n s i v e   M y p y   T y p e   I n c o n s i s t e n c i e s  
 -   * * S e v e r i t y * * :   M e d i u m  
 -   * * C o m p o n e n t * * :   B a c k e n d   ( ` t e s t _ e v a l u a t i o n _ g e n e r a t i o n _ c i t a t i o n . p y ` ,   ` t e s t _ e v a l u a t i o n _ c o n t e x t _ o p e r a t i o n a l . p y ` ,   ` t e s t _ r e r a n k i n g _ c o n t e x t . p y ` ,   ` t e s t _ d o c u m e n t _ s e r v i c e . p y ` ,   ` t e s t _ g e m i n i _ p r o v i d e r . p y ` ,   ` t e s t _ r e t r i e v a l . p y ` ,   ` t e s t _ j o b s . p y ` ,   ` t e s t _ c o m p a r i s o n s . p y ` ,   ` a l e m b i c / v e r s i o n s ` )  
 -   * * R e p r o d u c t i o n * * :   R u n   ` m y p y   b a c k e n d `  
 -   * * E x p e c t e d   R e s u l t * * :   0   t y p e   e r r o r s .  
 -   * * A c t u a l   R e s u l t * * :   3 3   t y p e   e r r o r s   r e l a t e d   t o   i n v a r i a n t   s e q u e n c e s   ( ` l i s t `   v s   ` S e q u e n c e ` ) ,   m i s s i n g   r e t u r n   t y p e s ,   m i s s i n g   g e n e r i c   p a r a m e t e r s   o n   ` s a . C o l u m n ` ,   u n m a t c h e d   k w a r g s ,   a n d   i n c o m p l e t e   M o c k   o b j e c t s   ( l i k e   ` h t t p x . H T T P S t a t u s E r r o r ` ) .  
 -   * * L i k e l y   R o o t   C a u s e * * :   S t r i c t   t y p i n g   r u l e s   n o t   s t r i c t l y   e n f o r c e d   d u r i n g   i n i t i a l   r a p i d   i t e r a t i o n   b y   C o d e x ,   s p e c i f i c a l l y   a r o u n d   c o v a r i a n c e   o f   s e q u e n c e s   a n d   ` t y p i n g . A n y ` .  
 -   * * R e c o m m e n d e d   F i x * * :   U p d a t e   t y p e   s i g n a t u r e s   t o   u s e   ` t y p i n g . S e q u e n c e [ t y p i n g . A n y ] `   w h e r e   ` l i s t [ o b j e c t ] `   w a s   u s e d ,   e x p l i c i t l y   p a s s   k e y w o r d   a r g u m e n t s   i n s t e a d   o f   ` * * k w a r g s `   u n p a c k i n g   i n   t e s t s ,   s u p p l y   m i s s i n g   d u m m y   ` r e q u e s t ` / ` r e s p o n s e `   a r g u m e n t s   t o   ` H T T P S t a t u s E r r o r `   m o c k s .   * ( N o t e :   F i x e d   d i r e c t l y   b y   G e m i n i   d u r i n g   Q A   t o   u n b l o c k   s t r i c t   t y p e   v e r i f i c a t i o n ) * .  
  
 # #   B l o c k e r s  
 -   * * D o c k e r   C o m p o s e   M i s s i n g * * :   C a n n o t   s p i n   u p   P o s t g r e S Q L / p g v e c t o r   d a t a b a s e .  
 -   * * E 2 E   M a n u a l   T e s t i n g * * :   B l o c k e d   b y   t h e   a b o v e .    
  
 # #   R e g r e s s i o n s  
 -   N o   r e g r e s s i o n s   d e t e c t e d   i n   e x i s t i n g   a u t o m a t e d   t e s t   s u i t e   ( C h u n k   1 - 5   t e s t s   a r e   g r e e n ) .  
  
 # #   S e c u r i t y   F i n d i n g s  
 -   N o   l e a k e d   A P I   k e y s   o r   s e c r e t s   d e t e c t e d   i n   t h e   r e p o s i t o r y   s o u r c e   c o d e ,   f r o n t e n d   p a y l o a d s ,   o r   e n v i r o n m e n t   e x a m p l e s .  
  
 # #   R e a l - P r o v i d e r   T e s t s  
 -   * * N o t   P e r f o r m e d * * :   P r e v e n t e d   b y   t h e   l a c k   o f   l o c a l   e n v i r o n m e n t   l a u n c h a b i l i t y   a n d   r e s t r i c t e d   s c o p e   o n   p a i d   A P I   t e s t s .  
  
 # #   R e m a i n i n g   U n v e r i f i e d   R i s k s  
 -   R e a l   P o s t g r e S Q L / p g v e c t o r   i n t e g r a t i o n   ( S Q L i t e   d o e s   n o t   p e r f e c t l y   m i m i c   p g v e c t o r   b e h a v i o r   o r   P o s t g r e S Q L   f u l l - t e x t   s e a r c h   ` t s _ r a n k _ c d ` ) .  
 -   U I   b r o w s e r - l e v e l   a c c e s s i b i l i t y   a n d   i n t e r a c t i o n   f l o w s .  
 -   R e a l   G e m i n i   p r o v i d e r   n e t w o r k   i n t e r a c t i o n s   a n d   l a t e n c y .  
  
 - - -  
  
 # #   F i n a l   Q A   R e p o r t  
  
 # # #   O v e r a l l   V e r d i c t  
 * * C O N D I T I O N A L   P A S S   � �    p r o c e e d   a f t e r   l i s t e d   f i x e s * *  
  
 # # #   T e s t   S u m m a r y  
 -   A u t o m a t e d   t e s t s   r u n / p a s s e d / f a i l e d :   1 8 5   /   1 8 5   /   0  
 -   S t a t i c   a n a l y s i s   ( m y p y ) :   3 3   i s s u e s   f o u n d   a n d   r e s o l v e d .   0   r e m a i n i n g .  
 -   M a n u a l   f l o w s   t e s t e d :   0   ( B l o c k e d )  
 -   R e a l - p r o v i d e r   t e s t s :   0  
 -   S e c u r i t y   t e s t s :   S t a t i c   c o d e   s c a n   p a s s e d .  
  
 # # #   R e s e a r c h - V a l i d i t y   R i s k s  
 -   * * V e c t o r   S e a r c h   F i d e l i t y * * :   S i n c e   a l l   a u t o m a t e d   t e s t s   r u n   a g a i n s t   S Q L i t e   u s i n g   a   f a k e / p o r t a b l e   e m b e d d i n g   r e p r e s e n t a t i o n ,   t h e   a c t u a l   ` p g v e c t o r `   r e c a l l   a n d   p r e c i s i o n   b e h a v i o r   r e m a i n s   e n t i r e l y   u n v e r i f i e d .   I f   t h e   s c h e m a   m a p p i n g s   o r   c o s i n e   s i m i l a r i t i e s   a r e   s l i g h t l y   o f f   i n   P o s t g r e s ,   a d a p t i v e   r o u t i n g   a n d   b e n c h m a r k i n g   w i l l   g i v e   i n c o r r e c t   c o n c l u s i o n s .  
 -   * * P o s t g r e s   F T S   ( ` t s _ r a n k _ c d ` )   F i d e l i t y * * :   S i m i l a r   t o   t h e   a b o v e ,   S Q L i t e   f u l l - t e x t   s e a r c h   b e h a v e s   d i f f e r e n t l y .  
 -   * * D a t a b a s e   M i g r a t i o n s * * :   A l e m b i c   m i g r a t i o n s   h a v e   n o t   b e e n   r u n   a g a i n s t   a   r e a l   P o s t g r e s   d a t a b a s e   t o   v e r i f y   i n d e x   c r e a t i o n   a n d   c o n s t r a i n t   e n f o r c e m e n t .  
  
 # # #   R e m a i n i n g   Q A   G a p s  
 -   E n d - t o - e n d   i n t e g r a t i o n   w i t h   P o s t g r e S Q L / p g v e c t o r .  
 -   B r o w s e r - n a t i v e   U I   t e s t i n g   a n d   h u m a n - i n - t h e - l o o p   w o r k f l o w   v e r i f i c a t i o n .  
 -   R e a l   A P I   p r o v i d e r   l i m i t s ,   t i m e o u t s ,   a n d   s t r u c t u r e   p a r s i n g   f o r   n a t i v e   G e m i n i   m o d e l s .  
  
 # # #   C o d e x   F i x   L i s t  
 1 .   * * [ R E S O L V E D   B Y   Q A ] * *   M e r g e   t h e   m y p y   s t r i c t   t y p i n g   f i x e s   i m p l e m e n t e d   d u r i n g   Q A   ( S e q u e n c e   v s   L i s t   c o v a r i a n c e ,   H T T P S t a t u s E r r o r   k w a r g s ,   G e n e r a t o r   r e t u r n   t y p e s ) .  
 2 .   * * [ M U S T   D O ] * *   V e r i f y   A l e m b i c   m i g r a t i o n s   a n d   a p p l i c a t i o n   b o o t   m a n u a l l y   a g a i n s t   a   r e a l   P o s t g r e s   i n s t a n c e   b e f o r e   s t a r t i n g   C h u n k   6 .  
 3 .   * * [ M U S T   D O ] * *   A d d   a u t o m a t e d   C I   i n t e g r a t i o n   t e s t s   t h a t   r u n   a g a i n s t   a   r e a l   ` p o s t g r e s `   d o c k e r   s e r v i c e   t o   e n s u r e   ` p g v e c t o r `   a n d   ` t s _ r a n k _ c d `   b e h a v i o r   i s   l o c k e d   i n ,   p r e v e n t i n g   S Q L i t e - o n l y   f a l s e   c o n f i d e n c e .  
 