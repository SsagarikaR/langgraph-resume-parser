import json
import sys
from pathlib import Path
from langgraph.types import Command
from app.graph.graph import graph


def config_for(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


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
    result = graph.invoke(
        {"resume_path": resume_path, "job_description": Path(jd_path).read_text()},
        config_for(thread_id),
    )
    show_result(result)


def review(thread_id: str):
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


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "start":
        start(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "review":
        review(sys.argv[2])