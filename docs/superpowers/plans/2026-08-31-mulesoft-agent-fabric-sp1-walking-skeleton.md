# MuleSoft Agent Fabric — SP1 Walking Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the whole MuleSoft Agent Fabric pipeline end-to-end with the smallest real payload — a broker on the Omni Gateway makes ONE A2A call to ONE lab face, authenticated as the machine identity `mulesoft-omni-gateway` using a gateway-acquired, auto-refreshing RS256 lab JWT, and the hop lands in the lab trace layer attributed to that caller.

**Architecture:** Three lab-side pieces (a machine caller identity, a client-credentials mint path, a public console `/oauth/token` endpoint) let the gateway acquire short-lived lab JWTs it refreshes natively; the A2A `WireTapMiddleware` reads the verified caller off the ASGI scope so the hop is attributed; a committed `mulesoft/` descriptor set (registry of 6 faces + 6 oauth2-client-credentials connections + a 1-hop broker) is built/published/deployed by the operator against the RUNNING gateway; the broker is exposed to the lab console as an ordinary A2A target so the existing Run button drives it with zero new client code.

**Tech Stack:** Python 3.11+ (uv), PyJWT (RS256), FastAPI (console), pure-ASGI middleware, MuleSoft `anypoint-cli-v4` + `agent-fabric-plugin` v1.3.0 (v2 `agentic-network` format), AgentScript (`# @dialect: AGENTFABRIC=1.1`), AWS Secrets Manager, Anypoint secured deployment variables.

**Spec:** `docs/superpowers/specs/2026-08-31-mulesoft-agent-fabric-broker-design.md` (SP1 is §4; SP2–SP4 are outlined there and OUT OF SCOPE for this plan).

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec and CLAUDE.md.

- **No account identifiers anywhere.** No AWS account id, GCP project, Azure subscription, Salesforce org id, SSO profile name, MuleSoft org id / business-group name — not as literals, not as `${VAR:-default}` fallbacks, not in comments, not in `mulesoft/` descriptors. Use `${VAR:?set VAR in .env}` in shell, `os.environ[...]` / bare `${VAR}` (expands to `""` when unset — loud, not silent) in config. Region-only hostnames (`console-lab.agenticthings.com`, `faces-lab.agenticthings.com`) are fine; org id / BG name stay in the gitignored auto-memory `ws10-mulesoft-agent-fabric-setup`. `tests/unit/test_no_account_identifiers.py` enforces the AWS half.
- **Secrets never committed.** The gateway client id/secret live lab-side in AWS Secrets Manager (`scripts/env_sync.py`, D39) as `A2ALAB_MULE_GW_CLIENT_ID` / `A2ALAB_MULE_GW_CLIENT_SECRET`, and fabric-side as a *secured deployment variable* (`exchange.json` `metadata.variables.gwClientSecret.secret: true`). No literal secret in any descriptor or test.
- **RS256 is asymmetric by design.** The private key (`A2ALAB_JWT_PRIVATE_KEY`) mints; the faces/containers hold only the public key and can never mint. The token-minting endpoint therefore lives ONLY on the console — never on a face.
- **Short TTL is deliberate.** The service JWT TTL defaults to 300s (`A2ALAB_SERVICE_JWT_TTL_S`) because the gateway refreshes it (spec §4.7). Do not reuse the 8h human TTL (`A2ALAB_JWT_TTL_S`).
- **Console feature = code in the image.** The hosted console is `deploy/console/Dockerfile`'s `COPY` lines and nothing else. `/oauth/token` uses `interop.identity` (already imported by the console) and the stdlib — introduce no new module or third-party dependency without adding it to the image. Smoke-test the image locally (`docker build … && docker run …`, hit `/oauth/token`) before calling it deployed.
- **Deployment map + diagrams + console copy update in the same change** as the architecture (`plan/09-deployment-map.md`, `config/diagrams.yaml`, the `*_DIAGRAM` constants and Details panes in `src/console/static/index.html`).
- **Delivery record:** when SP1's build is substantially done, add its work as `N. ✅` item lines under `## WS10` in `plan/07-workstreams.md`, dry-run `scripts/jira_sync.py`, and leave `--apply` for the operator (D58/D60).
- **Division of labor (spec §4.9):** Claude authors all descriptors, the lab-side code, and the tests, and verifies read-only via the `mulesoft-platform` MCP after deploy. The **operator** runs every `anypoint-cli-v4` command (CLI auth is SSO-federated and sealed in their shell), generates + stores the gateway client id/secret both sides, redeploys the console, does the Production move (spec §7), and runs `jira_sync.py --apply`.
- **Environment note:** the gateway `agent-network-shared-gw` (id `12cb93d2-8d59-449d-a75d-55a8b4c3515e`) is currently in **Design**. The Production move (spec §7) is an operator prerequisite for the *production* build but does NOT block authoring any deliverable in this plan — the descriptors and lab-side code are environment-independent.

---

## File Structure

**Lab-side (Claude authors, unit-tested):**
- Modify `config/users.yaml` — add the `mulesoft-omni-gateway` machine caller.
- Modify `src/interop/identity.py` — `SERVICE_CLIENTS`, `_issue`, `issue_service_token`, `authenticate_client`; refactor `issue_token` onto `_issue`.
- Modify `src/console/app.py` — add public `POST /oauth/token`; add `/oauth/token` to the console `TokenAuthMiddleware` exempt list.
- Modify `src/interop/servers/wiretap.py` — `_caller_from_scope` + source precedence.
- Modify `config/targets.yaml` — add `mule-broker-a2a` target + extend the status legend with `via-fabric`.

**Fabric-side descriptors (Claude authors, compiler-validated by the operator's build):**
- Create `mulesoft/README.md`, `mulesoft/.gitignore`.
- Create `mulesoft/agent-network/exchange.json` — GAV + deploy variables (6 agent URLs + gw client id/secret).
- Create `mulesoft/agent-network/agent-network.yaml` — `registry.agents.*` (6) + `context.connections.*` (6, oauth2-cc) + `brokers.broker1`.
- Create `mulesoft/agent-network/brokers/broker1.agent` — minimal 1-hop AgentScript.

**Tests:**
- Modify `tests/unit/test_identity.py` — machine-caller + mint tests.
- Create `tests/unit/test_oauth_token_endpoint.py` — the console route.
- Create `tests/unit/test_wiretap_attribution.py` — scope-derived source.
- Create `tests/unit/test_mulesoft_descriptors.py` — YAML shape assertions.
- Create `scripts/mule_broker_smoke.py` — deterministic post-deploy proof.
- Create `tests/live/test_mule_broker.py` — live-marked trace assertion (run post-deploy).

**Docs (same change):**
- Modify `plan/09-deployment-map.md`, `plan/07-workstreams.md`, `config/diagrams.yaml`, `src/console/static/index.html`.

---

## Task 1: Machine caller identity in `config/users.yaml`

**Files:**
- Modify: `config/users.yaml`
- Test: `tests/unit/test_identity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a directory user `mulesoft-omni-gateway` with `role: machine`. Later tasks (`issue_service_token`, `authenticate_client`) mint for this exact subject string.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_identity.py`:

```python
def test_machine_caller_is_in_directory_but_has_no_password_login(monkeypatch):
    from interop import identity

    users = identity.load_users()
    assert "mulesoft-omni-gateway" in users
    assert users["mulesoft-omni-gateway"]["role"] == "machine"

    # A machine caller has NO console password (ROLE_PASSWORD_ENVS has no
    # 'machine' key), so /api/login's authenticate() must fail closed for it
    # even if a password is supplied.
    monkeypatch.delenv("A2ALAB_OPERATOR_PASSWORD", raising=False)
    with pytest.raises(ValueError):
        identity.authenticate("mulesoft-omni-gateway", "anything")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_identity.py::test_machine_caller_is_in_directory_but_has_no_password_login -v`
Expected: FAIL — `assert "mulesoft-omni-gateway" in users` fails (KeyError-style assertion), user not yet added.

- [ ] **Step 3: Add the machine caller**

In `config/users.yaml`, extend the role comment block and add the user. After the `viewer` bullet in the header comment, add:

```yaml
#   machine  — a non-human client-credentials caller (WS10 SP1). NO console
#              password (identity.ROLE_PASSWORD_ENVS has no 'machine' key), so
#              it can never sign in through /api/login; it obtains a
#              SHORT-LIVED lab JWT only through the client-credentials mint
#              (identity.issue_service_token, exposed at console /oauth/token).
```

And under `users:`, after `vic`:

```yaml
  mulesoft-omni-gateway:
    name: MuleSoft Omni Gateway
    role: machine
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_identity.py::test_machine_caller_is_in_directory_but_has_no_password_login -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/users.yaml tests/unit/test_identity.py
git commit -m "WS10 SP1: add mulesoft-omni-gateway machine caller identity"
```

---

## Task 2: Client-credentials mint path in `identity.py`

**Files:**
- Modify: `src/interop/identity.py`
- Test: `tests/unit/test_identity.py`

**Interfaces:**
- Consumes: `mulesoft-omni-gateway` from Task 1; `_private_key()`, `load_users()`, `ISSUER`, `verify_token()` (existing).
- Produces:
  - `SERVICE_TTL_ENV = "A2ALAB_SERVICE_JWT_TTL_S"`, `DEFAULT_SERVICE_TTL_S = 300`
  - `SERVICE_CLIENTS: dict[str, tuple[str, str]]` — subject → (client-id env, client-secret env)
  - `issue_service_token(subject: str, ttl: int | None = None, users: dict | None = None) -> str`
  - `authenticate_client(client_id: str, client_secret: str) -> str` (returns the subject; raises `ValueError` on any failure)

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_identity.py`:

```python
def test_issue_service_token_mints_short_lived_machine_jwt(monkeypatch):
    from interop import identity

    monkeypatch.setenv("A2ALAB_SERVICE_JWT_TTL_S", "120")
    token = identity.issue_service_token("mulesoft-omni-gateway")
    claims = identity.verify_token(token)
    assert claims is not None
    assert claims["iss"] == "a2a-lab"
    assert claims["sub"] == "mulesoft-omni-gateway"
    assert claims["role"] == "machine"
    assert claims["exp"] - claims["iat"] == 120


def test_issue_service_token_rejects_unknown_subject():
    from interop import identity

    with pytest.raises(ValueError):
        identity.issue_service_token("nobody")


def test_authenticate_client_accepts_matching_creds_and_returns_subject(monkeypatch):
    from interop import identity

    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_ID", "gw-id-123")
    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_SECRET", "gw-secret-abc")
    assert (
        identity.authenticate_client("gw-id-123", "gw-secret-abc")
        == "mulesoft-omni-gateway"
    )


def test_authenticate_client_rejects_bad_creds(monkeypatch):
    from interop import identity

    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_ID", "gw-id-123")
    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_SECRET", "gw-secret-abc")
    with pytest.raises(ValueError):
        identity.authenticate_client("gw-id-123", "wrong")
    with pytest.raises(ValueError):
        identity.authenticate_client("", "")


def test_authenticate_client_fails_closed_when_unconfigured(monkeypatch):
    from interop import identity

    monkeypatch.delenv("A2ALAB_MULE_GW_CLIENT_ID", raising=False)
    monkeypatch.delenv("A2ALAB_MULE_GW_CLIENT_SECRET", raising=False)
    with pytest.raises(ValueError):
        identity.authenticate_client("gw-id-123", "gw-secret-abc")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_identity.py -k "service_token or authenticate_client" -v`
Expected: FAIL with `AttributeError: module 'interop.identity' has no attribute 'issue_service_token'` (and `authenticate_client`).

- [ ] **Step 3: Implement the mint path**

In `src/interop/identity.py`, add after the `DEFAULT_TTL_S` constant (near line 34):

```python
SERVICE_TTL_ENV = "A2ALAB_SERVICE_JWT_TTL_S"
DEFAULT_SERVICE_TTL_S = 300  # short: the machine caller refreshes (WS10 spec §4.7)

# Machine client-credentials callers (WS10 SP1). Maps the lab subject to mint
# to the env vars holding its expected client_id / client_secret. A machine
# caller has NO console password (ROLE_PASSWORD_ENVS has no 'machine' key), so
# it can never be obtained through /api/login — only through the client-creds
# mint (issue_service_token) below. Add a row to register another machine caller.
SERVICE_CLIENTS: dict[str, tuple[str, str]] = {
    "mulesoft-omni-gateway": ("A2ALAB_MULE_GW_CLIENT_ID", "A2ALAB_MULE_GW_CLIENT_SECRET"),
}
```

Refactor `issue_token` to share a private minter. Replace the existing `issue_token` body (lines ~195-211) with:

```python
def _issue(username: str, ttl: int, directory: dict[str, dict]) -> str:
    """Sign a lab JWT for a directory user with an explicit TTL: the single
    place claims are constructed, so the human (issue_token) and machine
    (issue_service_token) paths cannot drift."""
    entry = directory.get(username)
    if entry is None:
        raise ValueError(f"unknown user '{username}' — see config/users.yaml")
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": username,
        "name": entry.get("name") or username,
        "role": entry.get("role") or "viewer",
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(claims, _private_key(), algorithm="RS256")


def issue_token(username: str, users: dict[str, dict] | None = None) -> str:
    """A lab JWT for a directory user: {sub, name, role, iss, iat, exp}."""
    directory = users if users is not None else load_users()
    ttl = int(os.environ.get(TTL_ENV, str(DEFAULT_TTL_S)))
    return _issue(username, ttl, directory)


def issue_service_token(
    subject: str, ttl: int | None = None, users: dict[str, dict] | None = None
) -> str:
    """A SHORT-LIVED lab JWT for a MACHINE caller — no human password, no
    /api/login. Safe to keep short precisely because the caller refreshes
    (WS10 spec §4.7). Same claim shape as issue_token; only the TTL differs."""
    directory = users if users is not None else load_users()
    if ttl is None:
        ttl = int(os.environ.get(SERVICE_TTL_ENV, str(DEFAULT_SERVICE_TTL_S)))
    return _issue(subject, ttl, directory)


def authenticate_client(client_id: str, client_secret: str) -> str:
    """Validate a machine client-credentials pair against SERVICE_CLIENTS and
    return the lab subject to mint for. Fail CLOSED: missing input, an
    unconfigured client (either env unset), or a mismatch all raise ValueError.
    Both comparisons are constant-time (hmac.compare_digest)."""
    import hmac

    if not client_id or not client_secret:
        raise ValueError("missing client credentials")
    for subject, (id_env, secret_env) in SERVICE_CLIENTS.items():
        expected_id = os.environ.get(id_env, "")
        expected_secret = os.environ.get(secret_env, "")
        if not expected_id or not expected_secret:
            continue  # not configured on this deployment — cannot match
        if hmac.compare_digest(client_id, expected_id) and hmac.compare_digest(
            client_secret, expected_secret
        ):
            return subject
    raise ValueError("unknown or invalid client credentials")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_identity.py -v`
Expected: PASS (all existing identity tests plus the five new ones — the `issue_token` refactor must not regress existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/interop/identity.py tests/unit/test_identity.py
git commit -m "WS10 SP1: client-credentials mint (authenticate_client + issue_service_token)"
```

---

## Task 3: Public `POST /oauth/token` endpoint on the console

**Files:**
- Modify: `src/console/app.py` (add the route near `/api/login` at ~line 1788; add `/oauth/token` to `exempt_paths` at ~line 4657)
- Test: `tests/unit/test_oauth_token_endpoint.py` (create)

**Interfaces:**
- Consumes: `identity.authenticate_client`, `identity.issue_service_token`, `identity.SERVICE_TTL_ENV`, `identity.DEFAULT_SERVICE_TTL_S` (Task 2); `identity.verify_token`, `identity.public_key` (existing).
- Produces: `POST /oauth/token` returning `{"access_token": <jwt>, "token_type": "Bearer", "expires_in": <int>}`; 400 on wrong grant_type; 401 on bad/absent creds. Parsed with the stdlib (`urllib.parse.parse_qs`) — NO `python-multipart` dependency.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_oauth_token_endpoint.py`:

```python
"""Console client-credentials token endpoint (WS10 SP1). The MuleSoft Omni
Gateway POSTs form-encoded client_credentials and gets a short-lived RS256 lab
JWT for sub=mulesoft-omni-gateway."""

from fastapi.testclient import TestClient


def _client(monkeypatch):
    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_ID", "gw-id-123")
    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_SECRET", "gw-secret-abc")
    monkeypatch.setenv("A2ALAB_SERVICE_JWT_TTL_S", "300")
    from console.app import create_console_app

    return TestClient(create_console_app())


def test_oauth_token_happy_path(monkeypatch):
    from interop import identity

    client = _client(monkeypatch)
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "gw-id-123",
            "client_secret": "gw-secret-abc",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300
    claims = identity.verify_token(body["access_token"])
    assert claims is not None and claims["sub"] == "mulesoft-omni-gateway"


def test_oauth_token_bad_creds_returns_401(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "gw-id-123",
            "client_secret": "wrong",
        },
    )
    assert resp.status_code == 401


def test_oauth_token_wrong_grant_type_returns_400(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "password", "client_id": "gw-id-123", "client_secret": "gw-secret-abc"},
    )
    assert resp.status_code == 400
```

> Note: `create_console_app` is the console's app factory — confirm its exact name when implementing (grep `def create_console_app` / `def create_app` in `src/console/app.py`) and match the test to it. The route is registered inside that factory, alongside `/api/login`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_oauth_token_endpoint.py -v`
Expected: FAIL — 404 on `/oauth/token` (route not registered).

- [ ] **Step 3: Add the route**

In `src/console/app.py`, immediately after the `/api/login` route (ends ~line 1810), add:

```python
    @app.post("/oauth/token")
    async def oauth_token(request: Request):
        """Public client-credentials token endpoint (WS10 SP1). A machine
        caller (the MuleSoft Omni Gateway) POSTs form-encoded
        client_credentials; we mint a SHORT-LIVED RS256 lab JWT for the mapped
        subject. Lives on the console because the console is the only surface
        that legitimately holds the signing key (A2ALAB_JWT_PRIVATE_KEY) — the
        RS256 invariant (spec §3). Exempt from the console JWT exactly as
        /api/login is. Parsed with the stdlib to avoid a python-multipart
        dependency escaping the Docker image."""
        from urllib.parse import parse_qs

        from interop import identity

        raw = (await request.body()).decode("utf-8", errors="replace")
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        if form.get("grant_type") != "client_credentials":
            raise HTTPException(status_code=400, detail="unsupported_grant_type")
        try:
            subject = identity.authenticate_client(
                form.get("client_id", ""), form.get("client_secret", "")
            )
            token = identity.issue_service_token(subject)
        except ValueError:
            # One generic 401 — no probing which of id/secret was wrong.
            raise HTTPException(status_code=401, detail="invalid_client") from None
        ttl = int(
            os.environ.get(identity.SERVICE_TTL_ENV, str(identity.DEFAULT_SERVICE_TTL_S))
        )
        return {"access_token": token, "token_type": "Bearer", "expires_in": ttl}
```

- [ ] **Step 4: Add `/oauth/token` to the console exempt list**

In `src/console/app.py`, in the `TokenAuthMiddleware(...)` call (~line 4657), add `"/oauth/token"` to `exempt_paths` with a comment matching the neighbours:

```python
            "/api/login",
            # WS10 SP1: the gateway's client-credentials token fetch is
            # unauthenticated-by-middleware (it IS the credential exchange),
            # exactly like /api/login. authenticate_client validates the gw
            # client id/secret strictly and fails closed; the route mints only
            # a short-lived machine JWT (sub=mulesoft-omni-gateway), never a
            # human session.
            "/oauth/token",
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_oauth_token_endpoint.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Confirm no new image dependency + smoke the route in the image**

Verify the route pulls in no un-`COPY`'d path or new dependency:

```bash
grep -nE "import|open\(|Path\(|read_text|os.environ" src/console/app.py | grep -i oauth  # sanity only
grep -n "python-multipart\|multipart" pyproject.toml deploy/console/Dockerfile   # expect: not required by this route
```

Build and hit the route in the container (per CLAUDE.md "smoke-test the image locally"):

```bash
docker build -f deploy/console/Dockerfile -t a2alab-console-smoke .
docker run --rm -e A2ALAB_MULE_GW_CLIENT_ID=x -e A2ALAB_MULE_GW_CLIENT_SECRET=y -p 8299:8200 a2alab-console-smoke &
sleep 4
curl -s -X POST localhost:8299/oauth/token -d 'grant_type=client_credentials&client_id=x&client_secret=y'
# Expect: {"access_token":"eyJ...","token_type":"Bearer","expires_in":300}
```

Expected: a JWT is returned (the container has the identity module and the keypair path; a fresh in-container keypair is fine for the smoke — the real deploy injects `A2ALAB_JWT_PRIVATE_KEY`).

- [ ] **Step 7: Commit**

```bash
git add src/console/app.py tests/unit/test_oauth_token_endpoint.py
git commit -m "WS10 SP1: public /oauth/token client-credentials endpoint on the console"
```

---

## Task 4: Trace attribution in `WireTapMiddleware`

**Files:**
- Modify: `src/interop/servers/wiretap.py`
- Test: `tests/unit/test_wiretap_attribution.py` (create)

**Interfaces:**
- Consumes: `scope["state"]["lab_user"]` (set by `TokenAuthMiddleware` on a verified lab JWT — `src/interop/servers/auth.py:99`); existing `_extract_caller(body)`.
- Produces: `_caller_from_scope(scope) -> str | None`; the recorded `TraceEvent.source` becomes `_caller_from_scope(scope) or _extract_caller(body) or "remote-caller"`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_wiretap_attribution.py`:

```python
"""WS10 SP1: the A2A wiretap attributes an inbound hop to the verified lab
caller when the auth middleware stashed one on the ASGI scope."""

import asyncio
import json

from interop.servers.wiretap import WireTapMiddleware

_ENVELOPE = json.dumps(
    {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "metadata": {"trace_id": "t-attr"},
                "parts": [{"text": "hello"}],
            }
        },
    }
).encode()


async def _ok_app(scope, receive, send):
    while True:
        m = await receive()
        if not m.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


def _drive(scope):
    async def receive():
        return {"type": "http.request", "body": _ENVELOPE, "more_body": False}

    async def send(_m):
        pass

    mw = WireTapMiddleware(_ok_app, protocol="a2a", service="claude-a2a")
    asyncio.run(mw(scope, receive, send))


def _recorded_source(isolated_traces, trace_id):
    events = [
        json.loads(line)
        for f in isolated_traces.glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    return [e for e in events if e["trace_id"] == trace_id][0]["source"]


def test_verified_lab_user_becomes_trace_source(isolated_traces):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "state": {"lab_user": {"sub": "mulesoft-omni-gateway", "role": "machine"}},
    }
    _drive(scope)
    assert _recorded_source(isolated_traces, "t-attr") == "mulesoft-omni-gateway"


def test_no_lab_user_falls_back_to_remote_caller(isolated_traces):
    scope = {"type": "http", "method": "POST", "path": "/", "headers": []}
    _drive(scope)
    # No verified caller and no delegation rider in the body → unchanged behaviour.
    assert _recorded_source(isolated_traces, "t-attr") == "remote-caller"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_wiretap_attribution.py -v`
Expected: `test_verified_lab_user_becomes_trace_source` FAILS (source is `"remote-caller"`, not the sub); the fallback test PASSES already.

- [ ] **Step 3: Implement scope-derived attribution**

In `src/interop/servers/wiretap.py`, add after `_extract_caller` (after line 49):

```python
def _caller_from_scope(scope) -> str | None:
    """The authenticated caller from a verified lab JWT, if TokenAuthMiddleware
    stashed one on this scope (scope['state']['lab_user'] on a valid
    iss=a2a-lab token, auth.py:99). Its `sub` is the strongest source signal —
    it is cryptographically verified — so it outranks the body-derived
    delegation caller. Non-regressive: today's callers present the shared
    A2ALAB_TOKEN or cloud-IAM bearers, so lab_user is unset for them."""
    state = scope.get("state") if isinstance(scope, dict) else None
    if isinstance(state, dict):
        lab_user = state.get("lab_user")
        if isinstance(lab_user, dict) and lab_user.get("sub"):
            return str(lab_user["sub"])
    return None
```

Then in `__call__`, change the `source=` line inside the `TraceEvent(...)` (line 157) from:

```python
                        source=_extract_caller(body) or "remote-caller",
```

to:

```python
                        source=_caller_from_scope(scope)
                        or _extract_caller(body)
                        or "remote-caller",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_wiretap_attribution.py -v`
Expected: PASS (both).

- [ ] **Step 5: Guard against regressions in the existing A2A/wiretap suite**

Run: `uv run pytest tests/unit/test_models_and_trace.py tests/unit/test_auth.py -v && uv run pytest tests/e2e/test_loopback.py -v`
Expected: PASS. If any test asserted a specific `source` for a JWT-authenticated call, reconcile it to the new (correct) attributed value — the shared-token and IAM-bearer paths are unaffected by construction.

- [ ] **Step 6: Commit**

```bash
git add src/interop/servers/wiretap.py tests/unit/test_wiretap_attribution.py
git commit -m "WS10 SP1: attribute A2A hops to the verified lab caller in the wiretap"
```

---

## Task 5: The `mulesoft/` descriptor set

**Files:**
- Create: `mulesoft/README.md`, `mulesoft/.gitignore`
- Create: `mulesoft/agent-network/exchange.json`, `mulesoft/agent-network/agent-network.yaml`, `mulesoft/agent-network/brokers/broker1.agent`
- Test: `tests/unit/test_mulesoft_descriptors.py` (create)

**Interfaces:**
- Consumes: the console token URL `https://console-lab.agenticthings.com/oauth/token` (Task 3); the six bearer-auth face URLs (deploy variables).
- Produces: a committed v2 `agentic-network` project. `agent-network.yaml` has `registry.agents.{claude,openai,strands,guide,agentforce,langgraph}` and `context.connections.{...}Conn` (6, `kind: a2a`, `authentication.kind: oauth2-client-credentials`), plus `brokers.broker1` referencing `./brokers/broker1.agent`.

**Grounding:** all content below is authored from the plugin's own canonical templates at `…/mulesoft-anypoint-cli-agent-fabric-plugin/templates/agentic-network/*.template` and the verified schema at `…/dist/types/agent-network-v2.d.ts` (spec §3, §4.7). `agentNetwork: 2.0.0`, `info.label`/`info.version`, `broker.kind: AgentScript`, and the `secret: true` deploy-variable mechanism are taken verbatim from those templates.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mulesoft_descriptors.py`:

```python
"""WS10 SP1: shape assertions on the committed agentic-network descriptors.
The AgentScript broker is compiler-validated by `agent-network project build`
(operator-run), NOT here — this test asserts only the deterministic YAML/JSON
shape of what we author."""

import json
from pathlib import Path

import yaml

ROOT = Path("mulesoft/agent-network")
SIX = {"claude", "openai", "strands", "guide", "agentforce", "langgraph"}
TOKEN_URL = "https://console-lab.agenticthings.com/oauth/token"


def _network():
    return yaml.safe_load((ROOT / "agent-network.yaml").read_text())


def test_registry_has_the_six_faces():
    net = _network()
    assert set(net["registry"]["agents"]) == SIX


def test_every_connection_is_a2a_oauth2_client_credentials_to_the_console():
    net = _network()
    conns = net["context"]["connections"]
    assert {c[: -len("Conn")] for c in conns} == SIX  # claudeConn, openaiConn, …
    for name, conn in conns.items():
        assert conn["kind"] == "a2a", name
        auth = conn["authentication"]
        assert auth["kind"] == "oauth2-client-credentials", name
        assert auth["token"]["url"] == TOKEN_URL, name
        assert auth["token"]["bodyEncoding"] == "form", name
        # Secrets arrive as deploy variables, never literals.
        assert auth["clientId"] == "${gwClientId}", name
        assert auth["clientSecret"] == "${gwClientSecret}", name


def test_broker_references_the_agentscript_source():
    net = _network()
    assert net["brokers"]["broker1"]["kind"] == "AgentScript"
    assert net["brokers"]["broker1"]["implementation"] == "./brokers/broker1.agent"
    assert (ROOT / "brokers" / "broker1.agent").exists()


def test_exchange_declares_gw_secret_as_secured_variable_and_no_literals():
    ex = json.loads((ROOT / "exchange.json").read_text())
    variables = ex["metadata"]["variables"]
    assert set(variables) == SIX | {"gwClientId", "gwClientSecret"}
    assert variables["gwClientSecret"]["secret"] is True
    # No secret value is committed — defaults are empty.
    assert variables["gwClientSecret"].get("default", "") == ""
    assert variables["gwClientId"].get("default", "") == ""


def test_no_account_identifiers_in_descriptors():
    # Region-only hostnames are fine; an org id / BG name must never appear.
    blob = " ".join(
        p.read_text() for p in ROOT.rglob("*") if p.is_file()
    )
    assert "00b44e97" not in blob  # the MuleSoft root BG id (auto-memory)
    assert "salesforce-5782" not in blob  # the org domain
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mulesoft_descriptors.py -v`
Expected: FAIL — `mulesoft/agent-network/agent-network.yaml` does not exist.

- [ ] **Step 3: Author `mulesoft/agent-network/agent-network.yaml`**

The six agents share one card shape; `<id>`/`<Id>`/`<platform>` vary per row (claude→claude, openai→openai, strands→strands, guide→guide, agentforce→agentforce, langgraph→langgraph). Write all six out in full (do not abbreviate in the file):

```yaml
# A2A Interop Lab — Agent Fabric agent-network (WS10 SP1 walking skeleton).
# v2 agentic-network format (agentNetwork 2.0.0). Authored from the AF plugin's
# own templates + agent-network-v2.d.ts. Registers the lab's six bearer-auth
# hosted A2A faces and a single-hop broker that consults one of them. Agent
# URLs and the gateway client id/secret arrive as deploy variables
# (exchange.json) — no endpoint or secret is committed here.
agentNetwork: 2.0.0
info:
  label: a2a-interop-lab-fabric
  version: v1
registry:
  agents:
    claude:
      info:
        label: Claude research agent
      metadata:
        platform: Anthropic
        interfaces:
          a2a:
            card:
              name: claude-research
              description: Claude research agent (managed/sdk backend) — lab face.
              version: 1.0.0
              capabilities:
                streaming: false
                pushNotifications: false
              defaultInputModes: [text/plain]
              defaultOutputModes: [text/plain]
              skills:
                - id: ask
                  name: Ask Claude
                  description: Research question answered by the Claude face.
                  tags: [research, a2a-interop-lab]
    openai:
      info:
        label: OpenAI research agent
      metadata:
        platform: OpenAI
        interfaces:
          a2a:
            card:
              name: openai-research
              description: OpenAI agents-sdk research agent — lab face.
              version: 1.0.0
              capabilities:
                streaming: false
                pushNotifications: false
              defaultInputModes: [text/plain]
              defaultOutputModes: [text/plain]
              skills:
                - id: ask
                  name: Ask OpenAI
                  description: Research question answered by the OpenAI face.
                  tags: [research, a2a-interop-lab]
    strands:
      info:
        label: Strands research agent
      metadata:
        platform: Strands
        interfaces:
          a2a:
            card:
              name: strands-research
              description: AWS Strands research agent — lab face.
              version: 1.0.0
              capabilities:
                streaming: false
                pushNotifications: false
              defaultInputModes: [text/plain]
              defaultOutputModes: [text/plain]
              skills:
                - id: ask
                  name: Ask Strands
                  description: Research question answered by the Strands face.
                  tags: [research, a2a-interop-lab]
    guide:
      info:
        label: Lab Guide docent
      metadata:
        platform: Guide
        interfaces:
          a2a:
            card:
              name: lab-guide
              description: The lab's own docent agent served as a lab face.
              version: 1.0.0
              capabilities:
                streaming: false
                pushNotifications: false
              defaultInputModes: [text/plain]
              defaultOutputModes: [text/plain]
              skills:
                - id: ask
                  name: Ask the Lab Guide
                  description: Answer grounded in the lab's own docs/decisions.
                  tags: [docent, a2a-interop-lab]
    agentforce:
      info:
        label: Agentforce (via hosted A2A shim)
      metadata:
        platform: Agentforce
        interfaces:
          a2a:
            card:
              name: agentforce-a2a
              description: Salesforce Agentforce, reached via the hosted A2A shim (D28).
              version: 1.0.0
              capabilities:
                streaming: false
                pushNotifications: false
              defaultInputModes: [text/plain]
              defaultOutputModes: [text/plain]
              skills:
                - id: ask
                  name: Ask Agentforce
                  description: Salesforce Agent API answer, translated by the shim.
                  tags: [salesforce, a2a-interop-lab]
    langgraph:
      info:
        label: LangGraph research agent (Heroku)
      metadata:
        platform: LangGraph
        interfaces:
          a2a:
            card:
              name: langgraph-research
              description: LangGraph research agent hosted on Heroku — lab face.
              version: 1.0.0
              capabilities:
                streaming: false
                pushNotifications: false
              defaultInputModes: [text/plain]
              defaultOutputModes: [text/plain]
              skills:
                - id: ask
                  name: Ask LangGraph
                  description: Research question answered by the LangGraph face.
                  tags: [research, a2a-interop-lab]
context:
  connections:
    claudeConn:
      kind: a2a
      ref:
        name: claude
      url: ${claude.url}
      authentication: &gwOauth
        kind: oauth2-client-credentials
        clientId: ${gwClientId}
        clientSecret: ${gwClientSecret}
        token:
          url: https://console-lab.agenticthings.com/oauth/token
          bodyEncoding: form
          timeout: 10
        scopes: [a2a.invoke]
    openaiConn:
      kind: a2a
      ref:
        name: openai
      url: ${openai.url}
      authentication: *gwOauth
    strandsConn:
      kind: a2a
      ref:
        name: strands
      url: ${strands.url}
      authentication: *gwOauth
    guideConn:
      kind: a2a
      ref:
        name: guide
      url: ${guide.url}
      authentication: *gwOauth
    agentforceConn:
      kind: a2a
      ref:
        name: agentforce
      url: ${agentforce.url}
      authentication: *gwOauth
    langgraphConn:
      kind: a2a
      ref:
        name: langgraph
      url: ${langgraph.url}
      authentication: *gwOauth
brokers:
  broker1:
    kind: AgentScript
    info:
      label: Lab broker (SP1)
    implementation: ./brokers/broker1.agent
    interfaces:
      a2a:
        card:
          name: lab-broker
          description: A2A Interop Lab broker — SP1 single-hop walking skeleton.
          version: 1.0.0
          capabilities:
            streaming: false
            pushNotifications: false
          defaultInputModes: [text/plain]
          defaultOutputModes: [text/plain]
          skills:
            - id: consult
              name: Consult a lab face
              description: Forwards the question to one registered lab face and returns its answer.
              tags: [broker, a2a-interop-lab]
```

> The YAML anchor `&gwOauth` / `*gwOauth` keeps the six identical auth blocks DRY. If the operator's `agent-network project build` rejects anchors (some loaders flatten, some don't), inline the block into all six connections during the reconcile step (Step 6) — the unit test asserts the resolved value either way.

- [ ] **Step 4: Author `mulesoft/agent-network/exchange.json`**

```json
{
  "main": "agent-network.yaml",
  "name": "a2a-interop-lab-fabric",
  "classifier": "agentic-network",
  "descriptorVersion": "1.0.0",
  "apiVersion": "1.0.0",
  "tags": ["a2a-interop-lab", "ws10"],
  "dependencies": [],
  "metadata": {
    "variables": {
      "claude":     { "url": { "description": "Claude A2A face URL", "default": "", "secret": false } },
      "openai":     { "url": { "description": "OpenAI A2A face URL", "default": "", "secret": false } },
      "strands":    { "url": { "description": "Strands A2A face URL", "default": "", "secret": false } },
      "guide":      { "url": { "description": "Lab Guide A2A face URL", "default": "", "secret": false } },
      "agentforce": { "url": { "description": "Agentforce hosted A2A shim URL", "default": "", "secret": false } },
      "langgraph":  { "url": { "description": "LangGraph (Heroku) A2A face URL", "default": "", "secret": false } },
      "gwClientId":     { "description": "Lab client-credentials client id for /oauth/token", "default": "", "secret": true },
      "gwClientSecret": { "description": "Lab client-credentials client secret for /oauth/token", "default": "", "secret": true }
    }
  }
}
```

> `groupId` / `assetId` / `version` are supplied by the operator at `agent-network project create`/`publish` time (they encode the org id, an account identifier that must NOT be committed — that is why the template's `{{groupId}}` is left for the operator, not written here). The reconcile step (Step 6) confirms whether the toolchain requires them present-but-empty or injected at publish.

- [ ] **Step 5: Author `mulesoft/agent-network/brokers/broker1.agent`**

Minimal single-hop AgentScript (no LLM, no fan-out) — trigger → executor(one a2a action) → echo:

```
# @dialect: AGENTFABRIC=1.1
config:
  agent_name: "Lab Broker (SP1)"

actions:
  consultClaude:
    target: "a2a://claudeConn"
    kind: "a2a:send_message"

trigger brokerTrigger:
  kind: "a2a"
  target: "brokers://broker1/a2a"
  on_message: ->
    transition to @executor.consult

executor consult:
  do: ->
    run @actions.consultClaude
      with message = @request.payload.message.parts[0].text
  on_exit: ->
    transition to @echo.done

echo done:
  kind: "a2a:status_update_event"
  state: "TASK_STATE_COMPLETED"
  message: a2a.message({
    messageId: uuid(),
    parts: [
      a2a.textPart(@executor.consult.output)
    ]
  })
```

> This is the single highest-uncertainty artifact: AgentScript's exact action-input binding (`with message = …`) and `@request.payload` reference are taken from the plugin's `broker1.agent.template` but are only *validated* by `agent-network project build` (operator, Step 6 / Task 7). Treat a build error here as expected first-pass feedback (spec §8 "AF build toolchain"), not a plan defect — adjust the AgentScript to the compiler's message and re-run. Keep it a single deterministic hop.

- [ ] **Step 6: Author `mulesoft/README.md` and `mulesoft/.gitignore`, then reconcile against a scaffold**

`mulesoft/.gitignore`:

```
# Generated build output (Maven/AgentGraph target). Authored descriptors are
# committed; build artifacts are not — mirrors salesforce/.
target/
```

`mulesoft/README.md` — cover: what this is (WS10 SP1 walking skeleton), the file layout, the lifecycle commands (`agent-network project create|build|publish|deploy --gateway agent-network-shared-gw`), the operator-vs-Claude division (spec §4.9), the deploy variables (`--property <id>.url:…`, `gwClientId`/`gwClientSecret` as secured variables), and that the AgentScript is compiler-validated. Cite `D28`, `plan/15-mulesoft-agent-fabric-gateway-blocker.md`, and the spec so the console doc-chips linkify.

**Reconcile step (operator-assisted, one-time):** before the first publish, the operator runs `anypoint-cli-v4 agent-network project create` in a throwaway dir to emit the canonical scaffold; Claude diffs the authored `exchange.json` against it to lock the `groupId`/`assetId`/`version`/`main` conventions and the variable-interpolation syntax, and confirms the YAML-anchor handling from Step 3. Adjust the committed descriptors to match, keeping the six-agent / oauth2-cc shape the unit test guards.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mulesoft_descriptors.py -v`
Expected: PASS (all five).

- [ ] **Step 8: Lint the repo files (descriptors are not Python, so only the test is linted)**

Run: `uv run ruff check tests/unit/test_mulesoft_descriptors.py && uv run ruff format tests/unit/test_mulesoft_descriptors.py`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add mulesoft/ tests/unit/test_mulesoft_descriptors.py
git commit -m "WS10 SP1: agentic-network descriptors (6 faces, oauth2-cc, 1-hop broker)"
```

---

## Task 6: Expose the broker as a lab console target

**Files:**
- Modify: `config/targets.yaml`
- Test: `tests/unit/test_mulesoft_descriptors.py` (extend — the registry-load assertion lives with the other WS10 shape tests)

**Interfaces:**
- Consumes: `interop.registry.Registry.load()` (existing; `status` is a free string, no enum — confirmed).
- Produces: a target `mule-broker-a2a` (`platform: mulesoft`, `protocol: a2a`, `status: via-fabric`, `endpoint: ${A2ALAB_MULE_BROKER_URL}`), and a `via-fabric` entry in the status legend comment.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_mulesoft_descriptors.py`:

```python
def test_broker_target_registered_as_via_fabric():
    from interop.registry import Registry

    reg = Registry.load()
    target = reg.get("mule-broker-a2a")
    assert target.protocol == "a2a"
    assert target.platform == "mulesoft"
    assert target.status == "via-fabric"
```

> Confirm `Registry.get`'s exact accessor when implementing (grep `def get` in `src/interop/registry.py`); match the test to the real method name/shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mulesoft_descriptors.py::test_broker_target_registered_as_via_fabric -v`
Expected: FAIL — no such target (KeyError / lookup error).

- [ ] **Step 3: Add the target and extend the legend**

In `config/targets.yaml`, extend the status legend comment (top of file):

```yaml
#   via-fabric   WS10: routed THROUGH a MuleSoft Agent Fabric broker/Omni Gateway
```

Then add the target (near the other hosted A2A twins):

```yaml
  # ---- MuleSoft Agent Fabric broker (WS10 SP1). The broker's gateway A2A
  # ingress, driven by the console's existing Run button as an ordinary A2A
  # target — the payoff of the protocol-generic client. The broker's egress
  # back to the faces authenticates as mulesoft-omni-gateway via the
  # gateway-native oauth2-client-credentials token fetch (spec §4.7); this
  # target is the INGRESS the console calls. endpoint is set post-deploy to the
  # gateway ingress URL. Ingress auth is a confirm-at-deploy item (spec §8):
  # fill `auth` once the broker's inbound policy is known.
  mule-broker-a2a:
    platform: mulesoft
    protocol: a2a
    endpoint: ${A2ALAB_MULE_BROKER_URL}
    status: via-fabric
    auth: {}
    options: {protocol_version: "1.0", timeout: 65}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_mulesoft_descriptors.py::test_broker_target_registered_as_via_fabric -v`
Expected: PASS. Also run the full targets/registry suite to catch schema surprises: `uv run pytest -k "registry or targets" -v`.

- [ ] **Step 5: Commit**

```bash
git add config/targets.yaml tests/unit/test_mulesoft_descriptors.py
git commit -m "WS10 SP1: register mule-broker-a2a console target (via-fabric)"
```

---

## Task 7: Deterministic + human proof (post-deploy)

**Files:**
- Create: `scripts/mule_broker_smoke.py`
- Create: `tests/live/test_mule_broker.py`

**Interfaces:**
- Consumes: `interop.registry.Registry` / the A2A client resolving `mule-broker-a2a` (Task 6); the trace layer (`interop.trace`); `mulesoft-omni-gateway` attribution (Task 4).
- Produces: a CLI smoke that calls the deployed broker and prints the answer + the attributed trace; a `@pytest.mark.live` test asserting the hop is present and attributed.

> These artifacts are authored now but only *run green* after the operator has published + deployed the broker (Task 5 reconcile → build → publish → deploy) and set `A2ALAB_MULE_BROKER_URL` + the gateway client id/secret. Authoring them now is the TDD contract for "done"; the run is the post-deploy verification checklist below.

- [ ] **Step 1: Write the live-marked test**

Create `tests/live/test_mule_broker.py`:

```python
"""WS10 SP1 proof (live). Calls the DEPLOYED MuleSoft broker over A2A and
asserts the lab trace shows the broker→face hop attributed to
mulesoft-omni-gateway. Deselected by default; run with `-m live` once the
broker is deployed and A2ALAB_MULE_BROKER_URL is set.

Attribution is read back from the CONSOLE API, not a local file: the
broker→face hop is recorded by the HOSTED face's wiretap into the hosted trace
store (Aurora), so only the console can see it. /api/traces/{id} is the
windowless lookup (no 6h window — see the traces-need-windowless-lookup note)."""

import os

import httpx
import pytest

from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_broker_consults_claude_face_and_attributes_the_hop():
    if not os.environ.get("A2ALAB_MULE_BROKER_URL"):
        pytest.skip("A2ALAB_MULE_BROKER_URL unset — broker not deployed")
    reg = Registry.load()
    client = reg.client_for("mule-broker-a2a")
    trace_id = new_trace_id()
    resp = await client.ask(
        AgentRequest(message="In one sentence, what is A2A?", trace_id=trace_id)
    )
    assert resp.text.strip()

    # The broker→face egress must appear in the hosted trace, attributed to the
    # gateway's machine identity (Task 4). Read it back via the console's
    # windowless per-trace lookup with a persona JWT.
    console = os.environ["A2ALAB_CONSOLE_BASE"]  # e.g. https://console-lab.agenticthings.com
    token = os.environ["A2ALAB_CONSOLE_JWT"]     # a persona JWT from /api/login
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get(
            f"{console}/api/traces/{trace_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        events = r.json().get("events", r.json())
    assert any(e.get("source") == "mulesoft-omni-gateway" for e in events), events
```

> Confirm the console per-trace route shape (`/api/traces/{id}` and its JSON envelope) and the A2A client accessor against the running console/`src/interop/registry.py` when implementing. `A2ALAB_CONSOLE_BASE` / `A2ALAB_CONSOLE_JWT` are set by the operator for this live run alongside `A2ALAB_MULE_BROKER_URL`.

- [ ] **Step 2: Write the smoke script**

Create `scripts/mule_broker_smoke.py`:

```python
"""WS10 SP1 walking-skeleton smoke: call the deployed MuleSoft broker over A2A
and print its answer + the attributed trace. Mirrors scripts/sf_smoke.py in
spirit (a go/no-go against a real deployment). Run:

    A2ALAB_MULE_BROKER_URL=https://<gateway-ingress>/... \\
      uv run python scripts/mule_broker_smoke.py "what is A2A?"
"""

from __future__ import annotations

import asyncio
import os
import sys

from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry


async def main() -> int:
    if not os.environ.get("A2ALAB_MULE_BROKER_URL"):
        print("A2ALAB_MULE_BROKER_URL unset — deploy the broker first", file=sys.stderr)
        return 2
    question = sys.argv[1] if len(sys.argv) > 1 else "In one sentence, what is A2A?"
    reg = Registry.load()
    client = reg.client_for("mule-broker-a2a")
    trace_id = new_trace_id()
    resp = await client.ask(AgentRequest(message=question, trace_id=trace_id))
    print(f"trace_id: {trace_id}")
    print(f"answer:   {resp.text}")
    return 0 if resp.text.strip() else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 3: Confirm the artifacts import cleanly (no run)**

Run: `uv run python -c "import ast; ast.parse(open('scripts/mule_broker_smoke.py').read()); ast.parse(open('tests/live/test_mule_broker.py').read())"` and `uv run ruff check scripts/mule_broker_smoke.py tests/live/test_mule_broker.py`
Expected: clean. Confirm the default (non-live) suite still collects without running the live test: `uv run pytest --collect-only -q tests/live/test_mule_broker.py` shows it deselected under the default marker expression.

- [ ] **Step 4: Commit**

```bash
git add scripts/mule_broker_smoke.py tests/live/test_mule_broker.py
git commit -m "WS10 SP1: deterministic smoke + live trace-attribution proof for the broker"
```

---

## Task 8: Docs, diagrams, and delivery record (same change)

**Files:**
- Modify: `plan/09-deployment-map.md`, `config/diagrams.yaml`, `src/console/static/index.html` (Details/diagram copy), `plan/07-workstreams.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the deployment map, diagrams, and console copy reflect the token endpoint + fabric estate; `## WS10` carries `N. ✅` item lines so `jira_sync.py` imports stories.

- [ ] **Step 1: Deployment map**

In `plan/09-deployment-map.md`, add: the console `/oauth/token` endpoint (a new inbound seam on the existing console task — note it needs no new host), the MuleSoft Omni Gateway `agent-network-shared-gw` estate box (on `cloudhub-us-east-1`, currently Design), and an **L6 code→deployment row**: `mulesoft/agent-network/ → anypoint-cli-v4 agent-network project build|publish|deploy --gateway agent-network-shared-gw → registers 6 agents + a broker on the Omni Gateway → MuleSoft CloudHub 2.0 (us-east-1)`. Add a "Why not, in one place" line: the fabric's shared-space managed gateway is forced `large` (the entitlement gate, plan/15) — that is the buy-side sizing cost SP4 quantifies.

- [ ] **Step 2: Diagrams + console copy**

Update `config/diagrams.yaml` and the `*_DIAGRAM` constants / Details panes in `src/console/static/index.html` that narrate the console's inbound surfaces to include `/oauth/token` (a public, credential-gated mint route peer to `/api/login`). Keep the console-copy claim honest: the fabric broker is deployed in Design until the operator's Production move (spec §7). Grep the component name before calling it done: `grep -rn "oauth/token\|agent-network-shared-gw\|Agent Fabric" plan/ config/diagrams.yaml src/console/static/index.html`.

- [ ] **Step 3: Delivery-record item lines**

Under `## WS10` in `plan/07-workstreams.md`, add SP1's work as numbered `N. ✅` lines matching WS1–WS3's style — one per shippable unit (machine identity; client-credentials mint; `/oauth/token`; wiretap attribution; the six-agent descriptor set + 1-hop broker; the `mule-broker-a2a` console target; the deterministic + human proof). Add the deferred SP4 "dedicated Agent Fabric console section" as an explicit open item line so it imports as a story (spec §4.10, §5).

- [ ] **Step 4: Dry-run the Jira sync (do NOT `--apply`)**

Run: `uv run python scripts/jira_sync.py`
Expected: the dry-run diff shows the new WS10 stories. Read the diff; leave `--apply` for the operator (D58 — it is an outward publish). Redeploying the console (full rebuild — `plan/` is baked in by `COPY`) is likewise the operator's step for the Project page to show the stories hosted.

- [ ] **Step 5: Commit**

```bash
git add plan/09-deployment-map.md config/diagrams.yaml src/console/static/index.html plan/07-workstreams.md
git commit -m "WS10 SP1: deployment map, diagrams, console copy, delivery-record item lines"
```

---

## Post-deploy verification checklist (proof of done — spec §4.11)

Runs after the operator has reconciled + built + published + deployed the broker (Task 5), set `A2ALAB_MULE_BROKER_URL` and the gateway client id/secret (both sides), and redeployed the console with the `/oauth/token` route:

1. **Token endpoint (live):** `curl -s -X POST https://console-lab.agenticthings.com/oauth/token -d 'grant_type=client_credentials&client_id=…&client_secret=…'` returns a JWT; verify with the lab public key that `sub=mulesoft-omni-gateway` and the TTL matches `A2ALAB_SERVICE_JWT_TTL_S`.
2. **Unit suite green:** `uv run pytest` (all Task 1–6 tests) + `uv run ruff check . && uv run ruff format .`.
3. **Deterministic proof:** `A2ALAB_MULE_BROKER_URL=… uv run python scripts/mule_broker_smoke.py` returns a real Claude-face answer; `uv run pytest -m live tests/live/test_mule_broker.py` asserts the hop is present and attributed to `mulesoft-omni-gateway`.
4. **Human proof:** drive the same call from the console **Run** button against `mule-broker-a2a`; eyeball the trace timeline (lab→broker→claude-face) in the console, and confirm read-only via the `mulesoft-platform` MCP that the six agents + broker are registered on `agent-network-shared-gw`.

---

## Self-Review

**Spec coverage (§4):**
- §4.5.1 machine caller → Task 1. §4.5.2 mint path → Task 2. §4.5.3 `/oauth/token` → Task 3. §4.6 attribution (corrected to WireTap) → Task 4. §4.3/§4.4/§4.7/§4.8 descriptors → Task 5. §4.10 broker-as-target → Task 6. §4.11 proof → Task 7 + the checklist. §4.12 out-of-scope items are excluded (no orchestration, no `registry.mcps.*`, no adk/foundry). Cross-cutting §6 → Task 8. Operator prerequisites §7 → Global Constraints + checklist. Open items §8 (token cache cadence, AF build toolchain, scope semantics, ingress auth) → surfaced in Task 5 Step 6 reconcile, Task 6 `auth: {}` note, and Task 7; `trace_id` propagation is explicitly SP2, not SP1.
- **Scope decision (§8 "scope semantics"):** `scopes: [a2a.invoke]` is authored in the connection for fidelity but the lab mint does NOT gate on it in SP1 (`issue_service_token` ignores scope). This is called out honestly rather than implying enforcement — hardening moves to SP3 per the spec. No task claims scope enforcement.

**Placeholder scan:** every code step contains real content; no "TBD"/"add error handling"/"similar to Task N". The two genuinely deploy-time unknowns (AgentScript compiler acceptance; `groupId`/`assetId` conventions) are flagged as *reconcile-against-scaffold* steps with the reason, not left as vague placeholders — the committed content is the plugin-template-grounded best candidate and the unit tests pin the invariant shape.

**Type/name consistency:** `issue_service_token`, `authenticate_client`, `SERVICE_CLIENTS`, `SERVICE_TTL_ENV`, `DEFAULT_SERVICE_TTL_S`, `_issue`, `_caller_from_scope` are defined in Tasks 2/4 and consumed with identical signatures in Task 3 and the tests. Connection names (`claudeConn`…`langgraphConn`), the `${gwClientId}`/`${gwClientSecret}` deploy vars, the `mule-broker-a2a` target name, and `A2ALAB_MULE_GW_CLIENT_ID`/`_SECRET`/`A2ALAB_MULE_BROKER_URL`/`A2ALAB_SERVICE_JWT_TTL_S` env names are used consistently across descriptor, config, code, and tests. The Python accessors are verified exact against the code (`create_console_app(registry=None)` at `src/console/app.py:1216`; `Registry.get(name)` at `registry.py:79`; `Registry.client_for(name, *, exact=False)` at `registry.py:99`). The one remaining confirm-on-implementation item is the console per-trace route shape (`/api/traces/{id}` and its JSON envelope), flagged inline in Task 7 — there is no `tests/live/` dir yet, so Task 7 creates it fresh (no pre-existing helper to reuse).
