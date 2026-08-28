"use client";

import type { CSSProperties } from "react";
import type { ExperimentAggregate, ExperimentResultRow, ExperimentVisualizationData } from "@/lib/types";
import styles from "./ExperimentResultsCharts.module.css";

type Aggregate = { key: string; label: string; value: number | null; n: number; denominator: number };

const metricAliases = {
  recall: ["retrieval_recall_at_k", "recall_at_k", "recall@k", "recall_at_5"],
  correctness: ["answer_correctness", "answer_correctness_human", "model_judged_correctness"],
  citation: ["claim_support_rate", "citation_support_rate", "citation_precision"],
} as const;

function metric(row: ExperimentResultRow, aliases: readonly string[]): number | null {
  for (const alias of aliases) {
    const exact = row.metrics.find((item) => item.metric_name === alias && item.metric_value != null);
    if (exact) return exact.metric_value;
  }
  const fuzzy = row.metrics.find((item) => aliases.some((alias) => item.metric_name.startsWith(alias)) && item.metric_value != null);
  return fuzzy?.metric_value ?? null;
}

function pipelines(rows: ExperimentResultRow[]): Array<[string, ExperimentResultRow[]]> {
  const grouped = new Map<string, ExperimentResultRow[]>();
  for (const row of rows) {
    const key = row.pipeline_configuration_id;
    grouped.set(key, [...(grouped.get(key) ?? []), row]);
  }
  return [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right));
}

function metricAggregate(rows: ExperimentResultRow[], aliases: readonly string[]): Aggregate[] {
  return pipelines(rows).map(([key, group]) => {
    const label = group[0]?.pipeline_name || key;
    const eligible = group.filter((row) => !row.infrastructure_failure);
    const values = eligible.map((row) => metric(row, aliases)).filter((value): value is number => value != null);
    return { key, label, value: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null, n: values.length, denominator: eligible.length };
  });
}

function persistedAggregate(rows: ExperimentAggregate[], aliases: readonly string[]): Aggregate[] {
  const selected = new Map<string, { rank: number; aggregate: Aggregate }>();
  for (const row of rows) {
    const rank = aliases.findIndex((alias) => row.metric_name === alias || row.metric_name.startsWith(alias));
    if (rank < 0) continue;
    const group = Object.fromEntries(row.group);
    const label = String(group.pipeline_name ?? group.pipeline_id ?? group.pipeline_configuration_id ?? "All pipelines");
    const key = String(group.pipeline_id ?? group.pipeline_configuration_id ?? label);
    const current = selected.get(key);
    if (!current || rank < current.rank) selected.set(key, { rank, aggregate: { key, label, value: row.mean, n: row.denominator_count, denominator: row.total_run_count - row.excluded_infrastructure_count } });
  }
  return [...selected.values()].map((value) => value.aggregate);
}

function formatRate(value: number | null): string {
  return value == null ? "Missing" : `${(value * 100).toFixed(1)}%`;
}

function formatCost(value: number | null, currency?: string | null): string {
  if (value == null) return "Unavailable";
  return `${value.toFixed(6)} ${currency || "configured currency"}`;
}

function BarChart({ rows, label }: { rows: Aggregate[]; label: string }) {
  return <div className={styles.bars} role="img" aria-label={label}>{rows.map((row) => <div className={styles.barRow} key={row.key}>
    <span>{row.label}</span><div className={styles.barTrack} aria-hidden="true"><i style={{ "--bar-value": `${Math.max(0, Math.min(100, (row.value ?? 0) * 100))}%` } as CSSProperties} /></div>
    <strong>{formatRate(row.value)}</strong><small>n={row.n}; denominator={row.denominator}</small>
  </div>)}</div>;
}

function MetricByPipeline({ id, eyebrow, title, description, rows }: { id: string; eyebrow: string; title: string; description: string; rows: Aggregate[] }) {
  return <article className={styles.chartCard} aria-labelledby={id}><header><div><p className="eyebrow">{eyebrow}</p><h2 id={id}>{title}</h2></div><span className={styles.denominator}>Successful quality-evaluable runs only</span></header><p className="muted">{description}</p>
    {rows.length ? <><BarChart rows={rows} label={`${title}; bars grouped by pipeline`} /><details><summary>Accessible values and denominators</summary><table><thead><tr><th scope="col">Pipeline</th><th scope="col">Value</th><th scope="col">Sample</th><th scope="col">Eligible denominator</th></tr></thead><tbody>{rows.map((row) => <tr key={row.key}><th scope="row">{row.label}</th><td>{formatRate(row.value)}</td><td>{row.n}</td><td>{row.denominator}</td></tr>)}</tbody></table></details></> : <p className={styles.empty}>No matching result rows.</p>}
  </article>;
}

function CostCorrectness({ rows, persisted }: { rows: ExperimentResultRow[]; persisted?: ExperimentVisualizationData["cost_correctness"] }) {
  const points = persisted?.map((point) => ({ row: { query_run_id: point.run_id, pipeline_name: point.pipeline, cost_currency: point.currency } as ExperimentResultRow, correctness: point.correctness, cost: point.cost })) ?? rows.flatMap((row) => {
    const correctness = metric(row, metricAliases.correctness);
    return !row.infrastructure_failure && correctness != null && row.estimated_cost != null ? [{ row, correctness, cost: row.estimated_cost }] : [];
  });
  const maxCost = Math.max(...points.map((point) => point.cost), 0.000001);
  return <article className={styles.chartCard} aria-labelledby="cost-correctness"><header><div><p className="eyebrow">Figure 4</p><h2 id="cost-correctness">Cost versus answer correctness</h2></div><span className={styles.denominator}>n={points.length} paired observations</span></header><p className="muted">Only runs with both a configured cost and an answer-correctness label appear. Missing prices are never rendered as zero.</p>
    {points.length ? <><svg className={styles.scatter} viewBox="0 0 640 260" role="img" aria-labelledby="cost-title cost-desc"><title id="cost-title">Cost versus correctness scatter plot</title><desc id="cost-desc">{points.length} query runs. Horizontal position is configured cost; vertical position is answer correctness.</desc><line x1="55" y1="220" x2="620" y2="220"/><line x1="55" y1="20" x2="55" y2="220"/><text x="300" y="252">Estimated cost</text><text x="4" y="18">1.0</text><text x="15" y="224">0</text>{points.map(({ row, correctness, cost }, index) => <a href={`/laboratory/${row.query_run_id}`} key={`${row.query_run_id}-${index}`}><circle cx={55 + (cost / maxCost) * 555} cy={220 - correctness * 195} r="6"><title>{row.pipeline_name}: correctness {correctness.toFixed(3)}, cost {formatCost(cost, row.cost_currency)}</title></circle></a>)}</svg><details><summary>Accessible run values</summary><table><thead><tr><th>Run</th><th>Pipeline</th><th>Correctness</th><th>Cost</th></tr></thead><tbody>{points.map(({ row, correctness, cost }) => <tr key={row.query_run_id}><td><a href={`/laboratory/${row.query_run_id}`}>{row.query_run_id.slice(0, 8)}</a></td><td>{row.pipeline_name}</td><td>{correctness.toFixed(4)}</td><td>{formatCost(cost, row.cost_currency)}</td></tr>)}</tbody></table></details></> : <p className={styles.empty}>No run has both correctness and configured pricing.</p>}
  </article>;
}

function quantile(values: number[], fraction: number): number | null {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const position = (ordered.length - 1) * fraction;
  const lower = Math.floor(position);
  const remainder = position - lower;
  return ordered[lower + 1] === undefined ? ordered[lower] : ordered[lower] + remainder * (ordered[lower + 1] - ordered[lower]);
}

function LatencyDistribution({ rows, persisted }: { rows: ExperimentResultRow[]; persisted?: ExperimentVisualizationData["latency_by_pipeline"] }) {
  const distributions = persisted?.map((row) => ({ label: row.pipeline, n: row.n, failures: row.infrastructure_failures, min: row.minimum, q1: row.q1, median: row.median, q3: row.q3, max: row.maximum })) ?? pipelines(rows).map(([key, group]) => {
    const label = group[0]?.pipeline_name || key;
    const values = group.map((row) => row.total_latency_ms).filter((value): value is number => value != null);
    return { label, n: values.length, failures: group.filter((row) => row.infrastructure_failure).length, min: quantile(values, 0), q1: quantile(values, .25), median: quantile(values, .5), q3: quantile(values, .75), max: quantile(values, 1) };
  });
  const scale = Math.max(...distributions.map((row) => row.max ?? 0), 1);
  return <article className={styles.chartCard} aria-labelledby="latency-distribution"><header><div><p className="eyebrow">Figure 5</p><h2 id="latency-distribution">Latency distribution</h2></div><span className={styles.denominator}>All runs with recorded latency</span></header><p className="muted">Five-number summaries use milliseconds and keep infrastructure failures visible in each row.</p><div className={styles.ranges}>{distributions.map((row) => <div key={row.label}><strong>{row.label}</strong><div className={styles.rangeTrack} aria-hidden="true"><i style={{ left: `${((row.min ?? 0) / scale) * 100}%`, width: `${(((row.max ?? 0) - (row.min ?? 0)) / scale) * 100}%` }} /><b style={{ left: `${((row.median ?? 0) / scale) * 100}%` }} /></div><small>median {row.median == null ? "missing" : `${Math.round(row.median)} ms`} · n={row.n} · infrastructure failures={row.failures}</small></div>)}</div><table><caption>Latency five-number summary in milliseconds</caption><thead><tr><th>Pipeline</th><th>Min</th><th>Q1</th><th>Median</th><th>Q3</th><th>Max</th><th>n</th></tr></thead><tbody>{distributions.map((row) => <tr key={row.label}><th>{row.label}</th>{[row.min,row.q1,row.median,row.q3,row.max].map((value,index) => <td key={index}>{value == null ? "Missing" : Math.round(value)}</td>)}<td>{row.n}</td></tr>)}</tbody></table></article>;
}

function FailureDistribution({ rows, persisted }: { rows: ExperimentResultRow[]; persisted?: ExperimentVisualizationData["failure_distribution"] }) {
  const counts = new Map<string, number>();
  if (persisted) for (const row of persisted) counts.set(row.stage, (counts.get(row.stage) ?? 0) + row.count);
  else for (const row of rows) if (row.failure_stage) counts.set(row.failure_stage, (counts.get(row.failure_stage) ?? 0) + 1);
  const values = [...counts.entries()].sort((left, right) => right[1] - left[1]);
  const max = Math.max(...values.map(([, count]) => count), 1);
  return <article className={styles.chartCard} aria-labelledby="failure-distribution"><header><div><p className="eyebrow">Figure 6</p><h2 id="failure-distribution">Failure-stage distribution</h2></div><span className={styles.denominator}>n={values.reduce((sum, [, count]) => sum + count, 0)} attributed failures · {rows.length} filtered runs</span></header><p className="muted">Quality failures and infrastructure failures retain their persisted stage; no unattributed run is silently assigned.</p>{values.length ? <div className={styles.failureBars}>{values.map(([stage,count]) => <div key={stage}><span>{stage.replaceAll("_", " ")}</span><i style={{ "--bar-value": `${(count / max) * 100}%` } as CSSProperties}/><strong>{count}</strong></div>)}</div> : <p className={styles.empty}>No attributed failures in this filter.</p>}</article>;
}

function PerformanceByType({ rows, persisted }: { rows: ExperimentResultRow[]; persisted?: ExperimentVisualizationData["performance_by_question_type"] }) {
  const types = [...new Set(persisted?.map((row) => row.question_type) ?? rows.map((row) => row.question_type))].sort();
  const pipelineLabels = [...new Set(persisted?.map((row) => row.pipeline) ?? pipelines(rows).map(([key, group]) => group[0]?.pipeline_name || key))].sort();
  return <article className={`${styles.chartCard} ${styles.wide}`} aria-labelledby="performance-type"><header><div><p className="eyebrow">Figure 7</p><h2 id="performance-type">Performance by question type</h2></div><span className={styles.denominator}>Answer-correctness labels; infrastructure failures excluded</span></header><p className="muted">Each cell reveals its own applicable sample size rather than inheriting the global run count.</p>{types.length ? <div className={styles.tableScroll}><table><thead><tr><th scope="col">Question type</th>{pipelineLabels.map((pipeline) => <th scope="col" key={pipeline}>{pipeline}</th>)}</tr></thead><tbody>{types.map((type) => <tr key={type}><th scope="row">{type.replaceAll("_", " ")}</th>{pipelineLabels.map((pipeline) => { const stored=persisted?.find((row)=>row.question_type===type&&row.pipeline===pipeline); const group=rows.filter((row)=>row.pipeline_name===pipeline); const eligible=group.filter((row)=>row.question_type===type&&!row.infrastructure_failure); const values=eligible.map((row)=>metric(row,metricAliases.correctness)).filter((value):value is number=>value!=null); const average=stored?.value??(values.length?values.reduce((sum,value)=>sum+value,0)/values.length:null); return <td key={pipeline}><strong>{formatRate(average)}</strong><small>n={stored?.n??values.length}/{stored?.denominator??eligible.length}</small></td>; })}</tr>)}</tbody></table></div> : <p className={styles.empty}>No question-type labels are available.</p>}</article>;
}

function EvidenceSurvival({ rows, aggregates }: { rows: ExperimentResultRow[]; aggregates?: ExperimentAggregate[] }) {
  const persisted = new Map<string, { label: string; retrieval: number | null; reranking: number | null; context: number | null; n: number; denominator: number }>();
  for (const item of aggregates ?? []) {
    const group = Object.fromEntries(item.group);
    const label = String(group.pipeline_name ?? group.pipeline_id ?? group.pipeline_configuration_id ?? "All pipelines");
    const current = persisted.get(label) ?? { label, retrieval: null, reranking: null, context: null, n: 0, denominator: 0 };
    const stage = item.metric_name.split(".").at(-1);
    if (stage === "retrieval" || stage === "reranking" || stage === "context") current[stage] = item.mean;
    current.n = Math.max(current.n, item.denominator_count);
    current.denominator = Math.max(current.denominator, item.total_run_count - item.excluded_infrastructure_count);
    persisted.set(label, current);
  }
  const fallback = pipelines(rows).map(([key, group]) => {
    const label = group[0]?.pipeline_name || key;
    const usable = group.filter((row) => !row.infrastructure_failure && row.retrieval_required_count != null && row.retrieval_required_count > 0);
    const required = usable.reduce((sum,row)=>sum+(row.retrieval_required_count??0),0);
    const retrieval = usable.reduce((sum,row)=>sum+(row.retrieval_evidence_count??0),0);
    const reranking = usable.reduce((sum,row)=>sum+(row.reranking_evidence_count??0),0);
    const context = usable.reduce((sum,row)=>sum+(row.context_evidence_count??0),0);
    return { label, retrieval: required ? retrieval / required : null, reranking: required ? reranking / required : null, context: required ? context / required : null, n: usable.length, denominator: usable.length };
  });
  const values = persisted.size ? [...persisted.values()] : fallback;
  return <article className={`${styles.chartCard} ${styles.wide}`} aria-labelledby="evidence-survival"><header><div><p className="eyebrow">Figure 8</p><h2 id="evidence-survival">Required-evidence survival</h2></div><span className={styles.denominator}>Retrieval → reranking → context</span></header><p className="muted">Human-required evidence survival is aggregated per stage. A missing stage remains unavailable and is not converted to zero.</p>{values.length ? <div className={styles.survivalGrid}>{values.map((row)=><section key={row.label}><h3>{row.label}</h3><div><span>Eligible runs<strong>{row.denominator || "Missing"}</strong></span><b aria-hidden="true">→</b><span>Retrieved<strong>{formatRate(row.retrieval)}</strong></span><b aria-hidden="true">→</b><span>After reranking<strong>{formatRate(row.reranking)}</strong></span><b aria-hidden="true">→</b><span>Context<strong>{formatRate(row.context)}</strong></span></div><small>n={row.n} contributing benchmark-labelled runs; eligible denominator={row.denominator || "missing"}</small></section>)}</div> : <p className={styles.empty}>No human-required evidence annotations are available.</p>}</article>;
}

export function ExperimentResultsCharts({ rows, aggregates, evidenceSurvival, visualizations }: { rows: ExperimentResultRow[]; aggregates?: ExperimentAggregate[]; evidenceSurvival?: ExperimentAggregate[]; visualizations?: ExperimentVisualizationData }) {
  const recall = persistedAggregate(aggregates ?? [], metricAliases.recall); if (!recall.length) recall.push(...metricAggregate(rows, metricAliases.recall));
  const correctness = persistedAggregate(aggregates ?? [], metricAliases.correctness); if (!correctness.length) correctness.push(...metricAggregate(rows, metricAliases.correctness));
  const citation = persistedAggregate(aggregates ?? [], metricAliases.citation); if (!citation.length) citation.push(...metricAggregate(rows, metricAliases.citation));
  return <div className={styles.chartGrid}>
    <MetricByPipeline id="recall-pipeline" eyebrow="Figure 1" title="Retrieval Recall@k by pipeline" description="Best acceptable human evidence set at the metric's persisted k and version." rows={recall}/>
    <MetricByPipeline id="correctness-pipeline" eyebrow="Figure 2" title="Answer correctness by pipeline" description="Primary human labels are preferred; missing correctness labels stay missing." rows={correctness}/>
    <MetricByPipeline id="citation-pipeline" eyebrow="Figure 3" title="Citation support rate by pipeline" description="Claim support or citation support, using the persisted verifier or human method." rows={citation}/>
    <CostCorrectness rows={rows} persisted={visualizations?.cost_correctness}/><LatencyDistribution rows={rows} persisted={visualizations?.latency_by_pipeline}/><FailureDistribution rows={rows} persisted={visualizations?.failure_distribution}/><PerformanceByType rows={rows} persisted={visualizations?.performance_by_question_type}/><EvidenceSurvival rows={rows} aggregates={evidenceSurvival}/>
  </div>;
}
