"""Command-line runner for the screening workflow.

python -m app.cli start <resume.pdf> <jd.txt|jd.pdf>
python -m app.cli review <thread_id>

Goes through ScreeningService like the API, so input checks and error handling are the same.
"""

import argparse
import sys
from pathlib import Path

from app.core.logging import setup_logging
from app.repositories.screening_repository import ScreeningRepository
from app.services.exceptions import ScreeningError
from app.services.screening_service import ScreeningService


def show(view: dict) -> None:
    print(f"\nScreening {view['thread_id']}: {view['status']}")
    print(f"  {view['candidate'] or 'Candidate'} → {view['job_title'] or 'Role'}")
    print(f"  Skill match: {view['match_percentage']}%")
    print(f"  Matching: {', '.join(view['matching_skills']) or 'none'}")
    print(f"  Missing:  {', '.join(view['missing_skills']) or 'none'}")
    if view["status"] == "awaiting_review":
        for i, q in enumerate(view["interview_questions"], 1):
            print(f"  {i}. [{q['difficulty']}] {q['question']}")
        print(f"\nReview with:  python -m app.cli review {view['thread_id']}")
    elif view["status"] == "completed":
        print(f"Report saved to: {view['report_path']}")


def start(service: ScreeningService, resume_path: Path, jd_path: Path) -> None:
    view = service.start(
        resume_filename=resume_path.name,
        resume_bytes=resume_path.read_bytes(),
        jd_filename=jd_path.name,
        jd_bytes=jd_path.read_bytes(),
    )
    show(view)


def review(service: ScreeningService, thread_id: str) -> None:
    view = service.get(thread_id)
    show(view)
    if view["status"] != "awaiting_review":
        return

    if input("\n[a]pprove or [r]evise? ").strip().lower() == "a":
        action, feedback = "approve", ""
    else:
        action, feedback = "revise", ""
        while not feedback.strip():  # the service needs feedback to revise
            feedback = input("Feedback: ")
    show(service.review(thread_id, action, feedback))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Resume screening workflow")
    commands = parser.add_subparsers(dest="command", required=True)

    start_cmd = commands.add_parser("start", help="Start a new screening")
    start_cmd.add_argument("resume_path", type=Path, help="Resume PDF")
    start_cmd.add_argument("jd_path", type=Path, help="Job description, .txt or .pdf")

    review_cmd = commands.add_parser("review", help="Review a screening paused for approval")
    review_cmd.add_argument("thread_id")

    args = parser.parse_args(argv)
    setup_logging()
    service = ScreeningService(ScreeningRepository())
    try:
        if args.command == "start":
            start(service, args.resume_path, args.jd_path)
        else:
            review(service, args.thread_id)
    except OSError as e:  # unreadable or missing input file
        parser.error(str(e))
    except ScreeningError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
