# RAGScope research report template

This is a report scaffold. Bracketed entries are required placeholders, not claims.
Do not remove the result-status warning until a completed frozen experiment and
verified exports exist.

> **Result status:** MAIN EXPERIMENT RESULTS NOT YET INSERTED.

## 1. Abstract

[Problem, method, frozen corpus/benchmark size, exact evaluated conditions, primary
human-grounded results with n, and bounded conclusion. Do not state a preferred
pipeline until values are export-backed.]

## 2. Introduction

- Explain the black-box failure-attribution problem in scientific RAG.
- State the research questions and contribution of observable evidence flow.
- Distinguish engineering instrumentation from a model-quality claim.

## 3. Related work

[Review primary sources on lexical/dense/hybrid retrieval, reranking, RAG
evaluation, citation faithfulness, scientific-document parsing, and adaptive RAG.
Identify what RAGScope reproduces, adapts, or uses only as motivation.]

## 4. RAGScope design

Summarize [ARCHITECTURE.md](ARCHITECTURE.md): immutable research objects, one fixed/
adaptive orchestrator, rank preservation, exact artifacts, observable trace,
human-ground-truth boundaries, metrics, and experiment/analysis layers.

## 5. Research questions and hypotheses

- RQ1: [exact preregistered text]
- RQ2: [exact preregistered text]
- RQ3: [exact preregistered text]
- Primary metric/comparison: [exact identity]
- Hypotheses: [directional or explicitly exploratory]

## 6. Corpus and benchmark

- Corpus ID/hash and document selection: [NOT AVAILABLE]
- Document/language/domain distributions: [NOT AVAILABLE]
- Parser/chunker/index snapshots and quality review: [NOT AVAILABLE]
- Benchmark ID, question count, type/difficulty/answerability distribution: [NOT AVAILABLE]
- Annotation/review/evidence-set process: [NOT AVAILABLE]
- Leakage and disagreement handling: [NOT AVAILABLE]

## 7. Experimental method

Use [METHODOLOGY.md](METHODOLOGY.md) and report:

- experiment ID/configuration hash/code commit: [NOT AVAILABLE]
- pipeline conditions/hashes: [NOT AVAILABLE]
- controlled settings: [NOT AVAILABLE]
- repetitions and retry/exclusion policy: [NOT AVAILABLE]
- metric/judge/verifier/taxonomy versions: [NOT AVAILABLE]
- pricing source/date/currency: [NOT AVAILABLE]
- hardware/database/provider execution context: [NOT AVAILABLE]

## 8. Results

Populate only from [REPORT_INPUTS.md](REPORT_INPUTS.md) and deterministic exports.

- Main aggregate table: [NOT AVAILABLE]
- Figure 1 — Recall@k by pipeline: [NOT AVAILABLE]
- Figure 2 — answer correctness: [NOT AVAILABLE]
- Figure 3 — citation support: [NOT AVAILABLE]
- Figure 4 — cost vs correctness: [NOT AVAILABLE]
- Figure 5 — latency distribution: [NOT AVAILABLE]
- Figure 6 — failure stages: [NOT AVAILABLE]
- Figure 7 — performance by query type: [NOT AVAILABLE]
- Figure 8 — evidence survival: [NOT AVAILABLE]

Every caption must show n, missing/excluded counts, filters, metric identity, and
whether labels are human or automatic.

## 9. Failure analysis

[Report primary/secondary categories, taxonomy/rules version, human corrections,
and trace-backed cases. Distinguish parsing/retrieval/reranking/context/generation/
citation failures from infrastructure failures.]

## 10. Discussion

[Interpret practical trade-offs without declaring a universal winner. Compare
adaptive and fixed behavior only against the explicit baseline/criterion. Explain
where evidence was found but not used.]

## 11. Threats to validity

Adapt [THREATS_TO_VALIDITY.md](THREATS_TO_VALIDITY.md) using actual corpus,
annotation, missingness, repetitions, provider drift, and subgroup evidence.

## 12. Responsible-use considerations

[Document untrusted scientific content, prompt injection, human review boundaries,
secret/privacy handling, licensing, and why automated citation checks do not certify
high-stakes answers.]

## 13. Conclusion

[Answer only the declared RQs supported by completed stored results. State the
strongest limitation and avoid extrapolation beyond the sampled corpus/models.]

## Reproducibility statement

[Link archived code commit, environment, frozen IDs/hashes, run/aggregate exports,
study deviations, and the exact reproduction commands in REPRODUCIBILITY.md.]
