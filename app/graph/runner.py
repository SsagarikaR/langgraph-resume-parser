from collections.abc import Iterator

from langchain_core.callbacks import BaseCallbackHandler

from app.graph.graph import get_graph
from app.graph.state import config_for


class WorkflowRunner:
    """Executes the compiled graph. Storage (state, uploads) stays in the repository; this only runs it."""

    def stream(
        self,
        graph_input,
        thread_id: str,
        interrupt_after: list[str] | None = None,
        callbacks: list[BaseCallbackHandler] | None = None,
    ) -> Iterator[tuple[str, str]]:
        """Run (or resume) the workflow until it finishes or pauses, yielding progress as it goes.

        Yields (event, node) where event is "started", "finished", or "interrupted" for a node
        that paused for human input. Node errors are raised, as with invoke. `callbacks` reach
        every LLM call the nodes make (e.g. to count tokens).
        """
        config = config_for(thread_id)
        if callbacks:
            config["callbacks"] = callbacks
        tasks = get_graph().stream(graph_input, config, stream_mode="tasks", interrupt_after=interrupt_after or None)
        for task in tasks:
            if "result" not in task:  # task start events carry the input, end events the result
                yield "started", task["name"]
            else:
                yield ("interrupted" if task["interrupts"] else "finished"), task["name"]
