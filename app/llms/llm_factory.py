import logging
import threading
import time
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any, TypeVar, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.config.settings import get_settings
from app.exceptions.domain import LlmUnavailableError
from app.llms.gemini_chat_client import chat_model

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


class _CircuitBreaker:
    """Fails fast instead of calling the LLM once too many consecutive calls have failed.

    Each call still gets its own retries and fallback model (see `with_fallback`); this only
    guards against the case where *those* are exhausted repeatedly in a row, which means the
    provider itself is down rather than one request having bad luck. In process memory, matching
    the single-worker deployment (see app/services/concurrency.py).
    """

    def __init__(self, failure_threshold: int, cooldown_seconds: float):
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    def before_call(self) -> None:
        # Raise if the circuit is open and the cooldown hasn't elapsed yet.
        with self._lock:
            if self._opened_at is None:
                return
            remaining = self._cooldown_seconds - (time.monotonic() - self._opened_at)
            if remaining > 0:
                raise LlmUnavailableError(
                    f"The LLM provider has failed {self._consecutive_failures} times in a row; "
                    "skipping this call until it recovers",
                    retry_after=int(remaining) + 1,
                )
            # Cooldown elapsed: let one trial call through without resetting the failure count yet.

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._opened_at = time.monotonic()


@lru_cache
def _circuit_breaker() -> _CircuitBreaker:
    settings = get_settings()
    return _CircuitBreaker(settings.llm_circuit_failure_threshold, settings.llm_circuit_cooldown_seconds)


def get_llm() -> BaseChatModel:
    """The configured main chat model."""
    return chat_model(get_settings().gemini_model)


def with_fallback(build: Callable[[BaseChatModel], Runnable]) -> Runnable:
    """`build` applied to the main model, falling back to GEMINI_FALLBACK_MODEL (when set) for a
    call the main model still fails after its own retries."""
    settings = get_settings()
    primary = build(get_llm())
    if not settings.gemini_fallback_model:
        return primary
    return primary.with_fallbacks([build(chat_model(settings.gemini_fallback_model))])


@lru_cache
def get_structured_llm(schema: type[BaseModel]) -> Runnable:
    """The chat model constrained to return `schema`."""
    return with_fallback(lambda llm: llm.with_structured_output(schema))


def get_tool_llm(tools: Sequence[BaseTool]) -> Runnable:
    """The chat model able to call `tools`."""
    return with_fallback(lambda llm: llm.bind_tools(list(tools)))


def structured_call(schema: type[ModelT], system: str, human: str) -> ModelT:
    """One system + human message to the model, returned as an instance of `schema`.

    Raises LlmUnavailableError without calling the model at all if recent calls have failed
    enough in a row that the provider looks down (see _CircuitBreaker).
    """
    breaker = _circuit_breaker()
    breaker.before_call()
    try:
        result: Any = get_structured_llm(schema).invoke([("system", system), ("human", human)])
    except Exception:
        # Not the raw model text (langchain's own validation error rarely includes it) but the
        # exception itself, which is otherwise swallowed by the graph's retry before a human sees it
        logger.warning("Structured output for %s failed; retrying if attempts remain", schema.__name__, exc_info=True)
        breaker.record_failure()
        raise
    if result is None:  # the model returned nothing parseable
        breaker.record_failure()
        raise RuntimeError(f"LLM returned no {schema.__name__}")
    breaker.record_success()
    return cast(ModelT, result)
