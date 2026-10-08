"""Command-line runner for the screening workflow.

    python -m app.main start <thread_id> <resume.pdf> <jd.txt>
    python -m app.main review <thread_id>
"""
import argparse
import json
from pathlib import Path

from langgraph.types import Command

from app.core.logging import setup_logging
from app.graph.graph import get_graph
from app.repositories.screening_repository import config_for


def show_result(result: dict):
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print("\n⏸  Paused for human review:\n")
        print(json.dumps(payload, indent=2))
        print("\nResume with:  python -m app.main review <thread_id>")
    else:
        print(f"\n✅ Done. Path taken: {result['steps']}")
        print(f"Report saved to: {result['report_path']}")


def start(thread_id: str, resume_path: str, jd_path: str):
    result = get_graph().invoke(
        {"resume_path": resume_path, "job_description": Path(jd_path).read_text()},
        config_for(thread_id),
    )
    show_result(result)


def review(thread_id: str):
    graph = get_graph()
    config = config_for(thread_id)
    snapshot = graph.get_state(config)
    if not snapshot.next:
        print("This screening is already finished (or doesn't exist).")
        return

    pending = [i.value for task in snapshot.tasks for i in task.interrupts]
    print(json.dumps(pending[0], indent=2))

    choice = input("\n[a]pprove or [r]evise? ").strip().lower()
    if choice == "a":
        decision = {"action": "approve"}
    else:
        decision = {"action": "revise", "feedback": input("Feedback: ")}

    result = graph.invoke(Command(resume=decision), config)
    show_result(result)


def main():
    parser = argparse.ArgumentParser(description="Resume screening workflow")
    commands = parser.add_subparsers(dest="command", required=True)

    start_cmd = commands.add_parser("start", help="Start a new screening")
    start_cmd.add_argument("thread_id")
    start_cmd.add_argument("resume_path", help="Resume PDF")
    start_cmd.add_argument("jd_path", help="Job description text file")

    review_cmd = commands.add_parser("review", help="Review a screening paused for approval")
    review_cmd.add_argument("thread_id")

    args = parser.parse_args()
    setup_logging()
    if args.command == "start":
        start(args.thread_id, args.resume_path, args.jd_path)
    else:
        review(args.thread_id)


if __name__ == "__main__":
    main()
