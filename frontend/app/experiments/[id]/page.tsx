"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { StatusBadge } from "@/components/StatusBadge";
import { api, isOperationAccepted } from "@/lib/api";
import type { BackgroundJob, Experiment, ExperimentCostEstimate, OperationAccepted } from "@/lib/types";
import styles from "../experiments.module.css";

const money = (value: number | null, currency: string | null) => value == null ? "Unavailable" : `${value.toFixed(6)} ${currency || "configured currency"}`;

export default function ExperimentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [estimate, setEstimate] = useState<ExperimentCostEstimate | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [operation, setOperation] = useState<OperationAccepted | null>(null);
  const [operationJob, setOperationJob] = useState<BackgroundJob | null>(null);
  const [operationMessage, setOperationMessage] = useState("");
  const load = useCallback(async () => {
    try { const next = await api.getExperiment(id); setExperiment(next); setEstimate(next.cost_estimate ?? null); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load experiment"); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (experiment?.status !== "running") return; const timer = window.setInterval(() => void load(), 3000); return () => window.clearInterval(timer); }, [experiment?.status, load]);
  useEffect(() => {
    if (!operation) return;
    let active = true;
    const poll = async () => {
      try {
        const next = await api.getJob(operation.job_id);
        if (!active) return;
        setOperationJob(next);
        await load();
        if (["succeeded", "failed", "cancelled"].includes(next.status)) {
          window.clearInterval(timer);
          if (next.status === "succeeded") setOperationMessage(`${operation.job_type.replaceAll("_", " ")} completed.`);
          else setError(next.error_message ?? `${operation.job_type.replaceAll("_", " ")} ${next.status}.`);
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Operation status could not be refreshed");
      }
    };
    const timer = window.setInterval(() => void poll(), 2000);
    void poll();
    return () => { active = false; window.clearInterval(timer); };
  }, [operation, load]);
  async function action(name: string, execute: () => Promise<Experiment>) { setBusy(name); setError(""); try { setExperiment(await execute()); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : `Could not ${name} experiment`); } finally { setBusy(""); } }
  async function launchOperation(name: string, execute: () => ReturnType<typeof api.startExperiment> | ReturnType<typeof api.resumeExperiment> | ReturnType<typeof api.generateExperimentExports>) {
    setBusy(name); setError(""); setOperationMessage("");
    try {
      const result = await execute();
      if (isOperationAccepted(result)) {
        setOperation(result); setOperationJob(null);
        setOperationMessage(`${result.job_type.replaceAll("_", " ")} accepted. You may leave this page while the worker continues.`);
        await load();
      } else {
        setExperiment(result); await load();
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : `Could not ${name} experiment`); }
    finally { setBusy(""); }
  }
  async function calculateEstimate() { setBusy("estimate"); setError(""); try { setEstimate(await api.estimateExperiment(id)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Cost could not be estimated"); } finally { setBusy(""); } }
  async function cancelOperation() {
    if (!operation) return;
    setBusy("cancel"); setError("");
    try { setOperationJob(await api.cancelJob(operation.job_id)); setOperationMessage("Cancellation requested. Completed run cells remain preserved."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Cancellation could not be requested"); }
    finally { setBusy(""); }
  }
  if (!experiment) return <main className={`shell ${styles.page}`}><a className="back" href="/experiments">← Experiments</a>{error ? <div className="alert" role="alert">{error}</div> : <p aria-live="polite">Loading experiment…</p>}</main>;

  const progress = experiment.progress;
  const complete = (progress?.succeeded ?? 0) + (progress?.failed ?? 0);
  const percent = progress?.total ? Math.round(complete / progress.total * 100) : 0;
  const canResume = experiment.status === "paused";
  const operationActive = operation !== null && !["succeeded", "failed", "cancelled"].includes(operationJob?.status ?? operation.status);
  return <main id="main" className={`shell ${styles.page}`}><a className="back" href="/experiments">← Experiment registry</a>
      <section className={styles.hero}><div><p className="eyebrow">Controlled experiment</p><h1>{experiment.name}</h1><p className="muted">{experiment.research_question}</p></div><div className={styles.actions}>
        {experiment.status === "draft" && <button className="secondary" disabled={!!busy} onClick={() => void action("freeze", () => api.freezeExperiment(id))}>{busy === "freeze" ? "Freezing…" : "Freeze configuration"}</button>}
        {experiment.status === "frozen" && <button disabled={!!busy || operationActive} onClick={() => void launchOperation("start", () => api.startExperiment(id))}>{busy === "start" ? "Submitting…" : operationActive ? "Run matrix queued…" : "Start run matrix"}</button>}
        {experiment.status === "running" && <button className="secondary" disabled={!!busy} onClick={() => void action("pause", () => api.pauseExperiment(id))}>{busy === "pause" ? "Pausing…" : "Pause safely"}</button>}
        {canResume && <button disabled={!!busy || operationActive} onClick={() => void launchOperation("resume", () => api.resumeExperiment(id))}>{busy === "resume" ? "Submitting…" : operationActive ? "Resume queued…" : "Resume missing/retryable cells"}</button>}
        <a href={`/results/${id}`}>Open results</a><a href={`/experiments/${id}/review`}>Human review queue</a><button className="secondary" disabled={!!busy || operationActive} onClick={()=>void launchOperation("exports", () => api.generateExperimentExports(id))}>{busy === "exports" ? "Submitting…" : "Generate immutable exports"}</button><a href={api.experimentExportUrl(id, "csv")}>Download tidy CSV</a><a href={api.experimentExportUrl(id, "json")}>Download versioned JSON</a>
      </div></section>{error && <div className="alert" role="alert">{error}</div>}
      {operation && <section className="success-callout" role="status" aria-live="polite"><strong>{operation.job_type.replaceAll("_", " ")}</strong><span>{operationMessage}</span><span>{operationJob ? `${operationJob.status} · ${operationJob.progress_current}/${operationJob.progress_total ?? "?"}` : operation.status}</span>{operationActive && <button className="secondary compact-button" disabled={busy === "cancel"} onClick={() => void cancelOperation()}>{busy === "cancel" ? "Requesting…" : "Cancel operation"}</button>}</section>}
      <div className={styles.detailGrid}><section className={`panel ${styles.progressPanel}`} aria-labelledby="progress"><div className="panel-heading"><div><p className="eyebrow">Execution</p><h2 id="progress">Run progress</h2></div><StatusBadge status={experiment.status}/></div>
        <progress max="100" value={percent} aria-label={`${percent}% complete`}>{percent}%</progress>
        <div className={styles.countGrid}><div><span>Total matrix</span><strong>{progress?.total ?? 0}</strong></div><div><span>Completed</span><strong>{complete}</strong></div><div><span>Successful</span><strong>{progress?.succeeded ?? 0}</strong></div><div><span>Terminal failures</span><strong>{progress?.failed ?? 0}</strong></div><div><span>Running</span><strong>{progress?.running ?? 0}</strong></div><div><span>Planned / retryable</span><strong>{(progress?.planned ?? 0) + (progress?.retryable ?? 0)}</strong></div></div>
        <p className="muted" aria-live="polite">{complete} of {progress?.total ?? 0} cells complete. Resume skips already completed valid QueryRuns.</p>
        <table className={styles.runMatrix}><tbody><tr><th>Corpus snapshot</th><td><code>{experiment.corpus_version_id}</code></td></tr><tr><th>Benchmark snapshot</th><td><code>{experiment.benchmark_version_id}</code></td></tr><tr><th>Pipeline snapshots</th><td>{experiment.pipeline_configuration_ids.length}</td></tr><tr><th>Repetitions</th><td>{experiment.repetitions}</td></tr><tr><th>Code commit</th><td><code>{experiment.code_commit}</code></td></tr><tr><th>Configuration hash</th><td><code>{experiment.configuration_hash ?? "Draft—not frozen"}</code></td></tr></tbody></table>
        <details><summary>Frozen dependency audit</summary><pre className={styles.audit}>{JSON.stringify(experiment.dependency_snapshot, null, 2)}</pre></details>
      </section><aside className={`panel ${styles.costPanel}`}><div className="panel-heading"><div><p className="eyebrow">Preflight</p><h2>Cost estimate</h2></div><button className="secondary compact-button" disabled={!!busy} onClick={() => void calculateEstimate()}>{busy === "estimate" ? "Estimating…" : "Calculate"}</button></div>
        {estimate ? <div className={styles.cost}><div><span>Maximum expected total</span><strong>{money(estimate.maximum_expected_cost, estimate.currency)}</strong></div><div><span>Planned cells</span><strong>{estimate.run_count}</strong></div>{estimate.per_pipeline.map((item) => <div key={item.pipeline_configuration_id}><span>Pipeline {item.pipeline_configuration_id.slice(0, 8)}</span><strong>{money(item.maximum_expected_cost, item.currency)}</strong></div>)}{!estimate.cost_fully_configured && <div className={styles.warning} role="note"><strong>Estimate incomplete</strong><p>One or more model rates are missing. Unavailable cost is not treated as zero.</p></div>}</div> : <p className="muted">Estimate before freezing. If pricing is not configured, cost remains unavailable.</p>}
      </aside></div>
    </main>;
}
