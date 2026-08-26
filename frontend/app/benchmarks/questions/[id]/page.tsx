"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { ResearchHeader } from "@/components/ResearchHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { BenchmarkEvidence, BenchmarkQuestion, BenchmarkVersion, Chunk, DocumentElement, SourceDocument } from "@/lib/types";

const QUESTION_TYPES = ["direct_fact_lookup", "dataset_discovery", "dataset_comparison", "multi_document_synthesis", "multi_hop_reasoning", "table_based", "broad_summary", "ambiguous", "unanswerable", "false_premise", "contradictory_source", "distractor_sensitive"];
type PendingEvidence = Pick<BenchmarkEvidence, "document_id" | "page_number" | "element_id" | "chunk_id" | "selected_text">;

export default function BenchmarkQuestionPage() {
  const { id } = useParams<{ id: string }>();
  const [question, setQuestion] = useState<BenchmarkQuestion | null>(null);
  const [version, setVersion] = useState<BenchmarkVersion | null>(null);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [elements, setElements] = useState<DocumentElement[]>([]);
  const [documentId, setDocumentId] = useState("");
  const [selectedSource, setSelectedSource] = useState<PendingEvidence | null>(null);
  const [selectedText, setSelectedText] = useState("");
  const [pending, setPending] = useState<PendingEvidence[]>([]);
  const [setDescription, setSetDescription] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const storedQuestion = await api.getBenchmarkQuestion(id);
      const storedVersion = await api.getBenchmarkVersion(storedQuestion.benchmark_version_id);
      const [storedDocuments, storedChunks] = await Promise.all([api.listDocuments(storedVersion.corpus_version_id), api.getChunks(storedVersion.corpus_version_id)]);
      setQuestion(storedQuestion); setVersion(storedVersion); setDocuments(storedDocuments); setChunks(storedChunks); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Question annotation could not be loaded"); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  const documentChunks = useMemo(() => chunks.filter((chunk) => chunk.document_id === documentId), [chunks, documentId]);
  const frozen = version?.status === "frozen";

  async function selectDocument(nextId: string) {
    setDocumentId(nextId); setSelectedSource(null); setSelectedText("");
    try { setElements(await api.getElements(nextId)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Document elements could not be loaded"); }
  }

  function chooseSource(source: PendingEvidence) { setSelectedSource(source); setSelectedText(source.selected_text); }
  function stageEvidence() {
    if (!selectedSource || !selectedText.trim()) return;
    setPending((current) => [...current, { ...selectedSource, selected_text: selectedText.trim() }]); setSelectedSource(null); setSelectedText("");
  }

  async function saveQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!question || frozen) return; const data = new FormData(event.currentTarget); setBusy("question"); setError("");
    try {
      const answerable = data.get("answerable") === "on";
      setQuestion(await api.patchBenchmarkQuestion(question.id, {
        question_text: String(data.get("question_text")), question_type: String(data.get("question_type")), difficulty: String(data.get("difficulty")), answerable,
        expected_answerability: answerable ? String(data.get("expected_answerability") || "answerable") : "unanswerable",
        reference_answer: String(data.get("reference_answer") || "") || null, answer_criteria: String(data.get("answer_criteria") || "") || null,
        unanswerable_explanation: answerable ? null : String(data.get("unanswerable_explanation") || "") || null,
        tags: String(data.get("tags") || "").split(",").map((item) => item.trim()).filter(Boolean), annotation_notes: String(data.get("annotation_notes") || "") || null,
        annotation_status: String(data.get("annotation_status")),
      }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Question could not be saved"); }
    finally { setBusy(""); }
  }

  async function saveEvidenceSet() {
    if (!question || pending.length === 0) return; setBusy("evidence"); setError("");
    try { setQuestion(await api.addBenchmarkEvidence(question.id, { set_number: question.acceptable_evidence_sets.length + 1, description: setDescription || undefined, references: pending })); setPending([]); setSetDescription(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Evidence set could not be saved"); }
    finally { setBusy(""); }
  }

  async function removeSet(setId: string) {
    if (!question) return; setBusy("evidence"); setError(""); try { await api.removeBenchmarkEvidence(question.id, setId); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Evidence set could not be removed"); } finally { setBusy(""); }
  }

  async function runLeakageCheck() {
    if (!question) return; setBusy("leakage"); try { const result = await api.checkBenchmarkLeakage(question.id); setQuestion({ ...question, leakage_warning: result.warning, leakage_score: result.score }); } catch (reason) { setError(reason instanceof Error ? reason.message : "Leakage check failed"); } finally { setBusy(""); }
  }

  if (!question || !version) return <><ResearchHeader context="Evidence annotation" /><main id="main" className="shell"><p>{error || "Loading question…"}</p></main></>;
  return <><ResearchHeader context="Evidence annotation" /><main id="main" className="shell intelligence-shell"><a className="back" href={`/benchmarks/versions/${version.id}`}>← Benchmark version {version.version}</a><section className="page-title"><div><p className="eyebrow">Human evidence annotation</p><h1>{question.question_text}</h1><p>Evidence resolves to stable source objects. Model suggestions are shown separately and never become human ground truth automatically.</p></div><div className="action-bar"><StatusBadge status={question.annotation_status} />{frozen && <StatusBadge status="frozen" />}</div></section>{error && <div className="alert" role="alert">{error}</div>}{frozen && <div className="immutability-callout"><strong>Read-only frozen annotation</strong><span>Create a new benchmark version to change this question or its evidence.</span></div>}{question.leakage_warning && <div className="warnings"><strong>Possible question leakage</strong><p>This question overlaps strongly with an answer passage{question.leakage_score != null ? ` (score ${question.leakage_score.toFixed(2)})` : ""}. This is a warning, not an automatic rejection.</p></div>}<div className="annotation-layout"><section className="panel"><div className="panel-heading"><div><p className="eyebrow">Annotation</p><h2>Question and answer criteria</h2></div>{!frozen && <button className="secondary" disabled={Boolean(busy)} onClick={() => void runLeakageCheck()}>{busy === "leakage" ? "Checking…" : "Check leakage"}</button>}</div><form className="stack-form" onSubmit={saveQuestion}><label>Question<textarea name="question_text" required rows={4} defaultValue={question.question_text} disabled={frozen} /></label><div className="form-row"><label>Type<select name="question_type" defaultValue={question.question_type} disabled={frozen}>{QUESTION_TYPES.map((item) => <option value={item} key={item}>{item.replaceAll("_", " ")}</option>)}</select></label><label>Difficulty<select name="difficulty" defaultValue={question.difficulty} disabled={frozen}><option value="easy">Easy</option><option value="medium">Medium</option><option value="hard">Hard</option></select></label></div><label className="inline-check"><input name="answerable" type="checkbox" defaultChecked={question.answerable} disabled={frozen} /> Answerable from this corpus</label><label>Reference answer<textarea name="reference_answer" rows={4} defaultValue={question.reference_answer ?? ""} disabled={frozen} /></label><label>Answer criteria<textarea name="answer_criteria" rows={4} defaultValue={question.answer_criteria ?? ""} disabled={frozen} /></label><label>Unanswerable explanation<textarea name="unanswerable_explanation" rows={3} defaultValue={question.unanswerable_explanation ?? ""} disabled={frozen} placeholder="Required when answerable is unchecked" /></label><label>Tags<input name="tags" defaultValue={question.tags.join(", ")} disabled={frozen} /></label><label>Annotation notes<textarea name="annotation_notes" rows={3} defaultValue={question.annotation_notes ?? ""} disabled={frozen} /></label><label>Human review state<select name="annotation_status" defaultValue={question.annotation_status} disabled={frozen}><option value="draft">Draft</option><option value="in_review">In review</option><option value="reviewed">Reviewed</option></select></label>{!frozen && <button disabled={Boolean(busy)}>{busy === "question" ? "Saving…" : "Save human annotation"}</button>}</form>{Object.keys(question.model_suggestion ?? {}).length > 0 && <details className="model-suggestion"><summary>AI suggestion (not ground truth)</summary><pre>{JSON.stringify(question.model_suggestion, null, 2)}</pre></details>}</section><section className="panel"><div className="panel-heading"><div><p className="eyebrow">Acceptable evidence</p><h2>{question.acceptable_evidence_sets.length} distinct sets</h2></div></div>{question.answerable && question.acceptable_evidence_sets.length === 0 && <div className="warnings"><strong>Evidence required</strong><p>An answerable question needs at least one acceptable evidence set before review or freeze.</p></div>}{question.acceptable_evidence_sets.map((set) => <article className="evidence-set" key={set.id}><header><div><strong>Set {set.set_number}</strong><span>{set.description || "Alternative complete support"}</span></div>{!frozen && <button className="secondary compact-button" disabled={Boolean(busy)} onClick={() => void removeSet(set.id)}>Remove set</button>}</header>{set.references.map((reference) => <div className="evidence-reference" key={reference.id}><blockquote>{reference.selected_text}</blockquote><a className="source-link" href={`/documents/${reference.document_id}?${new URLSearchParams({ ...(reference.element_id ? { element: reference.element_id } : {}), ...(reference.chunk_id ? { chunk: reference.chunk_id } : {}), ...(reference.page_number ? { page: String(reference.page_number) } : {}) })}`}>Document Inspector · page {reference.page_number ?? "—"} →</a><small>Document <code>{reference.document_id}</code>{reference.chunk_id && <> · chunk <code>{reference.chunk_id}</code></>}</small></div>)}</article>)}{!question.answerable && question.acceptable_evidence_sets.length === 0 && <p className="muted">No evidence is required for an unanswerable question. Contradiction or distractor evidence remains optional.</p>}</section></div>{!frozen && <section className="panel evidence-author"><div className="panel-heading"><div><p className="eyebrow">Source selection</p><h2>Build an acceptable evidence set</h2></div><span>Stable element or chunk references</span></div><label>Source document<select value={documentId} onChange={(event) => void selectDocument(event.target.value)}><option value="">Select document</option>{documents.map((document) => <option value={document.id} key={document.id}>{document.title || document.id}</option>)}</select></label>{documentId && <div className="source-picker"><nav aria-label="Parsed elements and chunks"><h3>Parsed elements</h3>{elements.map((element) => <button className={selectedSource?.element_id === element.id ? "selected" : ""} key={element.id} onClick={() => chooseSource({ document_id: documentId, page_number: element.page_number, element_id: element.id, chunk_id: null, selected_text: element.text })}><span>{element.element_type} · page {element.page_number ?? "—"}</span><strong>{element.text.slice(0, 150)}</strong></button>)}<h3>Chunks</h3>{documentChunks.map((chunk) => <button className={selectedSource?.chunk_id === chunk.id ? "selected" : ""} key={chunk.id} onClick={() => chooseSource({ document_id: documentId, page_number: chunk.page_start, element_id: null, chunk_id: chunk.id, selected_text: chunk.text })}><span>Chunk {chunk.sequence_number} · page {chunk.page_start ?? "—"}</span><strong>{chunk.text.slice(0, 150)}</strong></button>)}</nav><div className="passage-editor">{selectedSource ? <><h3>Selected passage</h3><p className="field-note">Keep an exact passage or contiguous range from the source. The API verifies the reference.</p><textarea rows={10} value={selectedText} onChange={(event) => setSelectedText(event.target.value)} /><button onClick={stageEvidence}>Add passage to pending set</button><a className="source-link" href={`/documents/${selectedSource.document_id}?${new URLSearchParams({ ...(selectedSource.element_id ? { element: selectedSource.element_id } : {}), ...(selectedSource.chunk_id ? { chunk: selectedSource.chunk_id } : {}), ...(selectedSource.page_number ? { page: String(selectedSource.page_number) } : {}) })}`}>Inspect source →</a></> : <div className="empty"><strong>Select a source passage</strong><span>Choose a parsed element or chunk from the left.</span></div>}</div></div>}{pending.length > 0 && <div className="pending-evidence"><h3>Pending set ({pending.length} passages)</h3><label>Set description<input value={setDescription} onChange={(event) => setSetDescription(event.target.value)} placeholder="Why this set is sufficient" /></label>{pending.map((item, index) => <article key={`${item.element_id ?? item.chunk_id}-${index}`}><button className="text-button" aria-label={`Remove pending passage ${index + 1}`} onClick={() => setPending((current) => current.filter((_, itemIndex) => itemIndex !== index))}>Remove</button><blockquote>{item.selected_text}</blockquote><span>Page {item.page_number ?? "—"} · {item.element_id ? "element" : "chunk"}</span></article>)}<button disabled={Boolean(busy)} onClick={() => void saveEvidenceSet()}>{busy === "evidence" ? "Saving…" : "Save as distinct evidence set"}</button></div>}</section>}</main></>;
}
