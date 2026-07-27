# The Claude API inside the lab — three production integrations

**Feature area:** direct Anthropic SDK / Claude API usage in the lab's own
runtime (distinct from using Claude Code to *build* the lab).

The lab doesn't just talk *about* agent platforms — three of its own components
are Claude-powered, each demonstrating a different API surface.

## Engineering takeaway

Choose the least complex Claude surface that meets the runtime requirement.
Hosted sessions, a self-hosted agent harness, and a direct Messages API loop
have different control, security, and operational trade-offs.

## 1. Managed Agents backend — `src/platforms/claude/managed_backend.py`

The lab's Claude agent runs on the **Anthropic Managed Agents beta** by
default (`CLAUDE_BACKEND=managed`). Teaching-relevant details:

- **Control plane vs data plane.** The agent + environment are persistent,
  versioned resources provisioned once by `scripts/setup_managed_agent.py`;
  the request path only drives *sessions* (create/reuse keyed by the lab's
  session id, open the event stream first, send the message, collect
  `agent.message` events until a terminal stop reason).
- **Host-side custom tools as a security boundary.** Path B
  (Claude → Agentforce) is an `ask_agentforce` custom tool: the sandbox emits
  `agent.custom_tool_use`, the *host* calls Salesforce and returns
  `user.custom_tool_result`. Salesforce credentials never enter the managed
  sandbox — the tool seam is the trust boundary.
- **Backend swap is invisible.** `sdk_backend.py` (self-hosted
  `claude-agent-sdk`) implements the same interior and is the AgentCore
  containerization path (D26). Nothing outside the adapter knows which backend
  runs — a live demo of "same agent, two hosting models."

## 2. The Lab Guide — a hand-rolled tool-use loop with streaming
   (`src/platforms/guide/core.py`, D35)

The console's docent is a **direct `AsyncAnthropic` tool-use loop** — no
framework — grounded in the lab's own docs:

- **Curated read tools, not RAG.** Six tools (`get_decision`, `read_doc`,
  `list_recent_runs`, `get_trace`, `list_briefs`, `read_brief`) over a
  whitelisted corpus. The model
  resolves "why did the last ADK run take 35s?" by actually reading the ADR
  log and the wire-trace record — answers cite ground truth, not embeddings.
- **`client.messages.stream(...)` + tool loop.** Token streaming to the
  console UI via SSE while the loop handles up to `MAX_TOOL_ROUNDS=6` tool
  calls; `stream.get_final_message()` closes each round.
- **Right-sized model.** Haiku-tier by default (`GUIDE_MODEL`,
  `claude-haiku-4-5`) — grounded Q&A over curated tools doesn't need a
  frontier model, and the latency budget matters in a live console.
- **The meta exhibit.** Because the guide implements the lab's own
  `AgentAdapter` contract, `serve(adapter, protocol, port)` gives it REST, MCP,
  and A2A surfaces for free — the docent is itself a lab specimen. Its MCP
  server also exposes the raw read tools directly, so a *client's* model can do
  the reasoning instead: two integration shapes (server-side agent vs
  client-side tools) demonstrated side by side.

## 3. The hosted observability analyst — `scripts/obs_analysis.py` (D23)

The analysis layer above the deterministic ETL (D22): a scheduled, hosted
analyst that reads the cross-platform trace store (Aurora Postgres behind an
MCP front) and writes analyst briefs the console renders. Architecture note
worth teaching: **deterministic ETL below, agent analysis above** — the model
never parses raw platform logs; it reasons over an already-normalized store.

## Teaching points for the deck

- Pick the API surface per job: **Managed Agents** when you want hosted
  sessions + sandboxing, **agent SDK** when you need to own the container,
  **raw messages + tools** when the loop is simple enough that a framework
  would only add opacity.
- Custom tools are trust boundaries, not just capabilities (credentials stay
  host-side).
- Grounding via curated read tools beats RAG when the corpus is small,
  structured, and authoritative — and it makes answers auditable.
- Model tiering is an architecture decision: Haiku for the docent and the
  fast delegation path (the Path A timeout budget in `CLAUDE.md` *requires*
  it), bigger models only where judgment depth pays.

## Evidence and limits

- **Repository-backed:** the managed backend, six-tool Lab Guide loop, and
  analyst entry point are present in the referenced files; D35 records the
  guide design.
- **Vendor-documented:** Managed Agents supplies persistent agent/session
  resources and supports host-executed custom tools. It remains a beta API, so
  versioned API details should be checked before reusing the sample.
- “Curated tools beat RAG” is conditional: it applies here because the corpus is
  small, structured, and authoritative. It is not a general rejection of
  retrieval systems.

## Put this in the presentation

**Slide headline:** Three Claude surfaces, chosen by operational need.

| Need | Surface | Value |
|---|---|---|
| Hosted sessions and sandboxing | Managed Agents | Less runtime ownership; host-side credential boundary |
| Container and runtime control | Agent SDK | Portable agent interior; infrastructure remains yours |
| Small, transparent tool loop | Messages API | Minimal abstraction and visible control flow |

**Visual:** the table above or three side-by-side runtime boxes. Emphasize the
decision criteria, not a product-feature inventory.
