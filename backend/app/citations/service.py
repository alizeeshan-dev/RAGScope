from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    Chunk,
    Citation,
    ClaimSupportStatus,
    ContextSource,
    GeneratedClaim,
    QueryRun,
)
from backend.app.generation.schemas import GroundedAnswer

from .errors import CitationResolutionError, InventedCitationError

_CITATION_ID_RE = re.compile(r"\bS[1-9]\d*\b")


@dataclass(frozen=True, slots=True)
class ResolvedContextSource:
    citation_id: str
    chunk_id: UUID
    document_id: UUID
    text: str
    page_number: int | None
    sequence_number: int

    def prompt_record(self) -> dict[str, object]:
        return {
            "citation_id": self.citation_id,
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "page_number": self.page_number,
            "text": self.text,
        }


class CitationPersistenceService:
    """Validate Level-1 existence and persist exact source resolution.

    This service deliberately does not perform relevance or entailment evaluation.
    Citation presence remains recorded as ``not_evaluated`` support.
    """

    verification_method = "citation-existence-v1"

    def __init__(self, session: Session) -> None:
        self.session = session

    def context_sources(self, query_run: QueryRun) -> list[ResolvedContextSource]:
        rows = self.session.execute(
            select(ContextSource, Chunk)
            .join(Chunk, Chunk.id == ContextSource.chunk_id)
            .where(
                ContextSource.query_run_id == query_run.id,
                ContextSource.selected.is_(True),
            )
            .order_by(ContextSource.sequence_number, ContextSource.id)
        ).all()
        resolved: list[ResolvedContextSource] = []
        seen: set[str] = set()
        for source, chunk in rows:
            if not source.citation_id:
                raise CitationResolutionError(
                    "every selected context source needs a stable citation ID"
                )
            if (
                len(source.citation_id) > 30
                or _CITATION_ID_RE.fullmatch(source.citation_id) is None
            ):
                raise CitationResolutionError(
                    f"invalid context citation ID: {source.citation_id}"
                )
            if source.citation_id in seen:
                raise CitationResolutionError(
                    f"duplicate context citation ID: {source.citation_id}"
                )
            if (
                source.document_id != chunk.document_id
                or chunk.corpus_version_id != query_run.corpus_version_id
            ):
                raise CitationResolutionError(
                    f"context source {source.citation_id} does not resolve within the query corpus"
                )
            seen.add(source.citation_id)
            resolved.append(
                ResolvedContextSource(
                    citation_id=source.citation_id,
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    page_number=source.page_start,
                    sequence_number=source.sequence_number,
                )
            )
        return resolved

    def validate_ids(
        self, answer: GroundedAnswer, sources: list[ResolvedContextSource]
    ) -> None:
        allowed = {source.citation_id for source in sources}
        used = {
            citation_id
            for claim in answer.claims
            for citation_id in claim.citations
        }
        used.update(_CITATION_ID_RE.findall(answer.answer))
        invented = sorted(used - allowed)
        if invented:
            raise InventedCitationError(
                f"generated citation IDs were not supplied in context: {invented}"
            )

    def replace_claims_and_citations(
        self,
        query_run: QueryRun,
        answer: GroundedAnswer,
        sources: list[ResolvedContextSource],
    ) -> list[GeneratedClaim]:
        self.validate_ids(answer, sources)
        source_by_id = {source.citation_id: source for source in sources}
        existing = self.session.scalars(
            select(GeneratedClaim).where(GeneratedClaim.query_run_id == query_run.id)
        ).all()
        for claim in existing:
            self.session.delete(claim)
        self.session.flush()

        stored_claims: list[GeneratedClaim] = []
        for sequence, parsed_claim in enumerate(answer.claims, start=1):
            claim = GeneratedClaim(
                query_run_id=query_run.id,
                sequence_number=sequence,
                claim_text=parsed_claim.text,
                claim_type=parsed_claim.claim_type,
                citation_ids=list(parsed_claim.citations),
                support_status=ClaimSupportStatus.NOT_EVALUATED,
                verification_method=self.verification_method,
                verification_score=None,
            )
            self.session.add(claim)
            self.session.flush()
            for citation_id in parsed_claim.citations:
                source = source_by_id[citation_id]
                self.session.add(
                    Citation(
                        query_run_id=query_run.id,
                        claim_id=claim.id,
                        citation_id=citation_id,
                        chunk_id=source.chunk_id,
                        document_id=source.document_id,
                        page_number=source.page_number,
                        referenced_text=source.text,
                        entailment_status="not_evaluated",
                        entailment_score=None,
                    )
                )
            stored_claims.append(claim)
        self.session.flush()
        return stored_claims
