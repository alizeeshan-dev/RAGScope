"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { Corpus, PipelineConfiguration, ProviderCapability, QueryRun, RunClaim, RunContextSource, RunRetrievalResult, RouterConfiguration } from "@/lib/types";
import styles from "./runtime.module.css";

const PROVIDER_MODELS: Record<string, string> = { fake: "fake-generation-v1", gemini: "gemini-2.5-flash", "openai-compatible": "gpt-4.1-mini" };
const RETRIEVAL_MODES = ["none", "lexical", "dense", "hybrid"] as const;
type RetrievalMode = (typeof RETRIEVAL_MODES)[number];

export default function RuntimePage() {
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [pipelines, setPipelines] = useState<PipelineConfiguration[]>([]);
  const [routers, setRouters] = useState<RouterConfiguration[]>([]);
  const [capabilities, setCapabilities] = useState<ProviderCapability[]>([]);
  const [run, setRun] = useState<QueryRun | null>(null);
  const [retrieval, setRetrieval] = useState<RunRetrievalResult[]>([]);
  const [claims, setClaims] = useState<RunClaim[]>([]);
  const [sources, setSources] = useState<RunContextSource[]>([]);
  const [provider, setProvider] = useState("fake");
  const [executionMode, setExecutionMode] = useState<"fixed" | "adaptive">("fixed");
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("hybrid");
  const [rerankerProvider, setRerankerProvider] = useState("fake");
  const [model, setModel] = useState(PROVIDER_MODELS.fake);
  const [rewrite, setRewrite] = useState(false);
  const [rerank, setRerank] = useState(false);
  const [dedupe, setDedupe] = useState(true);
  const [topK, setTopK] = useState(8);
  const [candidates, setCandidates] = useState(20);
  const [rrfK, setRrfK] = useState(60);
  const [lexicalWeight, setLexicalWeight] = useState(1);
  const [denseWeight, setDenseWeight] = useState(1);
  const [fusionFinal, setFusionFinal] = useState(8);
  const [budget, setBudget] = useState(2048);
  const [overlapThreshold, setOverlapThreshold] = useState(0.85);
  const [rerankFinal, setRerankFinal] = useState(8);
  const [classification, setClassification] = useState(true);
  const [rewriteStrategy, setRewriteStrategy] = useState<"normalize-only" | "deterministic-keywords">("deterministic-keywords");
  const [requireCitations, setRequireCitations] = useState(true);
  const [promptId, setPromptId] = useState("grounded-answer");
  const [promptVersion, setPromptVersion] = useState(1);
  const [adaptiveAllowedModes, setAdaptiveAllowedModes] = useState<RetrievalMode[]>([...RETRIEVAL_MODES]);
  const [adaptiveAllowRewrite, setAdaptiveAllowRewrite] = useState(true);
  const [adaptiveAllowRerank, setAdaptiveAllowRerank] = useState(true);
  const [adaptiveMaxCandidates, setAdaptiveMaxCandidates] = useState(200);
  const [adaptiveMaxContext, setAdaptiveMaxContext] = useState(100_000);
  const [routerAllowedModes, setRouterAllowedModes] = useState<RetrievalMode[]>([...RETRIEVAL_MODES]);
  const [routerComplexRewrite, setRouterComplexRewrite] = useState(true);
  const [routerComplexRerank, setRouterComplexRerank] = useState(true);
  const [routerTrivialTokens, setRouterTrivialTokens] = useState(2);
  const [routerRewriteTokens, setRouterRewriteTokens] = useState(18);
  const [routerStandardCandidates, setRouterStandardCandidates] = useState(20);
  const [routerComplexCandidates, setRouterComplexCandidates] = useState(40);
  const [routerStandardBudget, setRouterStandardBudget] = useState(2048);
  const [routerBroadBudget, setRouterBroadBudget] = useState(4096);
  const [routerComplexBudget, setRouterComplexBudget] = useState(8192);
  const [saveIntent, setSaveIntent] = useState<"draft" | "freeze">("draft");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [storedCorpora, storedPipelines, storedRouters, storedCapabilities] = await Promise.all([api.listCorpora(), api.listPipelines(), api.listRouterConfigurations(), api.listProviderCapabilities()]);
      setCorpora(storedCorpora); setPipelines(storedPipelines); setRouters(storedRouters); setCapabilities(storedCapabilities); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Pipeline resources could not be loaded"); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function createPipeline(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy("pipeline"); setError("");
    const form = event.currentTarget; const data = new FormData(form);
    try {
      if (candidates < topK) throw new Error("Candidate count must be greater than or equal to top-k.");
      if (rerank && rerankFinal > candidates) throw new Error("Reranker final count cannot exceed the candidate count.");
      if (executionMode === "adaptive" && adaptiveAllowedModes.length === 0) throw new Error("Adaptive pipelines require at least one allowed retrieval mode.");
      if (executionMode === "adaptive" && !classification) throw new Error("Adaptive pipelines require query classification.");
      const inputPrice = String(data.get("input_price") ?? "").trim();
      const outputPrice = String(data.get("output_price") ?? "").trim();
      const pipeline = await api.createPipeline({
        name: String(data.get("name")), version: Number(data.get("version")), execution_mode: executionMode,
        router_configuration_id: executionMode === "adaptive" ? String(data.get("router_configuration")) : null,
        adaptive_configuration: { allowed_retrieval_modes: adaptiveAllowedModes, allow_rewriting: adaptiveAllowRewrite, allow_reranking: adaptiveAllowRerank, maximum_candidate_count: adaptiveMaxCandidates, maximum_context_budget: adaptiveMaxContext },
        retrieval_mode: retrievalMode,
        lexical_configuration: { top_k: topK, candidate_count: candidates },
        dense_configuration: { top_k: topK, candidate_count: candidates, similarity_method: "cosine" },
        fusion_configuration: { method: "rrf", rrf_k: rrfK, lexical_weight: lexicalWeight, dense_weight: denseWeight, final_count: fusionFinal },
        reranker_configuration: { enabled: rerank, provider: rerankerProvider, model: String(data.get("reranker_model")), model_revision: data.get("reranker_revision") ? String(data.get("reranker_revision")) : null, batch_size: Number(data.get("reranker_batch_size")), device: data.get("reranker_device") ? String(data.get("reranker_device")) : null, local_files_only: true, input_candidate_count: candidates, final_count: rerankFinal },
        query_processing_configuration: { classification_enabled: classification, rewriting_enabled: rewrite, rewriting_strategy: rewriteStrategy },
        context_configuration: { token_budget: budget, deduplicate: dedupe, overlap_threshold: overlapThreshold },
        generation_configuration: { provider, model, temperature: Number(data.get("temperature")), max_output_tokens: Number(data.get("output_tokens")), timeout_seconds: Number(data.get("timeout_seconds")), input_price_per_million_tokens: inputPrice ? Number(inputPrice) : null, output_price_per_million_tokens: outputPrice ? Number(outputPrice) : null, fake_answerability: String(data.get("fake_answerability")) },
        citation_configuration: { verification_method: "citation-existence-v1", require_factual_claim_citations: requireCitations },
        prompt_versions: { grounded_generation: { prompt_id: promptId, version: promptVersion } },
      });
      if (saveIntent === "freeze") await api.freezePipeline(pipeline.id);
      form.reset(); setProvider("fake"); setExecutionMode("fixed"); setRetrievalMode("hybrid"); setRerankerProvider("fake"); setModel(PROVIDER_MODELS.fake); setRewrite(false); setRerank(false); setDedupe(true); setTopK(8); setCandidates(20); setRrfK(60); setLexicalWeight(1); setDenseWeight(1); setFusionFinal(8); setBudget(2048); setOverlapThreshold(0.85); setRerankFinal(8); setClassification(true); setRewriteStrategy("deterministic-keywords"); setRequireCitations(true); setPromptId("grounded-answer"); setPromptVersion(1); setAdaptiveAllowedModes([...RETRIEVAL_MODES]); setAdaptiveAllowRewrite(true); setAdaptiveAllowRerank(true); setAdaptiveMaxCandidates(200); setAdaptiveMaxContext(100_000); setSaveIntent("draft"); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Pipeline creation failed"); }
    finally { setBusy(""); }
  }

  async function createRouter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy("router"); setError("");
    const form = event.currentTarget; const data = new FormData(form);
    try {
      if (routerAllowedModes.length === 0) throw new Error("Router configurations require at least one allowed retrieval mode.");
      if (routerComplexCandidates < routerStandardCandidates) throw new Error("Complex candidate count must cover the standard candidate count.");
      if (routerBroadBudget < routerStandardBudget || routerComplexBudget < routerBroadBudget) throw new Error("Router context budgets must increase from standard to broad to complex.");
      await api.createRouterConfiguration({
        name: String(data.get("router_name")),
        version: Number(data.get("router_version")),
        configuration: {
          router_id: String(data.get("router_id")), version: String(data.get("router_schema_version")), fallback_policy: "reject",
          trivial_query_max_tokens: routerTrivialTokens, rewrite_token_threshold: routerRewriteTokens,
          standard_candidate_count: routerStandardCandidates, complex_candidate_count: routerComplexCandidates,
          standard_context_budget: routerStandardBudget, broad_context_budget: routerBroadBudget, complex_context_budget: routerComplexBudget,
          enable_complex_reranking: routerComplexRerank, enable_complex_rewriting: routerComplexRewrite,
          allowed_retrieval_modes: routerAllowedModes,
        },
      });
      form.reset(); setRouterAllowedModes([...RETRIEVAL_MODES]); setRouterComplexRewrite(true); setRouterComplexRerank(true); setRouterTrivialTokens(2); setRouterRewriteTokens(18); setRouterStandardCandidates(20); setRouterComplexCandidates(40); setRouterStandardBudget(2048); setRouterBroadBudget(4096); setRouterComplexBudget(8192); await refresh();
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Router creation failed"); }
    finally { setBusy(""); }
  }

  async function runQuery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy("query"); setError(""); setRetrieval([]); setClaims([]); setSources([]);
    const data = new FormData(event.currentTarget);
    try {
      const created = await api.createQueryRun({ corpus_version_id: String(data.get("corpus_version")), pipeline_configuration_id: String(data.get("pipeline")), query_text: String(data.get("question")), filters: { publication_years: data.get("year") ? [Number(data.get("year"))] : [], document_ids: [] } });
      setRun(created);
      const [ranked, generatedClaims, context] = await Promise.all([api.getRunRetrieval(created.id), api.getRunClaims(created.id), api.getRunContext(created.id)]);
      setRetrieval(ranked); setClaims(generatedClaims); setSources(context);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Query failed"); }
    finally { setBusy(""); }
  }

  const readyVersions = corpora.flatMap((corpus) => (corpus.versions ?? []).filter((version) => version.status === "ready").map((version) => ({ ...version, corpusName: corpus.name })));
  const frozenPipelines = pipelines.filter((pipeline) => pipeline.frozen_at);
  const generationCapability = capabilities.find((item) => item.provider === provider && item.capability.includes("generation"));
  const rerankerCapability = capabilities.find((item) => item.provider === rerankerProvider && item.capability.includes("reranking"));
  const mapStages = [
    ["01", "Query", `${classification ? "Classify" : "No classification"} · rewrite ${rewrite ? rewriteStrategy : "off"}`, true],
    ["02", "Lexical", retrievalMode === "lexical" || retrievalMode === "hybrid" ? "PostgreSQL full-text search" : "Disabled", retrievalMode === "lexical" || retrievalMode === "hybrid"],
    ["03", "Dense", retrievalMode === "dense" || retrievalMode === "hybrid" ? `pgvector · ${candidates} candidates` : "Disabled", retrievalMode === "dense" || retrievalMode === "hybrid"],
    ["04", "Fusion", retrievalMode === "hybrid" ? `RRF k=${rrfK} · weights ${lexicalWeight}:${denseWeight} · final ${fusionFinal}` : "Not used", retrievalMode === "hybrid"],
    ["05", "Rerank", rerank ? `${rerankerProvider} · top ${candidates}→${rerankFinal}` : "Disabled", rerank],
    ["06", "Context", `${budget.toLocaleString()} tokens · dedupe ${dedupe ? `≥${overlapThreshold}` : "off"}`, true],
    ["07", "Generate", `${provider} · ${model}`, true],
    ["08", "Claims + citations", `${requireCitations ? "Required" : "Optional"} · citation-existence-v1 · ${promptId} v${promptVersion}`, true],
  ] as const;

  return <main id="main" className={`shell ${styles.page}`}>
    <section className={styles.hero}><p className="eyebrow">Reproducible conditions</p><h1>Pipeline builder</h1><p>Compose fixed or adaptive RAG conditions, inspect the exact execution map, then freeze the configuration.</p></section>
    {error && <div role="alert" className="alert">{error}</div>}
    <div className={styles.builderGrid}>
      <section className={styles.configuration}>
        <div className={styles.subheading}><h2>Configuration</h2><p>Every research-affecting value is persisted in the pipeline snapshot.</p></div>
        <form className={styles.form} onSubmit={createPipeline}>
          <fieldset className={styles.configurationGroup}>
            <legend>Identity and execution</legend>
            <div className="form-row"><label>Name<input name="name" required defaultValue="Hybrid baseline" /></label><label>Version<input name="version" type="number" min="1" defaultValue="1" required /></label></div>
            <div className="form-row"><label>Execution mode<select value={executionMode} onChange={(event)=>{const next=event.target.value as "fixed" | "adaptive";setExecutionMode(next);if(next === "adaptive")setClassification(true);}}><option value="fixed">Fixed</option><option value="adaptive">Adaptive</option></select></label><label>{executionMode === "adaptive" ? "Base retrieval snapshot" : "Retrieval mode"}<select name="mode" value={retrievalMode} onChange={(event)=>{const next=event.target.value as RetrievalMode;setRetrievalMode(next);if(next === "none")setRerank(false);}}>{RETRIEVAL_MODES.map((mode)=><option key={mode} value={mode}>{mode === "none" ? "No retrieval" : mode[0].toUpperCase()+mode.slice(1)}</option>)}</select></label></div>
            {executionMode === "adaptive" && <>
              <label>Frozen router<select name="router_configuration" required defaultValue=""><option value="" disabled>Select router</option>{routers.filter((item)=>item.frozen_at).map((item)=><option key={item.id} value={item.id}>{item.name} v{item.version}</option>)}</select></label>
              <div className={styles.policyBox}><strong>Adaptive pipeline guardrails</strong><p>These limits constrain the frozen router without mutating either snapshot.</p><div className={styles.modeChecks}>{RETRIEVAL_MODES.map((mode)=><label className={styles.inlineCheck} key={mode}><input type="checkbox" checked={adaptiveAllowedModes.includes(mode)} onChange={(event)=>setAdaptiveAllowedModes((current)=>event.target.checked?[...current,mode]:current.filter((item)=>item!==mode))}/>{mode}</label>)}</div><div className="form-row"><label>Maximum candidates<input type="number" min="1" max="10000" value={adaptiveMaxCandidates} onChange={(event)=>setAdaptiveMaxCandidates(Number(event.target.value))}/></label><label>Maximum context tokens<input type="number" min="1" max="1000000" value={adaptiveMaxContext} onChange={(event)=>setAdaptiveMaxContext(Number(event.target.value))}/></label></div><div className={styles.modeChecks}><label className={styles.inlineCheck}><input type="checkbox" checked={adaptiveAllowRewrite} onChange={(event)=>setAdaptiveAllowRewrite(event.target.checked)}/>Allow rewriting</label><label className={styles.inlineCheck}><input type="checkbox" checked={adaptiveAllowRerank} onChange={(event)=>setAdaptiveAllowRerank(event.target.checked)}/>Allow reranking</label></div></div>
            </>}
          </fieldset>

          <fieldset className={styles.configurationGroup}>
            <legend>Retrieval and fusion</legend>
            <div className="form-row"><label>Top-k<input name="top_k" type="number" min="1" max="100" value={topK} onChange={(event)=>setTopK(Number(event.target.value))}/></label><label>Candidate count<input name="candidates" type="number" min="1" max="200" value={candidates} onChange={(event)=>setCandidates(Number(event.target.value))}/></label></div>
            <label>Dense similarity method<input value="cosine" readOnly aria-readonly="true" /><span className={styles.inputHint}>pgvector cosine similarity is the only currently registered dense method.</span></label>
            <div className="form-row"><label>Fusion method<input value="Reciprocal Rank Fusion (RRF)" readOnly aria-readonly="true" /></label><label>RRF k<input name="rrf_k" type="number" min="1" max="10000" value={rrfK} onChange={(event)=>setRrfK(Number(event.target.value))}/></label></div>
            <div className={styles.threeFields}><label>Lexical weight<input type="number" min="0.01" max="100" step="0.1" value={lexicalWeight} onChange={(event)=>setLexicalWeight(Number(event.target.value))}/></label><label>Dense weight<input type="number" min="0.01" max="100" step="0.1" value={denseWeight} onChange={(event)=>setDenseWeight(Number(event.target.value))}/></label><label>Fused final count<input type="number" min="1" max="100" value={fusionFinal} onChange={(event)=>setFusionFinal(Number(event.target.value))}/></label></div>
          </fieldset>

          <fieldset className={styles.configurationGroup}>
            <legend>Query processing and context</legend>
            <div className={styles.options}><label className={styles.option}><input type="checkbox" checked={classification} disabled={executionMode === "adaptive"} onChange={(event)=>setClassification(event.target.checked)}/> Classification {executionMode === "adaptive" ? "(required)" : ""}</label><label className={styles.option}><input name="rewrite" type="checkbox" checked={rewrite} onChange={(event)=>setRewrite(event.target.checked)}/> Rewrite query</label><label className={styles.option}><input name="dedupe" type="checkbox" checked={dedupe} onChange={(event)=>setDedupe(event.target.checked)}/> Context deduplication</label></div>
            <label>Rewrite strategy<select value={rewriteStrategy} onChange={(event)=>setRewriteStrategy(event.target.value as "normalize-only" | "deterministic-keywords")}><option value="normalize-only">Normalize only</option><option value="deterministic-keywords">Deterministic keywords</option></select></label>
            <div className="form-row"><label>Context token budget<input name="budget" type="number" min="1" max="100000" value={budget} onChange={(event)=>setBudget(Number(event.target.value))}/></label><label>Deduplication overlap threshold<input type="number" min="0" max="1" step="0.01" value={overlapThreshold} onChange={(event)=>setOverlapThreshold(Number(event.target.value))}/></label></div>
          </fieldset>

          <fieldset className={styles.configurationGroup}>
            <legend>Reranking</legend>
            <label className={styles.option}><input name="rerank" type="checkbox" checked={rerank} disabled={retrievalMode === "none"} onChange={(event)=>setRerank(event.target.checked)}/> Enable reranker {retrievalMode === "none" ? "(unavailable without retrieval)" : ""}</label>
            {rerank && <><div className="form-row"><label>Reranker provider<select value={rerankerProvider} onChange={(event)=>setRerankerProvider(event.target.value)}><option value="fake">Deterministic fake</option><option value="sentence_transformers_cross_encoder">Local CrossEncoder</option></select></label><label>Final count<input name="rerank_final" type="number" min="1" max="100" value={rerankFinal} onChange={(event)=>setRerankFinal(Number(event.target.value))}/></label></div>{rerankerCapability&&<p className={styles.capability} data-available={rerankerCapability.available}><strong>{rerankerCapability.available?"Available":"Unavailable"}</strong><span>{rerankerCapability.reason}</span></p>}<div className="form-row"><label>Reranker model<input key={rerankerProvider} name="reranker_model" defaultValue={rerankerProvider === "fake" ? "fake-token-overlap-reranker-v1" : "cross-encoder/ms-marco-MiniLM-L-6-v2"}/></label><label>Batch size<input name="reranker_batch_size" type="number" min="1" max="1024" defaultValue="16"/></label></div>{rerankerProvider !== "fake" && <div className="form-row"><label>Pinned model revision<input name="reranker_revision" required placeholder="Hugging Face commit SHA"/></label><label>Device<input name="reranker_device" placeholder="cpu or cuda" defaultValue="cpu"/></label></div>}<p className={styles.fieldNote}>Local rerankers are persisted with <code>local_files_only=true</code>; runtime model downloads are never enabled.</p></>}
            {!rerank && <><input type="hidden" name="rerank_final" value={rerankFinal}/><input type="hidden" name="reranker_model" value="fake-token-overlap-reranker-v1"/><input type="hidden" name="reranker_batch_size" value="16"/></>}
          </fieldset>

          <fieldset className={styles.configurationGroup}>
            <legend>Generation and pricing</legend>
            <div className="form-row"><label>Generation provider<select value={provider} onChange={(event)=>{const next=event.target.value;setProvider(next);setModel(PROVIDER_MODELS[next]);}}><option value="fake">Deterministic fake</option><option value="gemini">Google Gemini</option><option value="openai-compatible">OpenAI-compatible</option></select></label><label>Generation model<input name="model" value={model} onChange={(event)=>setModel(event.target.value)} /></label></div>
            {generationCapability ? <p className={styles.capability} data-available={generationCapability.available}><strong>{generationCapability.available ? "Available" : "Unavailable"}</strong><span>{generationCapability.reason}</span></p> : <p className={styles.fieldNote}>Availability is not reported for this provider. Credentials remain backend-only.</p>}
            <div className={styles.threeFields}><label>Temperature<input name="temperature" type="number" step="0.1" min="0" max="2" defaultValue="0" /></label><label>Output limit<input name="output_tokens" type="number" min="1" max="32000" defaultValue="512" /></label><label>Timeout (seconds)<input name="timeout_seconds" type="number" min="1" max="600" step="1" defaultValue="60" /></label></div>
            <label>Fake-provider answerability<select name="fake_answerability" defaultValue="auto"><option value="auto">Auto</option><option value="answerable">Answerable</option><option value="partially_answerable">Partially answerable</option><option value="unanswerable">Unanswerable</option></select></label>
            <div className="form-row"><label>Input price / 1M tokens<input name="input_price" type="number" min="0" step="0.000001" placeholder="Unavailable unless configured" /></label><label>Output price / 1M tokens<input name="output_price" type="number" min="0" step="0.000001" placeholder="Unavailable unless configured" /></label></div>
            <p className={styles.fieldNote}>Leave pricing blank when unknown; RAGScope persists unknown cost as unavailable, never zero.</p>
          </fieldset>

          <fieldset className={styles.configurationGroup}>
            <legend>Prompt and citations</legend>
            <div className="form-row"><label>Grounded prompt ID<input value={promptId} onChange={(event)=>setPromptId(event.target.value)} required /></label><label>Prompt version<input type="number" min="1" value={promptVersion} onChange={(event)=>setPromptVersion(Number(event.target.value))} required /></label></div>
            <p className={styles.fieldNote}>Freeze validates this reference against the immutable prompt registry. The installed default is <code>grounded-answer v1</code>.</p>
            <label>Citation verification method<input value="citation-existence-v1" readOnly aria-readonly="true" /></label>
            <label className={styles.option}><input type="checkbox" checked={requireCitations} onChange={(event)=>setRequireCitations(event.target.checked)}/> Require citations for factual claims</label>
          </fieldset>
          <div className={styles.formActions}><button type="submit" className={styles.draftButton} disabled={Boolean(busy)} onClick={()=>setSaveIntent("draft")}>{busy === "pipeline" && saveIntent === "draft" ? "Saving…" : "Save draft"}</button><button type="submit" disabled={Boolean(busy)} onClick={()=>setSaveIntent("freeze")}>{busy === "pipeline" && saveIntent === "freeze" ? "Saving…" : "Save & freeze"}</button></div>
        </form>
      </section>
      <section className={styles.mapPanel}><div className={styles.subheading}><h2>Pipeline map</h2><p>Observable stages and the active form configuration.</p></div><div className={styles.map}>{mapStages.map(([number,name,detail,active],index)=><div key={number}>{index>0&&<div className={styles.arrow} aria-hidden="true">↓</div>}<div className={styles.mapStage} data-active={active}><span className={styles.stageNumber}>{number}</span><span><strong>{name}</strong><small>{detail}</small></span></div></div>)}</div><div className={styles.mapNote}>Frozen snapshots preserve all values, prompt versions, providers, model settings, and adaptive router references.</div></section>
    </div>
    <section className={styles.registry} aria-labelledby="configuration-registry">
      <div className={styles.registryHeader}><div><h2 id="configuration-registry">Configuration registry</h2><p>Drafts can be frozen once; frozen snapshots remain immutable.</p></div><span className="muted">{pipelines.length} pipelines · {routers.length} routers</span></div>
      <div className={styles.registryGrid}>
        <div className={styles.registrySection}><h3>Pipeline configurations</h3><div className={styles.registryList}>{pipelines.length===0?<div className="empty">No pipeline configurations yet.</div>:pipelines.map((pipeline)=><div className={styles.registryItem} key={pipeline.id}><span><strong>{pipeline.name} v{pipeline.version}</strong><small>{pipeline.execution_mode} · {pipeline.retrieval_mode} · {String(pipeline.generation_configuration.model)}</small></span>{pipeline.frozen_at?<StatusBadge status="frozen"/>:<button className="secondary" onClick={()=>void api.freezePipeline(pipeline.id).then(refresh).catch((reason:Error)=>setError(reason.message))}>Freeze</button>}</div>)}</div></div>
        <div className={styles.registrySection}>
          <h3>Adaptive routers</h3>
          <form className={styles.form} onSubmit={createRouter}>
            <fieldset className={styles.configurationGroup}>
              <legend>Identity and deterministic policy</legend>
              <div className="form-row"><label>Name<input name="router_name" defaultValue="P5 deterministic router" required/></label><label>Record version<input name="router_version" type="number" min="1" defaultValue="1" required/></label></div>
              <div className="form-row"><label>Router ID<input name="router_id" defaultValue="deterministic-rule-router" required/></label><label>Router schema version<input name="router_schema_version" defaultValue="1.0.0" required/></label></div>
              <label>Fallback policy<input value="Reject unavailable route" readOnly aria-readonly="true"/><span className={styles.inputHint}>No silent fallback: unavailable capabilities produce <code>ROUTE_UNAVAILABLE</code>.</span></label>
              <div className={styles.modeChecks}>{RETRIEVAL_MODES.map((mode)=><label className={styles.inlineCheck} key={mode}><input type="checkbox" checked={routerAllowedModes.includes(mode)} onChange={(event)=>setRouterAllowedModes((current)=>event.target.checked?[...current,mode]:current.filter((item)=>item!==mode))}/>{mode}</label>)}</div>
            </fieldset>
            <fieldset className={styles.configurationGroup}>
              <legend>Query and route thresholds</legend>
              <div className="form-row"><label>Trivial-query max tokens<input type="number" min="0" max="20" value={routerTrivialTokens} onChange={(event)=>setRouterTrivialTokens(Number(event.target.value))}/></label><label>Rewrite threshold tokens<input type="number" min="1" max="500" value={routerRewriteTokens} onChange={(event)=>setRouterRewriteTokens(Number(event.target.value))}/></label></div>
              <div className="form-row"><label>Standard candidates<input type="number" min="1" max="200" value={routerStandardCandidates} onChange={(event)=>setRouterStandardCandidates(Number(event.target.value))}/></label><label>Complex candidates<input type="number" min="1" max="200" value={routerComplexCandidates} onChange={(event)=>setRouterComplexCandidates(Number(event.target.value))}/></label></div>
              <div className={styles.threeFields}><label>Standard context<input type="number" min="1" max="100000" value={routerStandardBudget} onChange={(event)=>setRouterStandardBudget(Number(event.target.value))}/></label><label>Broad context<input type="number" min="1" max="100000" value={routerBroadBudget} onChange={(event)=>setRouterBroadBudget(Number(event.target.value))}/></label><label>Complex context<input type="number" min="1" max="100000" value={routerComplexBudget} onChange={(event)=>setRouterComplexBudget(Number(event.target.value))}/></label></div>
              <div className={styles.modeChecks}><label className={styles.inlineCheck}><input type="checkbox" checked={routerComplexRewrite} onChange={(event)=>setRouterComplexRewrite(event.target.checked)}/>Rewrite complex queries</label><label className={styles.inlineCheck}><input type="checkbox" checked={routerComplexRerank} onChange={(event)=>setRouterComplexRerank(event.target.checked)}/>Rerank complex queries</label></div>
            </fieldset>
            <details className={styles.reasonAudit}><summary>Auditable route reason rules</summary><dl><div><dt>TRIVIAL_QUERY_NO_RETRIEVAL</dt><dd>Direct fact at or below the trivial token threshold.</dd></div><div><dt>METADATA_OR_EXACT_MATCH</dt><dd>Metadata filters, quoted phrases, or identifiers select lexical retrieval.</dd></div><div><dt>COMPLEX_EVIDENCE_SEARCH</dt><dd>Comparison, synthesis, multi-hop, or potentially-unanswerable intent selects hybrid retrieval.</dd></div><div><dt>BROAD_SEMANTIC_SEARCH</dt><dd>Broad exploratory intent selects dense retrieval and the broad context budget.</dd></div><div><dt>SEMANTIC_LOOKUP</dt><dd>Other semantic lookup intent selects dense retrieval with standard limits.</dd></div></dl></details>
            <button className="secondary" disabled={Boolean(busy)}>{busy === "router" ? "Saving…" : "Create router draft"}</button>
          </form>
          <div className={styles.registryList}>{routers.length===0?<div className="empty">No adaptive router configurations yet.</div>:routers.map((item)=><div className={styles.routerRegistryItem} key={item.id}><div className={styles.routerRegistryHeader}><span><strong>{item.name} v{item.version}</strong><small>{item.router_version} · deterministic · {String(item.configuration.fallback_policy??"reject")} fallback</small></span>{item.frozen_at?<StatusBadge status="frozen"/>:<button className="secondary" onClick={()=>void api.freezeRouterConfiguration(item.id).then(refresh).catch((reason:Error)=>setError(reason.message))}>Freeze</button>}</div><details><summary>Inspect frozen-by-value settings</summary><dl className={styles.snapshotList}><div><dt>Allowed modes</dt><dd>{Array.isArray(item.configuration.allowed_retrieval_modes)?item.configuration.allowed_retrieval_modes.join(", "):"—"}</dd></div><div><dt>Candidates</dt><dd>{String(item.configuration.standard_candidate_count??"—")} standard / {String(item.configuration.complex_candidate_count??"—")} complex</dd></div><div><dt>Context</dt><dd>{String(item.configuration.standard_context_budget??"—")} / {String(item.configuration.broad_context_budget??"—")} / {String(item.configuration.complex_context_budget??"—")}</dd></div><div><dt>Complex policy</dt><dd>rewrite {String(item.configuration.enable_complex_rewriting??false)} · rerank {String(item.configuration.enable_complex_reranking??false)}</dd></div><div><dt>Hash</dt><dd><code>{item.configuration_hash}</code></dd></div></dl></details></div>)}</div>
        </div>
      </div>
    </section>
    <section className={styles.queryPanel}><div className={styles.subheading}><p className="eyebrow">Smoke test</p><h2>Run a frozen pipeline</h2><p>Execute through the same persisted QueryRun orchestrator used by Query Laboratory and experiments.</p></div><div className={styles.queryGrid}><form className={styles.form} onSubmit={runQuery}><label>Ready corpus version<select name="corpus_version" required defaultValue=""><option value="" disabled>Select version</option>{readyVersions.map((version)=><option value={version.id} key={version.id}>{version.corpusName} · {version.version_label}</option>)}</select></label><label>Frozen pipeline<select name="pipeline" required defaultValue=""><option value="" disabled>Select pipeline</option>{frozenPipelines.map((pipeline)=><option value={pipeline.id} key={pipeline.id}>{pipeline.name} v{pipeline.version}</option>)}</select></label><label>Publication year filter<input name="year" type="number" min="1000" max="3000" placeholder="Optional" /></label><label>Question<textarea name="question" required rows={5} placeholder="What evidence does the corpus provide?" /></label><button disabled={Boolean(busy)||!readyVersions.length||!frozenPipelines.length}>{busy==="query"?"Running…":"Run pipeline"}</button></form><aside className={styles.queryHelp}><h3>Need deeper inspection?</h3><p>This quick run verifies a frozen configuration. Query Laboratory exposes the full observable execution timeline, rank movement, exact context, claims, citations, evaluation, and failure attribution.</p><a href="/laboratory">Open Query Laboratory →</a><p>{readyVersions.length} ready corpus version{readyVersions.length===1?"":"s"} and {frozenPipelines.length} frozen pipeline{frozenPipelines.length===1?"":"s"} are currently eligible.</p></aside></div></section>
    {run&&<section className={styles.result}><div className="panel-heading"><div><p className="eyebrow">Persisted QueryRun</p><h2>Grounded result</h2></div><div className={styles.actions}><a className="source-link" href={`/laboratory/${run.id}`}>Open full trace →</a><StatusBadge status={run.status}/></div></div><div className={styles.meta}><span>Answerability: <strong>{run.answerability_decision??"—"}</strong></span><span>{run.total_latency_ms??"—"} ms</span><span>{run.input_tokens??"—"} in / {run.output_tokens??"—"} out</span><span>Cost: {run.estimated_cost==null?"Unavailable":`$${run.estimated_cost.toFixed(6)}`}</span></div><div className={styles.answer}>{run.answer_text||run.abstention_reason||"No valid answer was produced."}</div>{run.limitations.length>0&&<div className="warnings"><strong>Limitations</strong><ul>{run.limitations.map((item)=><li key={item}>{item}</li>)}</ul></div>}<div className={styles.resultGrid}><div><h3>Claims & citations</h3>{claims.length===0?<p className="muted">No claims persisted.</p>:claims.map((claim)=><article className={styles.claim} key={claim.id}><p>{claim.claim_text}</p><footer>{claim.citations.map((citation)=><a href={`/documents/${citation.document_id}`} key={citation.id}>[{citation.citation_id}] p.{citation.page_number??"—"}</a>)}</footer></article>)}</div><div><h3>Context sources</h3>{sources.length===0?<p className="muted">No corpus sources were selected.</p>:sources.map((source)=><article className={styles.source} data-selected={source.selected} key={source.id}><strong>{source.citation_id?`[${source.citation_id}]`:"Excluded"}</strong><span>{source.token_count} tokens · pages {source.page_start??"—"}–{source.page_end??"—"}</span><p>{source.text.slice(0,240)}</p>{source.exclusion_reason&&<small>{source.exclusion_reason}</small>}</article>)}</div></div><h3 className={styles.candidatesTitle}>Retrieved candidates</h3><div className={styles.candidateTable} role="table" aria-label="Retrieved candidates">{retrieval.length===0?<div className="empty">No retrieval candidates persisted for this route.</div>:retrieval.map((item)=><div className={styles.candidate} role="row" key={item.id}><span>{item.retriever_type}</span><span>rank {item.original_rank??item.fused_rank??"—"}</span><span>rerank {item.reranked_rank??"—"}</span><span>{item.selected_for_context?"Selected":"Candidate"}</span><p>{item.text.slice(0,180)}</p></div>)}</div></section>}
  </main>;
}
