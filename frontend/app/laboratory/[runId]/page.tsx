"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { EvaluationBundle, FailureAttribution, HumanReviewQueueItem, ObservableTraceExport, QueryRun, RunClaim, RunContextSource, RunRetrievalResult, TraceSpan } from "@/lib/types";

type RankedChunk = {
  chunkId: string;
  documentId: string;
  documentTitle: string | null;
  pageStart: number | null;
  pageEnd: number | null;
  sectionPath: string[];
  text: string;
  lexicalRank: number | null;
  lexicalScore: number | null;
  denseRank: number | null;
  denseScore: number | null;
  fusedRank: number | null;
  fusedScore: number | null;
  rerankedRank: number | null;
  rerankerScore: number | null;
  selected: boolean;
  retrievers: string[];
};

const numberOrNull = (value: unknown): number | null => typeof value === "number" ? value : null;
const textOr = (value: unknown, fallback = "—"): string => typeof value === "string" && value.length > 0 ? value : fallback;
const formatMs = (value: number | null | undefined) => value == null ? "Unavailable" : value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
const formatScore = (value: number | null) => value == null ? "—" : value.toFixed(4);

function mergeRankedRows(rows: RunRetrievalResult[]): RankedChunk[] {
  const grouped = new Map<string, RankedChunk>();
  for (const row of rows) {
    const current = grouped.get(row.chunk_id) ?? {
      chunkId: row.chunk_id, documentId: row.document_id, documentTitle: row.document_title ?? null,
      pageStart: row.page_start ?? null, pageEnd: row.page_end ?? null, sectionPath: row.section_path ?? [], text: row.text,
      lexicalRank: null, lexicalScore: null, denseRank: null, denseScore: null,
      fusedRank: row.fused_rank, fusedScore: row.fusion_score,
      rerankedRank: row.reranked_rank, rerankerScore: row.reranker_score,
      selected: row.selected_for_context, retrievers: [],
    };
    if (row.retriever_type === "lexical") {
      current.lexicalRank = row.original_rank;
      current.lexicalScore = row.original_score;
    }
    if (row.retriever_type === "dense") {
      current.denseRank = row.original_rank;
      current.denseScore = row.original_score;
    }
    current.fusedRank ??= row.fused_rank;
    current.fusedScore ??= row.fusion_score;
    current.rerankedRank ??= row.reranked_rank;
    current.rerankerScore ??= row.reranker_score;
    current.selected ||= row.selected_for_context;
    if (!current.retrievers.includes(row.retriever_type)) current.retrievers.push(row.retriever_type);
    grouped.set(row.chunk_id, current);
  }
  return [...grouped.values()].sort((left, right) =>
    (left.rerankedRank ?? left.fusedRank ?? left.lexicalRank ?? left.denseRank ?? Number.MAX_SAFE_INTEGER)
      - (right.rerankedRank ?? right.fusedRank ?? right.lexicalRank ?? right.denseRank ?? Number.MAX_SAFE_INTEGER),
  );
}

function SummaryValue({ label, value }: { label: string; value: string }) {
  return <div className="lab-stat"><span>{label}</span><strong>{value}</strong></div>;
}

function StageTimeline({ spans }: { spans: TraceSpan[] }) {
  const ordered = [...spans].sort((a, b) => a.sequence_number - b.sequence_number);
  const roots = new Set(ordered.filter((span) => span.parent_span_id === null).map((span) => span.id));
  return <ol className="trace-timeline" aria-label="Pipeline stages in execution order">{ordered.map((span) => {
    const child = span.parent_span_id !== null && roots.has(span.parent_span_id);
    const inputKind = typeof span.input_summary.data_kind === "string" ? span.input_summary.data_kind : null;
    const outputKind = typeof span.output_summary.data_kind === "string" ? span.output_summary.data_kind : null;
    return <li key={span.id} className={child ? "child-span" : ""}>
      <div className="timeline-index" aria-hidden="true">{span.sequence_number}</div>
      <div><div className="timeline-title"><strong>{span.name}</strong><StatusBadge status={span.status} /></div><span>{formatMs(span.latency_ms)}{span.error_code ? ` · ${span.error_code}` : ""}</span>{(inputKind || outputKind) && <div className="data-kind-list" aria-label="Data provenance">{inputKind && <span>Input: {inputKind.replaceAll("_", " ")}</span>}{outputKind && <span>Output: {outputKind.replaceAll("_", " ")}</span>}</div>}</div>
      <details><summary>Observable stage details</summary><dl className="trace-detail"><dt>Type</dt><dd>{span.span_type}</dd><dt>Input summary</dt><dd><pre>{JSON.stringify(span.input_summary, null, 2)}</pre></dd><dt>Output summary</dt><dd><pre>{JSON.stringify(span.output_summary, null, 2)}</pre></dd><dt>Configuration</dt><dd><pre>{JSON.stringify(span.configuration_snapshot, null, 2)}</pre></dd>{span.artifact_ids.length > 0 && <><dt>Artifacts</dt><dd>{span.artifact_ids.join(", ")}</dd></>}</dl></details>
    </li>;
  })}</ol>;
}

function CitationDetails({ claim }: { claim: RunClaim }) {
  return <article className="lab-claim">
    <div className="claim-heading"><span>Claim {claim.sequence_number}</span><StatusBadge status={claim.support_status} /></div>
    <p>{claim.claim_text}</p>
    {claim.citations.length === 0 ? <small>No citation attached.</small> : claim.citations.map((citation) => <details className="citation-detail" key={citation.id}>
      <summary>[{citation.citation_id}] View cited source passage</summary>
      <blockquote>{citation.referenced_text}</blockquote>
      <a className="source-link" href={`/documents/${citation.document_id}?page=${citation.page_number ?? ""}&chunk=${citation.chunk_id}`}>
        Open Document Inspector · page {citation.page_number ?? "unavailable"} ↗
      </a>
      <dl><dt>Document</dt><dd><code>{citation.document_id}</code></dd><dt>Chunk</dt><dd><code>{citation.chunk_id}</code></dd></dl>
    </details>)}
  </article>;
}

const failureLabels = [
  "PARSE_MISSING_CONTENT", "PARSE_READING_ORDER", "PARSE_TABLE_FAILURE", "PARSE_ENCODING_FAILURE",
  "RETRIEVAL_TOTAL_MISS", "RETRIEVAL_PARTIAL_EVIDENCE", "RETRIEVAL_WRONG_DOCUMENT", "RETRIEVAL_DISTRACTOR_DOMINANCE",
  "RERANK_REQUIRED_EVIDENCE_DEMOTED", "RERANK_IRRELEVANT_EVIDENCE_PROMOTED",
  "CONTEXT_REQUIRED_EVIDENCE_DROPPED", "CONTEXT_EXCESSIVE_REDUNDANCY", "CONTEXT_CONFLICT_UNMANAGED",
  "GENERATION_IGNORED_EVIDENCE", "GENERATION_UNSUPPORTED_CLAIM", "GENERATION_CONTRADICTED_EVIDENCE", "GENERATION_INCOMPLETE_ANSWER", "GENERATION_FAILED_TO_ABSTAIN", "GENERATION_INCORRECT_ABSTENTION",
  "CITATION_MISSING", "CITATION_INVALID_ID", "CITATION_IRRELEVANT", "CITATION_PARTIAL_SUPPORT",
  "MODEL_PROVIDER_FAILURE", "EMBEDDING_PROVIDER_FAILURE", "DATABASE_FAILURE", "TIMEOUT", "INVALID_STRUCTURED_OUTPUT",
];

function FailureReview({ runId, attribution, onSaved }: { runId: string; attribution: FailureAttribution; onSaved: () => Promise<void> }) {
  const [label, setLabel] = useState(attribution.human_override_label ?? attribution.automatic_label);
  const [note, setNote] = useState(attribution.human_override_note ?? "");
  const [saving, setSaving] = useState(false);
  async function save() {
    if (!note.trim()) return;
    setSaving(true);
    try { await api.reviewFailureAttribution(runId, attribution.id, { label, reviewer_note: note }); await onSaved(); }
    finally { setSaving(false); }
  }
  return <details><summary>Human review</summary><div className="query-grid"><label>Corrected taxonomy label<select value={label} onChange={(event) => setLabel(event.target.value)}>{failureLabels.map((value) => <option key={value}>{value}</option>)}</select></label><label>Reviewer note<textarea value={note} onChange={(event) => setNote(event.target.value)} rows={3} /></label></div><button className="secondary" disabled={saving || !note.trim()} onClick={() => void save()}>{saving ? "Saving…" : "Preserve human correction"}</button></details>;
}

const humanMetricFields = [
  ["answer_correctness", "Answer correctness"],
  ["answer_completeness", "Answer completeness"],
  ["appropriate_abstention", "Appropriate abstention"],
  ["false_premise_recognition", "False-premise recognition"],
  ["claim_support_rate", "Claim support rate"],
  ["citation_precision", "Citation precision"],
] as const;

type HumanMetricName = typeof humanMetricFields[number][0];

function HumanMetricReview({ runId, evaluation, onSaved }: { runId: string; evaluation: EvaluationBundle | null; onSaved: () => Promise<void> }) {
  const current = useMemo(() => {
    const values = new Map<HumanMetricName, string>();
    const humanRows = [...(evaluation?.metrics ?? [])]
      .filter((metric) => metric.evaluation_method === "human")
      .sort((left, right) => right.created_at.localeCompare(left.created_at));
    for (const metric of humanRows) {
      if (humanMetricFields.some(([name]) => name === metric.metric_name) && !values.has(metric.metric_name as HumanMetricName)) {
        values.set(metric.metric_name as HumanMetricName, metric.metric_value == null ? "" : String(metric.metric_value));
      }
    }
    return Object.fromEntries(humanMetricFields.map(([name]) => [name, values.get(name) ?? ""])) as Record<HumanMetricName, string>;
  }, [evaluation]);
  const [values, setValues] = useState<Record<HumanMetricName, string>>(current);
  const [reviewer, setReviewer] = useState("human-reviewer");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => { setValues(current); }, [current]);

  async function save() {
    const populated = humanMetricFields.filter(([name]) => values[name] !== "");
    if (!reviewer.trim() || populated.length === 0) return;
    setSaving(true); setMessage("");
    try {
      for (const [name] of populated) {
        await api.addHumanEvaluation(runId, {
          metric_name: name,
          metric_value: Number(values[name]),
          reviewer_label: reviewer.trim(),
          reviewer_note: note.trim() || undefined,
        });
      }
      await api.evaluateRun(runId);
      await onSaved();
      setMessage(`${populated.length} human labels preserved separately from automatic judgments.`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Human labels could not be saved");
    } finally { setSaving(false); }
  }

  return <div className="exact-context">
    <div><h3>Human evaluation review</h3><p className="muted">Enter reviewed scores from 0 to 1. Saving creates versioned human metrics; automatic judgments remain unchanged.</p></div>
    <div className="query-grid">
      {humanMetricFields.map(([name, label]) => <label key={name}>{label}<input aria-label={label} type="number" min="0" max="1" step="0.01" value={values[name]} onChange={(event) => setValues((stored) => ({ ...stored, [name]: event.target.value }))} /></label>)}
      <label>Reviewer label<input value={reviewer} maxLength={255} onChange={(event) => setReviewer(event.target.value)} /></label>
      <label>Reviewer note<textarea aria-label="Human evaluation reviewer note" value={note} maxLength={20000} rows={3} onChange={(event) => setNote(event.target.value)} /></label>
    </div>
    <button className="secondary" disabled={saving || !reviewer.trim() || humanMetricFields.every(([name]) => values[name] === "")} onClick={() => void save()}>{saving ? "Preserving labels…" : "Preserve human labels"}</button>
    {message && <p aria-live="polite">{message}</p>}
  </div>;
}

export default function LaboratoryRunPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<QueryRun | null>(null);
  const [trace, setTrace] = useState<ObservableTraceExport | null>(null);
  const [retrieval, setRetrieval] = useState<RunRetrievalResult[]>([]);
  const [claims, setClaims] = useState<RunClaim[]>([]);
  const [context, setContext] = useState<RunContextSource[]>([]);
  const [evaluation, setEvaluation] = useState<EvaluationBundle | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [exactContext, setExactContext] = useState<string | null>(null);
  const [loadingContext, setLoadingContext] = useState(false);
  const [reviewNavigation, setReviewNavigation] = useState<{ experimentId: string; position: number; total: number; previous: HumanReviewQueueItem | null; next: HumanReviewQueueItem | null } | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [nextRun, nextTrace, nextRetrieval, nextClaims, nextContext, nextEvaluation] = await Promise.all([
        api.getQueryRun(runId), api.getRunTrace(runId), api.getRunRetrieval(runId), api.getRunClaims(runId), api.getRunContext(runId), api.getRunEvaluation(runId),
      ]);
      setRun(nextRun); setTrace(nextTrace); setRetrieval(nextRetrieval); setClaims(nextClaims); setContext(nextContext); setEvaluation(nextEvaluation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The observable trace could not be loaded");
    }
  }, [runId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const parameters = new URLSearchParams(window.location.search);
    const experimentId = parameters.get("experiment");
    const position = Number(parameters.get("position") ?? "0");
    const total = Number(parameters.get("total") ?? "0");
    if (!experimentId || !Number.isInteger(position) || position < 1) return;
    const offset = Math.max(position - 2, 0);
    void api.getHumanReviewQueue(experimentId, offset, 3).then((page) => {
      const index = page.items.findIndex((item) => item.query_run_id === runId);
      setReviewNavigation({
        experimentId,
        position,
        total: total || page.remaining,
        previous: index > 0 ? page.items[index - 1] : null,
        next: index >= 0 && index + 1 < page.items.length ? page.items[index + 1] : null,
      });
    }).catch(() => setReviewNavigation({ experimentId, position, total, previous: null, next: null }));
  }, [runId]);
  const ranked = useMemo(() => mergeRankedRows(retrieval), [retrieval]);
  const selected = context.filter((source) => source.selected);
  const excluded = context.filter((source) => !source.selected);
  const contextSnapshot = trace?.spans.find((span) => span.span_type === "context_construction")?.configuration_snapshot;
  const nestedContextSnapshot = contextSnapshot?.context_configuration;
  const frozenContext = trace?.pipeline_configuration.context;
  const contextBudget = numberOrNull(contextSnapshot?.token_budget
    ?? (typeof nestedContextSnapshot === "object" && nestedContextSnapshot !== null ? (nestedContextSnapshot as Record<string, unknown>).token_budget : null)
    ?? (typeof frozenContext === "object" && frozenContext !== null ? (frozenContext as Record<string, unknown>).token_budget : null));
  const classification = run?.classification ?? {};

  async function revealExactContext() {
    if (!run?.context_artifact_id) return;
    setLoadingContext(true);
    try { setExactContext(await api.getArtifactText(run.context_artifact_id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Exact context could not be loaded"); }
    finally { setLoadingContext(false); }
  }

  async function evaluate() {
    setEvaluating(true); setError("");
    try { setEvaluation(await api.evaluateRun(runId)); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Evaluation failed"); }
    finally { setEvaluating(false); }
  }

  if (!run || !trace) return <main className="shell"><a className="back" href="/laboratory">← Query Laboratory</a>{error ? <div role="alert" className="alert">{error}</div> : <p aria-live="polite">Loading observable execution…</p>}</main>;

  const generationSpan = trace.spans.find((span) => span.span_type === "generation");
  const generationMetadata = run.generation_metadata ?? {};
  return <>
    <header className="topbar"><a className="brand" href="/"><span className="brand-mark">R</span><div><strong>RAGScope</strong><small>Query Laboratory</small></div></a><nav className="top-nav"><a href="/laboratory">New query</a><StatusBadge status={run.status} /></nav></header>
    <main id="main" className="shell laboratory-shell">
      <a className="back" href="/laboratory">← New laboratory query</a>
      {reviewNavigation && <nav className="lab-section-nav" aria-label="Human review navigation"><a href={`/experiments/${reviewNavigation.experimentId}/review`}>← Review queue</a><span>Review {reviewNavigation.position} of {reviewNavigation.total}</span>{reviewNavigation.previous ? <a href={`/laboratory/${reviewNavigation.previous.query_run_id}?review=1&experiment=${reviewNavigation.experimentId}&position=${reviewNavigation.position - 1}&total=${reviewNavigation.total}`}>← Previous run</a> : <span>First queued run</span>}{reviewNavigation.next ? <a href={`/laboratory/${reviewNavigation.next.query_run_id}?review=1&experiment=${reviewNavigation.experimentId}&position=${reviewNavigation.position + 1}&total=${reviewNavigation.total}`}>Next run →</a> : <span>Last loaded run</span>}</nav>}
      <section className="lab-run-header"><div><p className="eyebrow">Observable execution trace</p><h1>{run.query_text}</h1><p>Run <code>{run.id}</code> · schema <code>{trace.schema_version}</code></p></div>{run.failure_code && <div className="failure-callout" role="alert"><strong>Pipeline failed</strong><span>{run.failure_code}</span><p>{run.failure_message ?? "Successful earlier stages remain available below."}</p></div>}</section>
      {error && <div role="alert" className="alert">{error}</div>}
      <nav className="lab-section-nav" aria-label="Trace sections"><a href="#query">Query</a><a href="#timeline">Timeline</a><a href="#retrieval">Retrieval</a><a href="#context">Context</a><a href="#generation">Generation</a><a href="#evaluation">Evaluation</a><a href="#failures">Failures</a><a href="#claims">Claims & citations</a></nav>

      <section id="query" className="panel lab-section"><div className="section-heading"><div><p className="eyebrow">Query</p><h2>Observable input and configured route</h2></div><StatusBadge status={run.status} /></div>
        <div className="query-grid"><div><span>Original user query</span><p>{run.query_text}</p></div><div><span>Rewritten query</span><p>{run.rewritten_query ?? "Rewriting was disabled or made no change."}</p></div><div><span>Classification</span><strong>{textOr(classification.category, "Not classified")}</strong><small>{textOr(classification.reason, "No classification reason recorded")} · {textOr(classification.reason_code, "no reason code")}</small></div><div><span>Confidence</span><strong>{typeof classification.confidence === "number" ? `${Math.round(classification.confidence * 100)}%` : textOr(classification.confidence, "Unavailable")}</strong><small>Classifier {textOr(classification.classifier_version, "version unavailable")}</small></div><div><span>{run.route_decision.type === "adaptive" ? "Adaptive route" : "Configured route"}</span><strong>{textOr(run.route_decision.retrieval_mode, textOr(run.route_decision.mode, "Fixed pipeline"))}</strong><small>{run.route_decision.type === "adaptive" ? `${textOr(run.route_decision.reason_code)} · ${textOr(run.route_decision.reason)}` : "Selected by the frozen fixed pipeline configuration."}</small></div></div>
      </section>

      <section id="timeline" className="panel lab-section"><div className="section-heading"><div><p className="eyebrow">Timeline</p><h2>Stages in actual execution order</h2></div><span className="muted">Observable application events only</span></div><StageTimeline spans={trace.spans} /></section>

      <section id="retrieval" className="panel lab-section"><div className="section-heading"><div><p className="eyebrow">Evidence flow</p><h2>Retrieval → fusion → reranking → context</h2></div><span className="muted">Earlier ranks are never overwritten</span></div>
        <div className="lab-stats"><SummaryValue label="Unique candidates" value={String(trace.summary.retrieval_candidate_count ?? ranked.length)} /><SummaryValue label="Reranked" value={String(trace.summary.reranked_candidate_count ?? ranked.filter((row) => row.rerankedRank !== null).length)} /><SummaryValue label="Selected" value={String(trace.summary.selected_context_count ?? selected.length)} /><SummaryValue label="Excluded" value={String(trace.summary.excluded_context_count ?? excluded.length)} /></div>
        <div className="lab-table-wrap"><table className="lab-table"><caption>Candidate ranking history and context disposition</caption><thead><tr><th>Candidate</th><th title="Rank from lexical full-text retrieval">Lexical</th><th title="Rank from embedding similarity retrieval">Dense</th><th title="Rank after Reciprocal Rank Fusion">Fused</th><th title="Rank after the optional reranker">Reranked</th><th>Movement</th><th>Context</th><th>Source</th></tr></thead><tbody>{ranked.map((row) => {
          const before = row.fusedRank ?? row.lexicalRank ?? row.denseRank;
          const movement = before !== null && row.rerankedRank !== null ? before - row.rerankedRank : null;
          return <tr key={row.chunkId}><td><strong>{row.text.slice(0, 110)}</strong><small>{row.documentTitle ?? `Document ${row.documentId.slice(0, 8)}`} · {row.sectionPath.join(" / ") || "section unavailable"} · page {row.pageStart ?? "—"}{row.pageEnd !== null && row.pageEnd !== row.pageStart ? `–${row.pageEnd}` : ""}</small><small>{row.retrievers.join(" + ")} · <code>{row.chunkId.slice(0, 8)}</code></small></td><td>{row.lexicalRank == null ? "—" : `#${row.lexicalRank}`}<small>{formatScore(row.lexicalScore)}</small></td><td>{row.denseRank == null ? "—" : `#${row.denseRank}`}<small>{formatScore(row.denseScore)}</small></td><td>{row.fusedRank == null ? "—" : `#${row.fusedRank}`}<small>{formatScore(row.fusedScore)}</small></td><td>{row.rerankedRank == null ? "—" : `#${row.rerankedRank}`}<small>{formatScore(row.rerankerScore)}</small></td><td>{movement == null ? "Not reranked" : movement === 0 ? "No change" : movement > 0 ? `↑ ${movement}` : `↓ ${Math.abs(movement)}`}</td><td><span className={`flow-state ${row.selected ? "flow-selected" : "flow-excluded"}`}>{row.selected ? "Selected" : "Not selected"}</span></td><td><a className="source-link" href={`/documents/${row.documentId}?page=${row.pageStart ?? ""}&chunk=${row.chunkId}`}>Inspect ↗</a></td></tr>;
        })}</tbody></table></div>
      </section>

      <section id="context" className="panel lab-section"><div className="section-heading"><div><p className="eyebrow">Context</p><h2>Generator evidence package</h2></div><span className="muted">{selected.reduce((sum, source) => sum + source.token_count, 0)} / {contextBudget ?? "—"} tokens</span></div>
        <div className="context-columns"><div><h3>Selected sources</h3>{selected.length === 0 && <p className="muted">No corpus evidence was selected.</p>}{selected.map((source) => <article className="context-card selected-context" key={source.id}><header><strong>{source.citation_id ? `[${source.citation_id}]` : "Selected"} · {source.document_title ?? `Document ${source.document_id.slice(0, 8)}`}</strong><span>{source.token_count} tokens · pages {source.page_start ?? "—"}–{source.page_end ?? "—"}</span></header>{source.section_path && source.section_path.length > 0 && <small>{source.section_path.join(" / ")}</small>}<p>{source.text}</p><a className="source-link" href={`/documents/${source.document_id}?page=${source.page_start ?? ""}&chunk=${source.chunk_id}`}>Open source ↗</a></article>)}</div><div><h3>Excluded candidates</h3>{excluded.length === 0 && <p className="muted">No tracked context exclusions.</p>}{excluded.map((source) => <article className="context-card excluded-context" key={source.id}><header><strong>Excluded · {source.document_title ?? `Document ${source.document_id.slice(0, 8)}`}</strong><span>{source.exclusion_reason ?? "Not selected"}</span></header>{source.section_path && source.section_path.length > 0 && <small>{source.section_path.join(" / ")}</small>}<p>{source.text}</p><a className="source-link" href={`/documents/${source.document_id}?page=${source.page_start ?? ""}&chunk=${source.chunk_id}`}>Open source ↗</a></article>)}</div></div>
        <div className="exact-context"><div><h3>Exact final generator context</h3><p className="muted">Loaded from the immutable content-hashed artifact only when expanded.</p></div>{exactContext === null ? <button className="secondary" disabled={!run.context_artifact_id || loadingContext} onClick={() => void revealExactContext()}>{loadingContext ? "Loading…" : run.context_artifact_id ? "Reveal exact context" : "No context artifact"}</button> : <><pre>{exactContext}</pre><button className="secondary" onClick={() => setExactContext(null)}>Collapse context</button></>}</div>
      </section>

      <section id="generation" className="panel lab-section"><div className="section-heading"><div><p className="eyebrow">Generation</p><h2>Grounded output</h2></div><StatusBadge status={generationSpan?.status ?? run.status} /></div>
        <div className="lab-stats"><SummaryValue label="Answerability" value={run.answerability_decision?.replaceAll("_", " ") ?? "Unavailable"} /><SummaryValue label="Provider / model" value={`${run.generation_provider ?? textOr(generationMetadata.provider)} / ${run.generation_model ?? textOr(generationMetadata.model)}`} /><SummaryValue label="Generation latency" value={formatMs(run.generation_latency_ms ?? numberOrNull(generationMetadata.latency_ms) ?? generationSpan?.latency_ms)} /><SummaryValue label="Tokens" value={`${run.input_tokens ?? "—"} in / ${run.output_tokens ?? "—"} out`} /><SummaryValue label="Estimated cost" value={run.estimated_cost == null ? "Unavailable" : `$${run.estimated_cost.toFixed(6)}`} /></div>
        <div className="answer-body">{run.answer_text ?? run.abstention_reason ?? "No valid answer was produced."}</div>{run.limitations.length > 0 && <div className="warnings"><strong>Limitations</strong><ul>{run.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul></div>}{run.abstention_reason && <p><strong>Abstention reason:</strong> {run.abstention_reason}</p>}
      </section>

      <section id="evaluation" className="panel lab-section"><div className="section-heading"><div><p className="eyebrow">Scientific measurement</p><h2>Per-run evaluation</h2></div><button className="secondary" disabled={evaluating} onClick={() => void evaluate()}>{evaluating ? "Evaluating…" : evaluation?.metrics.length ? "Recompute versioned metrics" : "Evaluate run"}</button></div>
        {run.benchmark_question_id ? <p>Human benchmark ground truth linked: <code>{run.benchmark_question_id}</code>. Alternative evidence sets are scored as alternatives.</p> : <p className="muted">No benchmark question is linked. Human-grounded metrics remain missing rather than being recorded as zero.</p>}
        {evaluation && evaluation.metrics.length > 0 ? <div className="lab-table-wrap"><table className="lab-table"><caption>Versioned metrics; Missing means the required label or input was unavailable</caption><thead><tr><th>Metric</th><th>Scope</th><th>Value</th><th>Method</th><th>Version / inputs</th></tr></thead><tbody>{evaluation.metrics.map((metric) => <tr key={metric.id}><td><strong>{metric.metric_name.replaceAll("_", " ")}</strong></td><td>{metric.metric_scope}</td><td>{metric.metric_value == null ? "Missing" : metric.metric_value.toFixed(4)}</td><td><StatusBadge status={metric.evaluation_method} /></td><td><code>{metric.metric_version}</code><details><summary>Inspectable inputs and details</summary><pre>{JSON.stringify({ details: metric.details, input_snapshot: metric.input_snapshot, input_hash: metric.input_hash }, null, 2)}</pre></details></td></tr>)}</tbody></table></div> : <p className="muted">No evaluation has been calculated for this run.</p>}
        {run.benchmark_question_id && <HumanMetricReview runId={runId} evaluation={evaluation} onSaved={load} />}
      </section>

      <section id="failures" className="panel lab-section"><div className="section-heading"><div><p className="eyebrow">Failure attribution</p><h2>Earliest observable responsible stage</h2></div><span className="muted">Automatic labels and human corrections coexist</span></div>{evaluation?.failure_attributions.length ? <div className="claim-grid">{evaluation.failure_attributions.map((attribution) => <article className="lab-claim" key={attribution.id}><div className="claim-heading"><strong>{attribution.is_primary ? "Primary" : "Secondary"} · {attribution.pipeline_stage}</strong><StatusBadge status={attribution.human_override_label ? "human reviewed" : "automatic"} /></div><p><strong>{attribution.human_override_label ?? attribution.automatic_label}</strong></p>{attribution.human_override_label && <small>Automatic label preserved: {attribution.automatic_label}</small>}<p>{attribution.attribution_rule.replaceAll("_", " ")}</p><details><summary>Attribution evidence</summary><pre>{JSON.stringify(attribution.evidence, null, 2)}</pre></details><FailureReview runId={runId} attribution={attribution} onSaved={load} /></article>)}</div> : <p className="muted">No observable quality or infrastructure failure has been attributed.</p>}</section>

      <section id="claims" className="panel lab-section"><div className="section-heading"><div><p className="eyebrow">Claims & citations</p><h2>Source-resolved factual claims</h2></div><span className="muted">{claims.length} claims</span></div>{claims.length === 0 ? <p className="muted">No valid structured claims were persisted.</p> : <div className="claim-grid">{claims.map((claim) => <CitationDetails key={claim.id} claim={claim} />)}</div>}</section>
    </main>
  </>;
}
