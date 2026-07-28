"""Durable fan-out tasks: the store that makes submit/check honest on Lambda
(WS11 build item 3).

**Why this file has to exist, and why the obvious version is broken.** The
obvious submit/check is: start the leg in the background, return a task id, let
a later call read the result. On a function runtime that is a lie twice over.

D47 measured both halves on the lab's own hosted shim:

  1. **Lambda freezes the execution environment when the response is sent.**
     Work started before returning does not continue — it resumes only when a
     later invocation thaws that instance. Submitted then left alone for 45s, a
     task that takes ~30s was still WORKING; it finished only after twelve more
     polls. Background work is not free, it is stolen from the poller.
  2. **In-memory state is per-instance.** Nothing routes a later check to the
     instance that holds the task, so a task can become unreadable through no
     fault of the protocol.

So the state goes in Aurora, which every instance shares, and the work runs in a
SEPARATE invocation that owns its own execution window rather than in the
background of one that has already replied. `submit` writes SUBMITTED and asks
the dispatcher to start a worker; the worker runs the leg to completion and
writes the result; `check` reads the row. Nobody relies on a frozen thread.

The dispatcher is injected so the whole flow is testable without AWS: the
default one is an async Lambda self-invoke, and tests pass a fake that runs the
work inline.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol

SCHEMA = "lab"
TABLE = f"{SCHEMA}.fanout_tasks"

STATE_SUBMITTED = "SUBMITTED"
STATE_WORKING = "WORKING"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"

TERMINAL = frozenset({STATE_COMPLETED, STATE_FAILED})

# Mirrors observability.pg.DDL in spirit but lives here because the fan-out
# server is a separate bundle that does not import the observability package.
# Applied by scripts/pg_migrate.py, which runs as the table owner (D46).
DDL = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    task_id    text PRIMARY KEY,
    run_id     text NOT NULL,
    unit       text NOT NULL,
    state      text NOT NULL,
    situation  text,
    result     text,
    error      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
)"""

DDL_INDEX = f"""CREATE INDEX IF NOT EXISTS idx_fanout_tasks_run
    ON {TABLE} (run_id)"""


@dataclass
class TaskRow:
    task_id: str
    run_id: str
    unit: str
    state: str
    result: str = ""
    error: str = ""

    @property
    def done(self) -> bool:
        return self.state in TERMINAL

    def as_dict(self) -> dict[str, Any]:
        out = {"task_id": self.task_id, "unit": self.unit, "state": self.state}
        if self.result:
            out["result"] = self.result
        if self.error:
            out["error"] = self.error
        return out


class Dispatcher(Protocol):
    """Starts the worker for one task. Must NOT do the work itself — the whole
    point is that the work outlives the invocation that accepted it."""

    def __call__(self, task_id: str) -> None: ...


class TaskStore:
    """Thin SQL over whatever PgClient-shaped thing it is given.

    Deliberately not importing observability.pg at module scope: the fan-out
    bundle ships without that package, and an import error at cold start takes
    the whole MCP server down rather than just this feature.
    """

    def __init__(self, client: Any = None):
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from observability.pg import PgClient

            cluster = os.environ.get("A2ALAB_PG_CLUSTER_ARN")
            writer = os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN")
            # Writer, not from_env(): these rows are written, and the standard
            # pair is lab_reader (D46).
            self._client = (
                PgClient(cluster_arn=cluster, secret_arn=writer)
                if cluster and writer
                else PgClient.from_env()
            )
        return self._client

    def create(self, run_id: str, unit: str, situation: str) -> TaskRow:
        task_id = uuid.uuid4().hex
        self.client.execute(
            f"""INSERT INTO {TABLE} (task_id, run_id, unit, state, situation)
                VALUES (:task_id, :run_id, :unit, :state, :situation)""",
            {
                "task_id": task_id,
                "run_id": run_id,
                "unit": unit,
                "state": STATE_SUBMITTED,
                "situation": situation,
            },
        )
        return TaskRow(task_id=task_id, run_id=run_id, unit=unit, state=STATE_SUBMITTED)

    def mark_working(self, task_id: str) -> None:
        self.client.execute(
            f"UPDATE {TABLE} SET state = :state, updated_at = now() WHERE task_id = :task_id",
            {"state": STATE_WORKING, "task_id": task_id},
        )

    def finish(self, task_id: str, *, result: str = "", error: str = "") -> None:
        self.client.execute(
            f"""UPDATE {TABLE}
                SET state = :state, result = :result, error = :error, updated_at = now()
                WHERE task_id = :task_id""",
            {
                "state": STATE_FAILED if error else STATE_COMPLETED,
                "result": result,
                "error": error,
                "task_id": task_id,
            },
        )

    def get(self, task_id: str) -> TaskRow | None:
        rows = self.client.execute(
            f"""SELECT task_id, run_id, unit, state, COALESCE(result, '') AS result,
                       COALESCE(error, '') AS error
                FROM {TABLE} WHERE task_id = :task_id""",
            {"task_id": task_id},
        )
        return TaskRow(**rows[0]) if rows else None

    def for_run(self, run_id: str) -> list[TaskRow]:
        rows = self.client.execute(
            f"""SELECT task_id, run_id, unit, state, COALESCE(result, '') AS result,
                       COALESCE(error, '') AS error
                FROM {TABLE} WHERE run_id = :run_id ORDER BY created_at""",
            {"run_id": run_id},
        )
        return [TaskRow(**r) for r in rows]

    def situation_of(self, task_id: str) -> str:
        rows = self.client.execute(
            f"SELECT COALESCE(situation, '') AS situation FROM {TABLE} WHERE task_id = :task_id",
            {"task_id": task_id},
        )
        return rows[0]["situation"] if rows else ""


def lambda_dispatcher(function_name: str | None = None) -> Dispatcher:
    """Async self-invoke: `InvocationType='Event'` returns as soon as the
    payload is accepted, and the worker gets its OWN execution window with its
    own timeout — which is the property the frozen-background version lacks."""

    def dispatch(task_id: str) -> None:
        import boto3

        name = function_name or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
        if not name:
            raise RuntimeError("no Lambda function name — set AWS_LAMBDA_FUNCTION_NAME")
        boto3.client("lambda").invoke(
            FunctionName=name,
            InvocationType="Event",
            Payload=json.dumps({"a2alab_fanout_task": task_id}).encode(),
        )

    return dispatch


def run_task(task_id: str, store: TaskStore, runner: Callable[[str, str], str]) -> None:
    """The worker body. Runs in its own invocation, so it may take as long as
    the leg needs; failures are recorded as FAILED rather than raised, because
    a worker that dies silently leaves a task WORKING for ever."""
    row = store.get(task_id)
    if row is None or row.done:
        return
    store.mark_working(task_id)
    try:
        result = runner(row.unit, store.situation_of(task_id))
    except Exception as exc:  # noqa: BLE001 - the state machine is the error channel
        store.finish(task_id, error=f"{type(exc).__name__}: {exc}")
        return
    store.finish(task_id, result=result)
