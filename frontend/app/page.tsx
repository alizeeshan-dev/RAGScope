"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Benchmark, Corpus, Experiment, ExperimentResults, IndexStatus, PipelineConfiguration } from "@/lib/types";
import { StatusBadge } from "@/components/StatusBadge";

type OverviewState = { corpora: Corpus[]; pipelines: PipelineConfiguration[]; benchmarks: Benchmark[]; experiments: Experiment[]; indexes: IndexStatus[]; latestResults: ExperimentResults | null };
const emptyState: OverviewState = { corpora: [], pipelines: [], benchmarks: [], experiments: [], indexes: [], latestResults: null };
const numberSetting = (configuration: Record<string, unknown>, key: string) => typeof configuration[key] === "number" ? configuration[key] as number : null;

export default function OverviewPage() {
  const [state, setState] = useState<OverviewState>(emptyState);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [corpora, pipelines, benchmarkCollections, experimentRecords] = await Promise.all([api.listCorpora(), api.listPipelines(), api.listBenchmarks(), api.listExperiments()]);
      const [benchmarks, experimentDetails] = await Promise.all([
        Promise.all(benchmarkCollections.map(async (benchmark) => ({ ...benchmark, versions: await api.listBenchmarkVersions(benchmark.id) }))),
        Promise.all(experimentRecords.slice(0, 4).map((experiment) => api.getExperiment(experiment.id).catch(() => experiment))),
      ]);
      const experiments = [...experimentDetails, ...experimentRecords.slice(4)];
      const versions = corpora.flatMap((corpus) => corpus.versions ?? []);
      const indexGroups = await Promise.all(versions.map((version) => api.getIndexStatus(version.id).catch(() => [])));
      const latestCompleted = experiments.find((experiment) => ["completed", "completed_with_failures"].includes(experiment.status));
      const latestResults = latestCompleted ? await api.getExperimentResults(latestCompleted.id, undefined, 0, 1).catch(() => null) : null;
      setState({ corpora, pipelines, benchmarks, experiments, indexes: indexGroups.flat(), latestResults });
      setError("");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load the research overview."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const summary = useMemo(() => {
    const versions = state.corpora.flatMap((corpus) => corpus.versions ?? []);
    const readyVersions = versions.filter((version) => version.status === "ready");
    const frozenPipelines = state.pipelines.filter((pipeline) => pipeline.frozen_at);
    const indexedChunks = state.indexes.reduce((total, index) => index.index_type === "lexical" && index.status === "ready" ? total + index.indexed_count : total, 0);
    const hybrid = frozenPipelines.filter((pipeline) => pipeline.retrieval_mode === "hybrid").length;
    const reranked = frozenPipelines.filter((pipeline) => pipeline.reranker_configuration.enabled === true).length;
    const budgets = frozenPipelines.map((pipeline) => numberSetting(pipeline.context_configuration, "token_budget")).filter((value): value is number => value !== null);
    const evidence = state.latestResults?.headline_metrics.evidence_completeness;
    const frozenBenchmarks = state.benchmarks.flatMap((benchmark) => benchmark.versions ?? []).filter((version) => version.status === "frozen");
    const priced = state.experiments.some((experiment) => experiment.cost_estimate?.cost_fully_configured);
    return { versions, readyVersions, frozenPipelines, indexedChunks, hybrid, reranked, budgets, evidence, frozenBenchmarks, priced };
  }, [state]);

  return <main id="main" className="design-shell overview-screen">
    <section className="overview-hero"><div><p className="design-eyebrow">Observable by construction.</p><h1>RAG research,<br />made inspectable.</h1><p>Trace evidence from scientific source to grounded answer, compare controlled pipelines, and preserve every research condition.</p></div><Link className="design-button design-button-dark" href="/laboratory">Run a query <span aria-hidden="true">↗</span></Link></section>
    {error && <div className="alert" role="alert">{error} <button className="text-action" onClick={() => void load()}>Retry</button></div>}
    <section className="overview-metrics" aria-label="Research workspace metrics">
      <article><strong>{loading ? "—" : summary.versions.length}</strong><span>Corpus versions</span><small>{summary.readyVersions.length} ready for queries</small></article>
      <article><strong>{loading ? "—" : summary.indexedChunks.toLocaleString()}</strong><span>Indexed chunks</span><small>Ready lexical indexes</small></article>
      <article><strong>{loading ? "—" : summary.frozenPipelines.length}</strong><span>Frozen pipelines</span><small>Immutable configurations</small></article>
      <article><strong>{summary.evidence?.value == null ? "—" : `${Math.round(summary.evidence.value * 100)}%`}</strong><span>Evidence survival</span><small>{summary.evidence ? `n=${summary.evidence.denominator}` : "Awaiting evaluated runs"}</small></article>
    </section>
    <section className="overview-grid">
      <article className="design-card evidence-overview">
        <header className="design-section-heading"><div><p className="design-eyebrow">Pipeline anatomy</p><h2>Evidence flow</h2></div><Link href="/comparisons">Compare pipelines <span aria-hidden="true">→</span></Link></header>
        <div className="evidence-stage-flow" aria-label="Observable pipeline stages">
          <div><span>01</span><strong>Retrieve</strong><small>{summary.indexedChunks ? `${summary.indexedChunks.toLocaleString()} indexed` : "No ready index"}</small></div><i aria-hidden="true">→</i>
          <div><span>02</span><strong>Fuse</strong><small>{summary.hybrid} hybrid configs</small></div><i aria-hidden="true">→</i>
          <div><span>03</span><strong>Rerank</strong><small>{summary.reranked} enabled configs</small></div><i aria-hidden="true">→</i>
          <div><span>04</span><strong>Context</strong><small>{summary.budgets.length ? `${Math.round(summary.budgets.reduce((a, b) => a + b, 0) / summary.budgets.length)} avg tokens` : "Not configured"}</small></div><i aria-hidden="true">→</i>
          <div><span>05</span><strong>Generate</strong><small>Claims + citations</small></div>
        </div>
        <div className="evidence-note"><span aria-hidden="true">◎</span><p><strong>Rank history stays intact.</strong> Retrieval, fusion, reranking, and context decisions remain independently inspectable.</p></div>
      </article>
      <aside className="design-card research-health"><header className="design-section-heading"><div><p className="design-eyebrow">Preflight</p><h2>Research health</h2></div></header><ul>
        <li><span><i className={summary.readyVersions.length ? "health-ok" : "health-warn"} />Corpus</span><strong>{summary.readyVersions.length ? `${summary.readyVersions.length} ready` : "Needs a ready version"}</strong></li>
        <li><span><i className={summary.frozenBenchmarks.length ? "health-ok" : "health-warn"} />Benchmark</span><strong>{summary.frozenBenchmarks.length ? `${summary.frozenBenchmarks.length} frozen` : "Not frozen"}</strong></li>
        <li><span><i className={summary.evidence ? "health-ok" : "health-muted"} />Metrics</span><strong>{summary.evidence ? "Evaluated" : "Awaiting runs"}</strong></li>
        <li><span><i className={summary.priced ? "health-ok" : "health-warn"} />Pricing</span><strong>{summary.priced ? "Configured" : "Unavailable"}</strong></li>
      </ul></aside>
    </section>
    <section className="design-card recent-experiments"><header className="design-section-heading"><div><p className="design-eyebrow">Latest work</p><h2>Recent experiments</h2></div><Link href="/experiments">Open manager <span aria-hidden="true">→</span></Link></header><div className="recent-experiment-list">
      {state.experiments.slice(0, 4).map((experiment) => <Link href={`/experiments/${experiment.id}`} key={experiment.id}><div><strong>{experiment.name}</strong><small>{experiment.research_question || "No research question supplied"}</small></div><span>{experiment.progress?.succeeded ?? 0}/{experiment.progress?.total ?? 0} runs</span><StatusBadge status={experiment.status} /><i aria-hidden="true">↗</i></Link>)}
      {!loading && state.experiments.length === 0 && <div className="design-empty"><strong>No experiments yet</strong><span>Freeze a benchmark and pipeline before creating the first controlled study.</span><Link href="/experiments">Configure an experiment</Link></div>}
      {loading && <div className="design-loading">Loading research state…</div>}
    </div></section>
  </main>;
}
