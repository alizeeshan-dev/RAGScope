"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Corpus } from "@/lib/types";
import { StatusBadge } from "@/components/StatusBadge";
import { ConfigSummary } from "@/components/ConfigSummary";

export default function CorpusPage() {
  const { id } = useParams<{ id: string }>();
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => { try { setCorpus(await api.getCorpus(id)); } catch (err) { setError(err instanceof Error ? err.message : "Load failed"); } }, [id]);
  useEffect(() => { void load(); }, [load]);
  async function addVersion(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); try { await api.createVersion(id, { version_label: String(form.get("label")), parser_configuration: { parser_id: "docling-pdf", version: "2" }, chunker_configuration: { strategy: "structure-aware", target_tokens: 384, overlap_tokens: 32, include_section_titles: true, preserve_tables: true, version: "1" }, embedding_configuration: { provider: "fake", model: "fake-hash-embedding-v1", dimension: 64, preprocessing_version: "unicode-word-v1" } }); event.currentTarget.reset(); await load(); } catch (err) { setError(err instanceof Error ? err.message : "Version creation failed"); } }
  if (!corpus) return <main className="shell"><a href="/">← Corpora</a><p>{error || "Loading…"}</p></main>;
  return <><header className="topbar"><a className="brand" href="/"><span className="brand-mark">R</span><div><strong>RAGScope</strong><small>Corpus Studio</small></div></a><span className="phase">Version control</span></header><main id="main" className="shell"><a className="back" href="/">← All corpora</a><section className="page-title"><div><p className="eyebrow">{corpus.domain || "Scientific corpus"}</p><h1>{corpus.name}</h1><p>{corpus.description || "No description supplied."}</p></div><form className="inline-form" onSubmit={addVersion}><label>New version label<input name="label" required placeholder="v1" /></label><button>Create draft</button></form></section>{error && <div role="alert" className="alert">{error}</div>}<section className="version-list">{(corpus.versions ?? []).map((version) => <a href={`/versions/${version.id}`} className="version-card" key={version.id}><div className="version-head"><div><span className="mono">{version.version_label}</span><StatusBadge status={version.status} /></div><span>{version.document_count} documents →</span></div><div className="config-grid"><ConfigSummary label="Parser" value={version.parser_configuration} /><ConfigSummary label="Chunker" value={version.chunker_configuration} /><ConfigSummary label="Embeddings" value={version.embedding_configuration} /></div><div className="version-foot"><span>Created {new Date(version.created_at).toLocaleString()}</span><code>{version.content_hash ? version.content_hash.slice(0, 16) + "…" : "Hash computed at freeze"}</code></div></a>)}{!corpus.versions?.length && <div className="empty"><strong>No versions</strong><span>Create the first draft to ingest documents.</span></div>}</section></main></>;
}
