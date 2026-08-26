"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { ResearchHeader } from "@/components/ResearchHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { Chunk, DatasetCorrection, DatasetRecord, DocumentElement, FieldEvidence, FieldReviewAction, SourceDocument } from "@/lib/types";

const FIELDS = [
  ["name", "Dataset name"], ["description", "Description"], ["domain", "Domain"], ["modalities", "Modalities"],
  ["task_types", "Tasks supported"], ["instance_count", "Number of instances"], ["participant_count", "Number of participants"],
  ["annotation_types", "Annotation types"], ["human_ratings", "Human ratings"], ["languages", "Languages"],
  ["collection_method", "Collection method"], ["license", "License"], ["access_url", "Access URL"], ["known_limitations", "Known limitations"],
] as const;

const ARRAY_FIELDS = new Set(["modalities", "task_types", "annotation_types", "languages"]);
const NUMBER_FIELDS = new Set(["instance_count", "participant_count"]);
type EvidenceSource = { document_id: string; page_number: number | null; element_id: string | null; chunk_id: string | null; selected_text: string };

function showValue(value: unknown, notStated = false) {
  if (notStated) return "Not stated in source";
  if (value == null || value === "") return "Null / not yet resolved";
  if (Array.isArray(value)) return value.join(", ") || "Empty list";
  return String(value);
}

function parseValue(field: string, value: string): unknown {
  if (ARRAY_FIELDS.has(field)) return value.split(",").map((item) => item.trim()).filter(Boolean);
  if (NUMBER_FIELDS.has(field)) return value.trim() ? Number(value) : null;
  return value;
}

function evidenceFor(record: DatasetRecord, field: string): FieldEvidence[] {
  return (record.field_evidence ?? record.evidence ?? []).filter((item) => item.field_name === field);
}

function historyFor(record: DatasetRecord, field: string): DatasetCorrection[] {
  return (record.correction_history ?? record.revisions ?? []).filter((item) => item.field_name === field).sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export default function DatasetRecordPage() {
  const { id } = useParams<{ id: string }>();
  const [record, setRecord] = useState<DatasetRecord | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [evidenceField, setEvidenceField] = useState<string | null>(null);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [elements, setElements] = useState<DocumentElement[]>([]);
  const [evidenceDocument, setEvidenceDocument] = useState("");
  const [evidenceSource, setEvidenceSource] = useState<EvidenceSource | null>(null);
  const [evidenceText, setEvidenceText] = useState("");

  const load = useCallback(async () => {
    try { setRecord(await api.getDatasetRecord(id)); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Dataset record could not be loaded"); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  const supportedFields = useMemo(() => record ? FIELDS.filter(([field]) => evidenceFor(record, field).length > 0) : [], [record]);

  async function review(field: string, action: FieldReviewAction) {
    if (!record) return;
    setBusy(field); setError("");
    try {
      const value = action === "edit" ? parseValue(field, draft) : undefined;
      setRecord(await api.reviewDatasetField(record.id, { field_name: field, action, value, evidence_ids: evidenceFor(record, field).map((item) => item.id), reviewer_note: note || undefined }));
      setEditing(null); setDraft(""); setNote("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Review action failed"); }
    finally { setBusy(""); }
  }

  async function updateRecordState(action: "approve_record" | "reopen_record") {
    if (!record) return;
    setBusy("record"); setError("");
    try { setRecord(await api.reviewDatasetField(record.id, { action })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Record state could not be changed"); }
    finally { setBusy(""); }
  }

  async function openEvidencePicker(field: string) {
    if (!record) return;
    setEvidenceField(field); setEvidenceDocument(""); setEvidenceSource(null); setEvidenceText(""); setError("");
    try { const [storedDocuments, storedChunks] = await Promise.all([api.listDocuments(record.corpus_version_id), api.getChunks(record.corpus_version_id)]); setDocuments(storedDocuments); setChunks(storedChunks); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Source provenance could not be loaded"); }
  }

  async function chooseEvidenceDocument(documentId: string) {
    setEvidenceDocument(documentId); setEvidenceSource(null); setEvidenceText("");
    try { setElements(await api.getElements(documentId)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Parsed elements could not be loaded"); }
  }

  function chooseEvidenceSource(source: EvidenceSource) { setEvidenceSource(source); setEvidenceText(source.selected_text); }

  async function addEvidence() {
    if (!record || !evidenceField || !evidenceSource || !evidenceText.trim()) return;
    setBusy("evidence"); setError("");
    try {
      await api.addDatasetFieldEvidence(record.id, { ...evidenceSource, field_name: evidenceField, selected_text: evidenceText.trim(), reviewer_note: note || undefined });
      await load(); setEvidenceField(null); setEvidenceSource(null); setEvidenceText(""); setNote("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Evidence could not be added"); }
    finally { setBusy(""); }
  }

  function exportRecord() {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `dataset-record-${record.id}.json`; anchor.click(); URL.revokeObjectURL(url);
  }

  if (!record) return <><ResearchHeader context="Dataset review" /><main id="main" className="shell"><p>{error || "Loading dataset record…"}</p></main></>;

  return (
    <><ResearchHeader context="Dataset review" /><main id="main" className="shell intelligence-shell">
      <a className="back" href="/datasets">← Dataset catalog</a>
      <section className="page-title"><div><p className="eyebrow">Evidence-centric review</p><h1>{record.name ?? "Unnamed dataset"}</h1><p>Original model suggestions remain immutable. Every human action is recorded separately.</p></div><div className="action-bar"><StatusBadge status={record.review_status} /><button className="secondary" onClick={exportRecord}>Export JSON</button>{record.review_status !== "approved" ? <button disabled={Boolean(busy)} onClick={() => void updateRecordState("approve_record")}>Approve record</button> : <button className="secondary" disabled={Boolean(busy)} onClick={() => void updateRecordState("reopen_record")}>Reopen review</button>}</div></section>
      {error && <div className="alert" role="alert">{error}</div>}
      <section className="review-summary panel"><div><span>Extraction</span><strong>{record.extraction_status}</strong></div><div><span>Fields with evidence</span><strong>{supportedFields.length} / {FIELDS.length}</strong></div><div><span>Corrections</span><strong>{(record.correction_history ?? record.revisions ?? []).length}</strong></div><div><span>Schema/config</span><strong>{String(record.extraction_configuration?.field_schema_version ?? "recorded snapshot")}</strong></div></section>
      <section className="field-review-list" aria-label="Dataset fields">
        {FIELDS.map(([field, label]) => {
          const current = record.current_values?.[field] ?? record[field as keyof DatasetRecord];
          const original = record.original_values?.[field] ?? record.original_model_output?.[field];
          const evidence = evidenceFor(record, field);
          const history = historyFor(record, field);
          const fieldState = record.field_states?.[field];
          const isNotStated = (record.not_stated_fields ?? []).includes(field) || fieldState === "not_stated" || (typeof fieldState === "object" && (fieldState?.state === "not_stated" || fieldState?.extraction_state === "not_stated"));
          return <article className="field-review-card panel" key={field}>
            <header><div><p className="eyebrow">{label}</p><h2>{showValue(current, isNotStated)}</h2></div><span className={`evidence-count ${evidence.length ? "has-evidence" : ""}`}>{evidence.length} evidence {evidence.length === 1 ? "item" : "items"}</span></header>
            <div className="value-provenance"><div><span>Original model output</span><p>{showValue(original)}</p></div><div><span>Current reviewed value</span><p>{showValue(current, isNotStated)}</p></div></div>
            <div className="review-actions" role="group" aria-label={`Review ${label}`}>
                <button disabled={Boolean(busy) || evidence.length === 0} title={evidence.length ? "Accept evidence-backed model value" : "Evidence is required before acceptance"} onClick={() => void review(field, "accept")}>Accept</button>
              <button className="secondary" disabled={Boolean(busy)} onClick={() => { setEditing(field); setDraft(showValue(current) === "Null / not yet resolved" ? "" : showValue(current)); }}>Edit</button>
              <button className="secondary" disabled={Boolean(busy)} onClick={() => void review(field, "reject")}>Reject</button>
              <button className="secondary" disabled={Boolean(busy)} onClick={() => void review(field, "clear")}>Clear</button>
              <button className="secondary" disabled={Boolean(busy)} onClick={() => void review(field, "mark_not_stated")}>Mark not stated</button>
              <button className="secondary" disabled={Boolean(busy)} onClick={() => void openEvidencePicker(field)}>Add source evidence</button>
            </div>
            {editing === field && <div className="edit-field-panel"><label>Corrected value{ARRAY_FIELDS.has(field) && <small> Comma-separated</small>}<textarea rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} /></label><label>Reviewer note<textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Why is this correction appropriate?" /></label><div className="action-bar"><button className="secondary" onClick={() => setEditing(null)}>Cancel</button><button disabled={Boolean(busy)} onClick={() => void review(field, "edit")}>Save correction</button></div><p className="field-note">An edit without selected evidence is preserved as a manual assertion, not evidence-backed ground truth.</p></div>}
            <details open={evidence.length > 0}><summary>Source evidence ({evidence.length})</summary><div className="evidence-list">{evidence.length ? evidence.map((item) => <article key={item.id}><header><strong>Page {item.page_number ?? "—"}</strong><span>{item.extraction_method} · {item.model_confidence_label ?? "confidence not supplied"}</span></header><blockquote>{item.supporting_text}</blockquote><a className="source-link" href={`/documents/${item.document_id}?${new URLSearchParams({ ...(item.element_id ? { element: item.element_id } : {}), ...(item.chunk_id ? { chunk: item.chunk_id } : {}), ...(item.page_number ? { page: String(item.page_number) } : {}) })}`}>Open exact source in Document Inspector →</a>{item.reviewer_note && <p className="field-note">Reviewer: {item.reviewer_note}</p>}</article>) : <p className="muted">No evidence was extracted for this field. It cannot be accepted as evidence-backed.</p>}</div></details>
            <details><summary>Correction history ({history.length})</summary>{history.length ? <ol className="history-list">{history.map((item) => <li key={item.id}><strong>{item.action.replaceAll("_", " ")}</strong><span>{new Date(item.created_at).toLocaleString()} · {item.evidence_backed ? "evidence-backed" : "manual assertion"}</span><p>{showValue(item.previous_value)} → {showValue(item.corrected_value ?? item.new_value)}</p>{item.reviewer_note && <small>{item.reviewer_note}</small>}</li>)}</ol> : <p className="muted">No human corrections yet.</p>}</details>
            {evidenceField === field && <section className="inline-evidence-picker" aria-label={`Add source evidence for ${label}`}><header><div><p className="eyebrow">Human evidence</p><h3>Select an exact source passage</h3></div><button className="secondary compact-button" onClick={() => setEvidenceField(null)}>Close</button></header><label>Document<select value={evidenceDocument} onChange={(event) => void chooseEvidenceDocument(event.target.value)}><option value="">Select source document</option>{documents.map((document) => <option value={document.id} key={document.id}>{document.title || document.id}</option>)}</select></label>{evidenceDocument && <div className="source-picker"><nav aria-label="Dataset evidence sources"><h3>Parsed elements</h3>{elements.map((element) => <button className={evidenceSource?.element_id === element.id ? "selected" : ""} key={element.id} onClick={() => chooseEvidenceSource({ document_id: evidenceDocument, page_number: element.page_number, element_id: element.id, chunk_id: null, selected_text: element.text })}><span>{element.element_type} · page {element.page_number ?? "—"}</span><strong>{element.text.slice(0, 150)}</strong></button>)}<h3>Chunks</h3>{chunks.filter((chunk) => chunk.document_id === evidenceDocument).map((chunk) => <button className={evidenceSource?.chunk_id === chunk.id ? "selected" : ""} key={chunk.id} onClick={() => chooseEvidenceSource({ document_id: evidenceDocument, page_number: chunk.page_start, element_id: null, chunk_id: chunk.id, selected_text: chunk.text })}><span>Chunk {chunk.sequence_number} · page {chunk.page_start ?? "—"}</span><strong>{chunk.text.slice(0, 150)}</strong></button>)}</nav><div className="passage-editor">{evidenceSource ? <><label>Exact selected passage<textarea rows={9} value={evidenceText} onChange={(event) => setEvidenceText(event.target.value)} /></label><label>Reviewer note<textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} /></label><button disabled={busy === "evidence"} onClick={() => void addEvidence()}>{busy === "evidence" ? "Adding…" : "Attach evidence to field"}</button><a className="source-link" href={`/documents/${evidenceSource.document_id}?${new URLSearchParams({ ...(evidenceSource.element_id ? { element: evidenceSource.element_id } : {}), ...(evidenceSource.chunk_id ? { chunk: evidenceSource.chunk_id } : {}), ...(evidenceSource.page_number ? { page: String(evidenceSource.page_number) } : {}) })}`}>Open in Document Inspector →</a></> : <div className="empty"><strong>Choose an element or chunk</strong><span>The selected text must be an exact source passage.</span></div>}</div></div>}</section>}
          </article>;
        })}
      </section>
    </main></>
  );
}
