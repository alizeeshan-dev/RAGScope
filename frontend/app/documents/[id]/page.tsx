"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Artifact, DocumentElement, SourceDocument } from "@/lib/types";
import { StatusBadge } from "@/components/StatusBadge";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>(); const [document, setDocument] = useState<SourceDocument | null>(null); const [elements, setElements] = useState<DocumentElement[]>([]); const [artifacts, setArtifacts] = useState<Artifact[]>([]); const [selected, setSelected] = useState<DocumentElement | null>(null); const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [doc, els, storedArtifacts] = await Promise.all([api.getDocument(id), api.getElements(id), api.getArtifacts(id)]);
      const query = new URLSearchParams(window.location.search);
      const requestedElement = query.get("element");
      const requestedChunk = query.get("chunk");
      const requestedPage = Number(query.get("page"));
      let target = requestedElement ? els.find((element) => element.id === requestedElement) : undefined;
      if (!target && requestedChunk) {
        const chunk = (await api.getChunks(doc.corpus_version_id)).find((item) => item.id === requestedChunk);
        target = els.find((element) => chunk?.source_element_ids.includes(element.id));
      }
      if (!target && requestedPage) target = els.find((element) => element.page_number === requestedPage);
      setDocument(doc); setElements(els); setArtifacts(storedArtifacts); setSelected(target ?? els[0] ?? null);
    } catch (err) { setError(err instanceof Error ? err.message : "Load failed"); }
  }, [id]); useEffect(() => { void load(); }, [load]);
  if (!document) return <main className="shell"><p>{error || "Loading document…"}</p></main>;
  const original = artifacts.find((artifact) => artifact.artifact_type === "original");
  return <><header className="topbar"><a className="brand" href="/"><span className="brand-mark">R</span><div><strong>RAGScope</strong><small>Document inspector</small></div></a><StatusBadge status={document.parse_status} /></header><main id="main" className="shell"><a className="back" href={`/versions/${document.corpus_version_id}`}>← Corpus version</a><section className="page-title"><div><p className="eyebrow">Scientific source</p><h1>{document.title || "Untitled document"}</h1><p>{document.authors.join(", ") || "Authors not supplied"}{document.publication_year ? ` · ${document.publication_year}` : ""}</p></div><div className="action-bar"><a className="button-link secondary-link" href={`/datasets?document=${id}`}>Extract datasets</a><button onClick={() => void api.parseDocument(id).then(load).catch((err: Error) => setError(err.message))}>Parse document</button></div></section>{error && <div role="alert" className="alert">{error}</div>}{document.parse_warnings.length > 0 && <section className="warnings"><strong>Parser uncertainty</strong><ul>{document.parse_warnings.map((warning, index) => <li key={index}>{typeof warning === "string" ? warning : String(warning.message ?? warning.code ?? JSON.stringify(warning))}</li>)}</ul></section>}{original && document.mime_type === "application/pdf" && <section className="pdf-preview"><div><strong>Original artifact</strong><span>{original.original_filename} · {(original.size_bytes / 1024).toFixed(1)} KiB · SHA-256 {original.content_hash.slice(0, 16)}…</span></div><iframe title="Original PDF preview" src={api.artifactContentUrl(original.id)} /></section>}<div className="inspector"><nav className="element-tree" aria-label="Parsed elements">{elements.map((element) => <button className={selected?.id === element.id ? "selected" : ""} key={element.id} onClick={() => setSelected(element)}><span>{element.element_type}</span><strong>{element.text.slice(0, 90) || "Empty element"}</strong><small>#{element.sequence_number} · page {element.page_number ?? "—"}</small></button>)}</nav><section className="element-detail">{selected ? <><div className="element-meta"><StatusBadge status={selected.element_type}/><span>Page {selected.page_number ?? "not available"}</span><span>Sequence {selected.sequence_number}</span></div><h2>{selected.section_path.join(" / ") || "Document root"}</h2><div className={`element-body type-${selected.element_type}`}>{selected.text}</div><h3>Provenance</h3><dl><dt>Element ID</dt><dd><code>{selected.id}</code></dd><dt>Parent</dt><dd><code>{selected.parent_element_id ?? "none"}</code></dd><dt>Bounding box</dt><dd><code>{selected.bounding_box ? JSON.stringify(selected.bounding_box) : "unavailable"}</code></dd><dt>Parser metadata</dt><dd><pre>{JSON.stringify(selected.parser_metadata, null, 2)}</pre></dd></dl></> : <p>Select an element to inspect it.</p>}</section></div></main></>;
}
