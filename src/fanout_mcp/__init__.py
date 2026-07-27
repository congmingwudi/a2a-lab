"""The business units, exposed as remote MCP tools (WS7 item 4).

`orchestration/cma.py` runs the same scenario with a **custom** tool, which on
Managed Agents means the HOST executes it: the fan-out is `dispatch()` running
on a laptop, and the session cannot progress unless that laptop is attached and
watching. Two things follow, and both are the reason this package exists.

**It is not hosted.** A demo whose orchestrator needs a process on someone's
machine is not the "retire the laptop from the runtime path" end state WS7 is
about. MCP tools execute on the orchestration layer instead, so the session
needs nothing attached.

**It is not agentic.** With one `consult_business_units` tool the model's only
scheduling decision is *when* to call it; the order and the parallelism are
`asyncio.gather` in host code. Writing "call unit 1, then unit 2" is a program
with a language model in it. Here each unit is its OWN tool, so the model
chooses which to call, in what order, and whether to issue them together in one
turn. **Whether it actually does is a measurement, not an assumption** — the
lab's job is to report what the model emitted, including if it serialises them
or drops one.

The host-side variant is kept deliberately as the control. Same agent, same
prompt, same scenario, two tool inventories — so any difference in the answer
or the latency is the tool topology and nothing else.

Transport is `obs_mcp.core` / `obs_mcp.http` unchanged: the lab already runs a
hand-rolled MCP Streamable HTTP server on a Lambda behind API Gateway for the
obs analyst (D23), and a second MCP server is a second tool registry, not a
second transport.
"""

SERVER_INFO = {"name": "a2alab-fanout-mcp", "version": "0.1.0"}

# Same rule as obs_mcp: no tool import at the package root, because
# fanout_mcp.tools reaches the whole client stack through orchestration.
__all__ = ["SERVER_INFO"]
