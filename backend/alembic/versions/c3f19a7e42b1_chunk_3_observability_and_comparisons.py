"""chunk 3 observability and comparisons

Revision ID: c3f19a7e42b1
Revises: d232e4908b27
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f19a7e42b1"
down_revision: str | None = "d232e4908b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_spans",
        sa.Column("query_run_id", sa.Uuid(), nullable=False),
        sa.Column("parent_span_id", sa.Uuid(), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("span_type", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "failed",
                name="trace_span_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("artifact_ids", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parent_span_id"], ["trace_spans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["query_run_id"], ["query_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("query_run_id", "sequence_number"),
    )
    op.create_index("ix_trace_spans_error_code", "trace_spans", ["error_code"])
    op.create_index("ix_trace_spans_parent_span_id", "trace_spans", ["parent_span_id"])
    op.create_index("ix_trace_spans_query_run_id", "trace_spans", ["query_run_id"])
    op.create_index("ix_trace_spans_span_type", "trace_spans", ["span_type"])
    op.create_index("ix_trace_spans_status", "trace_spans", ["status"])

    op.create_table(
        "query_comparisons",
        sa.Column("corpus_version_id", sa.Uuid(), nullable=False),
        sa.Column("original_question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["corpus_version_id"], ["corpus_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_query_comparisons_corpus_version_id",
        "query_comparisons",
        ["corpus_version_id"],
    )
    op.create_index("ix_query_comparisons_status", "query_comparisons", ["status"])

    op.create_table(
        "query_comparison_runs",
        sa.Column("comparison_id", sa.Uuid(), nullable=False),
        sa.Column("query_run_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_configuration_id", sa.Uuid(), nullable=False),
        sa.Column("column_position", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["comparison_id"], ["query_comparisons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["pipeline_configuration_id"],
            ["pipeline_configurations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["query_run_id"], ["query_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("comparison_id", "column_position"),
        sa.UniqueConstraint("comparison_id", "pipeline_configuration_id"),
        sa.UniqueConstraint("comparison_id", "query_run_id"),
    )
    op.create_index(
        "ix_query_comparison_runs_comparison_id",
        "query_comparison_runs",
        ["comparison_id"],
    )
    op.create_index(
        "ix_query_comparison_runs_pipeline_configuration_id",
        "query_comparison_runs",
        ["pipeline_configuration_id"],
    )
    op.create_index(
        "ix_query_comparison_runs_query_run_id",
        "query_comparison_runs",
        ["query_run_id"],
    )

    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.add_column(sa.Column("query_run_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("trace_span_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_artifacts_query_run_id_query_runs",
            "query_runs",
            ["query_run_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_artifacts_trace_span_id_trace_spans",
            "trace_spans",
            ["trace_span_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_artifacts_query_run_id", ["query_run_id"])
        batch_op.create_index("ix_artifacts_trace_span_id", ["trace_span_id"])


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.drop_index("ix_artifacts_trace_span_id")
        batch_op.drop_index("ix_artifacts_query_run_id")
        batch_op.drop_constraint("fk_artifacts_trace_span_id_trace_spans", type_="foreignkey")
        batch_op.drop_constraint("fk_artifacts_query_run_id_query_runs", type_="foreignkey")
        batch_op.drop_column("trace_span_id")
        batch_op.drop_column("query_run_id")

    op.drop_index("ix_query_comparison_runs_query_run_id", table_name="query_comparison_runs")
    op.drop_index(
        "ix_query_comparison_runs_pipeline_configuration_id",
        table_name="query_comparison_runs",
    )
    op.drop_index("ix_query_comparison_runs_comparison_id", table_name="query_comparison_runs")
    op.drop_table("query_comparison_runs")
    op.drop_index("ix_query_comparisons_status", table_name="query_comparisons")
    op.drop_index("ix_query_comparisons_corpus_version_id", table_name="query_comparisons")
    op.drop_table("query_comparisons")
    op.drop_index("ix_trace_spans_status", table_name="trace_spans")
    op.drop_index("ix_trace_spans_span_type", table_name="trace_spans")
    op.drop_index("ix_trace_spans_query_run_id", table_name="trace_spans")
    op.drop_index("ix_trace_spans_parent_span_id", table_name="trace_spans")
    op.drop_index("ix_trace_spans_error_code", table_name="trace_spans")
    op.drop_table("trace_spans")
