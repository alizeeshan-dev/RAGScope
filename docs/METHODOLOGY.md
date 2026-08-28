# RAGScope research methodology

## Status of this methodology

This document defines how a RAGScope study should be run and reported. It does
not assert that the main corpus, 50–100-question benchmark, or main experiment
has been completed. Fields marked **REQUIRED BEFORE MAIN REPORT** must be filled
from frozen stored objects and exports, never from memory or illustrative data.

## Research questions

A study should declare its questions before freezing the experiment. Suitable
questions include:

1. How do lexical, dense, hybrid, and hybrid-reranked retrieval affect required-
   evidence discovery and survival?
2. Where do observable failures originate across parsing, retrieval, reranking,
   context construction, generation, and citation processing?
3. Does the deterministic adaptive router reduce retrieval/model calls, latency,
   tokens, or configured cost without an unacceptable loss in human-grounded quality?

**REQUIRED BEFORE MAIN REPORT:** exact research questions, directional hypotheses,
primary metrics, planned comparisons, and any multiple-comparison policy.

## Corpus selection

### Inclusion principles

- Scientific papers or dataset documentation must be legally usable for the study.
- Papers should actually describe datasets and include varied evidence structures:
  prose, tables, multiple datasets, incomplete metadata, limitations, licensing,
  and access information.
- The study must record language, domain, modality, publication period, source,
  and known selection bias.
- Corpus membership is identified by a frozen CorpusVersion content hash. A local
  PDF directory is not itself a research corpus.

The Git-ignored `benchmark/fixtures/representative/` directory is only a candidate
development set. Current filenames may be inspected locally, but documentation
must not claim ingestion or inclusion until the UI/API shows a frozen version.

**REQUIRED BEFORE MAIN REPORT:** corpus version ID/hash, inclusion/exclusion process,
document count, duplicate handling, parser version/configuration, parse warnings,
chunker snapshot, index identities, and corpus limitations.

## Benchmark creation

Human labels are primary ground truth. For each question:

1. select stable source documents;
2. author the question and assign type/difficulty;
3. record expected answerability (`answerable`, `partially_answerable`, or
   `unanswerable`);
4. write a reference answer or explicit answer criteria;
5. select every jointly required evidence passage;
6. place genuinely alternative sufficient evidence in distinct acceptable sets;
7. add an explanation for unanswerable questions;
8. review leakage warnings and annotation notes; and
9. mark reviewed before freezing the BenchmarkVersion.

Model suggestions may assist authoring but never become approved human labels
without an explicit review action. Frozen benchmark questions and evidence cannot
be edited in place.

The benchmark should cover direct fact lookup, dataset discovery/comparison,
multi-document synthesis, multi-hop, tables, broad summary, ambiguous,
unanswerable, false-premise, contradictory-source, and distractor-sensitive cases.

**REQUIRED BEFORE MAIN REPORT:** benchmark version ID, frozen timestamp, question
count/distribution, annotator/review procedure, disagreements, evidence-set rules,
leakage review, and answerability distribution.

## Experimental conditions

At minimum, compare frozen configurations for:

- no retrieval;
- lexical retrieval;
- dense retrieval;
- hybrid RRF;
- hybrid RRF plus reranking; and
- deterministic adaptive routing, when evaluating the router.

Each Experiment freezes exactly one corpus version, one benchmark version, a set
of frozen pipelines, repetitions, retry/stop policy, dependency snapshot, code
commit, and configuration hash. The run matrix is:

```text
benchmark questions × pipeline configurations × repetitions
```

Every cell produces an independent QueryRun. Resume may retry documented
infrastructure failures but must not duplicate completed valid cells.

## Controlled variables

Unless they are the planned independent variable, hold constant:

- corpus and benchmark versions;
- parser, chunker, lexical index, embedding provider/model/dimension, and filters;
- normalization/classifier/rewrite versions;
- retrieval candidate counts and RRF constant;
- reranker provider/model and final count;
- context token budget and deduplication threshold;
- prompt ID/version and structured-output schema;
- generation provider/model, temperature, output limit, and timeout;
- citation-verifier/judge versions;
- pricing snapshot/currency;
- code commit and environment; and
- repetition policy.

If a controlled setting changes, create a new frozen version/experiment rather
than editing a completed condition.

## Metrics

Metric identity is `(name, version, scope, evaluation method)`. Human,
deterministic, operational, and model-judge values must not be silently combined.

### Retrieval

- `Recall@k = max_S |top-k ∩ S| / |S|`, where each `S` is one acceptable evidence set.
- `Precision@k = relevant items in top-k / k`; relevance is the union of valid alternatives.
- Reciprocal rank is `1/r` for the first relevant top-k item, otherwise `0`.
- nDCG@k uses binary gain and `1/log2(rank+1)` discount, taking the best valid alternative.
- Evidence completeness reports no, partial, or one complete acceptable set.
- Required-document recall applies the same best-alternative rule at document level.

Alternative sets are OR alternatives; items within one set are jointly required.
Selecting one complete valid alternative earns full coverage without finding every
other alternative.

### Context

- precision uses selected chunks relevant to the union of acceptable evidence;
- recall/required-evidence-retained uses the best-covered acceptable set;
- redundancy is the share of selected chunks whose token-set Jaccard similarity
  reaches the versioned threshold against an earlier selected chunk;
- source diversity is unique selected documents / selected chunks; and
- token count is copied from the exact selected context sources.

### Generation and answerability

- Human correctness, completeness, partial-answer accuracy, unsupported claims,
  contradictions, and false-premise recognition remain primary labels.
- Correct abstention applies only to human-unanswerable questions.
- Incorrect abstention applies to answerable/partially-answerable questions.
- Unsupported-answer rate applies to human-unanswerable questions answered as factual.
- Deterministic token-cosine similarity and versioned structured model judges are
  secondary analyses, not substitutes for human correctness.

### Citations

- existence = resolved emitted references / emitted references;
- precision = relevant emitted references / references with relevance judgments;
- recall = mean per-claim best acceptable-set citation recall;
- claim support = factual claims with at least one supporting citation / judged
  or observably citation-free factual claims; and
- completeness = factual claims whose citations collectively support the complete claim.

The versioned deterministic verifier is a lexical baseline, not full natural-
language entailment. Human overrides preserve rather than delete its judgment.

### Operational and routing

Reuse QueryRun/Trace values for total/stage latency (milliseconds), input/output
tokens, configured cost, retrieval/reranking/model calls, and failure status.
Adaptive comparisons should report calls avoided, token/cost/latency change, and
quality loss relative to an explicitly named frozen baseline.

## Missingness and exclusion policy

- Missing human evidence or labels produce null/missing metrics, never zero.
- A measured miss with an applicable denominator is a valid zero.
- Unknown model pricing produces null cost, never zero.
- Provider/database/timeout/invalid-output failures remain visible as infrastructure
  failures and are excluded from answer-quality denominators only by an explicit
  persisted denominator policy.
- Poor answers are never removed as infrastructure failures.
- Every aggregate reports total runs, denominator count, missing count, excluded
  infrastructure count, and contributing/missing/excluded QueryRun IDs.
- Filters must be persisted/exported and must not silently change denominators.

## Cost accounting

Generation input/output and embedding prices are configured per million tokens.
An experiment computes a maximum expected cost from its frozen pipeline snapshots.
If any billable rate is unavailable, the total ceiling is unavailable/incomplete.
`RAGSCOPE_EXPERIMENT_COST_LIMIT`, when configured, is an optional guard rather
than an observed cost. Report configured currency, pricing source/date, units, and
whether estimates or provider-returned usage were used.

## Repetitions and nondeterminism

Use multiple repetitions when temperature/provider behavior is nondeterministic
and the budget permits. If one repetition is used, explicitly state that run-to-run
variance was not measured. Never retry answer-quality failures as though they were
infrastructure faults.

## Reproducibility record

Archive or export:

- code commit and environment/dependency versions;
- corpus, benchmark, pipeline, prompt, classifier/router, metric, judge, verifier,
  taxonomy, and attribution-rule versions/hashes;
- experiment dependency/configuration snapshots and retry policy;
- QueryRuns, observable trace/artifact references, and exact generator contexts;
- tidy run metrics and aggregate tables with denominator policies; and
- all exclusions, infrastructure failures, missing values, and manual overrides.

Follow [REPRODUCIBILITY.md](REPRODUCIBILITY.md) for executable checks and
[REPORT_INPUTS.md](REPORT_INPUTS.md) before transferring values to a report.
