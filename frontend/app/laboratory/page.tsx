"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { Corpus, PipelineConfiguration } from "@/lib/types";
import styles from "./laboratory.module.css";

export default function LaboratoryLauncher() {
  const router = useRouter();
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [pipelines, setPipelines] = useState<PipelineConfiguration[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.listCorpora(), api.listPipelines()])
      .then(([nextCorpora, nextPipelines]) => {
        setCorpora(nextCorpora);
        setPipelines(nextPipelines);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load the laboratory"))
      .finally(() => setLoading(false));
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
    <main id="main" className={`${styles.shell} shell laboratory-shell`}>
        <section className={styles.hero}>
          <div><p className="eyebrow">Query Laboratory</p><h1>Inspect every step.</h1><p>Run a frozen pipeline and follow the observable path from question to retrieved evidence, context, claims, and citations.</p></div>
          <div className={styles.heroMeta}><span><i className={styles.liveDot} /> Observable execution</span><a href="/runtime">Configure pipelines <span aria-hidden="true">↗</span></a></div>
        </section>
        {error && <div className="alert" role="alert">{error}</div>}
        <section className={styles.launcher} aria-labelledby="new-query-heading">
          <div className={styles.launcherHeading}><div><p className="eyebrow">New execution</p><h2 id="new-query-heading">Run a frozen pipeline</h2></div><span>Configuration snapshots are preserved with every run.</span></div>
          <form className={styles.form} onSubmit={runQuery}>
            <div className={styles.controlRow}>
              <label>Corpus version<select name="corpus_version_id" required defaultValue="" disabled={loading}><option value="" disabled>{loading ? "Loading corpus versions…" : "Select corpus version"}</option>{readyVersions.map((version) => <option key={version.id} value={version.id}>{version.corpusName} · {version.version_label}</option>)}</select></label>
              <label>Pipeline configuration<select name="pipeline_configuration_id" required defaultValue="" disabled={loading}><option value="" disabled>{loading ? "Loading pipelines…" : "Select frozen pipeline"}</option>{frozenPipelines.map((pipeline) => <option key={pipeline.id} value={pipeline.id}>{pipeline.name} v{pipeline.version} · {pipeline.retrieval_mode}</option>)}</select></label>
            </div>
            <label className={styles.question}>Question<textarea name="query_text" required rows={3} maxLength={10000} placeholder="Ask a question about the selected scientific corpus…" /></label>
            <div className={styles.runRow}><p>{readyVersions.length} ready corpus {readyVersions.length === 1 ? "version" : "versions"} · {frozenPipelines.length} frozen {frozenPipelines.length === 1 ? "pipeline" : "pipelines"}</p><button disabled={busy || loading || readyVersions.length === 0 || frozenPipelines.length === 0}>{busy ? "Running pipeline…" : "Run query"}<span aria-hidden="true">→</span></button></div>
          </form>
        </section>

        <section className={styles.inspectGrid} aria-label="Observable pipeline preview">
          <article className={styles.workspaceCard}>
            <div className={styles.cardHeading}><div><p className="eyebrow">Observable execution</p><h2>Stage-by-stage timeline</h2></div><span className={styles.pill}>After execution</span></div>
            <ol className={styles.stageList}>
              {["Query processing", "Configured route", "Retrieval & fusion", "Reranking", "Context construction", "Grounded generation"].map((stage, index) => <li key={stage}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{stage}</strong><small>Status, latency, configuration and artifacts</small></div></li>)}
            </ol>
          </article>
          <article className={styles.workspaceCard}>
            <div className={styles.cardHeading}><div><p className="eyebrow">Evidence movement</p><h2>Ranks stay inspectable</h2></div></div>
            <div className={styles.flow}><span>Retrieve</span><i>→</i><span>Fuse</span><i>→</i><span>Rerank</span><i>→</i><span>Context</span></div>
            <div className={styles.emptyState}><strong>Run a query to inspect evidence</strong><p>Candidate scores, original and fused ranks, rank movement, context exclusions, citations, and exact source passages will appear in the complete run view.</p></div>
            <div className={styles.securityNote}><span aria-hidden="true">◇</span><p><strong>Observable application trace</strong><br />Never hidden model reasoning or chain-of-thought.</p></div>
          </article>
        </section>
      </main>
  );
}
