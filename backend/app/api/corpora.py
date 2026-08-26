from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.core.errors import DomainError
from backend.app.corpora.schemas import (
    CorpusCreate,
    CorpusDetail,
    CorpusRead,
    CorpusVersionCreate,
    CorpusVersionRead,
)
from backend.app.corpora.service import freeze_version, get_corpus_or_error, get_version_or_error
from backend.app.db.models import Corpus, CorpusVersion
from backend.app.db.session import get_db

router = APIRouter(tags=["corpora"])


@router.post("/corpora", response_model=CorpusRead, status_code=status.HTTP_201_CREATED)
def create_corpus(payload: CorpusCreate, session: Annotated[Session, Depends(get_db)]) -> Corpus:
    corpus = Corpus(**payload.model_dump())
    session.add(corpus)
    session.commit()
    session.refresh(corpus)
    return corpus


@router.get("/corpora", response_model=list[CorpusDetail])
def list_corpora(session: Annotated[Session, Depends(get_db)]) -> list[Corpus]:
    return list(
        session.scalars(
            select(Corpus)
            .options(selectinload(Corpus.versions))
            .order_by(Corpus.created_at, Corpus.id)
        ).unique()
    )


@router.get("/corpora/{corpus_id}", response_model=CorpusDetail)
def get_corpus(corpus_id: UUID, session: Annotated[Session, Depends(get_db)]) -> Corpus:
    corpus = session.scalar(
        select(Corpus).options(selectinload(Corpus.versions)).where(Corpus.id == corpus_id)
    )
    if corpus is None:
        raise DomainError(
            "CORPUS_NOT_FOUND", "The requested corpus does not exist.", status_code=404
        )
    return corpus


@router.post(
    "/corpora/{corpus_id}/versions",
    response_model=CorpusVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    corpus_id: UUID,
    payload: CorpusVersionCreate,
    session: Annotated[Session, Depends(get_db)],
) -> CorpusVersion:
    get_corpus_or_error(session, corpus_id)
    version = CorpusVersion(corpus_id=corpus_id, **payload.model_dump())
    session.add(version)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DomainError(
            "CORPUS_VERSION_LABEL_CONFLICT",
            "This corpus already has a version with that label.",
        ) from exc
    session.refresh(version)
    return version


@router.get("/corpus-versions/{version_id}", response_model=CorpusVersionRead)
def get_version(
    version_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> CorpusVersion:
    return get_version_or_error(session, version_id)


@router.post("/corpus-versions/{version_id}/freeze", response_model=CorpusVersionRead)
def freeze(version_id: UUID, session: Annotated[Session, Depends(get_db)]) -> CorpusVersion:
    version = get_version_or_error(session, version_id)
    freeze_version(session, version)
    session.commit()
    session.refresh(version)
    return version
