"""AWS Lambda entrypoint for the fan-out MCP server (WS7 item 4).

Handler: fanout_mcp/lambda_entry.handler. Unlike the obs server this one is NOT
stdlib-only — each tool call makes a real cross-cloud agent call, so the bundle
carries httpx, the a2a-sdk, google-auth and azure-identity (see
deploy/fanout/build_zip.sh).

Env it needs, and why each one:

    A2ALAB_FANOUT_MCP_TOKEN     bearer auth, matching the vault static_bearer
                                credential on the Anthropic side
    A2ALAB_TARGETS_PATH         the bundled targets.yaml
    ADK_*/FOUNDRY_*/OPENAI_*    the leg endpoints. Shipping a target by NAME
                                without the ${VAR}s its endpoint expands from
                                is the deploy-manifest bug that cost the ADK
                                orchestrator three legs (7f0f625): an unset var
                                expands to "" and an empty endpoint fails as a
                                network error, so the manifest bug reads as
                                connectivity.
    A2ALAB_GCP_WORKLOAD_*       AWS -> GCP federation (interop/cloud_auth.py)
    AZURE_*                     the Entra service principal, from Secrets Manager
    A2ALAB_TRACE_SINK=dynamodb  per-leg Hops must leave the function; the local
                                jsonl sink writes to a container that is about
                                to disappear

Timeouts: two budgets, because two paths. The SYNC consult_* tools run inside an
API Gateway request and get A2ALAB_LEG_TIMEOUT_S (~25s, bounded by the gateway's
non-raisable 29s ceiling). The ASYNC submit_*/check_task worker (`_run_worker`
below) runs in its OWN self-invoke window with no gateway in front of it, so it
gets the larger A2ALAB_ASYNC_LEG_TIMEOUT_S (120s) — that is the whole point of
fire-then-poll (WS11/D47). The FUNCTION timeout must exceed the ASYNC budget, or
the worker is killed mid-leg and leaves its task stuck WORKING
(deploy/fanout/deploy_fanout.sh sets it to ASYNC + margin).
"""

from __future__ import annotations

from interop.secret_env import load_secret_env_and_log

# BEFORE anything reads os.environ. The Entra service principal and this
# server's own bearer token live in Secrets Manager (D39/F1), not in the
# function configuration, and `auth_token()` below reads one of them — so a
# later import order would silently produce an unauthenticated server with
# every business-unit agent behind it.
load_secret_env_and_log("fanout-mcp")

from fanout_mcp import SERVER_INFO  # noqa: E402
from fanout_mcp.tools import auth_token, build_registry, worker_runner  # noqa: E402
from mcp_http.http import make_lambda_handler  # noqa: E402

# Built at cold start, deliberately: the registry construction reads
# config/targets.yaml and the leg agent definitions, and doing that per request
# would pay the parse on every tool call for a value that never changes.
_mcp_handler = make_lambda_handler(build_registry(), auth_token(), SERVER_INFO)


def _run_worker(task_id: str) -> dict:
    """The async fire-then-poll worker (WS11 items 6-7).

    Reached by the SELF-INVOKE `submit_<unit>` fires (Payload
    `{"a2alab_fanout_task": <id>}`), NOT by an MCP HTTP request — this is the
    separate execution window the frozen-background version lacks (D47). It
    resolves the task's run id from the shared store so the leg's trace Hop
    correlates under the id the model threaded through submit, runs the leg to
    completion, and records COMPLETED/FAILED. Failures are recorded, not raised:
    a worker that dies silently leaves a task WORKING for ever.
    """
    from fanout_mcp.tasks import TaskStore, run_task

    store = TaskStore()
    row = store.get(task_id)
    run_id = row.run_id if row else ""
    run_task(task_id, store, worker_runner(run_id))
    return {"a2alab_fanout_task": task_id, "ok": True}


def handler(event: dict, context: object = None) -> dict:
    # Two shapes reach this function: the self-invoke worker payload, and the
    # Function-URL MCP request. The worker key is ours and unambiguous, so a
    # missing/absent key is always an MCP request.
    if isinstance(event, dict) and event.get("a2alab_fanout_task"):
        return _run_worker(str(event["a2alab_fanout_task"]))
    return _mcp_handler(event, context)
