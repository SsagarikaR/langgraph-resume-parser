"""Admission control for workflow runs: one request per screening, and a cap on concurrent runs.

Everything here is in process memory, which matches the single-worker deployment (the SQLite
checkpointer is per process too). Running several workers would need these guards in a shared
store (e.g. Redis locks and a distributed semaphore) behind the same RunGuard methods.
"""

import logging
import threading
from collections.abc import Generator, Iterator
from contextlib import ExitStack, contextmanager

from app.core.metrics import metrics
from app.exceptions.domain import ScreeningBusyError, ServerBusyError

logger = logging.getLogger(__name__)


class RunGuard:
    """Holds screenings and run slots for the requests that run or delete them."""

    def __init__(self, max_concurrent_runs: int):
        # Set up the busy-screening tracker and the fixed-size run-slot counter.
        self._busy: set[str] = set()  # thread ids with a run or delete in progress
        self._busy_lock = threading.Lock()
        self._run_slots = threading.BoundedSemaphore(max_concurrent_runs)

    @contextmanager
    def exclusive(self, thread_id: str):
        # Lock this specific screening so no second request can touch it at the same time.
        """Hold the screening for one run or delete; ScreeningBusyError if another has it."""
        with self._busy_lock:
            if thread_id in self._busy:
                raise ScreeningBusyError(f"Screening {thread_id} is busy with another request; try again shortly")
            self._busy.add(thread_id)
        try:
            yield
        finally:
            with self._busy_lock:
                self._busy.discard(thread_id)

    @contextmanager
    def run_slot(self):
        # Grab one of the N server-wide workflow slots; reject immediately if all are in use.
        """Hold one of the server's workflow slots; ServerBusyError if all are taken."""
        if not self._run_slots.acquire(blocking=False):
            raise ServerBusyError("The server is running as many screenings as it allows; try again shortly")
        metrics.inc("screening_runs_in_progress")
        try:
            yield
        finally:
            metrics.inc("screening_runs_in_progress", -1)
            self._run_slots.release()

    def hold(self, thread_id: str) -> ExitStack:
        # Acquire both locks together; if the screening is busy, give the slot back before raising.
        """A run slot and the screening, taken now; close the stack to release both."""
        hold = ExitStack()
        try:
            hold.enter_context(self.run_slot())
            hold.enter_context(self.exclusive(thread_id))
        except BaseException:
            hold.close()
            raise
        return hold

    def is_held(self, thread_id: str) -> bool:
        # Quick read-only check: is this screening currently locked by any request?
        """Whether a request is running (or deleting) the screening right now."""
        with self._busy_lock:
            return thread_id in self._busy


class HeldRun(Iterator[dict]):
    """The events of a run that already holds its screening and a run slot.

    The hold is released when the run ends, when the stream is closed (a client that goes away),
    or when the stream is dropped without being read at all: a generator that never started
    would not run its cleanup, so the hold cannot live in the generator alone.
    """

    def __init__(self, events: Generator[dict, None, None], hold: ExitStack):
        # Store the event stream and the lock bundle; closed flag prevents double-release.
        self._events = events
        self._hold = hold
        self._closed = False

    def __next__(self) -> dict:
        # Fetch the next event from the LangGraph stream; StopIteration signals the run is done.
        return next(self._events)

    def close(self) -> None:
        # Stop the generator and release both locks; safe to call multiple times.
        """Release the hold. Every caller closes the stream (final_view, the API's ndjson), so
        this happens at a known point; __del__ is only the safety net."""
        if self._closed:
            return
        self._closed = True
        try:
            self._events.close()
        finally:
            self._hold.close()

    def __del__(self) -> None:
        # Last-resort cleanup if the stream was garbage-collected without close() being called.
        if not self._closed:
            logger.warning("A screening stream was dropped without being closed; releasing its hold")
            self.close()
