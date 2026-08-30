"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { Benchmark, BenchmarkVersion, Corpus, Experiment, PipelineConfiguration } from "@/lib/types";
import styles from "./experiments.module.css";

export default function ExperimentManagerPage() {
  const router = useRouter();
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [benchmarkVersions, setBenchmarkVersions] = useState<BenchmarkVersion[]>([]);
  const [pipelines, setPipelines] = useState<PipelineConfiguration[]>([]);
  const [selectedPipelines, setSelectedPipelines] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextExperiments, nextCorpora, nextBenchmarks, nextPipelines] = await Promise.all([
        api.listExperiments(), api.listCorpora(), api.listBenchmarks(), api.listPipelines(),
      ]);
      const [versions, experimentDetails] = await Promise.all([
        Promise.all(nextBenchmarks.map((benchmark) => api.listBenchmarkVersions(benchmark.id))).then((items) => items.flat()),
        Promise.all(nextExperiments.map((experiment) => api.getExperiment(experiment.id).catch(() => experiment))),
      ]);
      setExperiments(experimentDetails); setCorpora(nextCorpora); setBenchmarks(nextBenchmarks); setBenchmarkVersions(versions); setPipelines(nextPipelines); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load experiments"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const readyVersions = useMemo(() => corpora.flatMap((corpus) => (corpus.versions ?? []).filter((version) => version.status === "ready" && version.frozen_at !== null).map((version) => ({ ...version, corpusName: corpus.name }))), [corpora]);
  const frozenBenchmarks = benchmarkVersions.filter((version) => version.status === "frozen");
  const frozenPipelines = pipelines.filter((pipeline) => pipeline.frozen_at !== null);
  const benchmarkName = (version: BenchmarkVersion) => benchmarks.find((benchmark) => benchmark.id === version.benchmark_id)?.name ?? "Benchmark";

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedPipelines.length < 2) { setError("Select at least two frozen pipelines for a controlled comparison."); return; }
    setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    try {
      const experiment = await api.createExperiment({
        name: String(form.get("name")), research_question: String(form.get("research_question")), corpus_version_id: String(form.get("corpus_version_id")), benchmark_version_id: String(form.get("benchmark_version_id")), pipeline_configuration_ids: selectedPipelines, repetitions: Number(form.get("repetitions")), code_commit: String(form.get("code_commit")), stop_on_error: form.get("stop_on_error") === "on",
      });
      router.push(`/experiments/${experiment.id}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Experiment could not be created"); setBusy(false); }
  }

  const runningCount = experiments.filter((item) => item.status === "running").length;
  const completedCount = experiments.filter((item) => item.status === "completed" || item.status === "completed_with_failures").length;
  const plannedRuns = experiments.reduce((sum, item) => sum + (item.progress?.total ?? 0), 0);
  const completedRuns = experiments.reduce((sum, item) => sum + (item.progress?.succeeded ?? 0) + (item.progress?.failed ?? 0), 0);

  return <main id="main" className={`shell ${styles.page}`}>
    <section className={styles.hero}>
      <div><p className="eyebrow">Controlled research</p><h1>Experiment manager</h1><p>Freeze conditions, estimate cost, execute reproducibly, and drill into every run.</p></div>
    </section>
    {error && <div className="alert" role="alert">{error}</div>}
    <div className={styles.layout}>
      <form className={`panel ${styles.form}`} onSubmit={create}>
        <div className={styles.formIntro}><h2>Configure experiment</h2><p>Only immutable research dependencies are eligible.</p></div>
        <label>Name<input name="name" required maxLength={200} placeholder="Adaptive RAG benchmark" /></label>
        <label>Research question<textarea name="research_question" required rows={3} maxLength={2000} placeholder="Can adaptive routing reduce cost without quality loss?" /></label>
        <label>Corpus<select name="corpus_version_id" required defaultValue=""><option value="" disabled>Select a ready corpus</option>{readyVersions.map((version)=><option value={version.id} key={version.id}>{version.corpusName} · {version.version_label} · Ready</option>)}</select></label>
        <label>Benchmark<select name="benchmark_version_id" required defaultValue=""><option value="" disabled>Select a frozen benchmark</option>{frozenBenchmarks.map((version)=><option value={version.id} key={version.id}>{benchmarkName(version)} · v{version.version} · Frozen</option>)}</select></label>
        <div className="form-row"><label>Repetitions<input name="repetitions" type="number" min="1" max="20" defaultValue="1" required /></label><label>Code commit<input name="code_commit" minLength={7} maxLength={100} required placeholder="git commit SHA" /></label></div>
        <fieldset><legend>Frozen pipelines (select at least 2)</legend><div className={styles.pipelineChecks}>{frozenPipelines.length === 0 ? <div className="empty"><strong>No frozen pipelines</strong><span>Freeze study conditions in Pipeline Builder first.</span></div> : frozenPipelines.map((pipeline)=><label key={pipeline.id}><input type="checkbox" checked={selectedPipelines.includes(pipeline.id)} onChange={(event)=>setSelectedPipelines((current)=>event.target.checked?[...current,pipeline.id]:current.filter((id)=>id!==pipeline.id))}/><span>{pipeline.name} v{pipeline.version}<small>{pipeline.execution_mode} · {pipeline.retrieval_mode}</small></span></label>)}</div></fieldset>
        <label className="inline-check"><input name="stop_on_error" type="checkbox"/> Pause the matrix on the first terminal failure</label>
        <div className={styles.formActions}><span className={styles.estimateHint}>Estimate cost after draft creation</span><button disabled={busy || selectedPipelines.length < 2}>{busy ? "Creating…" : "Create draft"}</button></div>
      </form>
      <section className={`panel ${styles.registry}`} aria-labelledby="experiment-list">
        <div className={styles.registryHeader}><div><h2 id="experiment-list">Experiment registry</h2><p>{experiments.length} experiment{experiments.length === 1 ? "" : "s"}</p></div><span className="muted">Immutable run matrices</span></div>
        <div className={styles.list}>{loading?<div className="design-loading" role="status">Loading experiment registry…</div>:experiments.length===0?<div className="empty"><strong>No experiments yet</strong><span>Create a small pilot after freezing corpus, benchmark, and pipeline versions.</span></div>:experiments.map((experiment)=>{const progress=experiment.progress; const complete=(progress?.succeeded??0)+(progress?.failed??0); const percent=progress?.total?Math.round(complete/progress.total*100):0; return <a className={styles.card} data-active={experiment.status === "running"} href={`/experiments/${experiment.id}`} key={experiment.id}><div><div className="panel-heading"><h2>{experiment.name}</h2><StatusBadge status={experiment.status}/></div><p>{experiment.research_question}</p><div className={styles.facts}><span>{experiment.pipeline_configuration_ids.length} pipelines</span><span>{experiment.repetitions} repetition{experiment.repetitions===1?"":"s"}</span><span>{progress?.total??"Matrix not frozen"} planned runs</span><span>{new Date(experiment.created_at).toLocaleDateString()}</span></div></div><div className={styles.progress}><strong>{percent}%</strong><progress max="100" value={percent}>{percent}%</progress><small>{complete}/{progress?.total??0} complete</small></div></a>;})}</div>
        <div className={styles.researchSummary} aria-label="Live experiment summary"><div><span>Running</span><strong>{runningCount}</strong></div><div><span>Completed</span><strong>{completedCount}</strong></div><div><span>Completed runs</span><strong>{completedRuns}</strong></div><div><span>Planned runs</span><strong>{plannedRuns || "—"}</strong></div></div>
      </section>
    </div>
  </main>;
}
