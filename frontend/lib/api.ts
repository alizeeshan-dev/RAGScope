import type { Artifact, Benchmark, BenchmarkEvidence, BenchmarkQuestion, BenchmarkVersion, Chunk, Corpus, CorpusVersion, DatasetComparison, DatasetRecord, DatasetRecordList, DocumentElement, EvaluationBundle, ExtractionJob, FailureAttribution, FieldEvidence, FieldReviewAction, IndexStatus, ObservableTraceExport, PipelineConfiguration, QueryComparison, QueryRun, RouterConfiguration, RunClaim, RunContextSource, RunRetrievalResult, SourceDocument } from "./types";

const API_ROOT = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, { ...init, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.error ?? body.detail ?? body;
    throw new ApiError(response.status, detail.code ?? "REQUEST_FAILED", detail.message ?? "Request failed");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestText(path: string): Promise<string> {
  const response = await fetch(`${API_ROOT}${path}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.error ?? body.detail ?? body;
    throw new ApiError(response.status, detail.code ?? "REQUEST_FAILED", detail.message ?? "Request failed");
  }
  return response.text();
}

export const api = {
  listCorpora: () => request<Corpus[]>("/corpora"),
  getCorpus: (id: string) => request<Corpus>(`/corpora/${id}`),
  createCorpus: (body: { name: string; description?: string; domain?: string }) =>
    request<Corpus>("/corpora", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  createVersion: (corpusId: string, body: Record<string, unknown>) =>
    request<CorpusVersion>(`/corpora/${corpusId}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  getVersion: (id: string) => request<CorpusVersion>(`/corpus-versions/${id}`),
  freezeVersion: (id: string) => request<CorpusVersion>(`/corpus-versions/${id}/freeze`, { method: "POST" }),
  uploadDocument: (versionId: string, data: FormData) => request<SourceDocument>(`/corpus-versions/${versionId}/documents`, { method: "POST", body: data }),
  getDocument: (id: string) => request<SourceDocument>(`/documents/${id}`),
  listDocuments: (versionId: string) => request<SourceDocument[]>(`/corpus-versions/${versionId}/documents?limit=500`),
  deleteDocument: (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" }),
  getElements: (id: string) => request<DocumentElement[]>(`/documents/${id}/elements?limit=500`),
  parseDocument: (id: string) => request<unknown>(`/documents/${id}/parse`, { method: "POST" }),
  getArtifacts: (id: string) => request<Artifact[]>(`/documents/${id}/artifacts`),
  artifactContentUrl: (id: string) => `${API_ROOT}/artifacts/${id}/content`,
  getChunks: (versionId: string, chunkerId?: string) => request<Chunk[]>(`/corpus-versions/${versionId}/chunks?limit=500${chunkerId ? `&chunker_id=${encodeURIComponent(chunkerId)}` : ""}`),
  chunkVersion: (id: string, body: Record<string, unknown>) => request<unknown>(`/corpus-versions/${id}/chunk`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  indexVersion: (id: string) => request<unknown>(`/corpus-versions/${id}/index`, { method: "POST" }),
  getIndexStatus: (id: string) => request<IndexStatus[]>(`/corpus-versions/${id}/index-status`),
  listPipelines: () => request<PipelineConfiguration[]>("/pipeline-configurations"),
  createPipeline: (body: Record<string, unknown>) => request<PipelineConfiguration>("/pipeline-configurations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  freezePipeline: (id: string) => request<PipelineConfiguration>(`/pipeline-configurations/${id}/freeze`, { method: "POST" }),
  createQueryRun: (body: Record<string, unknown>) => request<QueryRun>("/query-runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  getQueryRun: (id: string) => request<QueryRun>(`/query-runs/${id}`),
  getRunRetrieval: (id: string) => request<RunRetrievalResult[]>(`/query-runs/${id}/retrieval-results`),
  getRunClaims: (id: string) => request<RunClaim[]>(`/query-runs/${id}/claims`),
  getRunContext: (id: string) => request<RunContextSource[]>(`/query-runs/${id}/context`),
  getRunTrace: (id: string) => request<ObservableTraceExport>(`/query-runs/${id}/trace`),
  getRunEvaluation: (id: string) => request<EvaluationBundle>(`/query-runs/${id}/evaluation`),
  evaluateRun: (id: string, metricVersions?: string[]) => request<EvaluationBundle>(`/query-runs/${id}/evaluation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ metric_versions: metricVersions ?? null }),
  }),
  addHumanEvaluation: (id: string, body: { metric_name: string; metric_value: number | null; reviewer_label: string; reviewer_note?: string }) =>
    request<EvaluationBundle>(`/query-runs/${id}/evaluation/human-labels`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  reviewFailureAttribution: (runId: string, attributionId: string, body: { label: string; reviewer_note: string }) =>
    request<FailureAttribution>(`/query-runs/${runId}/failure-attributions/${attributionId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  listRouterConfigurations: () => request<RouterConfiguration[]>("/router-configurations"),
  createRouterConfiguration: (body: Record<string, unknown>) => request<RouterConfiguration>("/router-configurations", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }),
  freezeRouterConfiguration: (id: string) => request<RouterConfiguration>(`/router-configurations/${id}/freeze`, { method: "POST" }),
  getArtifactText: (id: string) => requestText(`/artifacts/${id}/content`),
  createComparison: (body: {
    corpus_version_id: string;
    question: string;
    pipeline_configuration_ids: string[];
    filters?: { document_ids: string[]; publication_years: number[] };
  }) => request<QueryComparison>("/query-comparisons", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }),
  getComparison: (id: string) => request<QueryComparison>(`/query-comparisons/${id}`),
  extractDatasets: (documentId: string, body: {
    strategy: "baseline" | "retrieval_assisted";
    provider: "fake" | "gemini" | "openai_compatible";
    model?: string;
  }) =>
    request<ExtractionJob>(`/documents/${documentId}/extract-datasets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  listDatasetRecords: (filters: Record<string, string> = {}) => {
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value));
    return request<DatasetRecord[] | DatasetRecordList>(`/dataset-records${query.size ? `?${query}` : ""}`);
  },
  getDatasetRecord: (id: string) => request<DatasetRecord>(`/dataset-records/${id}`),
  compareDatasetRecords: (recordIds: string[]) => request<DatasetComparison>("/dataset-records/compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ record_ids: recordIds }) }),
  patchDatasetRecord: (id: string, body: Record<string, unknown>) =>
    request<DatasetRecord>(`/dataset-records/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  reviewDatasetField: (id: string, body: {
    field_name?: string;
    action: FieldReviewAction;
    value?: unknown;
    evidence_ids?: string[];
    reviewer_note?: string;
  }) => request<DatasetRecord>(`/dataset-records/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }),
  addDatasetFieldEvidence: (id: string, body: {
    field_name: string;
    document_id: string;
    page_number: number | null;
    element_id: string | null;
    chunk_id: string | null;
    selected_text: string;
    reviewer_note?: string;
  }) => request<FieldEvidence>(`/dataset-records/${id}/evidence`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  listBenchmarks: () => request<Benchmark[]>("/benchmarks"),
  createBenchmark: (body: { name: string; description?: string }) =>
    request<Benchmark>("/benchmarks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getBenchmark: (id: string) => request<Benchmark>(`/benchmarks/${id}`),
  listBenchmarkVersions: (id: string) => request<BenchmarkVersion[]>(`/benchmarks/${id}/versions`),
  createBenchmarkVersion: (id: string, body: { corpus_version_id: string; version?: number; notes?: string }) =>
    request<BenchmarkVersion>(`/benchmarks/${id}/versions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getBenchmarkVersion: (id: string) => request<BenchmarkVersion>(`/benchmark-versions/${id}`),
  listBenchmarkQuestions: (id: string) => request<BenchmarkQuestion[]>(`/benchmark-versions/${id}/questions`),
  createBenchmarkQuestion: (id: string, body: Record<string, unknown>) =>
    request<BenchmarkQuestion>(`/benchmark-versions/${id}/questions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getBenchmarkQuestion: (id: string) => request<BenchmarkQuestion>(`/benchmark-questions/${id}`),
  patchBenchmarkQuestion: (id: string, body: Record<string, unknown>) =>
    request<BenchmarkQuestion>(`/benchmark-questions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  addBenchmarkEvidence: (id: string, body: {
    set_number?: number;
    description?: string;
    references: Array<Pick<BenchmarkEvidence, "document_id" | "page_number" | "element_id" | "chunk_id" | "selected_text">>;
  }) =>
    request<BenchmarkQuestion>(`/benchmark-questions/${id}/evidence`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  removeBenchmarkEvidence: (questionId: string, evidenceSetId: string) =>
    request<void>(`/benchmark-questions/${questionId}/evidence/${evidenceSetId}`, { method: "DELETE" }),
  checkBenchmarkLeakage: (questionId: string) => request<{ warning: boolean; score: number }>(`/benchmark-questions/${questionId}/leakage-check`),
  freezeBenchmarkVersion: (id: string) =>
    request<BenchmarkVersion>(`/benchmark-versions/${id}/freeze`, { method: "POST" }),
};
