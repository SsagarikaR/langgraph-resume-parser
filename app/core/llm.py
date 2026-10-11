from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from app.core.config import get_settings


@lru_cache
def get_llm() -> BaseChatModel:
    """Chat model, created on first use so importing the app needs no API key."""
    settings = get_settings()
    return init_chat_model(
        settings.gemini_model,
        model_provider="google_genai",
        api_key=settings.gemini_api_key.get_secret_value(),
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


@lru_cache
def get_structured_llm(schema: type[BaseModel]):
    """The chat model constrained to return `schema`."""
    return get_llm().with_structured_output(schema)
