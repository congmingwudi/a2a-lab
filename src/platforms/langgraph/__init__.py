"""LangGraph platform (WS4) — the open-source-framework column.

A LangGraph research agent (small ReAct graph: an agent node + an
``ask_agentforce`` tool node) that delegates CRM knowledge to Agentforce,
host-side, over the same delegation boundary as the Claude / OpenAI /
Strands agents. Same two-seam shape as platforms/strands and platforms/openai.

Hosting (D77 — the Heroku pivot): the plan's WS4 originally targeted
LangGraph Platform's managed Agent Server (native A2A/MCP). The operator
chose to host on **Heroku** instead, so the agent is served through the
lab's OWN serve() adapters (REST/MCP/A2A) — the first Heroku-hosted platform
in the lab. The agent interior is identical either way; only the host and
the "native vs. lab-served protocol endpoints" distinction change.

Observability: LangGraph/LangChain auto-emit runs to LangSmith when
LANGSMITH_API_KEY + LANGCHAIN_TRACING_V2 are set — WS4's queryable-SaaS
observability column survives the host change (LangSmith is host-agnostic).

The real backend (``LangGraphBackend``) is imported lazily so the scaffold,
loopback tests, and matrix all run on the deterministic stub before the
``langgraph`` extra is installed.
"""
