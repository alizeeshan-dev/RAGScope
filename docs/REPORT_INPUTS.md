# Research report inputs and figure provenance

This document maps every reportable statement to stored RAGScope state. It is a
data-provenance checklist, not a results summary.

## Current result status

> **MAIN RESULTS NOT PRESENT IN THIS DOCUMENTATION.** No pipeline winner, effect
> size, quality/cost trade-off, failure distribution, or adaptive-routing benefit
> is asserted here. Populate the placeholders only from a completed frozen
> experiment and its deterministic exports.

Engineering fixture tests and synthetic metric gold cases must not be reported as
empirical scientific findings.

## Required frozen identities

Record these before interpreting any result:

| Input | Required stored field |
| --- | --- |
| Code | Experiment `code_commit` |
| Experiment | ID, status, configuration hash, dependency snapshot, retry/stop policy, repetitions |
| Corpus | CorpusVersion ID, status, content hash, parser/chunker/embedding snapshots |
| Benchmark | BenchmarkVersion ID, status, frozen time, question count/distribution |
| Pipelines | IDs, names, versions, hashes, full frozen snapshots |
| Prompts | prompt ID/version/hash used by each QueryRun |
| Adaptive route | classifier/router versions, frozen router hash, chosen route/reason per run |
| Providers | provider/model/version, request metadata, temperature/output limit |
| Evaluation | metric name/version/scope/method and exact input hash/details |
| Failure attribution | taxonomy/rules version, automatic label and preserved human override |
| Pricing | currency, unit, rate source/date, completeness, experiment ceiling |

## Raw and derived data separation

### Raw/observable inputs

- QueryRun status, question, linked benchmark question, route, answer, tokens, cost,
  latency, and failure code;
- ordered TraceSpans and artifact references;
- original retrieval ranks/scores, fused/reranked ranks, and context disposition;
- exact context and raw/parsed generation artifacts;
- claims, citations, cited text, source IDs/pages; and
- human benchmark evidence, answerability, reference criteria, and corrections.

### Derived analysis

- EvaluationResults with complete metric identity;
- automatic/human FailureAttributions;
- aggregate means/medians/sums;
- denominator, missing, and infrastructure-exclusion counts; and
- evidence-survival summaries.

Never edit raw exports to manufacture a derived result. Regenerate aggregate
tables from the same run-level input and explicit aggregation plan.

## Deterministic export contents

The analysis layer produces:

1. tidy run-metric CSV: one row per QueryRun/metric observation;
2. aggregate CSV: one row per exact group/metric identity with denominator audit; and
3. versioned canonical JSON: aggregation plan, filters, selectors, runs, and aggregates.

Null metric values export as blank/null, not zero. Preserve the files together.
Before report transfer, confirm the HTTP export endpoint appears in the running
API OpenAPI document and that downloaded files are non-empty; do not infer endpoint
availability from frontend links alone.

## Required figures

| Figure | Stored input | Primary y/x value | Required audit fields |
| --- | --- | --- | --- |
| 1. Retrieval Recall@k by pipeline | retrieval EvaluationResults + human evidence sets | mean Recall@configured k | k, metric version/method, n, missing, infrastructure excluded, contributing runs |
| 2. Answer correctness by pipeline | human correctness labels | mean human correctness | n, label scale, missing labels, excluded infrastructure |
| 3. Citation support rate by pipeline | claim/citation judgments | human or explicitly named automatic support rate | verifier/reviewer method, n, missing collective labels |
| 4. Cost vs correctness | paired QueryRun cost and human correctness | configured currency vs correctness | paired n, missing prices/labels, pricing date/source |
| 5. Latency distribution | QueryRun/Trace total latency | milliseconds (median and distribution) | n, deployment/hardware, infrastructure population |
| 6. Failure-stage distribution | FailureAttribution | count/share per stage | taxonomy/rules version, primary/secondary policy, unreviewed count |
| 7. Performance by query type | benchmark type + primary metric | correctness or declared primary quality metric | per-cell n/missing/exclusions, type distribution |
| 8. Evidence survival | human required evidence + stage records | retrieval → reranking → context coverage | stage-specific denominator/missing count and metric version |

Every dashboard point/table row must link to contributing QueryRun IDs. The report
caption must state filters and denominator policy.

Generate the eight figure artifacts from the versioned JSON export with
`scripts/research/generate_figures.py` and an archived study-specific copy of
`scripts/research/figure_config.example.json`. The generator requires exact metric
name/version/scope/method selectors, records source/configuration hashes in its
manifest, and renders explicit placeholders when inputs are missing.

## Main results table placeholders

Replace `NOT AVAILABLE` only from stored exports.

| Pipeline | Recall@k | Context evidence retained | Human answer correctness | Citation support | Median latency | Median tokens | Median cost | Quality-evaluable n | Infrastructure failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| No RAG | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE |
| Lexical | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE |
| Dense | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE |
| Hybrid | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE |
| Hybrid + reranking | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE |
| Adaptive | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE |

## Adaptive comparison placeholders

- Baseline definition/configuration: **NOT AVAILABLE**
- Best-observed-pipeline criterion: **NOT AVAILABLE**
- Route accuracy n/value: **NOT AVAILABLE**
- Retrieval calls avoided: **NOT AVAILABLE**
- Reranking calls avoided: **NOT AVAILABLE**
- Token reduction: **NOT AVAILABLE**
- Cost reduction: **NOT AVAILABLE**
- Latency reduction: **NOT AVAILABLE**
- Quality loss versus named baseline: **NOT AVAILABLE**

The best observed pipeline is an explicit analysis criterion over completed runs,
not a production oracle or universally winning route.

## Qualitative case-study inputs

Select cases only after quantitative filters are declared. For each case record:

- QueryRun and benchmark-question IDs;
- expected answerability/evidence set;
- pipeline and route snapshot;
- retrieval/fusion/reranking ranks;
- exact context artifact hash;
- answer, claims, citations, and human labels;
- primary/secondary failure attribution and any override; and
- why the case is representative rather than merely dramatic.

Include at least one success, retrieval miss, evidence loss after retrieval, context-
present generation failure, citation failure, unanswerable case, and infrastructure
failure when such cases exist. Never invent a category solely to fill a report slot.

## Transfer-to-report verification

- [ ] Experiment is completed/completed-with-failures and frozen dependencies match.
- [ ] Run matrix count and attempt/retry accounting reconcile.
- [ ] Export schema/aggregation versions are recorded.
- [ ] Exact metric identities match the planned methodology.
- [ ] Aggregate accounting equals denominator + missing + infrastructure-excluded.
- [ ] Dashboard filters equal exported aggregation filters.
- [ ] Figure values are reproducible from exported rows.
- [ ] Report text distinguishes human, deterministic, operational, and model-judge values.
- [ ] No fixture/synthetic result is presented as a main result.
- [ ] Threats and deviations are updated from actual execution evidence.
