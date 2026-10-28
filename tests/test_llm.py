import time

import pytest
from pydantic import BaseModel

from app.exceptions import domain as llm_domain
from app.llms import gemini_chat_client
from app.llms import llm_factory as llm


class Answer(BaseModel):
    text: str


class FakeStructured:
    def __init__(self, result):
        self.result = result
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return self.result


def test_structured_call_sends_system_and_human_messages(monkeypatch):
    fake = FakeStructured(Answer(text="ok"))
    monkeypatch.setattr(llm, "get_structured_llm", lambda schema: fake)
    assert llm.structured_call(Answer, "be brief", "hi") == Answer(text="ok")
    assert fake.messages == [("system", "be brief"), ("human", "hi")]


def test_structured_call_raises_when_nothing_parseable_comes_back(monkeypatch):
    monkeypatch.setattr(llm, "get_structured_llm", lambda schema: FakeStructured(None))
    with pytest.raises(RuntimeError, match="LLM returned no Answer"):
        llm.structured_call(Answer, "s", "h")


def test_chat_model_uses_the_configured_settings(monkeypatch, test_settings):
    seen = {}
    monkeypatch.setattr(
        gemini_chat_client, "init_chat_model", lambda model, **kwargs: seen.update(model=model, **kwargs)
    )
    llm.chat_model.cache_clear()
    try:
        llm.get_llm()
    finally:
        llm.chat_model.cache_clear()
    assert seen["model"] == test_settings.gemini_model
    assert seen["api_key"] == "test-key"
    assert seen["timeout"] == test_settings.llm_timeout_seconds
    assert seen["max_retries"] == test_settings.llm_max_retries


class FakeChatModel:
    """Records which model each structured call went to; fails when told to."""

    def __init__(self, name, calls, fail):
        self.name, self.calls, self.fail = name, calls, fail

    def with_structured_output(self, schema):
        from langchain_core.runnables import RunnableLambda

        def call(messages):
            self.calls.append(self.name)
            if self.fail:
                raise RuntimeError(f"{self.name} unavailable")
            return Answer(text=self.name)

        return RunnableLambda(call)


@pytest.fixture
def models(monkeypatch):
    calls: list[str] = []
    failing: set[str] = set()
    monkeypatch.setattr(llm, "chat_model", lambda name: FakeChatModel(name, calls, name in failing))
    llm.get_structured_llm.cache_clear()
    yield calls, failing
    llm.get_structured_llm.cache_clear()


def test_a_failed_call_goes_to_the_fallback_model(models, monkeypatch):
    calls, failing = models
    monkeypatch.setenv("GEMINI_MODEL", "main")
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "backup")
    llm.get_settings.cache_clear()
    failing.add("main")
    assert llm.structured_call(Answer, "s", "h") == Answer(text="backup")
    assert calls == ["main", "backup"]


def test_without_a_fallback_model_the_error_is_raised(models, monkeypatch):
    calls, failing = models
    monkeypatch.setenv("GEMINI_MODEL", "main")
    llm.get_settings.cache_clear()
    failing.add("main")
    with pytest.raises(RuntimeError, match="main unavailable"):
        llm.structured_call(Answer, "s", "h")
    assert calls == ["main"]


def test_circuit_opens_after_the_configured_number_of_failures(models, monkeypatch):
    calls, failing = models
    monkeypatch.setenv("GEMINI_MODEL", "main")
    monkeypatch.setenv("LLM_CIRCUIT_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("LLM_CIRCUIT_COOLDOWN_SECONDS", "60")
    llm.get_settings.cache_clear()
    llm._circuit_breaker.cache_clear()
    failing.add("main")

    for _ in range(2):
        with pytest.raises(RuntimeError, match="main unavailable"):
            llm.structured_call(Answer, "s", "h")

    calls.clear()
    with pytest.raises(llm_domain.LlmUnavailableError):
        llm.structured_call(Answer, "s", "h")
    assert calls == []  # the open circuit skipped calling the model at all


def test_circuit_closes_again_after_a_success(models, monkeypatch):
    _calls, failing = models
    monkeypatch.setenv("GEMINI_MODEL", "main")
    monkeypatch.setenv("LLM_CIRCUIT_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("LLM_CIRCUIT_COOLDOWN_SECONDS", "0.01")
    llm.get_settings.cache_clear()
    llm._circuit_breaker.cache_clear()
    failing.add("main")

    with pytest.raises(RuntimeError, match="main unavailable"):
        llm.structured_call(Answer, "s", "h")

    time.sleep(0.02)  # let the cooldown elapse so the next call is a trial, not a skip
    failing.discard("main")
    llm.get_structured_llm.cache_clear()  # FakeChatModel snapshots `failing` at construction time
    assert llm.structured_call(Answer, "s", "h") == Answer(text="main")

    # the breaker reset on that success: another failure needs its own full threshold to reopen it
    failing.add("main")
    llm.get_structured_llm.cache_clear()
    with pytest.raises(RuntimeError, match="main unavailable"):
        llm.structured_call(Answer, "s", "h")
