from functools import lru_cache

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.schemas import ReviewRequest, ScreeningResponse
from app.repositories.screening_repository import ScreeningRepository
from app.services.screening_service import ScreeningService

router = APIRouter(prefix="/screenings", tags=["screenings"])


@lru_cache
def get_service() -> ScreeningService:
    # One shared service; FastAPI injects it into each route via Depends
    return ScreeningService(ScreeningRepository())


@router.post("", response_model=ScreeningResponse)
def start_screening(
    resume: UploadFile = File(..., description="Resume PDF"),
    jd_text: str | None = Form(None),
    jd_file: UploadFile | None = File(None),
    service: ScreeningService = Depends(get_service),
):
    """Upload a resume and JD; runs the workflow until it pauses for human review."""
    return service.start(
        resume_filename=resume.filename or "",
        resume_bytes=resume.file.read(),
        jd_text=jd_text,
        jd_filename=jd_file.filename if jd_file else None,
        jd_bytes=jd_file.file.read() if jd_file else None,
    )


@router.get("/{thread_id}", response_model=ScreeningResponse)
def get_screening(thread_id: str, service: ScreeningService = Depends(get_service)):
    return service.get(thread_id)


@router.post("/{thread_id}/review", response_model=ScreeningResponse)
def review_screening(
    thread_id: str, review: ReviewRequest, service: ScreeningService = Depends(get_service)
):
    """Approve the questions or send them back with feedback."""
    return service.review(thread_id, review.action, review.feedback)
