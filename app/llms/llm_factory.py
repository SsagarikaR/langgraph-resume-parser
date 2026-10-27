import logging
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any, TypeVar, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.config.settings import get_settings
from app.llms.gemini_chat_client import chat_model

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


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
    """One system + human message to the model, returned as an instance of `schema`."""
    try:
        result: Any = get_structured_llm(schema).invoke([("system", system), ("human", human)])
    except Exception:
        # Not the raw model text (langchain's own validation error rarely includes it) but the
        # exception itself, which is otherwise swallowed by the graph's retry before a human sees it
        logger.warning("Structured output for %s failed; retrying if attempts remain", schema.__name__, exc_info=True)
        raise
    if result is None:  # the model returned nothing parseable
        raise RuntimeError(f"LLM returned no {schema.__name__}")
    return cast(ModelT, result)
