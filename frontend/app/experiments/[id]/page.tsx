"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { Experiment, ExperimentCostEstimate } from "@/lib/types";
import styles from "../experiments.module.css";

const money = (value: number | null, currency: string | null) => value == null ? "Unavailable" : `${value.toFixed(6)} ${currency || "configured currency"}`;

export default function ExperimentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [estimate, setEstimate] = useState<ExperimentCostEstimate | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { const next = await api.getExperiment(id); setExperiment(next); setEstimate(next.cost_estimate ?? null); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load experiment"); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (experiment?.status !== "running") return; const timer = window.setInterval(() => void load(), 3000); return () => window.clearInterval(timer); }, [experiment?.status, load]);
  async function action(name: string, execute: () => Promise<Experiment>) { setBusy(name); setError(""); try { setExperiment(await execute()); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : `Could not ${name} experiment`); } finally { setBusy(""); } }
  async function calculateEstimate() { setBusy("estimate"); setError(""); try { setEstimate(await api.estimateExperiment(id)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Cost could not be estimated"); } finally { setBusy(""); } }
  async function generateExports() { setBusy("exports"); setError(""); try { await api.generateExperimentExports(id); } catch (reason) { setError(reason instanceof Error ? reason.message : "Exports could not be generated"); } finally { setBusy(""); } }
  if (!experiment) return <main className="shell"><a className="back" href="/experiments">← Experiments</a>{error ? <div className="alert" role="alert">{error}</div> : <p aria-live="polite">Loading experiment…</p>}</main>;

  const progress = experiment.progress;
  const complete = (progress?.succeeded ?? 0) + (progress?.failed ?? 0);
  const percent = progress?.total ? Math.round(complete / progress.total * 100) : 0;
  const canResume = experiment.status === "paused";
  return <>
    <header className="topbar"><a className="brand" href="/"><span className="brand-mark">R</span><div><strong>RAGScope</strong><small>Experiment Manager</small></div></a><nav className="top-nav"><a href="/experiments">All experiments</a><a href={`/results/${id}`}>Results Dashboard</a><StatusBadge status={experiment.status}/></nav></header>
    <main id="main" className="shell intelligence-shell"><a className="back" href="/experiments">← Experiments</a>
      <section className={styles.hero}><div><p className="eyebrow">Controlled experiment</p><h1>{experiment.name}</h1><p className="muted">{experiment.research_question}</p></div><div className={styles.actions}>
        {experiment.status === "draft" && <button className="secondary" disabled={!!busy} onClick={() => void action("freeze", () => api.freezeExperiment(id))}>{busy === "freeze" ? "Freezing…" : "Freeze configuration"}</button>}
        {experiment.status === "frozen" && <button disabled={!!busy} onClick={() => void action("start", () => api.startExperiment(id))}>{busy === "start" ? "Starting…" : "Start run matrix"}</button>}
        {experiment.status === "running" && <button className="secondary" disabled={!!busy} onClick={() => void action("pause", () => api.pauseExperiment(id))}>{busy === "pause" ? "Pausing…" : "Pause safely"}</button>}
        {canResume && <button disabled={!!busy} onClick={() => void action("resume", () => api.resumeExperiment(id))}>{busy === "resume" ? "Resuming…" : "Resume missing/retryable cells"}</button>}
        <a href={`/results/${id}`}>Open results</a><a href={`/experiments/${id}/review`}>Human review queue</a><button className="secondary" disabled={!!busy} onClick={()=>void generateExports()}>{busy === "exports" ? "Generating…" : "Generate immutable exports"}</button><a href={api.experimentExportUrl(id, "csv")}>Download tidy CSV</a><a href={api.experimentExportUrl(id, "json")}>Download versioned JSON</a>
      </div></section>{error && <div className="alert" role="alert">{error}</div>}
      <div className={styles.detailGrid}><section className={`panel ${styles.progressPanel}`} aria-labelledby="progress"><div className="panel-heading"><div><p className="eyebrow">Execution</p><h2 id="progress">Run progress</h2></div><StatusBadge status={experiment.status}/></div>
        <progress max="100" value={percent} aria-label={`${percent}% complete`}>{percent}%</progress>
        <div className={styles.countGrid}><div><span>Total matrix</span><strong>{progress?.total ?? 0}</strong></div><div><span>Completed</span><strong>{complete}</strong></div><div><span>Successful</span><strong>{progress?.succeeded ?? 0}</strong></div><div><span>Terminal failures</span><strong>{progress?.failed ?? 0}</strong></div><div><span>Running</span><strong>{progress?.running ?? 0}</strong></div><div><span>Planned / retryable</span><strong>{(progress?.planned ?? 0) + (progress?.retryable ?? 0)}</strong></div></div>
        <p className="muted" aria-live="polite">{complete} of {progress?.total ?? 0} cells complete. Resume skips already completed valid QueryRuns.</p>
        <table className={styles.runMatrix}><tbody><tr><th>Corpus snapshot</th><td><code>{experiment.corpus_version_id}</code></td></tr><tr><th>Benchmark snapshot</th><td><code>{experiment.benchmark_version_id}</code></td></tr><tr><th>Pipeline snapshots</th><td>{experiment.pipeline_configuration_ids.length}</td></tr><tr><th>Repetitions</th><td>{experiment.repetitions}</td></tr><tr><th>Code commit</th><td><code>{experiment.code_commit}</code></td></tr><tr><th>Configuration hash</th><td><code>{experiment.configuration_hash ?? "Draft—not frozen"}</code></td></tr></tbody></table>
        <details><summary>Frozen dependency audit</summary><pre className={styles.audit}>{JSON.stringify(experiment.dependency_snapshot, null, 2)}</pre></details>
      </section><aside className="panel"><div className="panel-heading"><div><p className="eyebrow">Preflight</p><h2>Cost estimate</h2></div><button className="secondary compact-button" disabled={!!busy} onClick={() => void calculateEstimate()}>{busy === "estimate" ? "Estimating…" : "Calculate"}</button></div>
        {estimate ? <div className={styles.cost}><div><span>Maximum expected total</span><strong>{money(estimate.maximum_expected_cost, estimate.currency)}</strong></div><div><span>Planned cells</span><strong>{estimate.run_count}</strong></div>{estimate.per_pipeline.map((item) => <div key={item.pipeline_configuration_id}><span>Pipeline {item.pipeline_configuration_id.slice(0, 8)}</span><strong>{money(item.maximum_expected_cost, item.currency)}</strong></div>)}{!estimate.cost_fully_configured && <div className={styles.warning} role="note"><strong>Estimate incomplete</strong><p>One or more model rates are missing. Unavailable cost is not treated as zero.</p></div>}</div> : <p className="muted">Estimate before freezing. If pricing is not configured, cost remains unavailable.</p>}
      </aside></div>
    </main>
  </>;
}
