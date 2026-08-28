"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Corpus } from "@/lib/types";
import { StatusBadge } from "@/components/StatusBadge";

export default function Home() {
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try { setCorpora(await api.listCorpora()); setError(""); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not load corpora"); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function createCorpus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api.createCorpus({ name: String(form.get("name")), description: String(form.get("description") ?? ""), domain: String(form.get("domain") ?? "") });
      formElement.reset(); await refresh();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not create corpus"); }
    finally { setBusy(false); }
  }

  const versions = corpora.flatMap((corpus) => corpus.versions ?? []);
  return (
    <>
      <header className="topbar"><div className="brand"><span className="brand-mark">R</span><div><strong>RAGScope</strong><small>Research runtime</small></div></div><nav className="top-nav"><a href="/laboratory">Query Laboratory</a><a href="/comparisons">Pipeline Comparison</a><a href="/datasets">Datasets</a><a href="/benchmarks">Benchmarks</a><a href="/experiments">Experiments</a><a href="/runtime">Pipelines</a></nav></header>
      <main id="main" className="shell">
        <section className="hero"><div><p className="eyebrow">Observable by construction</p><h1>Build evidence you can inspect.</h1><p>Turn scientific sources into frozen, provenance-preserving lexical and dense indexes.</p></div><div className="metrics"><div><strong>{corpora.length}</strong><span>Corpora</span></div><div><strong>{versions.filter((v) => v.status === "ready").length}</strong><span>Ready versions</span></div><div><strong>{versions.reduce((n, v) => n + v.document_count, 0)}</strong><span>Documents</span></div></div></section>
        {error && <div role="alert" className="alert">{error}</div>}
        <div className="workspace-grid">
          <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Library</p><h2>Scientific corpora</h2></div></div>
            <div className="corpus-list">{corpora.length === 0 ? <div className="empty"><strong>No corpus yet</strong><span>Create a corpus to start a reproducible document collection.</span></div> : corpora.map((corpus) => <a className="corpus-row" href={`/corpora/${corpus.id}`} key={corpus.id}><div><strong>{corpus.name}</strong><span>{corpus.domain || "Unspecified domain"}</span></div><div className="version-stack">{(corpus.versions ?? []).slice(0, 2).map((version) => <StatusBadge key={version.id} status={version.status} />)}{!corpus.versions?.length && <span className="muted">No versions</span>}</div><span aria-hidden="true">→</span></a>)}</div>
          </section>
          <aside className="panel create-panel"><p className="eyebrow">New collection</p><h2>Create corpus</h2><p className="muted">A corpus is a long-lived container. Its versions become immutable research snapshots.</p>
            <form onSubmit={createCorpus}><label>Name<input name="name" required maxLength={200} placeholder="Emotion datasets" /></label><label>Domain<input name="domain" maxLength={120} placeholder="Affective computing" /></label><label>Description<textarea name="description" rows={4} placeholder="Scope and inclusion criteria" /></label><button disabled={busy}>{busy ? "Creating…" : "Create corpus"}</button></form>
          </aside>
        </div>
      </main>
    </>
  );
}
