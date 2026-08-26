"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ResearchHeader } from "@/components/ResearchHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { BenchmarkQuestion, BenchmarkVersion } from "@/lib/types";

const QUESTION_TYPES = ["direct_fact_lookup", "dataset_discovery", "dataset_comparison", "multi_document_synthesis", "multi_hop_reasoning", "table_based", "broad_summary", "ambiguous", "unanswerable", "false_premise", "contradictory_source", "distractor_sensitive"];

export default function BenchmarkVersionPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [version, setVersion] = useState<BenchmarkVersion | null>(null);
  const [questions, setQuestions] = useState<BenchmarkQuestion[]>([]);
  const [answerable, setAnswerable] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => { try { const [storedVersion, storedQuestions] = await Promise.all([api.getBenchmarkVersion(id), api.listBenchmarkQuestions(id)]); setVersion(storedVersion); setQuestions(storedQuestions); setError(""); } catch (reason) { setError(reason instanceof Error ? reason.message : "Benchmark version could not be loaded"); } }, [id]);
  useEffect(() => { void load(); }, [load]);

  async function createQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy("question"); setError("");
    try {
      const question = await api.createBenchmarkQuestion(id, {
        question_text: String(data.get("question_text")), question_type: String(data.get("question_type")), difficulty: String(data.get("difficulty")), answerable,
        expected_answerability: answerable ? String(data.get("expected_answerability") || "answerable") : "unanswerable",
        reference_answer: String(data.get("reference_answer") || "") || null, answer_criteria: String(data.get("answer_criteria") || "") || null,
        unanswerable_explanation: answerable ? null : String(data.get("unanswerable_explanation") || ""), tags: String(data.get("tags") || "").split(",").map((item) => item.trim()).filter(Boolean),
        annotation_notes: String(data.get("annotation_notes") || "") || null, annotation_status: "draft",
      });
      router.push(`/benchmarks/questions/${question.id}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Question could not be created"); }
    finally { setBusy(""); }
  }

  async function freeze() {
    if (!window.confirm("Freeze this benchmark version? Questions and evidence will become immutable.")) return;
    setBusy("freeze"); setError(""); try { setVersion(await api.freezeBenchmarkVersion(id)); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Version could not be frozen"); } finally { setBusy(""); }
  }

  if (!version) return <><ResearchHeader context="Benchmark version" /><main id="main" className="shell"><p>{error || "Loading benchmark version…"}</p></main></>;
  const frozen = version.status === "frozen";
  return <><ResearchHeader context="Benchmark version" /><main id="main" className="shell intelligence-shell"><a className="back" href="/benchmarks">← Benchmarks</a><section className="page-title"><div><p className="eyebrow">{frozen ? "Immutable snapshot" : "Editable draft"}</p><h1>Benchmark version {version.version}</h1><p>Corpus version <code>{version.corpus_version_id}</code>. Human labels are separate from all model suggestions.</p></div><div className="action-bar"><StatusBadge status={version.status} />{!frozen && <button disabled={Boolean(busy)} onClick={() => void freeze()}>{busy === "freeze" ? "Freezing…" : "Freeze version"}</button>}</div></section>{error && <div className="alert" role="alert">{error}</div>}{frozen && <div className="immutability-callout"><strong>Read-only benchmark snapshot</strong><span>Questions, answer criteria, and evidence cannot be changed. Create a new version for revisions.</span></div>}<div className="intelligence-layout"><section className="panel"><div className="panel-heading"><div><p className="eyebrow">Questions</p><h2>{questions.length} annotations</h2></div></div>{questions.length === 0 ? <div className="empty"><strong>No questions yet</strong><span>Add a question while this version is a draft.</span></div> : <div className="question-list">{questions.map((question, index) => <a href={`/benchmarks/questions/${question.id}`} key={question.id}><span className="question-number">{index + 1}</span><div><strong>{question.question_text}</strong><small>{question.question_type.replaceAll("_", " ")} · {question.difficulty} · {question.answerable ? "answerable" : "unanswerable"}</small></div><div><StatusBadge status={question.annotation_status} />{question.leakage_warning && <span className="warning-chip">Leakage warning</span>}</div></a>)}</div>}</section><aside className="panel"><p className="eyebrow">Author</p><h2>New question</h2>{frozen ? <p className="muted">This version is frozen. Create a new draft version to author more questions.</p> : <form className="stack-form" onSubmit={createQuestion}><label>Question<textarea name="question_text" required rows={4} /></label><div className="form-row"><label>Type<select name="question_type">{QUESTION_TYPES.map((item) => <option value={item} key={item}>{item.replaceAll("_", " ")}</option>)}</select></label><label>Difficulty<select name="difficulty"><option value="easy">Easy</option><option value="medium">Medium</option><option value="hard">Hard</option></select></label></div><label className="inline-check"><input type="checkbox" checked={answerable} onChange={(event) => setAnswerable(event.target.checked)} /> Answerable from this corpus</label>{answerable ? <><label>Reference answer<textarea name="reference_answer" rows={3} /></label><label>Answer criteria<textarea name="answer_criteria" rows={3} /></label><p className="field-note">At least one acceptable evidence set is required before review/freeze.</p></> : <label>Why unanswerable?<textarea name="unanswerable_explanation" required rows={3} placeholder="Fact absent, false premise, no relevant source, or irreconcilable contradiction" /></label>}<label>Tags<input name="tags" placeholder="Comma-separated" /></label><label>Annotation notes<textarea name="annotation_notes" rows={2} /></label><button disabled={Boolean(busy)}>{busy === "question" ? "Creating…" : "Create and select evidence"}</button></form>}</aside></div></main></>;
}
