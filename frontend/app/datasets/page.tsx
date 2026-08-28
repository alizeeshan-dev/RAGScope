"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ResearchHeader } from "@/components/ResearchHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { DatasetRecord } from "@/lib/types";

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

  const load = useCallback(async () => {
    try {
      setRecords(listItems(await api.listDatasetRecords(filters)));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Dataset catalog could not be loaded");
    }
  }, [filters]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setExtractDocumentId(new URLSearchParams(window.location.search).get("document") ?? ""); }, []);

  const facets = useMemo(() => ({
    domains: [...new Set(records.map((item) => item.domain).filter(Boolean))] as string[],
    modalities: [...new Set(records.flatMap((item) => item.modalities ?? []))],
    tasks: [...new Set(records.flatMap((item) => item.task_types ?? []))],
    languages: [...new Set(records.flatMap((item) => item.languages ?? []))],
    licenses: [...new Set(records.map((item) => item.license).filter(Boolean))] as string[],
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
      setMessage(`Extraction job ${jobId} is ${job.status}. The catalog will show validated records when processing finishes.`);
      form.reset(); setExtractDocumentId("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Extraction could not be started"); }
    finally { setBusy(""); }
  }

  function choose(id: string, checked: boolean) {
    setSelected((current) => checked ? [...current, id].slice(-4) : current.filter((item) => item !== id));
  }

  return (
    <>
      <ResearchHeader context="Dataset intelligence" />
      <main id="main" className="shell intelligence-shell">
        <section className="page-title">
          <div><p className="eyebrow">Human-reviewed catalog</p><h1>Dataset intelligence</h1><p>Extract structured dataset facts, inspect field-level provenance, and keep model suggestions separate from human ground truth.</p></div>
          <div className="action-bar">
            <button className="secondary" onClick={() => saveJson("ragscope-dataset-catalog.json", records)}>Export JSON</button>
            <a className={`button-link ${selected.length < 2 ? "disabled-link" : ""}`} aria-disabled={selected.length < 2} href={selected.length >= 2 ? `/datasets/compare?ids=${selected.join(",")}` : undefined}>Compare {selected.length || ""}</a>
          </div>
        </section>
        {error && <div className="alert" role="alert">{error}</div>}
        {message && <div className="success-callout" role="status">{message}</div>}
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
              <button disabled={Boolean(busy)}>{busy ? "Starting…" : "Start extraction"}</button>
              <small className="muted">Uploaded documents are untrusted evidence, never instructions.</small>
            </form>
          </aside>
          <section className="panel">
            <div className="panel-heading"><div><p className="eyebrow">Catalog</p><h2>{records.length} records</h2></div><span>Select 2–4 to compare</span></div>
            {records.length === 0 ? <div className="empty"><strong>No matching dataset records</strong><span>Run extraction from a parsed document or change the filters.</span></div> : <div className="dataset-card-list">{records.map((record) => (
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
