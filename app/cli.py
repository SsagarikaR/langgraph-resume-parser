"""Command-line runner for the screening workflow.

python -m app.cli start <resume.pdf> <jd.txt|jd.pdf>
python -m app.cli review <thread_id>
python -m app.cli continue <thread_id>
python -m app.cli delete <thread_id>
python -m app.cli purge [--days N]

Goes through ScreeningService like the API, so input checks and error handling are the same.
"""

import argparse
import getpass
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.exceptions import ScreeningError
from app.services.screening_service import ScreeningService, create_service


def show(view: dict, service: ScreeningService) -> None:
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
        # The API keeps the report in the screening; the CLI user gets a file
        print(f"Report saved to: {service.export_report(view['thread_id'])}")
    elif view["status"] in ("paused", "stopped"):
        print(f"\nResume with:  python -m app.cli continue {view['thread_id']}")
    elif view["status"] == "failed":
        print(f"\nA step failed (see the log). Retry it with:  python -m app.cli continue {view['thread_id']}")


def start(service: ScreeningService, resume_path: Path, jd_path: Path) -> None:
    view = service.start(
        resume_filename=resume_path.name,
        resume_bytes=resume_path.read_bytes(),
        jd_filename=jd_path.name,
        jd_bytes=jd_path.read_bytes(),
    )
    show(view, service)


def review(service: ScreeningService, thread_id: str) -> None:
    view = service.get(thread_id)
    show(view, service)
    if view["status"] != "awaiting_review":
        return

    if view["revisions_remaining"] == 0:
        print("\nRevisions are turned off (MAX_REVISIONS=0).")
        if input("\n[a]pprove? ").strip().lower() == "a":
            show(service.review(thread_id, "approve", reviewer=getpass.getuser()), service)
        return
    if view["revisions_remaining"] == 1:
        print("\nLast revision: if you revise, the revised questions go into the report without another review.")
    if input("\n[a]pprove or [r]evise? ").strip().lower() == "a":
        action, feedback = "approve", ""
    else:
        action, feedback = "revise", ""
        while not feedback.strip():  # the service needs feedback to revise
            feedback = input("Feedback: ")
    # Recorded in the screening's audit trail as the person who decided
    show(service.review(thread_id, action, feedback, reviewer=getpass.getuser()), service)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Resume screening workflow")
    commands = parser.add_subparsers(dest="command", required=True)

    start_cmd = commands.add_parser("start", help="Start a new screening")
    start_cmd.add_argument("resume_path", type=Path, help="Resume PDF")
    start_cmd.add_argument("jd_path", type=Path, help="Job description, .txt or .pdf")

    review_cmd = commands.add_parser("review", help="Review a screening paused for approval")
    review_cmd.add_argument("thread_id")

    continue_cmd = commands.add_parser("continue", help="Resume a screening paused, stopped part-way or failed")
    continue_cmd.add_argument("thread_id")

    delete_cmd = commands.add_parser("delete", help="Erase a screening and all its stored data")
    delete_cmd.add_argument("thread_id")

    purge_cmd = commands.add_parser("purge", help="Erase screenings idle longer than the retention period")
    purge_cmd.add_argument("--days", type=int, default=None, help="Override RETENTION_DAYS")

    args = parser.parse_args(argv)
    setup_logging()
    service = create_service()
    try:
        if args.command == "start":
            start(service, args.resume_path, args.jd_path)
        elif args.command == "review":
            review(service, args.thread_id)
        elif args.command == "continue":
            show(service.continue_run(args.thread_id), service)
        elif args.command == "delete":
            service.delete(args.thread_id)
            print(f"Deleted screening {args.thread_id}")
        else:
            days = args.days if args.days is not None else get_settings().retention_days
            if days <= 0:
                parser.error("retention is off (RETENTION_DAYS=0); pass --days N")
            print(f"Erased {service.purge_expired(days)} screening(s) idle for over {days} days")
    except OSError as e:  # unreadable or missing input file
        parser.error(str(e))
    except ScreeningError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
