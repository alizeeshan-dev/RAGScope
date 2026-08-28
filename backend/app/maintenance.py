"""Explicit idempotent maintenance commands for stored research data."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from sqlalchemy import select

from backend.app.artifacts.service import LocalArtifactStore
from backend.app.core.config import get_settings
from backend.app.db.models import Artifact, QueryRun
from backend.app.db.session import SessionLocal
from backend.app.query_runtime.service import QueryOrchestrator
from backend.app.tracing.redaction import configured_sensitive_values


def backfill_trace_exports() -> int:
    settings = get_settings()
    store = LocalArtifactStore(settings.artifact_root)
    created = 0
    with SessionLocal() as session:
        existing = set(
            session.scalars(
                select(Artifact.query_run_id).where(
                    Artifact.artifact_type == "observable-trace.json",
                    Artifact.query_run_id.is_not(None),
                )
            )
        )
        runs = session.scalars(select(QueryRun).order_by(QueryRun.created_at, QueryRun.id))
        orchestrator = QueryOrchestrator(
            session, settings=settings, artifact_store=store
        )
        for run in runs:
            if run.id in existing:
                continue
            orchestrator.persist_trace_export_safely(run)
            created += 1
    return created


def scan_research_artifacts() -> tuple[str, ...]:
    """Return safe artifact IDs containing a configured sensitive value."""

    settings = get_settings()
    store = LocalArtifactStore(settings.artifact_root)
    secrets = tuple(value.encode("utf-8") for value in configured_sensitive_values() if value)
    if not secrets:
        return ()
    findings: list[str] = []
    with SessionLocal() as session:
        artifacts = session.scalars(select(Artifact).order_by(Artifact.id))
        for artifact in artifacts:
            content = store.read_bytes(artifact.storage_key)
            if any(secret in content for secret in secrets):
                findings.append(str(artifact.id))
    return tuple(findings)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAGScope maintenance commands")
    parser.add_argument(
        "command", choices=("backfill-trace-exports", "scan-research-artifacts")
    )
    args = parser.parse_args(argv)
    if args.command == "backfill-trace-exports":
        count = backfill_trace_exports()
        print(f"created_trace_artifacts={count}")
        return 0
    findings = scan_research_artifacts()
    print(f"secret_findings={len(findings)}")
    for artifact_id in findings:
        print(f"artifact_id={artifact_id}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
