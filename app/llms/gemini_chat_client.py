import re
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.config.settings import get_settings

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
