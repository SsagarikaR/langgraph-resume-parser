import logging
import re
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any, TypeVar, cast

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)
# Loose sanity check on configured model names, not an exhaustive allow-list: catches an obvious
# typo or a non-Gemini name at startup instead of mid-workflow, after a token-budget charge.
KNOWN_MODEL_NAME = re.compile(r"^gemini-", re.IGNORECASE)


@lru_cache
def chat_model(name: str) -> BaseChatModel:
    """A Gemini chat model, created on first use so importing the app needs no API key."""
    if not KNOWN_MODEL_NAME.match(name):
        raise RuntimeError(
            f"GEMINI_MODEL/GEMINI_FALLBACK_MODEL '{name}' doesn't look like a Gemini model name "
            "(expected something starting with 'gemini-'); the app only supports Gemini models "
            "with structured-output and tool-calling support"
        )
    settings = get_settings()
    return init_chat_model(
        name,
        model_provider="google_genai",
        api_key=settings.gemini_api_key.get_secret_value(),
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


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
