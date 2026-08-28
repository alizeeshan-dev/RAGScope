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
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [nextExperiments, nextCorpora, nextBenchmarks, nextPipelines] = await Promise.all([
        api.listExperiments(), api.listCorpora(), api.listBenchmarks(), api.listPipelines(),
      ]);
      const versions = (await Promise.all(nextBenchmarks.map((benchmark) => api.listBenchmarkVersions(benchmark.id)))).flat();
      setExperiments(nextExperiments); setCorpora(nextCorpora); setBenchmarks(nextBenchmarks); setBenchmarkVersions(versions); setPipelines(nextPipelines); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load experiments"); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const readyVersions = useMemo(() => corpora.flatMap((corpus) => (corpus.versions ?? []).filter((version) => version.status === "ready").map((version) => ({ ...version, corpusName: corpus.name }))), [corpora]);
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

  return <><header className="topbar"><a className="brand" href="/"><span className="brand-mark">R</span><div><strong>RAGScope</strong><small>Experiment Manager</small></div></a><nav className="top-nav" aria-label="Research tools"><a href="/laboratory">Query Laboratory</a><a href="/benchmarks">Benchmarks</a><span className="phase">Controlled execution</span></nav></header>
    <main id="main" className="shell intelligence-shell"><a className="back" href="/">← Corpora</a><section className={styles.hero}><div><p className="eyebrow">Experiment Manager</p><h1>Freeze the method, then execute the matrix.</h1><p className="muted">Every question × pipeline × repetition becomes an independent QueryRun. Completed valid cells are reused when an interrupted experiment resumes.</p></div></section>{error && <div className="alert" role="alert">{error}</div>}
      <div className={styles.layout}><form className={`panel ${styles.form}`} onSubmit={create}><div><p className="eyebrow">New draft</p><h2>Configure experiment</h2><p className="muted">Only immutable corpus, benchmark, and pipeline dependencies are offered.</p></div><label>Name<input name="name" required maxLength={200} /></label><label>Research question<textarea name="research_question" required rows={4} maxLength={2000} /></label><label>Ready corpus version<select name="corpus_version_id" required defaultValue=""><option value="" disabled>Select corpus</option>{readyVersions.map((version)=><option value={version.id} key={version.id}>{version.corpusName} · {version.version_label}</option>)}</select></label><label>Frozen benchmark version<select name="benchmark_version_id" required defaultValue=""><option value="" disabled>Select benchmark</option>{frozenBenchmarks.map((version)=><option value={version.id} key={version.id}>{benchmarkName(version)} · v{version.version}</option>)}</select></label><fieldset><legend>Frozen pipelines (at least 2)</legend><div className={styles.pipelineChecks}>{frozenPipelines.map((pipeline)=><label key={pipeline.id}><input type="checkbox" checked={selectedPipelines.includes(pipeline.id)} onChange={(event)=>setSelectedPipelines((current)=>event.target.checked?[...current,pipeline.id]:current.filter((id)=>id!==pipeline.id))}/><span>{pipeline.name} v{pipeline.version}<small>{pipeline.execution_mode} · {pipeline.retrieval_mode}</small></span></label>)}</div></fieldset><div className="form-row"><label>Repetitions<input name="repetitions" type="number" min="1" max="20" defaultValue="1" required /></label><label>Code commit<input name="code_commit" minLength={7} maxLength={100} required placeholder="git commit SHA" /></label></div><label className="inline-check"><input name="stop_on_error" type="checkbox"/> Stop matrix on the first terminal failure</label><button disabled={busy || selectedPipelines.length < 2}>{busy ? "Creating draft…" : "Create draft experiment"}</button></form>
        <section className="panel" aria-labelledby="experiment-list"><div className="panel-heading"><div><p className="eyebrow">Registry</p><h2 id="experiment-list">Experiments</h2></div><span className="muted">{experiments.length} total</span></div><div className={styles.list}>{experiments.length===0?<div className="empty"><strong>No experiments yet</strong><span>Create a small pilot after freezing benchmark and pipeline versions.</span></div>:experiments.map((experiment)=>{const progress=experiment.progress; const complete=(progress?.succeeded??0)+(progress?.failed??0); const percent=progress?.total?Math.round(complete/progress.total*100):0; return <a className={styles.card} href={`/experiments/${experiment.id}`} key={experiment.id}><div><div className="panel-heading"><h2>{experiment.name}</h2><StatusBadge status={experiment.status}/></div><p>{experiment.research_question}</p><div className={styles.facts}><span>{experiment.pipeline_configuration_ids.length} pipelines</span><span>{experiment.repetitions} repetition{experiment.repetitions===1?"":"s"}</span><span>{progress?.total??"Matrix not frozen"} planned runs</span><span>Created {new Date(experiment.created_at).toLocaleDateString()}</span></div></div><div className={styles.progress}><strong>{percent}%</strong><progress max="100" value={percent}>{percent}%</progress><small>{complete}/{progress?.total??0} complete</small></div></a>;})}</div></section></div>
    </main></>;
}
