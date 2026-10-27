"""Tool-calling loop that lets the LLM search the company interview guidelines and summarise them.

Not an LLM tool itself: the generate_questions node calls this, and this gives the LLM the
lookup_interview_guideline tool (knowledge_tools.py) and runs the calls the LLM asks for. It lives
here, beside that tool, rather than in app/graph/: it is LLM tool orchestration, not a workflow step.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.llms import get_tool_llm
from app.prompts import guideline_prompts as prompts
from app.tools.knowledge_tools import NO_RESULTS, SEARCH_FAILED, lookup_interview_guideline

logger = logging.getLogger(__name__)

TOOLS = {"lookup_interview_guideline": lookup_interview_guideline}
MAX_TOOL_ROUNDS = 5
# One reply can ask for many searches at once; each costs an embedding call
MAX_TOOL_CALLS = 12
TOOL_LIMIT_REACHED = "Search limit reached"


def _text(content) -> str:
    # Some providers return a list of content blocks instead of a string
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def gather_guidelines(topics: list[str]) -> str:
    """Let the LLM look up company guidelines with the knowledge base tool and summarise them."""
    llm_with_tools = get_tool_llm(list(TOOLS.values()))
    messages = [
        SystemMessage(prompts.GUIDELINE_RESEARCH),
        HumanMessage(f"Role and candidate skills: {', '.join(topics)}"),
    ]
    found: list[str] = []  # raw guideline texts, used if no summary comes back
    calls = 0
    for _ in range(MAX_TOOL_ROUNDS):  # safety limit on loop iterations
        ai = llm_with_tools.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:  # LLM is done using tools
            return _text(ai.content) or "\n\n".join(found)
        for call in ai.tool_calls:
            calls += 1
            tool_fn = TOOLS.get(call["name"])
            if calls > MAX_TOOL_CALLS:
                # Every call still gets an answer: the model expects one ToolMessage per call
                output = f"{TOOL_LIMIT_REACHED}; summarise what you have found."
            elif tool_fn:
                output = tool_fn.invoke(call["args"])
            else:
                output = f"Unknown tool: {call['name']}"
            if not output.startswith((NO_RESULTS, SEARCH_FAILED, TOOL_LIMIT_REACHED, "Unknown tool")):
                found.append(output)
            messages.append(ToolMessage(content=output, tool_call_id=call["id"]))
    logger.warning("Guideline lookup hit the %d-round tool limit", MAX_TOOL_ROUNDS)
    return "\n\n".join(found)
