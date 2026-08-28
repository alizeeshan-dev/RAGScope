# Threats to validity

This register must accompany reported RAGScope findings. “Mitigation” means the
platform can reduce or expose a threat; it does not mean the threat disappears.

## Internal validity

| Threat | Consequence | Mitigation/evidence to report |
| --- | --- | --- |
| Prompt differences favor a pipeline | Retrieval effects are confounded with generation instructions | Freeze one prompt version across conditions; report any intentional prompt ablation separately |
| Parser/reading-order/table errors | A parsing miss may be attributed to retrieval | Inspect parse warnings and required passages; preserve parsing failure categories and trace examples |
| Embedding/index mismatch | Dense results may be invalid or non-comparable | Freeze provider/model/dimension/preprocessing; require index integrity and corpus isolation |
| Provider/model drift | Repeated experiments may change without code changes | Record provider/model and run date/request metadata; do not claim exact reproduction across unversioned remote model updates |
| Human annotation error | Retrieval/generation metrics use incorrect ground truth | Field/evidence review history, reviewed benchmark state, source drill-down, and documented adjudication |
| Question/answer leakage | Apparent correctness is inflated | Review deterministic leakage warnings; retain warnings and decisions |
| LLM-judge bias | Automated scores mirror judge preferences | Keep judge metrics secondary and separate by prompt/model/version; audit against human labels |
| Retry selection bias | Hard failures disappear from analysis | Export attempt/failure state, show infrastructure counts, and define retry/exclusion policy before execution |
| Instrumentation changes behavior | Trace and non-trace conditions differ | Use one orchestrator; instrumentation records observable boundaries without changing stage inputs |
| Configuration mutation | Conditions silently change mid-study | Use frozen corpus/benchmark/pipeline/router/experiment snapshots and hashes |

## External validity

| Threat | Consequence | Reporting requirement |
| --- | --- | --- |
| Small scientific corpus | Findings may not generalize to larger or heterogeneous collections | Report document/domain count and avoid universal claims |
| Dataset-oriented papers | Results may not transfer to clinical QA, law, news, or general web RAG | Bound conclusions to the sampled scientific-document task |
| English-language dominance | Multilingual retrieval/generation behavior is unknown | Report language distribution and missing languages |
| One embedding/generation/reranking family | Model-specific behavior may be mistaken for an architectural effect | Name exact models and describe replication conditions needed |
| Author-created benchmark questions | Queries may differ from real researcher information needs | Report authoring process and question-type distribution; obtain external users in later work if possible |
| Local, single-user deployment | Operational results may not transfer to concurrent/cloud workloads | State hardware, database, concurrency, and deployment mode |
| Clean document set | OCR, malformed PDFs, and adversarial corpora may be underrepresented | Report parse-quality distribution and avoid claims about untested formats |

## Construct validity

| Construct | What the metric does not prove |
| --- | --- |
| Reference-answer/token similarity | Complete factual correctness or evidence faithfulness |
| Retrieval relevance/Recall@k | That evidence is sufficient, non-conflicting, or usable by the generator |
| Evidence-set completeness | That the retrieved text entails the answer |
| Citation existence | Relevance or entailment |
| Deterministic lexical citation support | Full semantic/collective claim support |
| Model-judged correctness/faithfulness | Human correctness or unbiased evaluation |
| Failure-attribution rules | A unique causal explanation for every poor answer |
| Estimated cost | Final billed amount when prices/usage/provider accounting differ |
| Adaptive “best observed” route | A production oracle or universally optimal policy |
| Passing automated tests | Trustworthiness of arbitrary real scientific answers |

Use multiple complementary metrics and qualitative trace inspection rather than
reducing “RAG quality” to a single score.

## Conclusion validity

| Threat | Mitigation/reporting requirement |
| --- | --- |
| Small sample sizes | Show `n`, missing count, and denominator on every aggregate; describe uncertainty |
| Single repetitions | State that nondeterminism was not estimated; avoid fine-grained rank claims |
| Multiple metrics/ablations | Predeclare primary comparisons or apply/report a multiple-comparison policy |
| Average metrics hide subgroups | Report question type, difficulty, answerability, and failure-stage breakdowns |
| Missing-not-at-random labels | Report missing QueryRun IDs/counts; do not convert missing values to zero |
| Infrastructure exclusions | Show excluded failures separately and rerun sensitivity analyses when feasible |
| Cost/correctness trade-off selection | Define the acceptable quality-loss rule before naming a preferred route |
| Effect-size instability | Export run-level data and avoid “winner” language from one question or pilot |

## Responsible-use and security limitations

- RAGScope does not certify answers for clinical, legal, safety-critical, or other
  high-stakes decisions.
- Scientific documents and retrieved passages are untrusted and may contain prompt
  injection, falsehoods, sensitive information, or malicious metadata.
- Automatic citation verification cannot replace expert review.
- Dataset extraction suggestions can reproduce paper errors or omissions; approval
  requires evidence and human action.
- The local application currently has no authentication or multi-user authorization.
  Do not expose it to untrusted networks or use it for sensitive corpora without an
  appropriate access-control layer.
- Do not publish trace/artifact exports until their document content and metadata
  have been reviewed for licensing and privacy constraints.

## Study-specific completion checklist

Before final conclusions, replace each placeholder with stored evidence:

- [ ] Corpus selection and representativeness reviewed.
- [ ] Parser/table/OCR failures sampled and reported.
- [ ] Benchmark evidence independently reviewed or limitations stated.
- [ ] Primary metrics/comparisons declared.
- [ ] Repetition and retry policies declared.
- [ ] Missingness and infrastructure exclusions exported.
- [ ] Model/provider/pricing dates and versions recorded.
- [ ] Question-type/difficulty subgroup results inspected.
- [ ] At least one qualitative success and failure trace audited.
- [ ] Claims match completed frozen experiment exports.
