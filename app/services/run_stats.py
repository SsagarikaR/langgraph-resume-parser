import logging
import time

from langchain_core.callbacks import UsageMetadataCallbackHandler

from app.core.metrics import metrics

logger = logging.getLogger(__name__)

# Nodes that call the LLM: if a provider response carries no usage metadata (a fallback model,
# a partial/streamed response, a provider outage), these node names are how we still charge the
# token budget something, instead of 0 (which would let such runs bypass DAILY_TOKEN_BUDGET entirely).
LLM_NODES = frozenset({"extract_resume", "analyze_job", "analyze_gaps", "research_guidelines", "generate_questions"})
FALLBACK_TOKENS_PER_LLM_NODE = 2000  # conservative estimate; errs toward charging too much, not too little


class RunStats:
    """What one workflow run cost: wall time, time per node and LLM tokens.

    Pass `usage` as a callback to the run, `record` each progress event, then `finish` once:
    it logs one line and updates the metrics.
    """

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.usage = UsageMetadataCallbackHandler()  # token counts of every LLM call in the run
        self._started = time.monotonic()
        self._running: dict[str, float] = {}  # node → when it started
        self.node_seconds: dict[str, float] = {}

    def record(self, event: str, node: str) -> None:
        now = time.monotonic()
        if event == "started":
            self._running[node] = now
        elif node in self._running:
            self.node_seconds[node] = self.node_seconds.get(node, 0.0) + now - self._running.pop(node)

    def finish(self, outcome: str) -> int:
        """Log the run and update the metrics. Returns the LLM tokens it used."""
        seconds = time.monotonic() - self._started
        tokens = {"input": 0, "output": 0, "total": 0}
        for counts in self.usage.usage_metadata.values():  # one entry per model
            tokens["input"] += counts.get("input_tokens", 0)
            tokens["output"] += counts.get("output_tokens", 0)
            tokens["total"] += counts.get("total_tokens", 0)
        if tokens["total"] == 0 and (ran := LLM_NODES & self.node_seconds.keys()):
            # The provider reported no usage for calls that did happen: estimate rather than charge 0
            tokens["total"] = len(ran) * FALLBACK_TOKENS_PER_LLM_NODE
            logger.warning(
                "Screening %s: no LLM usage metadata for %d node(s); charging estimated %d tokens",
                self.thread_id,
                len(ran),
                tokens["total"],
            )
        metrics.inc("screening_runs_total", outcome=outcome)
        metrics.inc("screening_run_seconds_sum", seconds)
        metrics.inc("screening_run_seconds_count")
        metrics.inc("llm_tokens_total", tokens["input"], direction="input")
        metrics.inc("llm_tokens_total", tokens["output"], direction="output")
        nodes = ", ".join(f"{node}={t:.1f}s" for node, t in self.node_seconds.items()) or "none"
        logger.info(
            "Screening %s run %s in %.1fs; nodes: %s; tokens: %d in, %d out, %d total",
            self.thread_id,
            outcome,
            seconds,
            nodes,
            tokens["input"],
            tokens["output"],
            tokens["total"],
        )
        return tokens["total"]
