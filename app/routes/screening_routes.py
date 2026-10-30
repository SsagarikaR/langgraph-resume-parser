from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from app.middleware.auth import Caller, ReadCaller, require_api_key
from app.schemas.api_schemas import ContinueRequest, ReviewRequest, ScreeningResponse, ScreeningSummary
from app.utils.deps import ServiceDep, Stream, ThreadId, read_upload, respond

# The API key and the rate limits (the stricter workflow ones on every POST) are enforced by
# ScreeningsGuardMiddleware, before the body is read. The key dependency repeats that check,
# documents the header in OpenAPI and, as Caller, gives each route the key's name: screenings
# belong to the key that started them.
router = APIRouter(prefix="/screenings", tags=["screenings"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=ScreeningResponse)
def start_screening(
    resume: Annotated[UploadFile, File(description="Resume PDF")],
    service: ServiceDep,
    caller: Caller,
    jd_text: Annotated[str | None, Form()] = None,
    jd_file: Annotated[UploadFile | None, File()] = None,
    breakpoints: Annotated[list[str] | None, Form(description="Nodes to pause after, e.g. match_requirements")] = None,
    stream: Stream = False,
):
    """Upload a resume and JD; runs the workflow until it pauses for review or at a breakpoint."""
    events = service.start_stream(
        resume_filename=resume.filename or "",
        resume_bytes=read_upload(resume),
        jd_text=jd_text,
        jd_filename=jd_file.filename if jd_file else None,
        jd_bytes=read_upload(jd_file) if jd_file else None,
        breakpoints=breakpoints,
        caller=caller,
    )
    return respond(events, stream)


@router.get("", response_model=list[ScreeningSummary])
def list_screenings(
    service: ServiceDep, caller: ReadCaller, limit: Annotated[int, Query(ge=1, le=200, description="Newest first")] = 50
):
    """The caller's screenings, newest first, so a lost screening id can be found again."""
    return service.list_screenings(caller, limit)


@router.get("/{thread_id}", response_model=ScreeningResponse)
def get_screening(thread_id: ThreadId, service: ServiceDep, caller: ReadCaller):
    return service.get(thread_id, caller)


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_screening(thread_id: ThreadId, service: ServiceDep, caller: Caller) -> None:
    """Erase the screening and everything stored for it: resume text, report and uploads."""
    service.delete(thread_id, caller)


@router.post("/{thread_id}/review", response_model=ScreeningResponse)
def review_screening(
    thread_id: ThreadId, review: ReviewRequest, service: ServiceDep, caller: Caller, stream: Stream = False
):
    """Approve the questions or send them back with feedback. Recorded with who decided."""
    events = service.review_stream(thread_id, review.action, review.feedback, review.reviewer, caller)
    return respond(events, stream)


@router.post("/{thread_id}/continue", response_model=ScreeningResponse)
def continue_screening(
    thread_id: ThreadId, body: ContinueRequest, service: ServiceDep, caller: Caller, stream: Stream = False
):
    """Resume a screening paused at a breakpoint (optionally correcting the skill match), one
    whose run was cut off part-way, or one that failed in a step (the step is run again)."""
    return respond(service.continue_stream(thread_id, body.matching_skills, caller), stream)
