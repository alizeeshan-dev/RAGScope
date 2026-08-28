export type Status = "draft" | "indexing" | "ready" | "failed" | "archived";

export interface CorpusVersion {
  id: string;
  corpus_id: string;
  version_label: string;
  status: Status;
  document_count: number;
  content_hash: string | null;
  parser_configuration: Record<string, unknown>;
  chunker_configuration: Record<string, unknown>;
  embedding_configuration: Record<string, unknown>;
  created_at: string;
  frozen_at: string | null;
}

export interface Corpus {
  id: string;
  name: string;
  description: string | null;
  domain: string | null;
  created_at: string;
  updated_at: string;
  versions?: CorpusVersion[];
}

export interface SourceDocument {
  id: string;
  corpus_version_id: string;
  title: string | null;
  authors: string[];
  publication_year: number | null;
  mime_type: string;
  page_count: number | null;
  parse_status: string;
  parse_warnings: Array<Record<string, unknown> | string>;
  file_hash: string;
  created_at: string;
}

export interface Artifact {
  id: string;
  artifact_type: string;
  content_hash: string;
  media_type: string;
  original_filename: string | null;
  producing_operation: string;
  producer_version: string | null;
  configuration: Record<string, unknown>;
  size_bytes: number;
}

export interface DocumentElement {
  id: string;
  document_id: string;
  parent_element_id: string | null;
  element_type: string;
  sequence_number: number;
  page_number: number | null;
  section_path: string[];
  text: string;
  bounding_box: Record<string, unknown> | null;
  parser_metadata: Record<string, unknown>;
}

export interface Chunk {
  id: string;
  document_id: string;
  corpus_version_id: string;
  chunker_id: string;
  sequence_number: number;
  text: string;
  token_count: number;
  page_start: number | null;
  page_end: number | null;
  section_path: string[];
  source_element_ids: string[];
  content_hash: string;
  metadata: Record<string, unknown>;
}

export interface IndexStatus {
  id: string;
  index_type: "lexical" | "dense";
  status: string;
  chunk_count: number;
  indexed_count: number;
  failure_count: number;
  integrity_valid: boolean;
  integrity_errors: string[];
}

export interface PipelineConfiguration {
  id: string;
  name: string;
  version: number;
  execution_mode: "fixed" | "adaptive";
  router_configuration_id: string | null;
  adaptive_configuration: Record<string, unknown>;
  retrieval_mode: "none" | "lexical" | "dense" | "hybrid";
  lexical_configuration: Record<string, unknown>;
  dense_configuration: Record<string, unknown>;
  fusion_configuration: Record<string, unknown>;
  reranker_configuration: Record<string, unknown>;
  query_processing_configuration: Record<string, unknown>;
  context_configuration: Record<string, unknown>;
  generation_configuration: Record<string, unknown>;
  citation_configuration: Record<string, unknown>;
  prompt_versions: Record<string, unknown>;
  configuration_hash: string;
  created_at: string;
  frozen_at: string | null;
}

export interface QueryRun {
  id: string;
  corpus_version_id: string;
  pipeline_configuration_id: string;
  benchmark_question_id: string | null;
  query_text: string;
  normalized_query: string;
  rewritten_query: string | null;
  status: "pending" | "running" | "succeeded" | "failed";
  route_decision: Record<string, unknown>;
  classification: Record<string, unknown>;
  answer_text: string | null;
  answerability_decision: "answerable" | "partially_answerable" | "unanswerable" | null;
  limitations: string[];
  abstention_reason: string | null;
  total_latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost: number | null;
  failure_code: string | null;
  failure_message?: string | null;
  generation_metadata?: Record<string, unknown>;
  generation_provider?: string | null;
  generation_model?: string | null;
  generation_request_id?: string | null;
  generation_latency_ms?: number | null;
  context_artifact_id?: string | null;
  raw_response_artifact_id?: string | null;
}

export interface EvaluationResult {
  id: string;
  query_run_id: string;
  metric_name: string;
  metric_scope: "parsing" | "retrieval" | "context" | "generation" | "citation" | "cost" | "overall";
  metric_value: number | null;
  metric_version: string;
  evaluation_method: "automated" | "deterministic" | "human" | "model_judge" | "operational" | string;
  details: Record<string, unknown>;
  input_snapshot: Record<string, unknown>;
  input_hash: string;
  created_at: string;
}

export interface FailureAttribution {
  id: string;
  query_run_id: string;
  sequence_number: number;
  is_primary: boolean;
  pipeline_stage: string;
  automatic_label: string;
  attribution_rule: string;
  evidence: Record<string, unknown>;
  taxonomy_version: string;
  rules_version: string;
  human_override_label: string | null;
  human_override_note: string | null;
  human_reviewed_at: string | null;
}

export interface EvaluationBundle {
  query_run_id: string;
  benchmark_question_id: string | null;
  metrics: EvaluationResult[];
  failure_attributions: FailureAttribution[];
}

export interface RouterConfiguration {
  id: string;
  name: string;
  version: number;
  router_type: string;
  router_version: string;
  classifier_version: string;
  configuration: Record<string, unknown>;
  configuration_hash: string;
  created_at: string;
  frozen_at: string | null;
}

export interface RunRetrievalResult {
  id: string;
  chunk_id: string;
  retriever_type: string;
  original_rank: number | null;
  original_score: number | null;
  normalized_score: number | null;
  fused_rank: number | null;
  fusion_score: number | null;
  reranked_rank: number | null;
  reranker_score: number | null;
  selected_for_context: boolean;
  timing_ms: number | null;
  text: string;
  document_id: string;
  document_title?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  section_path?: string[];
}

export interface RunCitation {
  id: string;
  citation_id: string;
  chunk_id: string;
  document_id: string;
  page_number: number | null;
  referenced_text: string;
}

export interface RunClaim {
  id: string;
  sequence_number: number;
  claim_text: string;
  claim_type: string;
  citation_ids: string[];
  support_status: string;
  citations: RunCitation[];
}

export interface RunContextSource {
  id: string;
  citation_id: string | null;
  chunk_id: string;
  document_id: string;
  selected: boolean;
  exclusion_reason: string | null;
  token_count: number;
  page_start: number | null;
  page_end: number | null;
  text: string;
  document_title?: string | null;
  section_path?: string[];
}

export type TraceSpanStatus = "running" | "succeeded" | "failed";

export interface TraceSpan {
  id: string;
  query_run_id: string;
  parent_span_id: string | null;
  sequence_number: number;
  span_type: string;
  name: string;
  status: TraceSpanStatus;
  started_at: string;
  finished_at: string | null;
  latency_ms: number | null;
  input_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  configuration_snapshot: Record<string, unknown>;
  error_code: string | null;
  artifact_ids: string[];
}

export interface TraceArtifactReference {
  id: string;
  query_run_id?: string | null;
  trace_span_id?: string | null;
  artifact_type: string;
  content_hash: string;
  media_type: string;
  producing_operation: string;
  producer_version: string | null;
  configuration?: Record<string, unknown>;
  size_bytes?: number;
}

export interface TraceSummary {
  total_latency_ms?: number | null;
  latency_by_stage_ms?: Record<string, number>;
  input_tokens?: number | null;
  output_tokens?: number | null;
  estimated_cost?: number | null;
  retrieval_candidate_count?: number;
  reranked_candidate_count?: number;
  selected_context_count?: number;
  excluded_context_count?: number;
  retrieved_document_ids?: string[];
  rank_movement?: Array<Record<string, unknown>>;
  evidence_flow?: Record<string, number>;
  unsupported_claim_count?: number;
  not_evaluated_claim_count?: number;
  failure_stage?: string | null;
  [key: string]: unknown;
}

export interface TraceRunSnapshot {
  id: string;
  corpus_version_id: string;
  pipeline_configuration_id: string;
  prompt_template_id: string | null;
  status: string;
  original_query: string;
  normalized_query: string;
  rewritten_query: string | null;
  classification: Record<string, unknown>;
  configured_route: Record<string, unknown>;
  answerability: string | null;
  failure_code: string | null;
}

export interface ObservableTraceExport {
  schema_version: "ragscope.observable-trace.v1" | string;
  exported_at: string;
  run: TraceRunSnapshot;
  pipeline_configuration: Record<string, unknown>;
  prompt: Record<string, unknown> | null;
  summary: TraceSummary;
  spans: TraceSpan[];
  artifacts: TraceArtifactReference[];
}

export interface ComparisonConfigurationFacet {
  key: string;
  label: string;
  values: unknown[];
}

export interface ComparisonStage {
  sequence_number: number;
  name: string;
  span_type: string;
  status: string;
  latency_ms: number | null;
  error_code: string | null;
}

export interface ComparisonContextSource {
  chunk_id: string;
  document_id: string;
  document_title: string | null;
  citation_id: string | null;
  sequence_number: number;
  selected: boolean;
  exclusion_reason: string | null;
  token_count: number;
  page_start: number | null;
  page_end: number | null;
  text: string;
}

export interface ComparisonCitation {
  citation_id: string;
  chunk_id: string;
  document_id: string;
  page_number: number | null;
  referenced_text: string;
}

export interface ComparisonClaim {
  sequence_number: number;
  text: string;
  support_status: string;
  citations: ComparisonCitation[];
}

export interface ComparisonColumn {
  position: number;
  query_run_id: string;
  pipeline: {
    id: string;
    name: string;
    version: number;
    configuration_hash: string;
    retrieval_mode: string;
    facets: Record<string, unknown>;
    frozen_snapshot: Record<string, unknown>;
  };
  run_status: string;
  original_query: string;
  rewritten_query: string | null;
  configured_route: Record<string, unknown>;
  classification: Record<string, unknown>;
  answerability: string | null;
  answer: string | null;
  limitations: string[];
  abstention_reason: string | null;
  context_artifact_id: string | null;
  context_sources: ComparisonContextSource[];
  claims: ComparisonClaim[];
  total_latency_ms: number | null;
  stage_latency_ms: Record<string, number>;
  timeline: ComparisonStage[];
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost: number | null;
  cost_currency: string | null;
  failure_code: string | null;
  failure_message: string | null;
}

export interface ComparisonEvidenceCell {
  query_run_id: string;
  pipeline_configuration_id: string;
  present: boolean;
  lexical_rank: number | null;
  lexical_score: number | null;
  dense_rank: number | null;
  dense_score: number | null;
  fused_rank: number | null;
  fusion_score: number | null;
  reranked_rank: number | null;
  reranker_score: number | null;
  selected_for_context: boolean;
  exclusion_reason: string | null;
  citation_id: string | null;
}

export interface ComparisonEvidenceRow {
  chunk_id: string;
  document_id: string;
  document_title: string | null;
  page_start: number | null;
  page_end: number | null;
  section_path: string[];
  text: string;
  cells: ComparisonEvidenceCell[];
}

export interface QueryComparison {
  id: string;
  corpus_version_id: string;
  original_question: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  failure_message: string | null;
  configuration_differences: ComparisonConfigurationFacet[];
  evidence_overlap: Array<{
    left_query_run_id: string;
    right_query_run_id: string;
    shared_chunk_ids: string[];
    left_only_chunk_ids: string[];
    right_only_chunk_ids: string[];
    jaccard: number | null;
  }>;
  evidence_rows: ComparisonEvidenceRow[];
  columns: ComparisonColumn[];
}

export type DatasetExtractionStatus = "pending" | "running" | "succeeded" | "failed" | "invalid_output";
export type DatasetReviewStatus = "unreviewed" | "in_review" | "approved" | "rejected";
export type FieldReviewAction = "accept" | "edit" | "reject" | "clear" | "mark_not_stated" | "approve_record" | "reopen_record";

export interface FieldEvidence {
  id: string;
  dataset_record_id: string;
  field_name: string;
  document_id: string;
  page_number: number | null;
  element_id: string | null;
  chunk_id: string | null;
  supporting_text: string;
  extraction_method: string;
  model_confidence_label: string | null;
  review_status: string;
  reviewer_note: string | null;
}

export interface DatasetCorrection {
  id: string;
  field_name: string;
  action: FieldReviewAction;
  original_value: unknown;
  previous_value: unknown;
  corrected_value?: unknown;
  new_value?: unknown;
  previous_state?: string;
  new_state?: string;
  reviewer_note: string | null;
  created_at: string;
  evidence_backed: boolean;
}

export interface DatasetRecord {
  id: string;
  corpus_version_id: string;
  source_document_id?: string | null;
  name: string | null;
  description: string | null;
  domain: string | null;
  modalities: string[] | null;
  task_types: string[] | null;
  instance_count: number | null;
  participant_count: number | null;
  annotation_types: string[] | null;
  languages: string[] | null;
  license: string | null;
  access_url: string | null;
  human_ratings: string | null;
  collection_method: string | null;
  known_limitations: string | null;
  extraction_status: DatasetExtractionStatus | string;
  review_status: DatasetReviewStatus | string;
  not_stated_fields?: string[];
  original_model_output?: Record<string, unknown>;
  original_values?: Record<string, unknown>;
  current_values?: Record<string, unknown>;
  field_states?: Record<string, string | Record<string, unknown>>;
  field_evidence?: FieldEvidence[];
  evidence?: FieldEvidence[];
  correction_history?: DatasetCorrection[];
  revisions?: DatasetCorrection[];
  extraction_configuration?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface DatasetRecordList {
  items: DatasetRecord[];
  total: number;
}

export interface DatasetComparison {
  corpus_version_id: string;
  fields: Record<string, Record<string, unknown>>;
  records: DatasetRecord[];
}

export type BenchmarkVersionStatus = "draft" | "frozen";
export type AnnotationStatus = "draft" | "in_review" | "approved" | "rejected";

export interface Benchmark {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  versions?: BenchmarkVersion[];
}

export interface BenchmarkVersion {
  id: string;
  benchmark_id: string;
  version: number;
  version_label?: string | null;
  corpus_version_id: string;
  status: BenchmarkVersionStatus;
  notes: string | null;
  created_at: string;
  frozen_at: string | null;
  question_count?: number;
  questions?: BenchmarkQuestion[];
}

export interface BenchmarkEvidence {
  id: string;
  evidence_set_id: string;
  document_id: string;
  page_number: number | null;
  element_id: string | null;
  chunk_id: string | null;
  selected_text: string;
  created_at?: string;
}

export interface BenchmarkEvidenceSet {
  id: string;
  benchmark_question_id: string;
  set_number: number;
  description: string | null;
  created_at: string;
  references: BenchmarkEvidence[];
}

export interface BenchmarkQuestion {
  id: string;
  benchmark_version_id: string;
  question_text: string;
  question_type: string;
  difficulty: "easy" | "medium" | "hard" | string;
  answerable: boolean;
  expected_answerability: "answerable" | "partially_answerable" | "unanswerable";
  reference_answer: string | null;
  answer_criteria: string | null;
  unanswerable_explanation: string | null;
  required_document_ids: string[];
  required_chunk_ids: string[];
  acceptable_evidence_sets: BenchmarkEvidenceSet[];
  tags: string[];
  annotation_notes: string | null;
  annotation_status: AnnotationStatus | string;
  leakage_warning: boolean;
  leakage_score?: number | null;
  model_suggestion?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ExtractionJob {
  id?: string;
  job_id?: string;
  record_ids?: string[];
  status: string;
  job_type?: string;
  error_code?: string | null;
}

export interface OperationAccepted {
  job_id: string;
  job_type: string;
  status: string;
  resource_type: string;
  resource_id: string;
  status_url: string;
}

export interface BackgroundJob {
  id: string;
  job_type: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | string;
  progress_current: number;
  progress_total: number | null;
  error_code: string | null;
  error_message: string | null;
  result_artifact_ids: string[];
  cancellation_requested_at: string | null;
}

export type ExperimentStatus =
  | "draft"
  | "frozen"
  | "running"
  | "paused"
  | "completed"
  | "completed_with_failures"
  | "failed"
  | "cancelled";

export interface ExperimentProgress {
  total: number;
  planned: number;
  running: number;
  retryable: number;
  succeeded: number;
  failed: number;
}

export interface PipelineCostEstimate {
  pipeline_configuration_id: string;
  run_count: number;
  maximum_generation_input_tokens: number;
  maximum_generation_output_tokens: number;
  maximum_embedding_input_tokens: number;
  maximum_reranker_calls: number;
  generation_cost: number | null;
  embedding_cost: number | null;
  reranker_cost: number | null;
  maximum_expected_cost: number | null;
  currency: string;
  missing_pricing: string[];
}

export interface ExperimentCostEstimate {
  experiment_id: string;
  run_count: number;
  per_pipeline: PipelineCostEstimate[];
  maximum_expected_cost: number | null;
  currency: string | null;
  cost_fully_configured: boolean;
}

export interface Experiment {
  id: string;
  name: string;
  research_question: string;
  corpus_version_id: string;
  benchmark_version_id: string;
  pipeline_configuration_ids: string[];
  repetitions: number;
  status: ExperimentStatus | string;
  code_commit: string;
  stop_on_error: boolean;
  dependency_snapshot: Record<string, unknown>;
  configuration_hash: string | null;
  retry_policy: Record<string, unknown>;
  analysis_configuration?: Record<string, unknown>;
  progress?: ExperimentProgress;
  cost_estimate?: ExperimentCostEstimate | null;
  created_at: string;
  frozen_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface ExperimentExecutionReport {
  experiment: Experiment;
  progress: ExperimentProgress;
  executed_attempts: number;
}

export interface HumanReviewQueueItem {
  query_run_id: string;
  benchmark_question_id: string;
  pipeline_configuration_id: string;
  missing_labels: string[];
}

export interface HumanReviewQueuePage {
  items: HumanReviewQueueItem[];
  total: number;
  reviewed: number;
  remaining: number;
  offset: number;
  limit: number;
}

export interface ExperimentDetail {
  experiment: Experiment;
  progress: ExperimentProgress;
}

export interface AnalysisMetricObservation {
  name: string;
  version: string;
  scope: string;
  method: string;
  value: number | null;
  details: Record<string, unknown>;
}

export interface AnalysisEvidenceSurvival {
  retrieval: number | null;
  reranking: number | null;
  context: number | null;
  metric_version: string;
}

export interface ExperimentAnalysisRun {
  run_id: string;
  pipeline_id: string;
  question_id: string;
  run_status: string;
  infrastructure_failure_code: string | null;
  dimensions: Record<string, string | number | boolean | null>;
  metrics: AnalysisMetricObservation[];
  evidence_survival: AnalysisEvidenceSurvival | null;
}

export interface ExperimentAggregate {
  group: Array<[string, string | number | boolean | null]>;
  metric_name: string;
  metric_version: string;
  metric_scope: string;
  evaluation_method: string;
  denominator_policy: { exclude_infrastructure_failures: boolean; description: string };
  total_run_count: number;
  denominator_count: number;
  missing_metric_count: number;
  excluded_infrastructure_count: number;
  value_sum: number | null;
  mean: number | null;
  median: number | null;
  contributing_run_ids: string[];
  missing_run_ids: string[];
  excluded_run_ids: string[];
}

export interface ExperimentResultsResponse {
  experiment_id: string;
  filters: Record<string, unknown>;
  sample_size: number;
  infrastructure_failures: number;
  aggregates: ExperimentAggregate[];
  evidence_survival: ExperimentAggregate[];
  adaptive_analysis: Record<string, unknown> | null;
  headline_metrics: Record<string, ExperimentHeadlineMetric>;
  visualizations: ExperimentVisualizationData;
  available_dimensions: Record<string, string[]>;
  runs: ExperimentAnalysisRun[];
  offset?: number;
  limit?: number;
  total_runs?: number;
}

export interface ExperimentHeadlineMetric {
  value: number | null;
  numerator: number | null;
  denominator: number;
  missing: number;
  excluded_infrastructure: number;
}

export interface ExperimentVisualizationData {
  cost_correctness: Array<{ run_id: string; pipeline: string; correctness: number; cost: number; currency: string | null }>;
  latency_by_pipeline: Array<{ pipeline: string; n: number; missing: number; infrastructure_failures: number; minimum: number | null; q1: number | null; median: number | null; q3: number | null; maximum: number | null }>;
  failure_distribution: Array<{ stage: string; category: string; code: string | null; count: number }>;
  performance_by_question_type: Array<{ question_type: string; pipeline: string; value: number | null; n: number; denominator: number; missing: number; excluded_infrastructure: number }>;
}

export interface ExperimentMetricValue {
  metric_name: string;
  metric_value: number | null;
  metric_version: string;
  evaluation_method: string;
  denominator?: number | null;
}

export interface ExperimentResultRow {
  query_run_id: string;
  benchmark_question_id: string;
  pipeline_configuration_id: string;
  pipeline_name: string;
  repetition: number;
  question_type: string;
  difficulty: string;
  answerability: string;
  pipeline_mode: string;
  run_status: string;
  infrastructure_failure: boolean;
  failure_stage: string | null;
  failure_category: string | null;
  failure_code: string | null;
  total_latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost: number | null;
  cost_currency: string | null;
  retrieval_required_count: number | null;
  retrieval_evidence_count: number | null;
  reranking_evidence_count: number | null;
  context_evidence_count: number | null;
  metrics: ExperimentMetricValue[];
}

export interface ExperimentResultsFilters {
  pipeline_configuration_ids: string[];
  question_types: string[];
  difficulties: string[];
  run_statuses: string[];
  answerabilities: string[];
  pipeline_modes: string[];
  failure_stages: string[];
  failure_categories: string[];
  failure_codes: string[];
  include_infrastructure_failures: boolean;
}

export interface ExperimentResults {
  schema_version: string;
  experiment_id: string;
  generated_at: string;
  filters: ExperimentResultsFilters;
  total_rows: number;
  offset: number;
  limit: number;
  rows: ExperimentResultRow[];
  available_question_types: string[];
  available_difficulties: string[];
  available_pipeline_ids: string[];
  aggregates: ExperimentAggregate[];
  evidence_survival: ExperimentAggregate[];
  headline_metrics: Record<string, ExperimentHeadlineMetric>;
  visualizations: ExperimentVisualizationData;
  available_dimensions: Record<string, string[]>;
}
