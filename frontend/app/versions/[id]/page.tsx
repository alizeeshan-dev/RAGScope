"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ConfigSummary } from "@/components/ConfigSummary";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { Chunk, CorpusVersion, IndexStatus, SourceDocument } from "@/lib/types";

const FIXED = { strategy: "fixed", target_tokens: 256, overlap_tokens: 32, include_section_titles: true };
const STRUCTURE = { strategy: "structure-aware", target_tokens: 384, overlap_tokens: 32, include_section_titles: true, preserve_tables: true };

export default function VersionPage() {
  const { id } = useParams<{ id: string }>();
  const [version, setVersion] = useState<CorpusVersion | null>(null);
  const [indexes, setIndexes] = useState<IndexStatus[]>([]);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const load = useCallback(async () => {
    try {
      setVersion(await api.getVersion(id));
      const [status, storedChunks, storedDocuments] = await Promise.allSettled([api.getIndexStatus(id), api.getChunks(id), api.listDocuments(id)]);
      if (status.status === "fulfilled") setIndexes(status.value);
      if (storedChunks.status === "fulfilled") setChunks(storedChunks.value);
      if (storedDocuments.status === "fulfilled") setDocuments(storedDocuments.value);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Load failed"); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);
  async function act(name: string, action: () => Promise<unknown>) { setBusy(name); setError(""); try { await action(); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : `${name} failed`); } finally { setBusy(""); } }
  async function upload(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = event.currentTarget; await act("upload", () => api.uploadDocument(id, new FormData(form))); form.reset(); }
  if (!version) return <main className="shell"><p>{error || "Loading version…"}</p></main>;
  const editable = version.status === "draft" && !version.frozen_at;
  const lexical = indexes.find((item) => item.index_type === "lexical");
  const dense = indexes.find((item) => item.index_type === "dense");
  return <>
    <header className="topbar"><a className="brand" href="/"><span className="brand-mark">R</span><div><strong>RAGScope</strong><small>Corpus Studio</small></div></a><StatusBadge status={version.status} /></header>
    <main id="main" className="shell">
      <a className="back" href={`/corpora/${version.corpus_id}`}>← Corpus</a>
      <section className="page-title"><div><p className="eyebrow">Corpus version</p><h1>{version.version_label}</h1><p>{version.frozen_at ? `Frozen ${new Date(version.frozen_at).toLocaleString()}` : "Editable research snapshot"}</p></div><div className="action-bar"><button disabled={!editable || Boolean(busy)} onClick={() => void act("fixed chunks", () => api.chunkVersion(id, FIXED))}>Fixed chunks</button><button disabled={!editable || Boolean(busy)} onClick={() => void act("structure chunks", () => api.chunkVersion(id, STRUCTURE))}>Structure chunks</button><button disabled={!editable || Boolean(busy)} onClick={() => void act("index", () => api.indexVersion(id))}>Build indexes</button><button className="secondary" disabled={version.status !== "ready" || Boolean(busy)} onClick={() => void act("freeze", () => api.freezeVersion(id))}>Freeze version</button></div></section>
      {error && <div role="alert" className="alert">{error}</div>}
      <div className="config-grid"><ConfigSummary label="Parser snapshot" value={version.parser_configuration}/><ConfigSummary label="Active chunker" value={version.chunker_configuration}/><ConfigSummary label="Embedding snapshot" value={version.embedding_configuration}/></div>
      <div className="workspace-grid">
        <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Sources</p><h2>Ingest documents</h2></div><span>{version.document_count} stored</span></div>{editable ? <form className="upload" onSubmit={upload}><label>Scientific file<input type="file" name="file" accept=".pdf,.md,.markdown,.txt,application/pdf,text/markdown,text/plain" required /></label><label>Title override<input name="title" placeholder="Optional" /></label><button disabled={Boolean(busy)}>{busy === "upload" ? "Uploading…" : "Preserve & upload"}</button></form> : <p className="muted">This snapshot is immutable.</p>}<div className="document-list">{documents.map((document) => <a href={`/documents/${document.id}`} key={document.id}><span><strong>{document.title || "Untitled"}</strong><small>{document.mime_type}</small></span><StatusBadge status={document.parse_status}/><span>Inspect →</span></a>)}</div><p className="security-note">Originals are content-hashed and random-keyed outside application source. PDF, Markdown and plain text only.</p></section>
        <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Integrity</p><h2>Index status</h2></div>{indexes.length > 0 && <StatusBadge status={indexes.every((item) => item.integrity_valid) ? "ready" : "failed"} />}</div>{indexes.length ? <div className="index-metrics"><div><strong>{lexical?.chunk_count ?? 0}</strong><span>Active chunks</span></div><div><strong>{lexical?.indexed_count ?? 0}</strong><span>Lexical</span></div><div><strong>{dense?.indexed_count ?? 0}</strong><span>Dense</span></div><div><strong>{indexes.reduce((sum, item) => sum + item.failure_count, 0)}</strong><span>Failures</span></div></div> : <p className="muted">No index has been built.</p>}</section>
      </div>
      <section className="panel"><div className="panel-heading"><div><p className="eyebrow">Retrieval units</p><h2>Chunk inspector</h2></div><span>{chunks.length} shown · both strategies retained</span></div><div className="chunk-list">{chunks.slice(0, 100).map((chunk) => <article className="chunk" key={chunk.id}><div><span className="mono">#{chunk.sequence_number} · {chunk.chunker_id}</span><span>{chunk.token_count} tokens · pages {chunk.page_start ?? "—"}–{chunk.page_end ?? "—"}</span></div><h3>{chunk.section_path.join(" / ") || "Unsectioned content"}</h3><p>{chunk.text}</p><footer><code>{chunk.content_hash.slice(0, 20)}…</code><a href={`/documents/${chunk.document_id}`}>{chunk.source_element_ids.length} source elements →</a></footer></article>)}{!chunks.length && <div className="empty"><strong>No chunks yet</strong><span>Parse documents, then generate fixed or structure-aware chunks.</span></div>}</div></section>
    </main>
  </>;
}
