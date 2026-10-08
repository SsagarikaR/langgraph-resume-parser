from pathlib import Path

from langgraph.types import StateSnapshot

from app.core.config import get_settings
from app.graph.graph import get_graph


def config_for(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


class ScreeningRepository:
    """Data access for screenings.

    Workflow state lives in the LangGraph checkpointer (checkpoints.db);
    uploaded files live on disk. Nothing else in the app touches either directly.
    """

    def __init__(self, uploads_dir: Path | None = None):
        self.uploads_dir = uploads_dir or get_settings().uploads_dir

    def run(self, graph_input, thread_id: str, interrupt_after: list[str] | None = None) -> None:
        """Run (or resume) the workflow for a thread until it finishes or pauses."""
        get_graph().invoke(graph_input, config_for(thread_id), interrupt_after=interrupt_after or None)

    def get_state(self, thread_id: str) -> StateSnapshot:
        return get_graph().get_state(config_for(thread_id))

    def update_state(self, thread_id: str, values: dict, as_node: str) -> None:
        """Write values into a paused thread as if `as_node` had produced them."""
        get_graph().update_state(config_for(thread_id), values, as_node=as_node)

    def save_upload(self, thread_id: str, kind: str, data: bytes, suffix: str) -> Path:
        """Save an uploaded file as data/uploads/<thread_id>_<kind><suffix>."""
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        path = self.uploads_dir / f"{thread_id}_{kind}{suffix}"
        path.write_bytes(data)
        return path
