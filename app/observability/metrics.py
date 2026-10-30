"""Process metrics in the Prometheus text format, served at GET /metrics.

A few counters do not justify a client library: values live in memory, per process (the API runs
one worker), and reset on restart, which Prometheus's rate() handles.
"""

import math
import threading
from collections import defaultdict

Labels = tuple[tuple[str, str], ...]

# name → (type, help)
METRICS = {
    "http_requests_total": ("counter", "HTTP requests handled, by method and status code"),
    "http_request_duration_seconds": ("histogram", "Time until the response starts, by method"),
    "screening_runs_total": ("counter", "Workflow runs, by outcome (ok, invalid_input, failed, aborted)"),
    "screening_run_seconds_sum": ("counter", "Total wall time of finished workflow runs"),
    "screening_run_seconds_count": ("counter", "Finished workflow runs timed in screening_run_seconds_sum"),
    "llm_tokens_total": ("counter", "LLM tokens used by workflow runs, by direction (input, output)"),
    "screening_runs_in_progress": ("gauge", "Workflow runs executing now"),
}
# Upper bounds (seconds) of the histogram buckets: reads take milliseconds, workflow runs minutes
BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)


def number(value: float) -> str:
    """A sample value written exactly: 1234567, not 1.23457e+06 as the :g format gives."""
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def label_text(labels: Labels) -> str:
    return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}" if labels else ""


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, dict[Labels, float]] = defaultdict(dict)
        # histogram name → labels → (count per bucket, sum, count)
        self._histograms: dict[str, dict[Labels, tuple[list[int], float, int]]] = defaultdict(dict)

    def inc(self, name: str, amount: float = 1, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[name][key] = self._values[name].get(key, 0) + amount

    def observe(self, name: str, value: float, **labels: str) -> None:
        """Record one value in a histogram."""
        key = tuple(sorted(labels.items()))
        with self._lock:
            buckets, total, count = self._histograms[name].get(key, ([0] * len(BUCKETS), 0.0, 0))
            buckets = [n + (value <= bound) for n, bound in zip(buckets, BUCKETS, strict=True)]
            self._histograms[name][key] = (buckets, total + value, count + 1)

    def get(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._values[name].get(tuple(sorted(labels.items())), 0)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()
            self._histograms.clear()

    def render(self) -> str:
        lines = []
        with self._lock:
            for name, (kind, help_text) in METRICS.items():
                lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
                if kind == "histogram":
                    lines += self._render_histogram(name)
                    continue
                series = self._values.get(name) or {(): 0}
                for labels, value in sorted(series.items()):
                    lines.append(f"{name}{label_text(labels)} {number(value)}")
        return "\n".join(lines) + "\n"

    def _render_histogram(self, name: str) -> list[str]:
        lines = []
        for labels, (buckets, total, count) in sorted(self._histograms.get(name, {}).items()):
            # Prometheus buckets are cumulative; observe() already counts each value in every bucket it fits
            for bound, n in [*zip(BUCKETS, buckets, strict=True), (math.inf, count)]:
                lines.append(f"{name}_bucket{label_text((*labels, ('le', number(bound))))} {n}")
            lines.append(f"{name}_sum{label_text(labels)} {number(total)}")
            lines.append(f"{name}_count{label_text(labels)} {count}")
        return lines


metrics = Registry()
