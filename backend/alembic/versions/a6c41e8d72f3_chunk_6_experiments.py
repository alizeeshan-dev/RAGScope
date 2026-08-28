"""chunk 6 experiments and immutable run matrix

Revision ID: a6c41e8d72f3
Revises: f5b30d8a51e2
Create Date: 2026-08-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "a6c41e8d72f3"
down_revision: str | None = "f5b30d8a51e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("research_question", sa.Text(), nullable=False),
        sa.Column("corpus_version_id", sa.Uuid(), nullable=False),
        sa.Column("benchmark_version_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_configuration_ids", sa.JSON(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "frozen",
                "running",
                "paused",
                "completed",
                "completed_with_failures",
                "failed",
                "cancelled",
                name="experiment_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("code_commit", sa.String(length=100), nullable=False),
        sa.Column("stop_on_error", sa.Boolean(), nullable=False),
        sa.Column("retry_policy", sa.JSON(), nullable=False),
        sa.Column("dependency_snapshot", sa.JSON(), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=True),
        sa.Column("cost_estimate", sa.JSON(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("estimated_cost_currency", sa.String(length=3), nullable=True),
        sa.Column("cost_fully_configured", sa.Boolean(), nullable=False),
        sa.Column("current_job_id", sa.Uuid(), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_created_columns(),
        sa.CheckConstraint("repetitions >= 1", name="experiment_repetitions_positive"),
        sa.ForeignKeyConstraint(
            ["corpus_version_id"], ["corpus_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["benchmark_version_id"], ["benchmark_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["current_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "name",
        "corpus_version_id",
        "benchmark_version_id",
        "status",
        "configuration_hash",
        "current_job_id",
        "frozen_at",
    ):
        op.create_index(f"ix_experiments_{column}", "experiments", [column])

    op.create_table(
        "experiment_runs",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("benchmark_question_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_configuration_id", sa.Uuid(), nullable=False),
        sa.Column("repetition_index", sa.Integer(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "planned",
                "running",
                "retryable",
                "succeeded",
                "failed",
                name="experiment_cell_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_failure_code", sa.String(length=100), nullable=True),
        sa.Column("last_failure_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_created_columns(),
        sa.CheckConstraint(
            "repetition_index >= 1", name="experiment_run_repetition_positive"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="experiment_run_attempt_count_nonnegative"
        ),
        sa.CheckConstraint("max_attempts >= 1", name="experiment_run_max_attempts_positive"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["benchmark_question_id"], ["benchmark_questions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_configuration_id"],
            ["pipeline_configurations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint(
            "experiment_id",
            "benchmark_question_id",
            "pipeline_configuration_id",
            "repetition_index",
            name="uq_experiment_run_matrix_cell",
        ),
    )
    for column in (
        "experiment_id",
        "benchmark_question_id",
        "pipeline_configuration_id",
        "status",
        "last_failure_code",
    ):
        op.create_index(f"ix_experiment_runs_{column}", "experiment_runs", [column])

    with op.batch_alter_table("query_runs") as batch_op:
        batch_op.add_column(sa.Column("experiment_run_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("experiment_attempt_number", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_query_runs_experiment_run_id",
            "experiment_runs",
            ["experiment_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "query_run_experiment_attempt_positive",
            "experiment_attempt_number IS NULL OR experiment_attempt_number >= 1",
        )
        batch_op.create_unique_constraint(
            "uq_query_run_experiment_attempt",
            ["experiment_run_id", "experiment_attempt_number"],
        )
        batch_op.create_index("ix_query_runs_experiment_run_id", ["experiment_run_id"])

    op.create_table(
        "experiment_run_attempts",
        sa.Column("experiment_run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "failed",
                "interrupted",
                name="experiment_attempt_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("query_run_id", sa.Uuid(), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_created_columns(),
        sa.CheckConstraint("attempt_number >= 1", name="experiment_attempt_number_positive"),
        sa.ForeignKeyConstraint(
            ["experiment_run_id"], ["experiment_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["query_run_id"], ["query_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_run_id", "attempt_number"),
        sa.UniqueConstraint("query_run_id"),
    )
    for column in ("experiment_run_id", "status", "query_run_id", "failure_code"):
        op.create_index(
            f"ix_experiment_run_attempts_{column}",
            "experiment_run_attempts",
            [column],
        )


def downgrade() -> None:
    op.drop_table("experiment_run_attempts")
    with op.batch_alter_table("query_runs") as batch_op:
        batch_op.drop_index("ix_query_runs_experiment_run_id")
        batch_op.drop_constraint("uq_query_run_experiment_attempt", type_="unique")
        batch_op.drop_constraint("query_run_experiment_attempt_positive", type_="check")
        batch_op.drop_constraint("fk_query_runs_experiment_run_id", type_="foreignkey")
        batch_op.drop_column("experiment_attempt_number")
        batch_op.drop_column("experiment_run_id")
    op.drop_table("experiment_runs")
    op.drop_table("experiments")
