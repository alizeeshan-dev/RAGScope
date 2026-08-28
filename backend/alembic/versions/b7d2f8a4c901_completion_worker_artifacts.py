"""completion worker leases, experiment artifacts, and analysis configuration

Revision ID: b7d2f8a4c901
Revises: a6c41e8d72f3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f8a4c901"
down_revision: str | None = "a6c41e8d72f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("available_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("lease_owner", sa.String(length=255)))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("cancellation_requested_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")
        )
        batch.create_check_constraint("job_max_attempts_positive", "max_attempts >= 1")
        batch.create_index("ix_jobs_available_at", ["available_at"])
        batch.create_index("ix_jobs_lease_owner", ["lease_owner"])
        batch.create_index("ix_jobs_lease_expires_at", ["lease_expires_at"])
        batch.create_index(
            "ix_jobs_cancellation_requested_at", ["cancellation_requested_at"]
        )
    op.execute("UPDATE jobs SET available_at = created_at WHERE available_at IS NULL")

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "failed",
                "interrupted",
                "cancelled",
                name="job_attempt_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint("attempt_number >= 1", name="job_attempt_number_positive"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_number"),
    )
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])
    op.create_index("ix_job_attempts_worker_id", "job_attempts", ["worker_id"])
    op.create_index("ix_job_attempts_status", "job_attempts", ["status"])

    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("experiment_id", sa.Uuid()))
        batch.create_foreign_key(
            "fk_artifacts_experiment_id_experiments",
            "experiments",
            ["experiment_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_artifacts_experiment_id", ["experiment_id"])

    with op.batch_alter_table("experiments") as batch:
        batch.add_column(
            sa.Column(
                "analysis_configuration",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("experiments") as batch:
        batch.drop_column("analysis_configuration")
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_index("ix_artifacts_experiment_id")
        batch.drop_constraint(
            "fk_artifacts_experiment_id_experiments", type_="foreignkey"
        )
        batch.drop_column("experiment_id")
    op.drop_index("ix_job_attempts_status", table_name="job_attempts")
    op.drop_index("ix_job_attempts_worker_id", table_name="job_attempts")
    op.drop_index("ix_job_attempts_job_id", table_name="job_attempts")
    op.drop_table("job_attempts")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_cancellation_requested_at")
        batch.drop_index("ix_jobs_lease_expires_at")
        batch.drop_index("ix_jobs_lease_owner")
        batch.drop_index("ix_jobs_available_at")
        batch.drop_constraint("job_max_attempts_positive", type_="check")
        batch.drop_column("max_attempts")
        batch.drop_column("cancellation_requested_at")
        batch.drop_column("heartbeat_at")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
        batch.drop_column("available_at")
