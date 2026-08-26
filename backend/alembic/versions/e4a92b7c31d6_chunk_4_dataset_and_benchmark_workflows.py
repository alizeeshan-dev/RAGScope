"""chunk 4 dataset and benchmark workflows

Revision ID: e4a92b7c31d6
Revises: c3f19a7e42b1
Create Date: 2026-08-25
"""

import typing
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4a92b7c31d6"
down_revision: str | None = "c3f19a7e42b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps(*, updated: bool = False) -> list[sa.Column[typing.Any]]:
    columns = [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        )
    ]
    if updated:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            )
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "dataset_records",
        sa.Column("corpus_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_job_id", sa.Uuid(), nullable=True),
        sa.Column("strategy", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("modalities", sa.JSON(), nullable=True),
        sa.Column("task_types", sa.JSON(), nullable=True),
        sa.Column("instance_count", sa.Integer(), nullable=True),
        sa.Column("participant_count", sa.Integer(), nullable=True),
        sa.Column("annotation_types", sa.JSON(), nullable=True),
        sa.Column("languages", sa.JSON(), nullable=True),
        sa.Column("license", sa.Text(), nullable=True),
        sa.Column("access_url", sa.Text(), nullable=True),
        sa.Column("human_ratings", sa.Text(), nullable=True),
        sa.Column("collection_method", sa.Text(), nullable=True),
        sa.Column("known_limitations", sa.Text(), nullable=True),
        sa.Column(
            "extraction_status",
            sa.Enum(
                "pending",
                "running",
                "valid",
                "invalid",
                "failed",
                name="dataset_extraction_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "review_status",
            sa.Enum(
                "unreviewed",
                "in_review",
                "approved",
                "rejected",
                name="dataset_review_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("original_values", sa.JSON(), nullable=False),
        sa.Column("current_values", sa.JSON(), nullable=False),
        sa.Column("field_states", sa.JSON(), nullable=False),
        sa.Column("extraction_configuration", sa.JSON(), nullable=False),
        sa.Column("raw_response_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("structured_result_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(updated=True),
        sa.ForeignKeyConstraint(["corpus_version_id"], ["corpus_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["extraction_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["raw_response_artifact_id"], ["artifacts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["structured_result_artifact_id"], ["artifacts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "corpus_version_id",
        "source_document_id",
        "extraction_job_id",
        "strategy",
        "name",
        "domain",
        "extraction_status",
        "review_status",
        "raw_response_artifact_id",
        "structured_result_artifact_id",
    ):
        op.create_index(f"ix_dataset_records_{column}", "dataset_records", [column])

    op.create_table(
        "field_evidence",
        sa.Column("dataset_record_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("element_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_id", sa.Uuid(), nullable=True),
        sa.Column("supporting_text", sa.Text(), nullable=False),
        sa.Column("original_supporting_text", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.String(length=100), nullable=False),
        sa.Column("model_confidence_label", sa.String(length=50), nullable=True),
        sa.Column(
            "review_status",
            sa.Enum(
                "unreviewed",
                "accepted",
                "rejected",
                "not_stated",
                "cleared",
                name="field_review_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(updated=True),
        sa.CheckConstraint(
            "element_id IS NOT NULL OR chunk_id IS NOT NULL",
            name=op.f("ck_field_evidence_source_reference_required"),
        ),
        sa.ForeignKeyConstraint(["dataset_record_id"], ["dataset_records.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["source_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["element_id"], ["document_elements.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "dataset_record_id",
        "field_name",
        "document_id",
        "element_id",
        "chunk_id",
        "extraction_method",
        "review_status",
    ):
        op.create_index(f"ix_field_evidence_{column}", "field_evidence", [column])

    op.create_table(
        "dataset_field_review_revisions",
        sa.Column("dataset_record_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("previous_state", sa.String(length=50), nullable=True),
        sa.Column("new_state", sa.String(length=50), nullable=False),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("reviewer_label", sa.String(length=255), nullable=True),
        sa.Column("evidence_backed", sa.Boolean(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["dataset_record_id"], ["dataset_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("dataset_record_id", "field_name", "action"):
        op.create_index(
            f"ix_dataset_field_review_revisions_{column}",
            "dataset_field_review_revisions",
            [column],
        )

    op.create_table(
        "benchmarks",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(updated=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_benchmarks_name", "benchmarks", ["name"])

    op.create_table(
        "benchmark_versions",
        sa.Column("benchmark_id", sa.Uuid(), nullable=False),
        sa.Column("corpus_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "frozen",
                name="benchmark_version_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["benchmark_id"], ["benchmarks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["corpus_version_id"], ["corpus_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("benchmark_id", "version"),
    )
    for column in (
        "benchmark_id",
        "corpus_version_id",
        "status",
        "frozen_at",
    ):
        op.create_index(f"ix_benchmark_versions_{column}", "benchmark_versions", [column])

    op.create_table(
        "benchmark_questions",
        sa.Column("benchmark_version_id", sa.Uuid(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column(
            "question_type",
            sa.Enum(
                "direct_fact_lookup",
                "dataset_discovery",
                "dataset_comparison",
                "multi_document_synthesis",
                "multi_hop_reasoning",
                "table_based",
                "broad_summary",
                "ambiguous",
                "unanswerable",
                "false_premise",
                "contradictory_source",
                "distractor_sensitive",
                name="benchmark_question_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "difficulty",
            sa.Enum(
                "easy",
                "medium",
                "hard",
                name="benchmark_difficulty",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("answerable", sa.Boolean(), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=True),
        sa.Column("answer_criteria", sa.Text(), nullable=True),
        sa.Column("unanswerable_explanation", sa.Text(), nullable=True),
        sa.Column("required_document_ids", sa.JSON(), nullable=False),
        sa.Column("required_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("annotation_notes", sa.Text(), nullable=True),
        sa.Column(
            "annotation_status",
            sa.Enum(
                "draft",
                "in_review",
                "reviewed",
                name="benchmark_annotation_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("leakage_warning", sa.Boolean(), nullable=False),
        sa.Column("leakage_score", sa.Float(), nullable=True),
        sa.Column("model_suggestion", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(updated=True),
        sa.ForeignKeyConstraint(
            ["benchmark_version_id"], ["benchmark_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "benchmark_version_id",
        "question_type",
        "difficulty",
        "answerable",
        "annotation_status",
    ):
        op.create_index(f"ix_benchmark_questions_{column}", "benchmark_questions", [column])

    op.create_table(
        "benchmark_evidence_sets",
        sa.Column("benchmark_question_id", sa.Uuid(), nullable=False),
        sa.Column("set_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["benchmark_question_id"], ["benchmark_questions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("benchmark_question_id", "set_number"),
    )
    op.create_index(
        "ix_benchmark_evidence_sets_benchmark_question_id",
        "benchmark_evidence_sets",
        ["benchmark_question_id"],
    )

    op.create_table(
        "benchmark_evidence_references",
        sa.Column("evidence_set_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("element_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_id", sa.Uuid(), nullable=True),
        sa.Column("selected_text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("evidence_role", sa.String(length=50), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "element_id IS NOT NULL OR chunk_id IS NOT NULL",
            name=op.f("ck_benchmark_evidence_references_source_reference_required"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_set_id"], ["benchmark_evidence_sets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["document_id"], ["source_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["element_id"], ["document_elements.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evidence_set_id",
            "document_id",
            "element_id",
            "chunk_id",
            "selected_text",
        ),
    )
    for column in (
        "evidence_set_id",
        "document_id",
        "element_id",
        "chunk_id",
        "evidence_role",
    ):
        op.create_index(
            f"ix_benchmark_evidence_references_{column}",
            "benchmark_evidence_references",
            [column],
        )

    with op.batch_alter_table("query_runs") as batch_op:
        batch_op.create_foreign_key(
            "fk_query_runs_benchmark_question_id_benchmark_questions",
            "benchmark_questions",
            ["benchmark_question_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("query_runs") as batch_op:
        batch_op.drop_constraint(
            "fk_query_runs_benchmark_question_id_benchmark_questions",
            type_="foreignkey",
        )
    op.drop_table("benchmark_evidence_references")
    op.drop_table("benchmark_evidence_sets")
    op.drop_table("benchmark_questions")
    op.drop_table("benchmark_versions")
    op.drop_table("benchmarks")
    op.drop_table("dataset_field_review_revisions")
    op.drop_table("field_evidence")
    op.drop_table("dataset_records")
