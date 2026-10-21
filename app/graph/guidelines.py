"""Tool-calling loop that lets the LLM search the company interview guidelines and summarise them."""

import logging

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.core.llm import get_llm
from app.graph import prompts
from app.tools.knowledge_tools import NO_RESULTS, SEARCH_FAILED, lookup_interview_guideline

logger = logging.getLogger(__name__)

TOOLS = {"lookup_interview_guideline": lookup_interview_guideline}
MAX_TOOL_ROUNDS = 5


def _text(content) -> str:
    # Some providers return a list of content blocks instead of a string
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def gather_guidelines(topics: list[str]) -> str:
    """Let the LLM look up company guidelines with the knowledge base tool and summarise them."""
    llm_with_tools = get_llm().bind_tools(list(TOOLS.values()))
    messages = [
        SystemMessage(prompts.GUIDELINE_RESEARCH),
        HumanMessage(f"Role and candidate skills: {', '.join(topics)}"),
    ]
    found = []  # raw guideline texts, used if no summary comes back
    for _ in range(MAX_TOOL_ROUNDS):  # safety limit on loop iterations
        ai = llm_with_tools.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:  # LLM is done using tools
            return _text(ai.content) or "\n\n".join(found)
        for call in ai.tool_calls:
            tool_fn = TOOLS.get(call["name"])
            output = tool_fn.invoke(call["args"]) if tool_fn else f"Unknown tool: {call['name']}"
            if not output.startswith((NO_RESULTS, SEARCH_FAILED, "Unknown tool")):
                found.append(output)
            messages.append(ToolMessage(content=output, tool_call_id=call["id"]))
    logger.warning("Guideline lookup hit the %d-round tool limit", MAX_TOOL_ROUNDS)
    return "\n\n".join(found)
