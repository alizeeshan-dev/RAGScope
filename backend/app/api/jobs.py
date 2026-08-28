from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Job
from backend.app.db.session import get_db
from backend.app.indexing.api_schemas import JobRead
from backend.app.jobs.service import request_job_cancellation

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: UUID, session: Annotated[Session, Depends(get_db)]) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(
    session: Annotated[Session, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Job]:
    return list(
        session.scalars(
            select(Job).order_by(Job.created_at.desc(), Job.id).offset(offset).limit(limit)
        )
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
def cancel_queued_or_running_job(
    job_id: UUID, session: Annotated[Session, Depends(get_db)]
) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    request_job_cancellation(job)
    session.commit()
    session.refresh(job)
    return job
