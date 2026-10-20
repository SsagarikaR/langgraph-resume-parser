from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile

from app.api.rate_limit import api_rate_limit, workflow_rate_limit
from app.api.schemas import ContinueRequest, ReviewRequest, ScreeningResponse
from app.core.config import get_settings
from app.repositories.screening_repository import ScreeningRepository
from app.services.exceptions import InvalidScreeningInputError
from app.services.screening_service import ScreeningService

router = APIRouter(prefix="/screenings", tags=["screenings"], dependencies=[Depends(api_rate_limit)])

# New ids are uuid4 hex; older CLI runs used names the user picked, so allow a safe superset
ThreadId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


@lru_cache
def get_service() -> ScreeningService:
    # One shared service; FastAPI injects it into each route via Depends
    return ScreeningService(ScreeningRepository())


ServiceDep = Annotated[ScreeningService, Depends(get_service)]


def read_upload(upload: UploadFile) -> bytes:
    """Read an upload, refusing anything over the configured size limit."""
    limit = get_settings().max_upload_bytes
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise InvalidScreeningInputError(f"'{upload.filename}' is larger than {get_settings().max_upload_mb} MB")
    return data


@router.post("", response_model=ScreeningResponse, dependencies=[Depends(workflow_rate_limit)])
def start_screening(
    resume: Annotated[UploadFile, File(description="Resume PDF")],
    service: ServiceDep,
    jd_text: Annotated[str | None, Form()] = None,
    jd_file: Annotated[UploadFile | None, File()] = None,
    breakpoints: Annotated[list[str] | None, Form(description="Nodes to pause after, e.g. match_requirements")] = None,
):
    """Upload a resume and JD; runs the workflow until it pauses for review or at a breakpoint."""
    return service.start(
        resume_filename=resume.filename or "",
        resume_bytes=read_upload(resume),
        jd_text=jd_text,
        jd_filename=jd_file.filename if jd_file else None,
        jd_bytes=read_upload(jd_file) if jd_file else None,
        breakpoints=breakpoints,
    )


@router.get("/{thread_id}", response_model=ScreeningResponse)
def get_screening(thread_id: ThreadId, service: ServiceDep):
    return service.get(thread_id)


@router.post("/{thread_id}/review", response_model=ScreeningResponse, dependencies=[Depends(workflow_rate_limit)])
def review_screening(thread_id: ThreadId, review: ReviewRequest, service: ServiceDep):
    """Approve the questions or send them back with feedback."""
    return service.review(thread_id, review.action, review.feedback)


@router.post("/{thread_id}/continue", response_model=ScreeningResponse, dependencies=[Depends(workflow_rate_limit)])
def continue_screening(thread_id: ThreadId, body: ContinueRequest, service: ServiceDep):
    """Resume a screening paused at a breakpoint, optionally correcting the skill match."""
    return service.continue_run(thread_id, body.matching_skills)
