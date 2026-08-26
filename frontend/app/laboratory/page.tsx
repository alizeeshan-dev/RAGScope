"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { Corpus, PipelineConfiguration } from "@/lib/types";

export default function LaboratoryLauncher() {
  const router = useRouter();
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [pipelines, setPipelines] = useState<PipelineConfiguration[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.listCorpora(), api.listPipelines()])
      .then(([nextCorpora, nextPipelines]) => {
        setCorpora(nextCorpora);
        setPipelines(nextPipelines);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load the laboratory"));
  }, []);

  async function runQuery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const run = await api.createQueryRun({
        corpus_version_id: String(form.get("corpus_version_id")),
        pipeline_configuration_id: String(form.get("pipeline_configuration_id")),
        query_text: String(form.get("query_text")),
        filters: { document_ids: [], publication_years: [] },
      });
      router.push(`/laboratory/${run.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The query could not be started");
      setBusy(false);
    }
  }

  const readyVersions = corpora.flatMap((corpus) =>
    (corpus.versions ?? [])
      .filter((version) => version.status === "ready")
      .map((version) => ({ ...version, corpusName: corpus.name })),
  );
  const frozenPipelines = pipelines.filter((pipeline) => pipeline.frozen_at !== null);

  return (
    <>
      <header className="topbar">
        <a className="brand" href="/"><span className="brand-mark">R</span><div><strong>RAGScope</strong><small>Query Laboratory</small></div></a>
        <nav className="top-nav" aria-label="Research tools"><a href="/runtime">Pipeline configuration</a><span className="phase">Observable execution</span></nav>
      </header>
      <main id="main" className="shell laboratory-shell">
        <a className="back" href="/">← Corpora</a>
        <section className="lab-hero">
          <div><p className="eyebrow">Query Laboratory</p><h1>Inspect the path from question to evidence.</h1><p>Run one frozen pipeline and reconstruct its observable stages, ranks, context, claims, and citations. This is an execution trace—not model reasoning.</p></div>
        </section>
        {error && <div className="alert" role="alert">{error}</div>}
        <section className="panel lab-launcher" aria-labelledby="new-query-heading">
          <div><p className="eyebrow">New execution</p><h2 id="new-query-heading">Run a fixed pipeline</h2><p className="muted">The corpus version and pipeline snapshot are persisted with the QueryRun.</p></div>
          <form className="runtime-form" onSubmit={runQuery}>
            <div className="form-row">
              <label>Ready corpus version<select name="corpus_version_id" required defaultValue=""><option value="" disabled>Select corpus version</option>{readyVersions.map((version) => <option key={version.id} value={version.id}>{version.corpusName} · {version.version_label}</option>)}</select></label>
              <label>Frozen pipeline<select name="pipeline_configuration_id" required defaultValue=""><option value="" disabled>Select pipeline</option>{frozenPipelines.map((pipeline) => <option key={pipeline.id} value={pipeline.id}>{pipeline.name} v{pipeline.version} · {pipeline.retrieval_mode}</option>)}</select></label>
            </div>
            <label>Question<textarea name="query_text" required rows={5} maxLength={10000} placeholder="What does the corpus evidence show?" /></label>
            <button disabled={busy || readyVersions.length === 0 || frozenPipelines.length === 0}>{busy ? "Running pipeline…" : "Run and inspect"}</button>
          </form>
        </section>
      </main>
    </>
  );
}
