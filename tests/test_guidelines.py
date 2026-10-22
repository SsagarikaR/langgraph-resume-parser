from langchain_core.messages import AIMessage

from app.tools import guidelines


class ScriptedLLM:
    """Replays AI messages in order, like a chat model with tools bound."""

    def __init__(self, replies):
        self.replies = iter(replies)
        self.seen = []

    def invoke(self, messages):
        self.seen.append(list(messages))
        return next(self.replies)


def tool_call(topic, call_id="1"):
    return AIMessage("", tool_calls=[{"name": "lookup_interview_guideline", "args": {"topic": topic}, "id": call_id}])


def test_tool_results_are_fed_back_and_the_summary_returned(monkeypatch):
    llm = ScriptedLLM([tool_call("Kafka"), AIMessage("Focus on partitioning")])
    monkeypatch.setattr(guidelines, "get_tool_llm", lambda tools: llm)
    monkeypatch.setattr(guidelines.lookup_interview_guideline, "func", lambda topic: f"[{topic}] Focus area: offsets")

    assert guidelines.gather_guidelines(["Backend Engineer", "Kafka"]) == "Focus on partitioning"
    assert llm.seen[1][-1].content == "[Kafka] Focus area: offsets"  # tool output went back to the model


def test_round_limit_falls_back_to_raw_results(monkeypatch):
    llm = ScriptedLLM([tool_call("Kafka", str(i)) for i in range(guidelines.MAX_TOOL_ROUNDS)])
    monkeypatch.setattr(guidelines, "get_tool_llm", lambda tools: llm)
    monkeypatch.setattr(guidelines.lookup_interview_guideline, "func", lambda topic: "found")

    assert guidelines.gather_guidelines(["Kafka"]) == "\n\n".join(["found"] * guidelines.MAX_TOOL_ROUNDS)


def test_unknown_tool_is_reported_to_the_model_not_raised(monkeypatch):
    bad = AIMessage("", tool_calls=[{"name": "rm_rf", "args": {}, "id": "1"}])
    llm = ScriptedLLM([bad, AIMessage("done")])
    monkeypatch.setattr(guidelines, "get_tool_llm", lambda tools: llm)

    assert guidelines.gather_guidelines(["x"]) == "done"
    assert llm.seen[1][-1].content == "Unknown tool: rm_rf"


def test_searches_beyond_the_call_limit_are_not_run(monkeypatch):
    many = AIMessage(
        "",
        tool_calls=[
            {"name": "lookup_interview_guideline", "args": {"topic": f"t{i}"}, "id": str(i)}
            for i in range(guidelines.MAX_TOOL_CALLS + 3)
        ],
    )
    llm = ScriptedLLM([many, AIMessage("summary")])
    searched = []
    monkeypatch.setattr(guidelines, "get_tool_llm", lambda tools: llm)
    monkeypatch.setattr(guidelines.lookup_interview_guideline, "func", lambda topic: searched.append(topic) or "found")

    assert guidelines.gather_guidelines(["x"]) == "summary"
    assert len(searched) == guidelines.MAX_TOOL_CALLS
    answers = [m for m in llm.seen[1] if m.type == "tool"]
    assert len(answers) == guidelines.MAX_TOOL_CALLS + 3  # every call still got a reply
    assert answers[-1].content.startswith(guidelines.TOOL_LIMIT_REACHED)
