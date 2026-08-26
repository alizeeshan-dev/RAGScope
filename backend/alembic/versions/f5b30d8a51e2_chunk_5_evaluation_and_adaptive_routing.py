"""chunk 5 evaluation and adaptive routing

Revision ID: f5b30d8a51e2
Revises: e4a92b7c31d6
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5b30d8a51e2"
down_revision: str | None = "e4a92b7c31d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


import typing

def _id_and_created_at() -> list[sa.Column[typing.Any]]:
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
    with op.batch_alter_table("benchmark_questions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "expected_answerability",
                sa.Enum(
                    "answerable",
                    "partially_answerable",
                    "unanswerable",
                    name="answerability",
                    native_enum=False,
                ),
                nullable=False,
                server_default="answerable",
            )
        )
        batch_op.create_index(
            "ix_benchmark_questions_expected_answerability",
            ["expected_answerability"],
            unique=False,
        )
    op.execute(
        sa.text(
            "UPDATE benchmark_questions SET expected_answerability = "
            "'unanswerable' WHERE answerable = false"
        )
    )
    op.create_table(
        "router_configurations",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("router_type", sa.String(length=50), nullable=False),
        sa.Column("router_version", sa.String(length=100), nullable=False),
        sa.Column("classifier_version", sa.String(length=100), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        *_id_and_created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version"),
    )
    for column in ("name", "router_type", "configuration_hash", "frozen_at"):
        op.create_index(f"ix_router_configurations_{column}", "router_configurations", [column])

    with op.batch_alter_table("pipeline_configurations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "execution_mode",
                sa.Enum("fixed", "adaptive", name="pipeline_execution_mode", native_enum=False),
                server_default="fixed",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("router_configuration_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("adaptive_configuration", sa.JSON(), server_default="{}", nullable=False)
        )
        batch_op.create_foreign_key(
            "fk_pipeline_configurations_router_configuration_id",
            "router_configurations",
            ["router_configuration_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_pipeline_configurations_execution_mode", ["execution_mode"]
        )
        batch_op.create_index(
            "ix_pipeline_configurations_router_configuration_id",
            ["router_configuration_id"],
        )

    op.create_table(
        "evaluation_results",
        sa.Column("query_run_id", sa.Uuid(), nullable=False),
        sa.Column("metric_name", sa.String(length=255), nullable=False),
        sa.Column(
            "metric_scope",
            sa.Enum(
                "parsing",
                "retrieval",
                "context",
                "generation",
                "citation",
                "cost",
                "overall",
                name="evaluation_metric_scope",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("metric_value", sa.Float(), nullable=True),
        sa.Column("metric_version", sa.String(length=100), nullable=False),
        sa.Column("evaluation_method", sa.String(length=100), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        *_id_and_created_at(),
        sa.ForeignKeyConstraint(["query_run_id"], ["query_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "query_run_id",
            "metric_name",
            "metric_version",
            "evaluation_method",
            "input_hash",
        ),
    )
    for column in (
        "query_run_id",
        "metric_name",
        "metric_scope",
        "metric_version",
        "evaluation_method",
        "input_hash",
    ):
        op.create_index(f"ix_evaluation_results_{column}", "evaluation_results", [column])

    op.create_table(
        "citation_verifications",
        sa.Column("citation_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(length=100), nullable=False),
        sa.Column("verifier_version", sa.String(length=100), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("automatic_label", sa.String(length=50), nullable=False),
        sa.Column("automatic_score", sa.Float(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("human_label", sa.String(length=50), nullable=True),
        sa.Column("human_score", sa.Float(), nullable=True),
        sa.Column("human_note", sa.Text(), nullable=True),
        sa.Column("human_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        *_id_and_created_at(),
        sa.ForeignKeyConstraint(["citation_id"], ["citations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("citation_id", "method", "verifier_version"),
    )
    for column in ("citation_id", "method", "automatic_label", "human_label"):
        op.create_index(
            f"ix_citation_verifications_{column}", "citation_verifications", [column]
        )

    op.create_table(
        "failure_attributions",
        sa.Column("query_run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("pipeline_stage", sa.String(length=50), nullable=False),
        sa.Column("automatic_label", sa.String(length=100), nullable=False),
        sa.Column("attribution_rule", sa.String(length=255), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=100), nullable=False),
        sa.Column("rules_version", sa.String(length=100), nullable=False),
        sa.Column("human_override_label", sa.String(length=100), nullable=True),
        sa.Column("human_override_note", sa.Text(), nullable=True),
        sa.Column("human_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        *_id_and_created_at(),
        sa.ForeignKeyConstraint(["query_run_id"], ["query_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "query_run_id",
        "is_primary",
        "pipeline_stage",
        "automatic_label",
        "taxonomy_version",
        "input_hash",
        "human_override_label",
    ):
        op.create_index(
            f"ix_failure_attributions_{column}", "failure_attributions", [column]
        )
    op.create_index(
        "ix_failure_attributions_run_version_input_sequence",
        "failure_attributions",
        [
            "query_run_id",
            "taxonomy_version",
            "rules_version",
            "input_hash",
            "sequence_number",
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("failure_attributions")
    op.drop_table("citation_verifications")
    op.drop_table("evaluation_results")
    with op.batch_alter_table("pipeline_configurations") as batch_op:
        batch_op.drop_index("ix_pipeline_configurations_router_configuration_id")
        batch_op.drop_index("ix_pipeline_configurations_execution_mode")
        batch_op.drop_constraint(
            "fk_pipeline_configurations_router_configuration_id", type_="foreignkey"
        )
        batch_op.drop_column("adaptive_configuration")
        batch_op.drop_column("router_configuration_id")
        batch_op.drop_column("execution_mode")
    op.drop_table("router_configurations")
    with op.batch_alter_table("benchmark_questions") as batch_op:
        batch_op.drop_index("ix_benchmark_questions_expected_answerability")
        batch_op.drop_column("expected_answerability")
