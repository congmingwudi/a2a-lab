# WS25 A2 — viewer role at the protocol faces: design for Codex's critique (2026-09-06)

Per `plan/16-joint-agent-workflow.md`, the auth-middleware half of A2 gets a
design critique from Codex (GPT-6 Astra) BEFORE the edit. The console half
(`DELETE /api/traces` gated, method-aware `_OPERATOR_ONLY`) is already built on
`ws25-b1-security-seams` because it reuses the existing role gate unchanged.

## The defect (F01, confirmed by probe)

`interop.servers.auth.TokenAuthMiddleware` (src/interop/servers/auth.py#L110)
admits ANY JWT that `_verify_lab_jwt` accepts, stores the claims in
`scope["state"]["lab_user"]`, and passes the request on. Role is never read.
So a `viewer` persona, which the console refuses on `/api/run`, can invoke any
protocol face directly: REST `POST /invoke` and `/invocations`, MCP
`tools/call`, A2A v1 `SendMessage` and the 0.3 `message/send`.

## Proposed change

1. **Role resolution in the middleware, from the directory, not the claim.**
   After a JWT verifies, look up `identity.load_users()[sub]["role"]` (the same
   source `_viewer_forbidden` uses in the console, so a stale claim cannot
   escalate). Cache the directory per process the way the console does.
2. **Deny on INVOKING requests only.** A request is invoking when:
   - REST: method `POST` and path in `{/invoke, /invocations}`;
   - MCP: method `POST` and the JSON-RPC body's `method == "tools/call"`
     (`tools/list`, `initialize`, `ping` stay open — discovery is read-only);
   - A2A: method `POST` and the JSON-RPC body's `method` in
     `{"SendMessage", "message/send", "SendStreamingMessage", "message/stream"}`
     (`tasks/get`, `GetTask`, the agent card stay open — polling an existing
     task is reading).
   Everything else (`/healthz`, `/.well-known/agent.json`, GET) is unchanged.
3. **Who is denied.** `viewer` only. `operator`/`owner` personas pass; the
   header-borne shared service token has no persona and passes (the operator's
   own legacy credential); the `machine` role passes — WS10 SP1's gateway
   identity is an attributed FACES caller by design.
4. **Response.** 403 `{"detail": "'<action>' is operator-only (D36 role model)"}`,
   matching the console's wording so a viewer sees one rule everywhere.
5. **Body inspection cost.** MCP/A2A need the JSON-RPC method, which lives in
   the body. The middleware already sits under the WireTap, which buffers the
   body; the auth middleware would read `receive()` once and replay it (the
   standard Starlette pattern) — bounded by the existing size clip.

## Questions for the critique

- Is reading the JSON-RPC method in the auth middleware the right layer, or
  should the deny live in the MCP/A2A handlers where the method is already
  parsed (two places instead of one, but no body replay)?
- Should `machine` be allowed on ALL invoking routes, or only the A2A face the
  Agent Fabric gateway calls (WS10 SP1)? Today the mount is per-face, so a
  per-face allow-list is cheap.
- Is denying `SendStreamingMessage` correct given streaming is advertised but
  not exercised (CLAUDE.md), or should it be left alone to avoid asserting a
  policy on a path that does not run?

## Tests planned (RED before the edit)

`tests/unit/test_auth_roles.py`: viewer JWT → 403 on each invoking route per
protocol, 200 on discovery/health/`tasks/get`; operator JWT and shared token
unchanged; `machine` allowed; stale `role` claim in the token does not
override the directory. Loopback e2e (`tests/e2e/test_loopback.py`) gains one
viewer-denied case per protocol against the real servers.

## Decisions (after Codex's critique, 2026-09-06) — BUILT

1. **Enforcement point:** one policy function `interop.authz.invoke_denial`.
   REST calls it in the handler; MCP and A2A call it from the WireTap, which
   already buffers the body exactly once, so the JSON-RPC method is read with
   no second `receive()` anywhere. Refusals go out through the tap and are
   recorded as 403 error hops.
2. **`machine`:** allowed at every face, denied at the console. The gateway
   fronts every face (plan/07 WS10), so a per-face allow-list would equal the
   full list.
3. **Streaming:** left out of the deny set, documented as deliberate in
   `authz.py`; add when a streaming route exists and is tested.

Tests: `tests/unit/test_authz.py` (12 cases across the three protocols, the
0.3 spelling, discovery/poll reads, stale-claim non-escalation, the recorded
error hop).
