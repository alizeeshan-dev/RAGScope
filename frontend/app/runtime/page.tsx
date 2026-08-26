"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type {
  Corpus,
  PipelineConfiguration,
  QueryRun,
  RunClaim,
  RunContextSource,
  RunRetrievalResult,
} from "@/lib/types";

const PROVIDER_MODELS: Record<string, string> = {
  fake: "fake-generation-v1",
  gemini: "gemini-2.5-flash",
  "openai-compatible": "gpt-4.1-mini",
};

export default function RuntimePage() {
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [pipelines, setPipelines] = useState<PipelineConfiguration[]>([]);
  const [run, setRun] = useState<QueryRun | null>(null);
  const [retrieval, setRetrieval] = useState<RunRetrievalResult[]>([]);
  const [claims, setClaims] = useState<RunClaim[]>([]);
  const [sources, setSources] = useState<RunContextSource[]>([]);
  const [provider, setProvider] = useState("fake");
  const [model, setModel] = useState(PROVIDER_MODELS.fake);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [storedCorpora, storedPipelines] = await Promise.all([
        api.listCorpora(),
        api.listPipelines(),
      ]);
      setCorpora(storedCorpora);
      setPipelines(storedPipelines);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Load failed");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function createPipeline(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("pipeline");
    setError("");
    const data = new FormData(event.currentTarget);
    const topK = Number(data.get("top_k"));
    const rerankEnabled = data.get("rerank") === "on";
    try {
      await api.createPipeline({
        name: String(data.get("name")),
        version: Number(data.get("version")),
        retrieval_mode: String(data.get("mode")),
        lexical_configuration: {
          top_k: topK,
          candidate_count: Number(data.get("candidates")),
        },
        dense_configuration: {
          top_k: topK,
          candidate_count: Number(data.get("candidates")),
          similarity_method: "cosine",
        },
        fusion_configuration: {
          method: "rrf",
          rrf_k: Number(data.get("rrf_k")),
          lexical_weight: 1,
          dense_weight: 1,
          final_count: topK,
        },
        reranker_configuration: {
          enabled: rerankEnabled,
          provider: "fake",
          model: "fake-token-overlap-reranker-v1",
          input_candidate_count: Number(data.get("candidates")),
          final_count: Number(data.get("rerank_final")),
        },
        query_processing_configuration: {
          classification_enabled: true,
          rewriting_enabled: data.get("rewrite") === "on",
          rewriting_strategy: "deterministic-keywords",
        },
        context_configuration: {
          token_budget: Number(data.get("budget")),
          deduplicate: data.get("dedupe") === "on",
          overlap_threshold: 0.85,
        },
        generation_configuration: {
          provider,
          model,
          temperature: Number(data.get("temperature")),
          max_output_tokens: Number(data.get("output_tokens")),
          timeout_seconds: 60,
          fake_answerability: "auto",
        },
        citation_configuration: {
          verification_method: "citation-existence-v1",
          require_factual_claim_citations: true,
        },
        prompt_versions: {
          grounded_generation: { prompt_id: "grounded-answer", version: 1 },
        },
      });
      event.currentTarget.reset();
      setProvider("fake");
      setModel(PROVIDER_MODELS.fake);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Pipeline creation failed");
    } finally {
      setBusy("");
    }
  }

  async function runQuery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("query");
    setError("");
    setRetrieval([]);
    setClaims([]);
    setSources([]);
    const data = new FormData(event.currentTarget);
    try {
      const created = await api.createQueryRun({
        corpus_version_id: String(data.get("corpus_version")),
        pipeline_configuration_id: String(data.get("pipeline")),
        query_text: String(data.get("question")),
        filters: {
          publication_years: data.get("year") ? [Number(data.get("year"))] : [],
          document_ids: [],
        },
      });
      setRun(created);
      const [ranked, generatedClaims, context] = await Promise.all([
        api.getRunRetrieval(created.id),
        api.getRunClaims(created.id),
        api.getRunContext(created.id),
      ]);
      setRetrieval(ranked);
      setClaims(generatedClaims);
      setSources(context);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Query failed");
    } finally {
      setBusy("");
    }
  }

  const readyVersions = corpora.flatMap((corpus) =>
    (corpus.versions ?? [])
      .filter((version) => version.status === "ready")
      .map((version) => ({ ...version, corpusName: corpus.name })),
  );
  const frozenPipelines = pipelines.filter((pipeline) => pipeline.frozen_at);

  return (
    <>
      <header className="topbar">
        <a className="brand" href="/">
          <span className="brand-mark">R</span>
          <div><strong>RAGScope</strong><small>Fixed pipeline runtime</small></div>
        </a>
        <span className="phase">No adaptive routing</span>
      </header>
      <main id="main" className="shell">
        <a className="back" href="/">← Corpora</a>
        <section className="page-title">
          <div>
            <p className="eyebrow">Chunk 2</p>
            <h1>Grounded query runtime</h1>
            <p>Configure a reproducible fixed retrieval path, then inspect its answer, claims, citations, and source selection.</p>
          </div>
        </section>
        {error && <div role="alert" className="alert">{error}</div>}
        <div className="runtime-grid">
          <section className="panel">
            <p className="eyebrow">Configuration</p>
            <h2>New fixed pipeline</h2>
            <form className="runtime-form" onSubmit={createPipeline}>
              <div className="form-row">
                <label>Name<input name="name" required defaultValue="Hybrid baseline" /></label>
                <label>Version<input name="version" type="number" min="1" defaultValue="1" required /></label>
              </div>
              <label>
                Retrieval mode
                <select name="mode" defaultValue="hybrid">
                  <option value="none">No retrieval</option>
                  <option value="lexical">Lexical</option>
                  <option value="dense">Dense</option>
                  <option value="hybrid">Hybrid</option>
                </select>
              </label>
              <div className="form-row">
                <label>Top-k<input name="top_k" type="number" min="1" max="100" defaultValue="8" /></label>
                <label>Candidates<input name="candidates" type="number" min="1" max="200" defaultValue="20" /></label>
                <label>RRF k<input name="rrf_k" type="number" min="1" defaultValue="60" /></label>
              </div>
              <div className="check-row">
                <label><input name="rewrite" type="checkbox" /> Rewrite query</label>
                <label><input name="rerank" type="checkbox" /> Fake reranker</label>
                <label><input name="dedupe" type="checkbox" defaultChecked /> Deduplicate</label>
              </div>
              <div className="form-row">
                <label>Reranker final<input name="rerank_final" type="number" min="1" defaultValue="8" /></label>
                <label>Context tokens<input name="budget" type="number" min="1" defaultValue="2048" /></label>
              </div>
              <div className="form-row">
                <label>
                  Provider
                  <select
                    name="provider"
                    value={provider}
                    onChange={(event) => {
                      const nextProvider = event.target.value;
                      setProvider(nextProvider);
                      setModel(PROVIDER_MODELS[nextProvider]);
                    }}
                  >
                    <option value="fake">Deterministic fake</option>
                    <option value="gemini">Google Gemini</option>
                    <option value="openai-compatible">OpenAI-compatible</option>
                  </select>
                </label>
                <label>Model<input name="model" value={model} onChange={(event) => setModel(event.target.value)} /></label>
              </div>
              {provider === "gemini" && (
                <p className="field-note">Requires <code>RAGSCOPE_GEMINI_API_KEY</code> in the backend environment.</p>
              )}
              <div className="form-row">
                <label>Temperature<input name="temperature" type="number" step="0.1" min="0" max="2" defaultValue="0" /></label>
                <label>Output limit<input name="output_tokens" type="number" min="1" defaultValue="512" /></label>
              </div>
              <button disabled={Boolean(busy)}>{busy === "pipeline" ? "Saving…" : "Save pipeline"}</button>
            </form>
            <div className="pipeline-list">
              {pipelines.map((pipeline) => (
                <div key={pipeline.id}>
                  <span>
                    <strong>{pipeline.name} v{pipeline.version}</strong>
                    <small>{pipeline.retrieval_mode} · {String(pipeline.generation_configuration.model)}</small>
                  </span>
                  {pipeline.frozen_at ? (
                    <StatusBadge status="frozen" />
                  ) : (
                    <button className="secondary" onClick={() => void api.freezePipeline(pipeline.id).then(refresh).catch((reason: Error) => setError(reason.message))}>Freeze</button>
                  )}
                </div>
              ))}
            </div>
          </section>
          <section className="panel">
            <p className="eyebrow">Single query</p>
            <h2>Run pipeline</h2>
            <form className="runtime-form" onSubmit={runQuery}>
              <label>
                Ready corpus version
                <select name="corpus_version" required defaultValue="">
                  <option value="" disabled>Select version</option>
                  {readyVersions.map((version) => <option value={version.id} key={version.id}>{version.corpusName} · {version.version_label}</option>)}
                </select>
              </label>
              <label>
                Frozen pipeline
                <select name="pipeline" required defaultValue="">
                  <option value="" disabled>Select pipeline</option>
                  {frozenPipelines.map((pipeline) => <option value={pipeline.id} key={pipeline.id}>{pipeline.name} v{pipeline.version}</option>)}
                </select>
              </label>
              <label>Publication year filter<input name="year" type="number" min="1000" max="3000" placeholder="Optional" /></label>
              <label>Question<textarea name="question" required rows={6} placeholder="What evidence does the corpus provide?" /></label>
              <button disabled={Boolean(busy) || !readyVersions.length || !frozenPipelines.length}>{busy === "query" ? "Running…" : "Run fixed pipeline"}</button>
            </form>
          </section>
        </div>
        {run && (
          <section className="run-result panel">
            <div className="panel-heading">
              <div><p className="eyebrow">Persisted QueryRun</p><h2>Answer</h2></div>
              <div className="action-bar"><a className="source-link" href={`/laboratory/${run.id}`}>Open full Query Laboratory →</a><StatusBadge status={run.status} /></div>
            </div>
            <div className="run-meta">
              <span>Answerability: <strong>{run.answerability_decision ?? "—"}</strong></span>
              <span>{run.total_latency_ms ?? "—"} ms</span>
              <span>{run.input_tokens ?? "—"} in / {run.output_tokens ?? "—"} out</span>
              <span>Cost: {run.estimated_cost == null ? "unknown" : `$${run.estimated_cost.toFixed(6)}`}</span>
            </div>
            <div className="answer-body">{run.answer_text || run.abstention_reason || "No valid answer was produced."}</div>
            {run.limitations.length > 0 && (
              <div className="warnings"><strong>Limitations</strong><ul>{run.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>
            )}
            <div className="runtime-grid">
              <div>
                <h3>Claims & citations</h3>
                {claims.map((claim) => (
                  <article className="claim" key={claim.id}>
                    <p>{claim.claim_text}</p>
                    <footer>{claim.citations.map((citation) => <a href={`/documents/${citation.document_id}`} key={citation.id}>[{citation.citation_id}] p.{citation.page_number ?? "—"}</a>)}</footer>
                  </article>
                ))}
              </div>
              <div>
                <h3>Context sources</h3>
                {sources.map((source) => (
                  <article className={`source-row ${source.selected ? "selected-source" : "excluded-source"}`} key={source.id}>
                    <strong>{source.citation_id ? `[${source.citation_id}]` : "Excluded"}</strong>
                    <span>{source.token_count} tokens · pages {source.page_start ?? "—"}–{source.page_end ?? "—"}</span>
                    <p>{source.text.slice(0, 240)}</p>
                    {source.exclusion_reason && <small>{source.exclusion_reason}</small>}
                  </article>
                ))}
              </div>
            </div>
            <h3>Retrieved candidates</h3>
            <div className="retrieval-table" role="table" aria-label="Retrieved candidates">
              {retrieval.map((item) => (
                <div role="row" key={item.id}>
                  <span>{item.retriever_type}</span>
                  <span>rank {item.original_rank ?? item.fused_rank ?? "—"}</span>
                  <span>rerank {item.reranked_rank ?? "—"}</span>
                  <span>{item.selected_for_context ? "Selected" : "Candidate"}</span>
                  <p>{item.text.slice(0, 180)}</p>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>
    </>
  );
}
