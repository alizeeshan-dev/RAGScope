"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ResearchHeader } from "@/components/ResearchHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { Benchmark, Corpus } from "@/lib/types";
import styles from "./benchmarks.module.css";

export default function BenchmarksPage() {
  const router = useRouter();
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [items, storedCorpora] = await Promise.all([api.listBenchmarks(), api.listCorpora()]);
      const detailed = await Promise.all(items.map(async (item) => ({ ...item, versions: await api.listBenchmarkVersions(item.id).catch(() => []) })));
      setBenchmarks(detailed); setCorpora(storedCorpora); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Benchmarks could not be loaded"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function createBenchmark(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); setBusy("benchmark"); setError("");
    try { await api.createBenchmark({ name: String(data.get("name")), description: String(data.get("description") || "") }); form.reset(); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Benchmark could not be created"); }
    finally { setBusy(""); }
  }

  async function createVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setBusy("version"); setError("");
    try {
      const rawVersion = String(data.get("version") || "");
      const created = await api.createBenchmarkVersion(String(data.get("benchmark_id")), { corpus_version_id: String(data.get("corpus_version_id")), ...(rawVersion ? { version: Number(rawVersion) } : {}), notes: String(data.get("notes") || "") });
      router.push(`/benchmarks/versions/${created.id}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Version could not be created"); }
    finally { setBusy(""); }
  }

  const corpusVersions = corpora.flatMap((corpus) => (corpus.versions ?? []).map((version) => ({ ...version, corpusName: corpus.name })));
  const versions = benchmarks.flatMap((benchmark) => benchmark.versions ?? []);
  const frozenVersions = versions.filter((version) => version.status === "frozen").length;
  const questionCount = versions.reduce((count, version) => count + (version.question_count ?? version.questions?.length ?? 0), 0);
  return <><ResearchHeader context="Benchmark authoring" /><main id="main" className={`${styles.page} shell intelligence-shell`} aria-busy={loading}><section className="page-title"><div><p className="eyebrow">Human ground truth</p><h1>Benchmark authoring</h1><p>Author evidence-backed questions in editable drafts, then freeze immutable versions for reproducible evaluation.</p></div></section>{error && <div className="alert" role="alert">{error}</div>}<section className={styles.stats} aria-label="Benchmark summary"><div className={styles.stat}><span>Collections</span><strong>{loading ? "—" : benchmarks.length}</strong></div><div className={styles.stat}><span>Versions</span><strong>{loading ? "—" : versions.length}</strong></div><div className={styles.stat}><span>Frozen</span><strong>{loading ? "—" : frozenVersions}</strong></div><div className={styles.stat}><span>Questions</span><strong>{loading ? "—" : questionCount}</strong></div></section><div className="intelligence-layout"><section className="panel"><div className="panel-heading"><div><p className="eyebrow">Collections</p><h2>Benchmark versions</h2></div><span>{benchmarks.length} benchmarks</span></div>{loading ? <div className={styles.loading} role="status">Loading benchmark versions…</div> : benchmarks.length === 0 ? <div className="empty"><strong>No benchmark yet</strong><span>Create a logical benchmark collection to begin.</span></div> : <div className="benchmark-list">{benchmarks.map((benchmark) => <article key={benchmark.id}><header><div><h3>{benchmark.name}</h3><p>{benchmark.description || "No description"}</p></div><span>{benchmark.versions?.length ?? 0} versions</span></header><div className="benchmark-version-row">{(benchmark.versions ?? []).map((version) => <a key={version.id} href={`/benchmarks/versions/${version.id}`}><strong>{`Version ${version.version}`}</strong><StatusBadge status={version.status} /><small>{version.question_count ?? version.questions?.length ?? 0} questions</small></a>)}</div></article>)}</div>}</section><aside className="panel"><p className="eyebrow">Create</p><h2>New benchmark</h2><form className="stack-form" onSubmit={createBenchmark}><label>Name<input name="name" required maxLength={200} /></label><label>Description<textarea name="description" rows={3} /></label><button disabled={Boolean(busy)}>{busy === "benchmark" ? "Creating…" : "Create benchmark"}</button></form><hr /><p className="eyebrow">Version</p><h2>New draft version</h2><form className="stack-form" onSubmit={createVersion}><label>Benchmark<select name="benchmark_id" required defaultValue=""><option value="" disabled>Select benchmark</option>{benchmarks.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><label>Corpus version<select name="corpus_version_id" required defaultValue=""><option value="" disabled>Select source snapshot</option>{corpusVersions.map((version) => <option value={version.id} key={version.id}>{version.corpusName} · {version.version_label}</option>)}</select></label><label>Version number (optional)<input name="version" type="number" min="1" placeholder="Next available" /></label><label>Notes<textarea name="notes" rows={2} /></label><button disabled={Boolean(busy) || !benchmarks.length || !corpusVersions.length}>{busy === "version" ? "Creating…" : "Create draft version"}</button></form></aside></div></main></>;
}
