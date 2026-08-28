import type { Artifact, BackgroundJob, Benchmark, BenchmarkEvidence, BenchmarkQuestion, BenchmarkVersion, Chunk, Corpus, CorpusVersion, DatasetComparison, DatasetRecord, DatasetRecordList, DocumentElement, EvaluationBundle, Experiment, ExperimentCostEstimate, ExperimentDetail, ExperimentExecutionReport, ExperimentResultRow, ExperimentResults, ExperimentResultsFilters, ExperimentResultsResponse, ExtractionJob, FailureAttribution, FieldEvidence, FieldReviewAction, HumanReviewQueuePage, IndexStatus, ObservableTraceExport, OperationAccepted, PipelineConfiguration, QueryComparison, QueryRun, RouterConfiguration, RunClaim, RunContextSource, RunRetrievalResult, SourceDocument } from "./types";

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

const isOperation = (value: unknown): value is OperationAccepted => Boolean(value && typeof value === "object" && "job_id" in value);

async function waitForJob(receipt: OperationAccepted, timeoutMs = 300_000): Promise<BackgroundJob> {
  const started = Date.now();
  for (;;) {
    const job = await request<BackgroundJob>(`/jobs/${receipt.job_id}`);
    if (["succeeded", "failed", "cancelled"].includes(job.status)) {
      if (job.status !== "succeeded") throw new ApiError(409, job.error_code ?? "JOB_FAILED", job.error_message ?? `Job ${job.status}`);
      return job;
    }
    if (Date.now() - started > timeoutMs) throw new ApiError(408, "JOB_POLL_TIMEOUT", "The operation is still running. You can return to it later.");
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

function adaptExperimentResults(response: ExperimentResultsResponse): ExperimentResults {
  const rows: ExperimentResultRow[] = response.runs.map((run) => {
    const dimensions = run.dimensions;
    const numberValue = (name: string): number | null => typeof dimensions[name] === "number" ? dimensions[name] : null;
    const textValue = (name: string): string | null => typeof dimensions[name] === "string" ? dimensions[name] : null;
    return {
      query_run_id: run.run_id,
      benchmark_question_id: run.question_id,
      pipeline_configuration_id: run.pipeline_id,
      pipeline_name: textValue("pipeline_name") ?? run.pipeline_id,
      repetition: numberValue("repetition") ?? 1,
      question_type: textValue("question_type") ?? "unclassified",
      difficulty: textValue("difficulty") ?? "unlabelled",
      answerability: textValue("answerability") ?? "unlabelled",
      pipeline_mode: textValue("pipeline_mode") ?? "fixed",
      run_status: run.run_status,
      infrastructure_failure: run.infrastructure_failure_code !== null,
      failure_stage: textValue("failure_stage"),
      failure_category: textValue("failure_category"),
      failure_code: run.infrastructure_failure_code,
      total_latency_ms: numberValue("total_latency_ms"),
      input_tokens: numberValue("input_tokens"),
      output_tokens: numberValue("output_tokens"),
      estimated_cost: numberValue("estimated_cost"),
      cost_currency: textValue("cost_currency"),
      retrieval_required_count: run.evidence_survival ? 1 : null,
      retrieval_evidence_count: run.evidence_survival?.retrieval ?? null,
      reranking_evidence_count: run.evidence_survival?.reranking ?? null,
      context_evidence_count: run.evidence_survival?.context ?? null,
      metrics: run.metrics.map((item) => ({ metric_name: item.name, metric_value: item.value, metric_version: item.version, evaluation_method: item.method })),
    };
  });
  return {
    schema_version: "ragscope-analysis-results.v1",
    experiment_id: response.experiment_id,
    generated_at: new Date().toISOString(),
    filters: {
      pipeline_configuration_ids: [], question_types: [], difficulties: [], run_statuses: [], answerabilities: [], pipeline_modes: [], failure_stages: [], failure_categories: [], failure_codes: [], include_infrastructure_failures: true,
    },
    total_rows: response.total_runs ?? response.sample_size,
    offset: response.offset ?? 0,
    limit: response.limit ?? response.runs.length,
    rows,
    available_question_types: response.available_dimensions.question_types ?? [],
    available_difficulties: response.available_dimensions.difficulties ?? [],
    available_pipeline_ids: [...new Set(rows.map((row) => row.pipeline_configuration_id))].sort(),
    aggregates: response.aggregates,
    evidence_survival: response.evidence_survival,
    headline_metrics: response.headline_metrics,
    visualizations: response.visualizations,
    available_dimensions: response.available_dimensions,
  };
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
  getChunk: (id: string) => request<Chunk>(`/chunks/${id}`),
  parseDocument: async (id: string) => {
    const result = await request<SourceDocument | OperationAccepted>(`/documents/${id}/parse`, { method: "POST" });
    if (isOperation(result)) await waitForJob(result);
    return api.getDocument(id);
  },
  getArtifacts: (id: string) => request<Artifact[]>(`/documents/${id}/artifacts`),
  artifactContentUrl: (id: string) => `${API_ROOT}/artifacts/${id}/content`,
  getChunks: (versionId: string, chunkerId?: string) => request<Chunk[]>(`/corpus-versions/${versionId}/chunks?limit=500${chunkerId ? `&chunker_id=${encodeURIComponent(chunkerId)}` : ""}`),
  chunkVersion: async (id: string, body: Record<string, unknown>) => {
    const result = await request<Chunk[] | OperationAccepted>(`/corpus-versions/${id}/chunk`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (isOperation(result)) await waitForJob(result);
    return api.getChunks(id);
  },
  indexVersion: async (id: string) => {
    const result = await request<OperationAccepted>(`/corpus-versions/${id}/index`, { method: "POST" });
    await waitForJob(result);
    return api.getIndexStatus(id);
  },
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
  evaluateRun: async (id: string, metricVersions?: string[]) => {
    const result = await request<EvaluationBundle | OperationAccepted>(`/query-runs/${id}/evaluation`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ metric_versions: metricVersions ?? null }),
    });
    if (isOperation(result)) await waitForJob(result);
    return api.getRunEvaluation(id);
  },
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
  extractDatasets: async (documentId: string, body: {
    strategy: "baseline" | "retrieval_assisted";
    provider: "fake" | "gemini" | "openai_compatible";
    model?: string;
  }) => {
    const result = await request<ExtractionJob | OperationAccepted>(`/documents/${documentId}/extract-datasets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (isOperation(result)) await waitForJob(result);
    return result;
  },
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
  listExperiments: () => request<{ items: Experiment[]; total: number; offset: number; limit: number }>("/experiments?limit=100").then((page) => page.items),
  createExperiment: (body: {
    name: string;
    research_question: string;
    corpus_version_id: string;
    benchmark_version_id: string;
    pipeline_configuration_ids: string[];
    repetitions: number;
    code_commit: string;
    stop_on_error?: boolean;
  }) => request<Experiment>("/experiments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }),
  getExperiment: (id: string) => request<ExperimentDetail>(`/experiments/${id}`).then((detail) => ({ ...detail.experiment, progress: detail.progress })),
  getHumanReviewQueue: (id: string, offset = 0, limit = 100) => request<HumanReviewQueuePage>(`/experiments/${id}/review-queue?offset=${offset}&limit=${limit}`),
  estimateExperiment: (id: string) =>
    request<ExperimentCostEstimate>(`/experiments/${id}/estimate`, { method: "POST" }),
  freezeExperiment: (id: string) =>
    request<Experiment>(`/experiments/${id}/freeze`, { method: "POST" }),
  startExperiment: (id: string) =>
    request<ExperimentExecutionReport | OperationAccepted>(`/experiments/${id}/start`, { method: "POST" }).then(async (report) => { if(isOperation(report)) { await waitForJob(report); return api.getExperiment(id); } return { ...report.experiment, progress: report.progress }; }),
  resumeExperiment: (id: string) =>
    request<ExperimentExecutionReport | OperationAccepted>(`/experiments/${id}/resume`, { method: "POST" }).then(async (report) => { if(isOperation(report)) { await waitForJob(report); return api.getExperiment(id); } return { ...report.experiment, progress: report.progress }; }),
  pauseExperiment: (id: string) => request<Experiment>(`/experiments/${id}/pause`, { method: "POST" }),
  generateExperimentExports: async (id: string) => { const operation = await request<OperationAccepted>(`/experiments/${id}/exports`, { method: "POST" }); await waitForJob(operation); },
  getExperimentResults: (id: string, filters?: Partial<ExperimentResultsFilters>, offset = 0, limit = 500) => {
    const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    for (const value of filters?.pipeline_configuration_ids ?? []) query.append("pipeline_configuration_id", value);
    for (const value of filters?.question_types ?? []) query.append("question_type", value);
    for (const value of filters?.difficulties ?? []) query.append("difficulty", value);
    for (const value of filters?.run_statuses ?? []) query.append("run_status", value);
    for (const value of filters?.answerabilities ?? []) query.append("answerability", value);
    for (const value of filters?.pipeline_modes ?? []) query.append("pipeline_mode", value);
    for (const value of filters?.failure_stages ?? []) query.append("failure_stage", value);
    for (const value of filters?.failure_categories ?? []) query.append("failure_category", value);
    for (const value of filters?.failure_codes ?? []) query.append("failure_code", value);
    if (filters?.include_infrastructure_failures !== undefined) query.set("include_infrastructure_failures", String(filters.include_infrastructure_failures));
    return request<ExperimentResultsResponse>(`/experiments/${id}/results?${query}`).then(adaptExperimentResults);
  },
  experimentExportUrl: (id: string, format: "csv" | "json") =>
    `${API_ROOT}/experiments/${id}/export?format=${format}`,
};
