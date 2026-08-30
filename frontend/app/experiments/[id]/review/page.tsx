"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Experiment, HumanReviewQueuePage } from "@/lib/types";
import styles from "../../experiments.module.css";

export default function HumanReviewQueue() {
  const { id } = useParams<{ id: string }>();
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [queue, setQueue] = useState<HumanReviewQueuePage | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [storedExperiment, storedQueue] = await Promise.all([
        api.getExperiment(id), api.getHumanReviewQueue(id, offset, 50),
      ]);
      setExperiment(storedExperiment); setQueue(storedQueue); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Review queue could not be loaded"); }
  }, [id, offset]);
  useEffect(() => {
    const requestedOffset = Number(new URLSearchParams(window.location.search).get("offset") ?? "0");
    if (Number.isInteger(requestedOffset) && requestedOffset >= 0) setOffset(requestedOffset);
  }, []);
  useEffect(() => { void load(); }, [load]);
  return <main id="main" className={`shell ${styles.page}`}>
    <a className="back" href={`/experiments/${id}`}>← Experiment</a>
    <section className={styles.hero}><div><p className="eyebrow">Primary human ground truth</p><h1>{experiment?.name ?? "Experiment review"}</h1><p>Inspect evidence and add human labels without replacing preserved automatic judgments.</p></div></section>
    {error&&<div className="alert" role="alert">{error}</div>}
    {!queue && !error ? <p aria-live="polite">Loading human review queue…</p> : <>
      <section className={styles.reviewStats}><div className={styles.reviewStat}><span>Total terminal runs</span><strong>{queue?.total ?? "—"}</strong></div><div className={styles.reviewStat}><span>Fully reviewed</span><strong>{queue?.reviewed ?? "—"}</strong></div><div className={styles.reviewStat}><span>Remaining</span><strong>{queue?.remaining ?? "—"}</strong></div></section>
      <section className={`panel ${styles.reviewPanel}`}><div className="panel-heading"><div><p className="eyebrow">Next work</p><h2>Runs missing required labels</h2></div><span className="muted">Versioned human evaluations</span></div><div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Run</th><th>Question</th><th>Missing labels</th><th>Review</th></tr></thead><tbody>{queue?.items.map((item, index)=><tr key={item.query_run_id}><td><code>{item.query_run_id.slice(0,8)}</code></td><td><code>{item.benchmark_question_id.slice(0,8)}</code></td><td>{item.missing_labels.map((label)=><span className="warning-chip" key={label}>{label.replaceAll("_"," ")}</span>)}</td><td><a className="source-link" href={`/laboratory/${item.query_run_id}?review=1&experiment=${id}&position=${offset+index+1}&total=${queue.remaining}`}>Inspect evidence and label →</a></td></tr>)}{queue?.items.length===0&&<tr><td colSpan={4}>All runs have the required human labels.</td></tr>}</tbody></table></div>{queue && queue.remaining > queue.limit && <nav className={styles.pagination} aria-label="Review queue pages">{offset > 0 ? <a href={`?offset=${Math.max(0, offset - queue.limit)}`}>← Previous page</a> : <span>First page</span>}<span>Showing {queue.items.length === 0 ? 0 : offset + 1}–{offset + queue.items.length} of {queue.remaining} remaining runs</span>{offset + queue.items.length < queue.remaining ? <a href={`?offset=${offset + queue.limit}`}>Next page →</a> : <span>Last page</span>}</nav>}</section>
    </>}
  </main>;
}
