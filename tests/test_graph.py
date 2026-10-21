from dataclasses import dataclass

from app.graph.graph import build_graph
from app.repositories import screening_repository
from app.repositories.screening_repository import ScreeningRepository


@dataclass
class Untrusted:
    command: str


def test_checkpoints_do_not_rebuild_arbitrary_classes(tmp_path):
    # A tampered checkpoints.db could name any class; strict mode loads it as plain data
    serde = build_graph(tmp_path / "checkpoints.db").checkpointer.serde
    loaded = serde.loads_typed(serde.dumps_typed({"value": Untrusted("rm -rf /")}))
    assert loaded["value"] == {"command": "rm -rf /"}


def test_repository_stream_reports_step_progress(monkeypatch):
    class FakeGraph:  # task events as LangGraph's stream_mode="tasks" sends them
        def stream(self, *args, **kwargs):
            yield {"id": "1", "name": "parse_resume", "input": {}, "triggers": []}
            yield {"id": "1", "name": "parse_resume", "error": None, "result": {}, "interrupts": []}
            yield {"id": "2", "name": "human_review", "input": {}, "triggers": []}
            yield {"id": "2", "name": "human_review", "error": None, "result": {}, "interrupts": [{"value": {}}]}

    monkeypatch.setattr(screening_repository, "get_graph", FakeGraph)
    assert list(ScreeningRepository().stream({}, "t1")) == [
        ("started", "parse_resume"),
        ("finished", "parse_resume"),
        ("started", "human_review"),
        ("interrupted", "human_review"),  # paused for the reviewer, not done
    ]
