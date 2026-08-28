# RAGScope demo workflow

This runbook produces a short, auditable demonstration of RAGScope as a research
instrument. It is not a substitute for the frozen main experiment, and fixture or
fake-provider output must never be narrated as an empirical research result.

## Demo status

> **RECORDED DEMO NOT YET ATTACHED.** Add the final media path or URL, recording
> date, code commit, corpus/benchmark/pipeline IDs, and showcased QueryRun IDs
> only after recording. Do not fill numeric result placeholders from synthetic
> metric fixtures.

## Prerequisites

- The API is healthy at `http://localhost:8000/health` and the UI is available at
  `http://localhost:3000`.
- At least one ready CorpusVersion contains parsed scientific documents and built
  lexical/dense indexes for the routes being shown.
- The fixed pipeline configurations used for comparison are frozen.
- For human-grounded metrics, the selected question belongs to a frozen,
  human-reviewed BenchmarkVersion with stable acceptable evidence sets.
- The demonstration mode is disclosed: deterministic fake providers for an
  engineering demo, or Gemini with exact provider/model settings for a real-call
  demo. Never present one as the other.
- If aggregate results are shown, the experiment is frozen and complete and the
  result/export endpoints have been verified in the running API schema.

Record these identifiers before starting:

| Item | Demo value |
| --- | --- |
| Code commit | `[NOT RECORDED]` |
| CorpusVersion ID/hash | `[NOT RECORDED]` |
| BenchmarkVersion ID/hash | `[NOT RECORDED]` |
| Dense pipeline ID/hash | `[NOT RECORDED]` |
| Hybrid pipeline ID/hash | `[NOT RECORDED]` |
| Experiment ID/hash, if shown | `[NOT RECORDED]` |
| QueryRun IDs | `[NOT RECORDED]` |
| Provider/model | `[NOT RECORDED]` |

## Recommended 8-minute sequence

### 1. Frame the black-box problem — 30 seconds

Open `http://localhost:3000`.

Narration:

> A final RAG answer does not reveal whether a failure came from parsing,
> retrieval, reranking, context budgeting, generation, or citation. RAGScope
> preserves the observable evidence path so those stages can be inspected and
> measured without claiming access to hidden model reasoning.

### 2. Inspect one scientific paper — 60 seconds

Open a corpus, select a version, then open a document at
`/documents/{document_id}`.

Show:

- the preserved PDF artifact and content hash;
- parsed elements with page and structural provenance;
- one table or section where available;
- parser warnings rather than hiding uncertainty.

If the document has not been parsed or indexed, stop and complete the preparation
steps in [REPRODUCIBILITY.md](REPRODUCIBILITY.md); do not improvise around missing
state during the recording.

### 3. Run one benchmark-linked question — 60 seconds

Open `/laboratory`, select the ready corpus version and a frozen pipeline, enter
the exact benchmark question, and submit it. Open `/laboratory/{query_run_id}`.

Show:

- original query, classification, optional rewrite, and configured/adaptive route;
- the ordered observable stage timeline and latency;
- answerability, answer, claims, and citations.

Say explicitly whether the generation call is deterministic/fake or Gemini-backed.

### 4. Compare dense and hybrid retrieval — 90 seconds

Open `/comparisons`. Use the same original question and CorpusVersion, select the
frozen dense and hybrid configurations, and run the comparison.

Show:

- interpreted configuration differences instead of only raw JSON;
- lexical, dense, fused, and reranked ranks in separate columns;
- evidence overlap and context inclusion differences;
- links from both columns to their complete Query Laboratory runs.

Do not call either pipeline a winner based on this single question.

### 5. Follow required evidence through the pipeline — 90 seconds

Return to one `/laboratory/{query_run_id}` view. Starting with an annotated required
passage, follow it through retrieval, fusion/reranking, and context selection.

Show one of these truthful cases:

- evidence retrieved and retained;
- evidence retrieved but demoted by reranking;
- evidence survived reranking but was excluded by deduplication/token budget;
- evidence was absent from top-k.

Open the exact generator-context artifact and the source passage in Document
Inspector. Confirm that the context displayed in the UI equals the stored artifact.

### 6. Explain a citation or failure attribution — 60 seconds

Show a stored, real example only. Suitable examples include a missing citation,
invalid citation ID, partial support, unsupported claim, or a successful citation
that resolves to the exact document/page/chunk.

Show:

- automatic metric/verifier output and its version;
- human label/override separately, if one exists;
- primary and secondary failure labels with observable evidence;
- the boundary between an answer-quality outcome and infrastructure failure.

Do not manufacture an error solely for the recording unless it is clearly labelled
as a deterministic engineering fixture.

### 7. Show aggregate experiment results — 90 seconds

Only include this step when a completed frozen experiment exists. Open
`/experiments/{experiment_id}`, then `/results/{experiment_id}`.

Show active filters, sample sizes, denominators, missing values, and infrastructure
failure counts before interpreting any chart. Demonstrate a contributing-run link
back to Query Laboratory. If downloads are enabled, verify both tidy CSV and
versioned JSON exports and preserve them together.

If no completed experiment exists, display this message instead:

> Aggregate main-experiment results are not available yet. The experiment manager,
> deterministic aggregation code, and report placeholders are present, but no
> scientific conclusion is claimed.

### 8. Close with one bounded finding and limitation — 30 seconds

For an engineering-only demo, use no empirical finding:

> The demonstrated contribution is reconstructable evidence flow and versioned
> measurement. Quality and cost conclusions require the completed frozen study.

For a completed study, read one export-backed result with its denominator and one
limitation from [THREATS_TO_VALIDITY.md](THREATS_TO_VALIDITY.md). Avoid universal
pipeline-winner language.

## Recording and privacy checklist

- [ ] Browser contains no `.env`, API key, authorization header, cookie, or secret.
- [ ] Trace/export redaction checks pass.
- [ ] Uploaded paper content is permitted to appear in the recording.
- [ ] Provider/model and fake-versus-real mode are disclosed.
- [ ] Every visible result comes from the recorded frozen IDs.
- [ ] Filters, n, denominator, missingness, and infrastructure exclusions are shown.
- [ ] Citations drill down to the exact stored source passage.
- [ ] No UI element is described as chain-of-thought or hidden reasoning.
- [ ] The video does not claim fixture values as scientific results.
- [ ] Final media location and SHA-256 hash are recorded below.

## Recording record

| Field | Value |
| --- | --- |
| Video path/URL | `NOT AVAILABLE` |
| Recording date/time zone | `NOT AVAILABLE` |
| Duration | `NOT AVAILABLE` |
| SHA-256 | `NOT AVAILABLE` |
| Code commit | `NOT AVAILABLE` |
| Frozen input IDs/hashes | `NOT AVAILABLE` |
| QueryRun IDs shown | `NOT AVAILABLE` |
| Experiment/export IDs shown | `NOT AVAILABLE` |

