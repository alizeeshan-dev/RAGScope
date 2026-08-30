"use client";

import { useEffect, useState } from "react";
import { ResearchHeader } from "@/components/ResearchHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import type { DatasetRecord } from "@/lib/types";
import styles from "../datasets.module.css";

const ROWS = [
  ["description", "Description"], ["domain", "Domain"], ["modalities", "Modalities"], ["task_types", "Tasks"],
  ["instance_count", "Instances"], ["participant_count", "Participants"], ["annotation_types", "Annotations"],
  ["human_ratings", "Human ratings"], ["languages", "Languages"], ["collection_method", "Collection method"],
  ["license", "License"], ["access_url", "Access URL"], ["known_limitations", "Known limitations"],
] as const;

function value(record: DatasetRecord, field: string) {
  const state = record.field_states?.[field];
  if ((record.not_stated_fields ?? []).includes(field) || state === "not_stated" || (typeof state === "object" && (state?.state === "not_stated" || state?.extraction_state === "not_stated"))) return "Not stated";
  const stored = record.current_values?.[field] ?? record[field as keyof DatasetRecord];
  if (Array.isArray(stored)) return stored.join(", ") || "Empty list";
  return stored == null || stored === "" ? "Null / unresolved" : String(stored);
}

export default function DatasetComparisonPage() {
  const [records, setRecords] = useState<DatasetRecord[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const ids = new URLSearchParams(window.location.search).get("ids")?.split(",").filter(Boolean).slice(0, 4) ?? [];
    if (ids.length < 2) { setError("Select between two and four dataset records from the catalog."); setLoading(false); return; }
    void api.compareDatasetRecords(ids).then((comparison) => setRecords(comparison.records)).catch((reason: Error) => setError(reason.message)).finally(() => setLoading(false));
  }, []);

  function exportComparison() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(records, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "ragscope-dataset-comparison.json"; anchor.click(); URL.revokeObjectURL(url);
  }

  return <><ResearchHeader context="Dataset comparison" /><main id="main" className={`${styles.page} shell intelligence-shell`} aria-busy={loading}><a className="back" href="/datasets">← Dataset catalog</a><section className="page-title"><div><p className="eyebrow">Aligned evidence review</p><h1>Compare dataset records</h1><p>Values, review states, and evidence coverage remain visible side-by-side. This view does not rank datasets.</p></div><button className="secondary" disabled={!records.length} onClick={exportComparison}>Export JSON</button></section>{error && <div className="alert" role="alert">{error}</div>}{loading ? <div className={styles.loading} role="status">Aligning dataset evidence…</div> : records.length > 0 && <div className="comparison-scroll"><table className="dataset-comparison-table"><caption>Field-level comparison of {records.length} dataset records</caption><thead><tr><th scope="col">Field</th>{records.map((record) => <th scope="col" key={record.id}><a href={`/datasets/${record.id}`}>{record.name ?? "Unnamed dataset"}</a><StatusBadge status={record.review_status} /></th>)}</tr></thead><tbody>{ROWS.map(([field, label]) => <tr key={field}><th scope="row">{label}</th>{records.map((record) => { const evidenceCount = (record.field_evidence ?? record.evidence ?? []).filter((item) => item.field_name === field).length; return <td key={record.id}><p>{value(record, field)}</p><span className={evidenceCount ? "evidence-ok" : "evidence-missing"}>{evidenceCount ? `${evidenceCount} evidence item${evidenceCount === 1 ? "" : "s"}` : "No evidence"}</span></td>; })}</tr>)}</tbody></table></div>}</main></>;
}
