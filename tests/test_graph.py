from dataclasses import dataclass

from app.graph.graph import build_graph


@dataclass
class Untrusted:
    command: str


def test_checkpoints_do_not_rebuild_arbitrary_classes(tmp_path):
    # A tampered checkpoints.db could name any class; strict mode loads it as plain data
    serde = build_graph(tmp_path / "checkpoints.db").checkpointer.serde
    loaded = serde.loads_typed(serde.dumps_typed({"value": Untrusted("rm -rf /")}))
    assert loaded["value"] == {"command": "rm -rf /"}
