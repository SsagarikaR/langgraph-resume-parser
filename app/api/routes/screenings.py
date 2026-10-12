from functools import lru_cache

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.schemas import ContinueRequest, ReviewRequest, ScreeningResponse
from app.core.config import get_settings
from app.repositories.screening_repository import ScreeningRepository
from app.services.exceptions import InvalidScreeningInputError
from app.services.screening_service import ScreeningService

router = APIRouter(prefix="/screenings", tags=["screenings"])


@lru_cache
def get_service() -> ScreeningService:
    # One shared service; FastAPI injects it into each route via Depends
    return ScreeningService(ScreeningRepository())


def read_upload(upload: UploadFile) -> bytes:
    """Read an upload, refusing anything over the configured size limit."""
    limit = get_settings().max_upload_bytes
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise InvalidScreeningInputError(f"'{upload.filename}' is larger than {get_settings().max_upload_mb} MB")
    return data


@router.post("", response_model=ScreeningResponse)
def start_screening(
    resume: UploadFile = File(..., description="Resume PDF"),
    jd_text: str | None = Form(None),
    jd_file: UploadFile | None = File(None),
    breakpoints: list[str] = Form([], description="Nodes to pause after, e.g. match_requirements"),
    service: ScreeningService = Depends(get_service),
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
def get_screening(thread_id: str, service: ScreeningService = Depends(get_service)):
    return service.get(thread_id)


@router.post("/{thread_id}/review", response_model=ScreeningResponse)
def review_screening(thread_id: str, review: ReviewRequest, service: ScreeningService = Depends(get_service)):
    """Approve the questions or send them back with feedback."""
    return service.review(thread_id, review.action, review.feedback)


@router.post("/{thread_id}/continue", response_model=ScreeningResponse)
def continue_screening(thread_id: str, body: ContinueRequest, service: ScreeningService = Depends(get_service)):
    """Resume a screening paused at a breakpoint, optionally correcting the skill match."""
    return service.continue_run(thread_id, body.matching_skills)
