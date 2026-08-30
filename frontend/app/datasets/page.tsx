"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ResearchHeader } from "@/components/ResearchHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { api, isOperationAccepted } from "@/lib/api";
import type { BackgroundJob, DatasetRecord } from "@/lib/types";
import styles from "./datasets.module.css";

function listItems(value: Awaited<ReturnType<typeof api.listDatasetRecords>>): DatasetRecord[] {
  return Array.isArray(value) ? value : value.items;
}

function saveJson(filename: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function DatasetCatalogPage() {
  const [records, setRecords] = useState<DatasetRecord[]>([]);
  const [filters, setFilters] = useState({ search: "", domain: "", modality: "", task_type: "", language: "", license: "", review_status: "" });
  const [selected, setSelected] = useState<string[]>([]);
  const [extractDocumentId, setExtractDocumentId] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [extractionJobId, setExtractionJobId] = useState("");
  const [extractionJob, setExtractionJob] = useState<BackgroundJob | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRecords(listItems(await api.listDatasetRecords(filters)));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Dataset catalog could not be loaded");
    } finally { setLoading(false); }
  }, [filters]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setExtractDocumentId(new URLSearchParams(window.location.search).get("document") ?? ""); }, []);
  useEffect(() => {
    if (!extractionJobId) return;
    let active = true;
    const poll = async () => {
      try {
        const next = await api.getJob(extractionJobId);
        if (!active) return;
        setExtractionJob(next);
        if (["succeeded", "failed", "cancelled"].includes(next.status)) {
          window.clearInterval(timer);
          if (next.status === "succeeded") {
            setMessage(`Dataset extraction ${next.id} completed. Validated records are now shown in the catalog.`);
            await load();
          } else {
            setError(next.error_message ?? `Dataset extraction ${next.status}.`);
          }
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Extraction status could not be refreshed");
      }
    };
    const timer = window.setInterval(() => void poll(), 2000);
    void poll();
    return () => { active = false; window.clearInterval(timer); };
  }, [extractionJobId, load]);

  const facets = useMemo(() => ({
    domains: [...new Set(records.map((item) => item.domain).filter(Boolean))] as string[],
    modalities: [...new Set(records.flatMap((item) => item.modalities ?? []))],
    tasks: [...new Set(records.flatMap((item) => item.task_types ?? []))],
    languages: [...new Set(records.flatMap((item) => item.languages ?? []))],
    licenses: [...new Set(records.map((item) => item.license).filter(Boolean))] as string[],
  }), [records]);

  const summary = useMemo(() => ({
    approved: records.filter((record) => record.review_status === "approved").length,
    inReview: records.filter((record) => record.review_status === "in_review").length,
    evidence: records.reduce((count, record) => count + (record.field_evidence ?? record.evidence ?? []).length, 0),
  }), [records]);

  async function extract(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy("extract"); setError(""); setMessage("");
    try {
      const model = String(data.get("model") ?? "").trim();
      const job = await api.extractDatasets(String(data.get("document_id")), {
        strategy: String(data.get("strategy")) as "baseline" | "retrieval_assisted",
        provider: String(data.get("provider")) as "fake" | "gemini" | "openai_compatible",
        ...(model ? { model } : {}),
      });
      const jobId = "job_id" in job ? job.job_id : job.id;
      if (!jobId) throw new Error("The extraction operation did not return a job identifier.");
      setExtractionJobId(jobId);
      setExtractionJob(null);
      if (isOperationAccepted(job)) setMessage(`Dataset extraction ${jobId} was accepted. This page will refresh when the worker finishes.`);
      else {
        setMessage(`Dataset extraction ${jobId} is ${job.status}.`);
        if (job.status === "succeeded") await load();
      }
      form.reset(); setExtractDocumentId("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Extraction could not be started"); }
    finally { setBusy(""); }
  }

  function choose(id: string, checked: boolean) {
    setSelected((current) => checked ? [...current, id].slice(-4) : current.filter((item) => item !== id));
  }

  const extractionActive = Boolean(extractionJobId) && !["succeeded", "failed", "cancelled"].includes(extractionJob?.status ?? "queued");

  return (
    <>
      <ResearchHeader context="Dataset intelligence" />
      <main id="main" className={`${styles.page} shell intelligence-shell`} aria-busy={loading}>
        <section className="page-title">
          <div><p className="eyebrow">Human-reviewed catalog</p><h1>Dataset intelligence</h1><p>Extract structured dataset facts, inspect field-level provenance, and keep model suggestions separate from human ground truth.</p></div>
          <div className="action-bar">
            <button className="secondary" onClick={() => saveJson("ragscope-dataset-catalog.json", records)}>Export JSON</button>
            <a className={`button-link ${selected.length < 2 ? "disabled-link" : ""}`} aria-disabled={selected.length < 2} href={selected.length >= 2 ? `/datasets/compare?ids=${selected.join(",")}` : undefined}>Compare {selected.length || ""}</a>
          </div>
        </section>
        {error && <div className="alert" role="alert">{error}</div>}
        {message && <div className="success-callout" role="status" aria-live="polite"><strong>{message}</strong>{extractionJob && <span>{extractionJob.status} · {extractionJob.progress_current}/{extractionJob.progress_total ?? "?"}</span>}{extractionActive && <span>You may navigate elsewhere; the PostgreSQL worker continues independently.</span>}</div>}
        <section className={styles.stats} aria-label="Dataset catalog summary">
          <div className={styles.stat}><span>Visible records</span><strong>{loading ? "—" : records.length}</strong></div>
          <div className={styles.stat}><span>Approved</span><strong>{loading ? "—" : summary.approved}</strong></div>
          <div className={styles.stat}><span>In review</span><strong>{loading ? "—" : summary.inReview}</strong></div>
          <div className={styles.stat}><span>Evidence links</span><strong>{loading ? "—" : summary.evidence}</strong></div>
        </section>
        <div className="intelligence-layout">
          <aside className="panel filter-panel">
            <p className="eyebrow">Structured filters</p><h2>Find records</h2>
            <label>Search<input value={filters.search} onChange={(event) => setFilters({ ...filters, search: event.target.value })} placeholder="Name or description" /></label>
            {([
              ["domain", "Domain", facets.domains], ["modality", "Modality", facets.modalities], ["task_type", "Task type", facets.tasks],
              ["language", "Language", facets.languages], ["license", "License", facets.licenses],
            ] as const).map(([key, label, values]) => <label key={key}>{label}<select value={filters[key]} onChange={(event) => setFilters({ ...filters, [key]: event.target.value })}><option value="">All</option>{values.map((value) => <option key={value}>{value}</option>)}</select></label>)}
            <label>Review state<select value={filters.review_status} onChange={(event) => setFilters({ ...filters, review_status: event.target.value })}><option value="">All</option><option value="unreviewed">Unreviewed</option><option value="in_review">In review</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select></label>
            <button className="secondary" onClick={() => setFilters({ search: "", domain: "", modality: "", task_type: "", language: "", license: "", review_status: "" })}>Clear filters</button>
            <hr />
            <p className="eyebrow">New extraction</p>
            <form className="stack-form" onSubmit={extract}>
              <label>Document ID<input name="document_id" required value={extractDocumentId} onChange={(event) => setExtractDocumentId(event.target.value)} placeholder="Source document UUID" /></label>
              <label>Strategy<select name="strategy"><option value="baseline">Whole document baseline</option><option value="retrieval_assisted">Retrieval-assisted</option></select></label>
              <label>Generation provider<select name="provider"><option value="fake">Deterministic fake</option><option value="gemini">Gemini (environment key)</option><option value="openai_compatible">OpenAI-compatible</option></select></label>
              <label>Model (optional)<input name="model" placeholder="gemini-2.5-flash" /></label>
              <button disabled={Boolean(busy) || extractionActive}>{busy ? "Starting…" : extractionActive ? "Extraction in progress…" : "Start extraction"}</button>
              <small className="muted">Uploaded documents are untrusted evidence, never instructions.</small>
            </form>
          </aside>
          <section className="panel">
            <div className="panel-heading"><div><p className="eyebrow">Catalog</p><h2>{records.length} records</h2></div><span>Select 2–4 to compare</span></div>
            {loading ? <div className={styles.loading} role="status">Loading reviewed dataset records…</div> : records.length === 0 ? <div className="empty"><strong>No matching dataset records</strong><span>Run extraction from a parsed document or change the filters.</span></div> : <div className="dataset-card-list">{records.map((record) => (
              <article className="dataset-card" key={record.id}>
                <label className="compare-check"><input type="checkbox" checked={selected.includes(record.id)} disabled={!selected.includes(record.id) && selected.length === 4} onChange={(event) => choose(record.id, event.target.checked)} /> Compare</label>
                <a href={`/datasets/${record.id}`}><div className="dataset-card-title"><div><h3>{record.name ?? <em>Not stated</em>}</h3><p>{record.description ?? "No description stated in the source."}</p></div><StatusBadge status={record.review_status} /></div>
                  <dl className="dataset-facts"><div><dt>Domain</dt><dd>{record.domain ?? "Not stated"}</dd></div><div><dt>Modalities</dt><dd>{record.modalities?.join(", ") || "Not stated"}</dd></div><div><dt>Tasks</dt><dd>{record.task_types?.join(", ") || "Not stated"}</dd></div><div><dt>License</dt><dd>{record.license ?? "Not stated"}</dd></div></dl>
                </a>
              </article>
            ))}</div>}
          </section>
        </div>
      </main>
    </>
  );
}
