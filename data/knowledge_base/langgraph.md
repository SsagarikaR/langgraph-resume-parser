# LangGraph Interview Guidelines

Interviewers should focus on:
- Modelling a workflow as a graph: nodes, edges, and conditional edges.
- State design: TypedDict state, reducers (e.g. operator.add), and partial state updates.
- Control flow: loops, retries, and stopping conditions (e.g. a max revision count).
- Human-in-the-loop: interrupts and checkpointers for pausing and resuming.
- Tool calling: binding tools to an LLM and routing with a ToolNode.

Red flags: treats LangGraph as a linear chain; no idea how state is merged between nodes.
