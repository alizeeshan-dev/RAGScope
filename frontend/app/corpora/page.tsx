"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Corpus } from "@/lib/types";
import { StatusBadge } from "@/components/StatusBadge";

export default function CorpusStudioPage() {
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    setLoading(true);
    try { const rows = await api.listCorpora(); setCorpora(rows); setSelectedId((current) => current ?? rows[0]?.id ?? null); setError(""); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load corpora."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  async function createCorpus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); const formElement = event.currentTarget; const form = new FormData(formElement);
    try { const created = await api.createCorpus({ name: String(form.get("name")), domain: String(form.get("domain") ?? ""), description: String(form.get("description") ?? "") }); formElement.reset(); await refresh(); setSelectedId(created.id); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not create corpus."); }
    finally { setBusy(false); }
  }
  const versions = corpora.flatMap((corpus) => corpus.versions ?? []);
  const selected = corpora.find((corpus) => corpus.id === selectedId) ?? corpora[0] ?? null;
  const selectedVersion = selected?.versions?.[0] ?? null;
  const metrics = useMemo(() => ({ ready: versions.filter((version) => version.status === "ready").length, documents: versions.reduce((sum, version) => sum + version.document_count, 0) }), [versions]);

  return <main id="main" className="design-shell corpus-studio-screen">
    <header className="design-page-heading"><div><p className="design-eyebrow">Corpus Studio</p><h1>Scientific corpora</h1><p>Build immutable, provenance-preserving collections for reproducible retrieval research.</p></div><span className="design-status-note"><i />PostgreSQL + pgvector</span></header>
    {error && <div role="alert" className="alert">{error}</div>}
    <section className="studio-metrics" aria-label="Corpus metrics"><article><strong>{corpora.length}</strong><span>Corpora</span></article><article><strong>{metrics.ready}</strong><span>Ready versions</span></article><article><strong>{metrics.documents}</strong><span>Documents</span></article><article><strong>{versions.length}</strong><span>Version snapshots</span></article></section>
    <section className="corpus-studio-grid">
      <div className="design-card corpus-library"><header className="design-section-heading"><div><p className="design-eyebrow">Library</p><h2>Research collections</h2></div><span>{corpora.length} total</span></header>
        <div className="corpus-design-list">
          {corpora.map((corpus) => { const latest = corpus.versions?.[0]; const isSelected = corpus.id === selected?.id; return <article className={isSelected ? "selected" : ""} key={corpus.id} onMouseEnter={() => setSelectedId(corpus.id)}>
            <button className="corpus-select" type="button" onClick={() => setSelectedId(corpus.id)} aria-label={`Preview ${corpus.name}`} aria-pressed={isSelected}><span aria-hidden="true">{isSelected ? "●" : "○"}</span></button>
            <Link href={`/corpora/${corpus.id}`}><div><strong>{corpus.name}</strong><small>{corpus.domain || "Unspecified scientific domain"}</small></div><p>{corpus.description || "No collection description supplied."}</p><footer><span>{corpus.versions?.length ?? 0} versions</span><span>{(corpus.versions ?? []).reduce((sum, version) => sum + version.document_count, 0)} documents</span>{latest ? <StatusBadge status={latest.status} /> : <span className="muted">Draft container</span>}<i aria-hidden="true">↗</i></footer></Link>
          </article>; })}
          {!loading && corpora.length === 0 && <div className="design-empty"><strong>No scientific corpus yet</strong><span>Create the first collection, then add a version to upload and index documents.</span></div>}
          {loading && <div className="design-loading">Loading corpus library…</div>}
        </div>
        {selected && <div className="selected-corpus-config"><p className="design-eyebrow">Selected configuration</p><div><span><small>Collection</small><strong>{selected.name}</strong></span><span><small>Latest version</small><strong>{selectedVersion?.version_label ?? "Not created"}</strong></span><span><small>Chunking</small><strong>{String(selectedVersion?.chunker_configuration.strategy ?? "Not configured")}</strong></span><span><small>Embedding</small><strong>{String(selectedVersion?.embedding_configuration.model ?? "Not configured")}</strong></span><Link href={`/corpora/${selected.id}`}>Open collection →</Link></div></div>}
      </div>
      <aside className="design-card new-corpus-card"><p className="design-eyebrow">New research collection</p><h2>Create corpus</h2><p>A corpus is the long-lived container. Version-specific parsing, chunking, embeddings, and files are configured after creation.</p><form onSubmit={createCorpus}><label>Corpus name<input name="name" required maxLength={200} placeholder="Scientific dataset papers" /></label><label>Research domain<input name="domain" maxLength={120} placeholder="Natural language processing" /></label><label>Scope and inclusion criteria<textarea name="description" rows={5} placeholder="Describe the intended source set and selection criteria…" /></label><button disabled={busy}>{busy ? "Creating…" : "Create collection"} <span aria-hidden="true">→</span></button></form><div className="untrusted-note"><span aria-hidden="true">◇</span><p><strong>Documents remain untrusted data.</strong> Uploaded content can never issue instructions or trigger external actions.</p></div></aside>
    </section>
  </main>;
}
