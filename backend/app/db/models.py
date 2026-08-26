from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CorpusVersionStatus(StrEnum):
    DRAFT = "draft"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class ParseStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    READY = "ready"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IndexStatus(StrEnum):
    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


class IndexType(StrEnum):
    LEXICAL = "lexical"
    DENSE = "dense"


class RetrievalMode(StrEnum):
    NONE = "none"
    LEXICAL = "lexical"
    DENSE = "dense"
    HYBRID = "hybrid"


class QueryRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Answerability(StrEnum):
    ANSWERABLE = "answerable"
    PARTIALLY_ANSWERABLE = "partially_answerable"
    UNANSWERABLE = "unanswerable"


class ClaimSupportStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    NOT_EVALUATED = "not_evaluated"


class TraceSpanStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DatasetExtractionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    VALID = "valid"
    INVALID = "invalid"
    FAILED = "failed"


class DatasetReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class FieldReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NOT_STATED = "not_stated"
    CLEARED = "cleared"


class BenchmarkVersionStatus(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"


class BenchmarkQuestionType(StrEnum):
    DIRECT_FACT_LOOKUP = "direct_fact_lookup"
    DATASET_DISCOVERY = "dataset_discovery"
    DATASET_COMPARISON = "dataset_comparison"
    MULTI_DOCUMENT_SYNTHESIS = "multi_document_synthesis"
    MULTI_HOP_REASONING = "multi_hop_reasoning"
    TABLE_BASED = "table_based"
    BROAD_SUMMARY = "broad_summary"
    AMBIGUOUS = "ambiguous"
    UNANSWERABLE = "unanswerable"
    FALSE_PREMISE = "false_premise"
    CONTRADICTORY_SOURCE = "contradictory_source"
    DISTRACTOR_SENSITIVE = "distractor_sensitive"


class BenchmarkDifficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class BenchmarkAnnotationStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"


class EvaluationMetricScope(StrEnum):
    PARSING = "parsing"
    RETRIEVAL = "retrieval"
    CONTEXT = "context"
    GENERATION = "generation"
    CITATION = "citation"
    COST = "cost"
    OVERALL = "overall"


class PipelineExecutionMode(StrEnum):
    FIXED = "fixed"
    ADAPTIVE = "adaptive"


def enum_type(enum: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Corpus(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "corpora"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255))

    versions: Mapped[list[CorpusVersion]] = relationship(
        back_populates="corpus", cascade="all, delete-orphan", passive_deletes=True
    )


class CorpusVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "corpus_versions"
    __table_args__ = (
        UniqueConstraint("corpus_id", "version_label"),
        CheckConstraint("document_count >= 0", name="document_count_nonnegative"),
    )

    corpus_id: Mapped[UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_label: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[CorpusVersionStatus] = mapped_column(
        enum_type(CorpusVersionStatus, "corpus_version_status"),
        default=CorpusVersionStatus.DRAFT,
        nullable=False,
        index=True,
    )
    document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    parser_configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    chunker_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    embedding_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    corpus: Mapped[Corpus] = relationship(back_populates="versions")
    documents: Mapped[list[SourceDocument]] = relationship(
        back_populates="corpus_version", cascade="all, delete-orphan", passive_deletes=True
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="corpus_version", cascade="all, delete-orphan", passive_deletes=True
    )
    indexes: Mapped[list[SearchIndex]] = relationship(
        back_populates="corpus_version", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None


class SourceDocument(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "source_documents"
    __table_args__ = (UniqueConstraint("corpus_version_id", "file_hash"),)

    corpus_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("corpus_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(500))
    authors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    publication_year: Mapped[int | None] = mapped_column(Integer)
    source_type: Mapped[str | None] = mapped_column(String(100))
    source_uri: Mapped[str | None] = mapped_column(Text)
    license_information: Mapped[str | None] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    parse_status: Mapped[ParseStatus] = mapped_column(
        enum_type(ParseStatus, "document_parse_status"),
        default=ParseStatus.PENDING,
        nullable=False,
    )
    parse_warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    metadata_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    corpus_version: Mapped[CorpusVersion] = relationship(back_populates="documents")
    elements: Mapped[list[DocumentElement]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentElement(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_elements"
    __table_args__ = (UniqueConstraint("document_id", "sequence_number"),)

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_element_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_elements.id", ondelete="SET NULL"), index=True
    )
    element_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bounding_box: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    parser_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    document: Mapped[SourceDocument] = relationship(back_populates="elements")


class Chunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunker_id", "sequence_number"),
        UniqueConstraint("corpus_version_id", "id"),
        CheckConstraint("token_count >= 0", name="token_count_nonnegative"),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    corpus_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("corpus_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunker_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source_element_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    document: Mapped[SourceDocument] = relationship(back_populates="chunks")
    corpus_version: Mapped[CorpusVersion] = relationship(back_populates="chunks")


class Artifact(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("storage_key"),)

    corpus_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("corpus_versions.id", ondelete="RESTRICT"), index=True
    )
    document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    query_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "query_runs.id",
            ondelete="CASCADE",
            use_alter=True,
            name="fk_artifacts_query_run_id_query_runs",
        ),
        index=True,
    )
    trace_span_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "trace_spans.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_artifacts_trace_span_id_trace_spans",
        ),
        index=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(500))
    producing_operation: Mapped[str] = mapped_column(String(255), nullable=False)
    producer_version: Mapped[str | None] = mapped_column(String(255))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("progress_current >= 0", name="progress_current_nonnegative"),
        CheckConstraint("progress_total >= 0", name="progress_total_nonnegative"),
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
    )

    job_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    input_reference: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        enum_type(JobStatus, "job_status"), default=JobStatus.QUEUED, nullable=False, index=True
    )
    progress_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    result_artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)


class SearchIndex(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "search_indexes"
    __table_args__ = (
        UniqueConstraint("corpus_version_id", "index_type", "configuration_hash"),
        CheckConstraint("chunk_count >= 0", name="chunk_count_nonnegative"),
        CheckConstraint("indexed_count >= 0", name="indexed_count_nonnegative"),
        CheckConstraint("failure_count >= 0", name="failure_count_nonnegative"),
    )

    corpus_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("corpus_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index_type: Mapped[IndexType] = mapped_column(
        enum_type(IndexType, "search_index_type"), nullable=False
    )
    status: Mapped[IndexStatus] = mapped_column(
        enum_type(IndexStatus, "search_index_status"),
        default=IndexStatus.PENDING,
        nullable=False,
        index=True,
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(255))
    model_id: Mapped[str | None] = mapped_column(String(255))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    preprocessing_version: Mapped[str | None] = mapped_column(String(255))
    similarity_method: Mapped[str | None] = mapped_column(String(50))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    indexed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    corpus_version: Mapped[CorpusVersion] = relationship(back_populates="indexes")
    entries: Mapped[list[IndexEntry]] = relationship(
        back_populates="search_index", cascade="all, delete-orphan", passive_deletes=True
    )


class IndexEntry(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "index_entries"
    __table_args__ = (UniqueConstraint("index_id", "chunk_id"),)

    index_id: Mapped[UUID] = mapped_column(
        ForeignKey("search_indexes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    searchable_text: Mapped[str | None] = mapped_column(Text)
    # SQLite stores portable JSON in tests; PostgreSQL gets the native pgvector type.
    embedding: Mapped[list[float] | None] = mapped_column(
        JSON().with_variant(Vector(), "postgresql")
    )
    entry_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    search_index: Mapped[SearchIndex] = relationship(back_populates="entries")


class RouterConfiguration(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "router_configurations"
    __table_args__ = (UniqueConstraint("name", "version"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    router_type: Mapped[str] = mapped_column(
        String(50), default="rule_based", nullable=False, index=True
    )
    router_version: Mapped[str] = mapped_column(String(100), nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(100), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None


class PipelineConfiguration(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "pipeline_configurations"
    __table_args__ = (UniqueConstraint("name", "version"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_mode: Mapped[PipelineExecutionMode] = mapped_column(
        enum_type(PipelineExecutionMode, "pipeline_execution_mode"),
        default=PipelineExecutionMode.FIXED,
        nullable=False,
        index=True,
    )
    router_configuration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("router_configurations.id", ondelete="RESTRICT"), index=True
    )
    adaptive_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    retrieval_mode: Mapped[RetrievalMode] = mapped_column(
        enum_type(RetrievalMode, "retrieval_mode"), nullable=False, index=True
    )
    lexical_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    dense_configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    fusion_configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reranker_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    query_processing_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    context_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    generation_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    citation_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    prompt_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    query_runs: Mapped[list[QueryRun]] = relationship(back_populates="pipeline_configuration")

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None


class PromptTemplate(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (UniqueConstraint("prompt_id", "version"),)

    prompt_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    query_runs: Mapped[list[QueryRun]] = relationship(back_populates="prompt_template")

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None


class QueryRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "query_runs"

    corpus_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("corpus_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    benchmark_question_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("benchmark_questions.id", ondelete="SET NULL"), index=True
    )
    pipeline_configuration_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipeline_configurations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    prompt_template_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="RESTRICT"), index=True
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_query: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_query: Mapped[str | None] = mapped_column(Text)
    status: Mapped[QueryRunStatus] = mapped_column(
        enum_type(QueryRunStatus, "query_run_status"),
        default=QueryRunStatus.PENDING,
        nullable=False,
        index=True,
    )
    route_decision: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    classification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    extracted_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    answer_text: Mapped[str | None] = mapped_column(Text)
    answerability_decision: Mapped[Answerability | None] = mapped_column(
        enum_type(Answerability, "answerability"), index=True
    )
    limitations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    abstention_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    failure_code: Mapped[str | None] = mapped_column(String(100), index=True)
    failure_message: Mapped[str | None] = mapped_column(Text)
    generation_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    context_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), index=True
    )
    raw_response_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), index=True
    )

    pipeline_configuration: Mapped[PipelineConfiguration] = relationship(
        back_populates="query_runs"
    )
    prompt_template: Mapped[PromptTemplate | None] = relationship(back_populates="query_runs")
    retrieval_results: Mapped[list[RetrievalResultRecord]] = relationship(
        back_populates="query_run", cascade="all, delete-orphan", passive_deletes=True
    )
    context_sources: Mapped[list[ContextSource]] = relationship(
        back_populates="query_run", cascade="all, delete-orphan", passive_deletes=True
    )
    claims: Mapped[list[GeneratedClaim]] = relationship(
        back_populates="query_run", cascade="all, delete-orphan", passive_deletes=True
    )
    trace_spans: Mapped[list[TraceSpan]] = relationship(
        back_populates="query_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="TraceSpan.query_run_id",
    )
    comparison_links: Mapped[list[QueryComparisonRun]] = relationship(
        back_populates="query_run", cascade="all, delete-orphan", passive_deletes=True
    )


class TraceSpan(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One observable application-stage span; never hidden model reasoning."""

    __tablename__ = "trace_spans"
    __table_args__ = (UniqueConstraint("query_run_id", "sequence_number"),)

    query_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_span_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("trace_spans.id", ondelete="SET NULL"), index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    span_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[TraceSpanStatus] = mapped_column(
        enum_type(TraceSpanStatus, "trace_span_status"),
        default=TraceSpanStatus.RUNNING,
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(100), index=True)
    artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    query_run: Mapped[QueryRun] = relationship(
        back_populates="trace_spans", foreign_keys=[query_run_id]
    )
    parent: Mapped[TraceSpan | None] = relationship(
        remote_side="TraceSpan.id", foreign_keys=[parent_span_id]
    )


class QueryComparison(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "query_comparisons"

    corpus_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("corpus_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    original_question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="running", nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_message: Mapped[str | None] = mapped_column(Text)

    runs: Mapped[list[QueryComparisonRun]] = relationship(
        back_populates="comparison", cascade="all, delete-orphan", passive_deletes=True
    )


class QueryComparisonRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "query_comparison_runs"
    __table_args__ = (
        UniqueConstraint("comparison_id", "pipeline_configuration_id"),
        UniqueConstraint("comparison_id", "column_position"),
        UniqueConstraint("comparison_id", "query_run_id"),
    )

    comparison_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_comparisons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    query_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pipeline_configuration_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipeline_configurations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    column_position: Mapped[int] = mapped_column(Integer, nullable=False)

    comparison: Mapped[QueryComparison] = relationship(back_populates="runs")
    query_run: Mapped[QueryRun] = relationship(back_populates="comparison_links")


class DatasetRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reviewable dataset description with immutable model-origin snapshots."""

    __tablename__ = "dataset_records"

    corpus_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("corpus_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    extraction_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    strategy: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(500), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    modalities: Mapped[list[str] | None] = mapped_column(JSON)
    task_types: Mapped[list[str] | None] = mapped_column(JSON)
    instance_count: Mapped[int | None] = mapped_column(Integer)
    participant_count: Mapped[int | None] = mapped_column(Integer)
    annotation_types: Mapped[list[str] | None] = mapped_column(JSON)
    languages: Mapped[list[str] | None] = mapped_column(JSON)
    license: Mapped[str | None] = mapped_column(Text)
    access_url: Mapped[str | None] = mapped_column(Text)
    human_ratings: Mapped[str | None] = mapped_column(Text)
    collection_method: Mapped[str | None] = mapped_column(Text)
    known_limitations: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[DatasetExtractionStatus] = mapped_column(
        enum_type(DatasetExtractionStatus, "dataset_extraction_status"),
        default=DatasetExtractionStatus.PENDING,
        nullable=False,
        index=True,
    )
    review_status: Mapped[DatasetReviewStatus] = mapped_column(
        enum_type(DatasetReviewStatus, "dataset_review_status"),
        default=DatasetReviewStatus.UNREVIEWED,
        nullable=False,
        index=True,
    )
    original_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    current_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    field_states: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    extraction_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    raw_response_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), index=True
    )
    structured_result_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), index=True
    )

    evidence: Mapped[list[FieldEvidence]] = relationship(
        back_populates="dataset_record",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    review_history: Mapped[list[DatasetFieldReviewRevision]] = relationship(
        back_populates="dataset_record",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FieldEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "field_evidence"
    __table_args__ = (
        CheckConstraint(
            "element_id IS NOT NULL OR chunk_id IS NOT NULL",
            name="source_reference_required",
        ),
    )

    dataset_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    element_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_elements.id", ondelete="RESTRICT"), index=True
    )
    chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"), index=True
    )
    supporting_text: Mapped[str] = mapped_column(Text, nullable=False)
    original_supporting_text: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model_confidence_label: Mapped[str | None] = mapped_column(String(50))
    review_status: Mapped[FieldReviewStatus] = mapped_column(
        enum_type(FieldReviewStatus, "field_review_status"),
        default=FieldReviewStatus.UNREVIEWED,
        nullable=False,
        index=True,
    )
    reviewer_note: Mapped[str | None] = mapped_column(Text)

    dataset_record: Mapped[DatasetRecord] = relationship(back_populates="evidence")


class DatasetFieldReviewRevision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "dataset_field_review_revisions"

    dataset_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    previous_value: Mapped[Any | None] = mapped_column(JSON)
    new_value: Mapped[Any | None] = mapped_column(JSON)
    previous_state: Mapped[str | None] = mapped_column(String(50))
    new_state: Mapped[str] = mapped_column(String(50), nullable=False)
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    reviewer_label: Mapped[str | None] = mapped_column(String(255))
    evidence_backed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    dataset_record: Mapped[DatasetRecord] = relationship(back_populates="review_history")


class Benchmark(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "benchmarks"
    __table_args__ = (UniqueConstraint("name"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    versions: Mapped[list[BenchmarkVersion]] = relationship(
        back_populates="benchmark",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class BenchmarkVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "benchmark_versions"
    __table_args__ = (UniqueConstraint("benchmark_id", "version"),)

    benchmark_id: Mapped[UUID] = mapped_column(
        ForeignKey("benchmarks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    corpus_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("corpus_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BenchmarkVersionStatus] = mapped_column(
        enum_type(BenchmarkVersionStatus, "benchmark_version_status"),
        default=BenchmarkVersionStatus.DRAFT,
        nullable=False,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    benchmark: Mapped[Benchmark] = relationship(back_populates="versions")
    questions: Mapped[list[BenchmarkQuestion]] = relationship(
        back_populates="benchmark_version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class BenchmarkQuestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "benchmark_questions"

    benchmark_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("benchmark_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[BenchmarkQuestionType] = mapped_column(
        enum_type(BenchmarkQuestionType, "benchmark_question_type"),
        nullable=False,
        index=True,
    )
    difficulty: Mapped[BenchmarkDifficulty] = mapped_column(
        enum_type(BenchmarkDifficulty, "benchmark_difficulty"),
        nullable=False,
        index=True,
    )
    answerable: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    expected_answerability: Mapped[Answerability] = mapped_column(
        enum_type(Answerability, "answerability"),
        nullable=False,
        default=Answerability.ANSWERABLE,
        index=True,
    )
    reference_answer: Mapped[str | None] = mapped_column(Text)
    answer_criteria: Mapped[str | None] = mapped_column(Text)
    unanswerable_explanation: Mapped[str | None] = mapped_column(Text)
    required_document_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_chunk_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    annotation_notes: Mapped[str | None] = mapped_column(Text)
    annotation_status: Mapped[BenchmarkAnnotationStatus] = mapped_column(
        enum_type(BenchmarkAnnotationStatus, "benchmark_annotation_status"),
        default=BenchmarkAnnotationStatus.DRAFT,
        nullable=False,
        index=True,
    )
    leakage_warning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    leakage_score: Mapped[float | None] = mapped_column(Float)
    model_suggestion: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    benchmark_version: Mapped[BenchmarkVersion] = relationship(back_populates="questions")
    evidence_sets: Mapped[list[BenchmarkEvidenceSet]] = relationship(
        back_populates="benchmark_question",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class BenchmarkEvidenceSet(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "benchmark_evidence_sets"
    __table_args__ = (UniqueConstraint("benchmark_question_id", "set_number"),)

    benchmark_question_id: Mapped[UUID] = mapped_column(
        ForeignKey("benchmark_questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    set_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    benchmark_question: Mapped[BenchmarkQuestion] = relationship(back_populates="evidence_sets")
    references: Mapped[list[BenchmarkEvidenceReference]] = relationship(
        back_populates="evidence_set",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class BenchmarkEvidenceReference(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "benchmark_evidence_references"
    __table_args__ = (
        UniqueConstraint(
            "evidence_set_id",
            "document_id",
            "element_id",
            "chunk_id",
            "selected_text",
        ),
        CheckConstraint(
            "element_id IS NOT NULL OR chunk_id IS NOT NULL",
            name="source_reference_required",
        ),
    )

    evidence_set_id: Mapped[UUID] = mapped_column(
        ForeignKey("benchmark_evidence_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    element_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_elements.id", ondelete="RESTRICT"), index=True
    )
    chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"), index=True
    )
    selected_text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int | None] = mapped_column(Integer)
    end_offset: Mapped[int | None] = mapped_column(Integer)
    evidence_role: Mapped[str] = mapped_column(
        String(50), default="required", nullable=False, index=True
    )

    evidence_set: Mapped[BenchmarkEvidenceSet] = relationship(back_populates="references")


class RetrievalResultRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "retrieval_results"
    __table_args__ = (UniqueConstraint("query_run_id", "chunk_id", "retriever_type"),)

    query_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    retriever_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    original_rank: Mapped[int | None] = mapped_column(Integer)
    original_score: Mapped[float | None] = mapped_column(Float)
    normalized_score: Mapped[float | None] = mapped_column(Float)
    fused_rank: Mapped[int | None] = mapped_column(Integer)
    fusion_score: Mapped[float | None] = mapped_column(Float)
    reranked_rank: Mapped[int | None] = mapped_column(Integer)
    reranker_score: Mapped[float | None] = mapped_column(Float)
    selected_for_context: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timing_ms: Mapped[int | None] = mapped_column(Integer)
    result_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    query_run: Mapped[QueryRun] = relationship(back_populates="retrieval_results")


class ContextSource(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "context_sources"
    __table_args__ = (UniqueConstraint("query_run_id", "chunk_id"),)

    query_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    citation_id: Mapped[str | None] = mapped_column(String(30))
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    exclusion_reason: Mapped[str | None] = mapped_column(String(50), index=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)

    query_run: Mapped[QueryRun] = relationship(back_populates="context_sources")


class GeneratedClaim(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "generated_claims"
    __table_args__ = (UniqueConstraint("query_run_id", "sequence_number"),)

    query_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(50), default="factual", nullable=False)
    citation_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    support_status: Mapped[ClaimSupportStatus] = mapped_column(
        enum_type(ClaimSupportStatus, "claim_support_status"),
        default=ClaimSupportStatus.NOT_EVALUATED,
        nullable=False,
        index=True,
    )
    verification_method: Mapped[str] = mapped_column(
        String(100), default="citation-existence-v1", nullable=False
    )
    verification_score: Mapped[float | None] = mapped_column(Float)

    query_run: Mapped[QueryRun] = relationship(back_populates="claims")
    citations: Mapped[list[Citation]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )


class Citation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "citations"
    __table_args__ = (UniqueConstraint("claim_id", "citation_id"),)

    query_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[UUID] = mapped_column(
        ForeignKey("generated_claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    citation_id: Mapped[str] = mapped_column(String(30), nullable=False)
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    referenced_text: Mapped[str] = mapped_column(Text, nullable=False)
    entailment_status: Mapped[str] = mapped_column(
        String(50), default="not_evaluated", nullable=False
    )
    entailment_score: Mapped[float | None] = mapped_column(Float)

    claim: Mapped[GeneratedClaim] = relationship(back_populates="citations")


class EvaluationResult(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint(
            "query_run_id",
            "metric_name",
            "metric_version",
            "evaluation_method",
            "input_hash",
        ),
    )

    query_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    metric_scope: Mapped[EvaluationMetricScope] = mapped_column(
        enum_type(EvaluationMetricScope, "evaluation_metric_scope"),
        nullable=False,
        index=True,
    )
    metric_value: Mapped[float | None] = mapped_column(Float)
    metric_version: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    evaluation_method: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class CitationVerification(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "citation_verifications"
    __table_args__ = (UniqueConstraint("citation_id", "method", "verifier_version"),)

    citation_id: Mapped[UUID] = mapped_column(
        ForeignKey("citations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    verifier_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(255))
    automatic_label: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    automatic_score: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    human_label: Mapped[str | None] = mapped_column(String(50), index=True)
    human_score: Mapped[float | None] = mapped_column(Float)
    human_note: Mapped[str | None] = mapped_column(Text)
    human_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FailureAttribution(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "failure_attributions"

    query_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    pipeline_stage: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    automatic_label: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    attribution_rule: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    taxonomy_version: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    rules_version: Mapped[str] = mapped_column(String(100), nullable=False)
    human_override_label: Mapped[str | None] = mapped_column(String(100), index=True)
    human_override_note: Mapped[str | None] = mapped_column(Text)
    human_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_failure_attributions_run_version_input_sequence",
    FailureAttribution.query_run_id,
    FailureAttribution.taxonomy_version,
    FailureAttribution.rules_version,
    FailureAttribution.input_hash,
    FailureAttribution.sequence_number,
    unique=True,
)


Index("ix_index_entries_index_chunk", IndexEntry.index_id, IndexEntry.chunk_id)
Index("ix_chunks_version_document", Chunk.corpus_version_id, Chunk.document_id)
Index(
    "ix_retrieval_results_run_type_rank",
    RetrievalResultRecord.query_run_id,
    RetrievalResultRecord.retriever_type,
    RetrievalResultRecord.original_rank,
)
Index("ix_context_sources_run_selected", ContextSource.query_run_id, ContextSource.selected)
