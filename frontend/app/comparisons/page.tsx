"use client";

import { FormEvent, useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { Corpus, PipelineConfiguration, QueryComparison } from "@/lib/types";
import styles from "./page.module.css";

function show(value: unknown): string {
  if (value == null) return "Unavailable";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function formatLatency(value: number | null): string {
  if (value == null) return "Unavailable";
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s`;
}

export default function ComparisonsPage() {
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [pipelines, setPipelines] = useState<PipelineConfiguration[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [comparison, setComparison] = useState<QueryComparison | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.listCorpora(), api.listPipelines()])
      .then(([nextCorpora, nextPipelines]) => {
        setCorpora(nextCorpora);
        setPipelines(nextPipelines);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load comparison controls"));
  }, []);

  async function runComparison(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selected.length < 2 || selected.length > 4) {
      setError("Select between two and four frozen pipelines.");
      return;
    }
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      setComparison(await api.createComparison({
        corpus_version_id: String(form.get("corpus_version_id")),
        question: String(form.get("question")),
        pipeline_configuration_ids: selected,
        filters: { document_ids: [], publication_years: [] },
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Comparison failed");
    } finally {
      setBusy(false);
    }
  }

  const readyVersions = corpora.flatMap((corpus) =>
    (corpus.versions ?? []).filter((version) => version.status === "ready")
      .map((version) => ({ ...version, corpusName: corpus.name })),
  );
  const frozenPipelines = pipelines.filter((pipeline) => pipeline.frozen_at !== null);

  return (
    <>
      <header className="topbar">
        <a className="brand" href="/"><span className="brand-mark">R</span><div><strong>RAGScope</strong><small>Pipeline Comparison</small></div></a>
        <nav className="top-nav" aria-label="Research tools"><a href="/laboratory">Query Laboratory</a><span className="phase">Same question · same corpus</span></nav>
      </header>
      <main id="main" className="shell">
        <a className="back" href="/">← Corpora</a>
        <section className={styles.hero}><p className="eyebrow">Pipeline Comparison</p><h1>Compare observable outcomes, not a universal winner.</h1><p className="muted">Run two to four frozen configurations against one exact question and corpus version. Evidence, ranks, context, answers, failures, latency, and cost stay aligned.</p></section>
        {error && <div className="alert" role="alert">{error}</div>}
        <section className={`panel ${styles.launcher}`} aria-labelledby="compare-heading">
          <div><p className="eyebrow">Controlled execution</p><h2 id="compare-heading">New comparison</h2><p className="muted">Each column remains a complete, independently inspectable QueryRun.</p></div>
          <form className={styles.form} onSubmit={runComparison}>
            <label>Ready corpus version<select name="corpus_version_id" required defaultValue=""><option value="" disabled>Select corpus version</option>{readyVersions.map((version) => <option key={version.id} value={version.id}>{version.corpusName} · {version.version_label}</option>)}</select></label>
            <fieldset><legend>Frozen pipelines (select 2–4)</legend><div className={styles.checks}>{frozenPipelines.map((pipeline) => <label className={styles.check} key={pipeline.id}><input type="checkbox" checked={selected.includes(pipeline.id)} disabled={!selected.includes(pipeline.id) && selected.length === 4} onChange={(event) => setSelected((current) => event.target.checked ? [...current, pipeline.id] : current.filter((id) => id !== pipeline.id))} /><span>{pipeline.name} v{pipeline.version}<small>{pipeline.retrieval_mode} · {show(pipeline.generation_configuration.model)}</small></span></label>)}</div></fieldset>
            <label>Exact shared question<textarea name="question" rows={5} maxLength={10000} required /></label>
            <button disabled={busy || selected.length < 2}>{busy ? "Running pipelines…" : `Compare ${selected.length || "selected"} pipelines`}</button>
          </form>
        </section>

        {comparison && <>
          <section className={`panel ${styles.section}`} aria-labelledby="configuration-differences"><div className="panel-heading"><div><p className="eyebrow">Frozen snapshots</p><h2 id="configuration-differences">Meaningful configuration differences</h2></div><StatusBadge status={comparison.status} /></div>
            {comparison.configuration_differences.length === 0 ? <p className="muted">The selected interpreted settings are identical.</p> : <div className={styles.scroll}><table className={styles.matrix}><thead><tr><th scope="col">Setting</th>{comparison.columns.map((column) => <th scope="col" key={column.query_run_id}>{column.pipeline.name} v{column.pipeline.version}</th>)}</tr></thead><tbody>{comparison.configuration_differences.map((difference) => <tr key={difference.key}><th scope="row">{difference.label}</th>{difference.values.map((value, index) => <td key={`${difference.key}-${comparison.columns[index].query_run_id}`}>{show(value)}</td>)}</tr>)}</tbody></table></div>}
          </section>

          <section className={`panel ${styles.section}`} aria-labelledby="evidence-flow"><p className="eyebrow">Evidence flow</p><h2 id="evidence-flow">Retrieved → fused → reranked → context</h2><div className={styles.overlap}>{comparison.evidence_overlap.map((overlap) => <span key={`${overlap.left_query_run_id}-${overlap.right_query_run_id}`}>{overlap.shared_chunk_ids.length} shared · {overlap.left_only_chunk_ids.length}/{overlap.right_only_chunk_ids.length} unique · Jaccard {overlap.jaccard == null ? "unavailable" : overlap.jaccard.toFixed(2)}</span>)}</div>
            <div className={styles.scroll}><table className={styles.matrix}><thead><tr><th scope="col">Source passage</th>{comparison.columns.map((column) => <th scope="col" key={column.query_run_id}>{column.pipeline.name}</th>)}</tr></thead><tbody>{comparison.evidence_rows.map((row) => <tr key={row.chunk_id}><td><a className={styles.trace} href={`/documents/${row.document_id}?page=${row.page_start ?? ""}&chunk=${row.chunk_id}`}>{row.document_title || "Untitled document"} · p.{row.page_start ?? "—"}</a><p>{row.text.slice(0, 180)}</p></td>{row.cells.map((cell) => <td className={cell.present ? styles.present : styles.missing} key={cell.query_run_id}>{cell.present ? <div className={styles.rank}><span>Lexical: {cell.lexical_rank ?? "—"}</span><span>Dense: {cell.dense_rank ?? "—"}</span><span>Fused: {cell.fused_rank ?? "—"}</span><span>Reranked: {cell.reranked_rank ?? "—"}</span><span>{cell.selected_for_context ? `Context ${cell.citation_id ? `[${cell.citation_id}]` : "selected"}` : cell.exclusion_reason || "Not selected"}</span></div> : "Not retrieved"}</td>)}</tr>)}</tbody></table></div>
          </section>

          <section className={styles.section} aria-labelledby="aligned-outcomes"><p className="eyebrow">Aligned outcomes</p><h2 id="aligned-outcomes">Route, context, answer, and metrics</h2><div className={styles.columns} style={{ "--column-count": comparison.columns.length } as React.CSSProperties}>{comparison.columns.map((column) => <article className={styles.column} key={column.query_run_id}><header><div><h3>{column.pipeline.name} v{column.pipeline.version}</h3><span className="muted">{column.pipeline.retrieval_mode}</span></div><StatusBadge status={column.run_status} /></header>
            {column.failure_code && <section className={styles.failure} aria-label="Infrastructure failure"><strong>{column.failure_code}</strong><p>{column.failure_message}</p></section>}
            <section><h4>Route and rewrite</h4><p><strong>Rewrite:</strong> {column.rewritten_query || "Not rewritten"}</p><p><strong>Route:</strong> {show(column.configured_route.retrieval_mode ?? column.pipeline.retrieval_mode)}</p></section>
            <section><h4>Final context</h4><div className={styles.meta}><span>{column.context_sources.filter((source) => source.selected).length} selected</span><span>{column.context_sources.filter((source) => !source.selected).length} excluded</span></div>{column.context_artifact_id && <p><a className={styles.trace} href={api.artifactContentUrl(column.context_artifact_id)} target="_blank" rel="noreferrer">Open exact generator context</a></p>}{column.context_sources.map((source) => <div className={`${styles.source} ${source.selected ? "" : styles.excluded}`} key={source.chunk_id}><a href={`/documents/${source.document_id}?page=${source.page_start ?? ""}&chunk=${source.chunk_id}`}>{source.citation_id ? `[${source.citation_id}] ` : ""}{source.document_title || "Source"} p.{source.page_start ?? "—"}</a><p>{source.text.slice(0, 150)}</p>{!source.selected && <small>Excluded: {source.exclusion_reason || "not selected"}</small>}</div>)}</section>
            <section><h4>Answer</h4><p><strong>{column.answerability || "No answerability outcome"}</strong></p><p>{column.answer || column.abstention_reason || "No answer produced."}</p>{column.limitations.length > 0 && <ul>{column.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>}</section>
            <section><h4>Claims and citations</h4>{column.claims.length === 0 ? <p className="muted">No persisted claims.</p> : column.claims.map((claim) => <div className={styles.claim} key={claim.sequence_number}><p>{claim.text}</p><small>{claim.support_status}</small>{claim.citations.map((citation) => <p key={citation.citation_id}><a href={`/documents/${citation.document_id}?page=${citation.page_number ?? ""}&chunk=${citation.chunk_id}`}>[{citation.citation_id}] p.{citation.page_number ?? "—"}</a>: {citation.referenced_text.slice(0, 120)}</p>)}</div>)}</section>
            <section><h4>Latency and cost</h4><div className={styles.meta}><span>{formatLatency(column.total_latency_ms)}</span><span>{column.input_tokens ?? "—"} input / {column.output_tokens ?? "—"} output tokens</span><span>{column.estimated_cost == null ? "Cost unavailable" : `${column.estimated_cost.toFixed(6)} ${column.cost_currency || "configured units"}`}</span></div></section>
            <section><a className={styles.trace} href={`/laboratory/${column.query_run_id}`}>Open complete observable trace →</a></section>
          </article>)}</div></section>
        </>}
      </main>
    </>
  );
}
