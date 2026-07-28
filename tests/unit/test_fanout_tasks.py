"""WS11 — the durable fan-out task store.

These tests exist because the design is a reaction to a MEASUREMENT, not a
preference: D47 showed that on Lambda, work started before the response does not
progress on its own and in-memory task state is per-instance. So the properties
worth pinning are (a) submit does not do the work, (b) the state survives in a
store rather than in the process, and (c) a worker that dies leaves a terminal
row instead of a task stuck WORKING for ever.
"""

from fanout_mcp.tasks import (
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_SUBMITTED,
    TaskStore,
    run_task,
)


class FakeClient:
    """Enough SQL to hold rows in a dict, keyed the way the real table is."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def execute(self, sql: str, params: dict | None = None):
        params = params or {}
        head = sql.strip().split()[0].upper()
        if head == "INSERT":
            self.rows[params["task_id"]] = {
                "task_id": params["task_id"],
                "run_id": params["run_id"],
                "unit": params["unit"],
                "state": params["state"],
                "situation": params.get("situation", ""),
                "result": "",
                "error": "",
            }
            return []
        if head == "UPDATE":
            row = self.rows[params["task_id"]]
            row["state"] = params["state"]
            if "result" in params:
                row["result"] = params["result"] or ""
            if "error" in params:
                row["error"] = params["error"] or ""
            return []
        if "WHERE task_id" in sql and "situation" in sql:
            row = self.rows.get(params["task_id"])
            return [{"situation": row["situation"]}] if row else []
        if "WHERE task_id" in sql:
            row = self.rows.get(params["task_id"])
            return (
                [{k: row[k] for k in ("task_id", "run_id", "unit", "state", "result", "error")}]
                if row
                else []
            )
        if "WHERE run_id" in sql:
            return [
                {k: r[k] for k in ("task_id", "run_id", "unit", "state", "result", "error")}
                for r in self.rows.values()
                if r["run_id"] == params["run_id"]
            ]
        return []


def test_submit_records_the_task_without_doing_the_work():
    """The whole point of fire-then-poll: the accepting call returns with the
    work not yet started. If submit ran the leg, the gateway ceiling D41
    measured would still apply and nothing would have been gained."""
    store = TaskStore(FakeClient())
    row = store.create("run-1", "Logistics", "a supplier fire")

    assert row.state == STATE_SUBMITTED
    assert row.task_id
    assert not row.done
    # No result yet — nothing has run.
    assert store.get(row.task_id).result == ""


def test_state_lives_in_the_store_not_the_process():
    """A second TaskStore over the same backing rows must see the task. This is
    the per-instance-memory failure from D47, expressed as a test: a fresh
    'instance' has to be able to read a task it never created."""
    client = FakeClient()
    created = TaskStore(client).create("run-2", "Commercial", "port strike")

    other_instance = TaskStore(client)
    seen = other_instance.get(created.task_id)
    assert seen is not None
    assert seen.unit == "Commercial"
    assert seen.state == STATE_SUBMITTED


def test_worker_completes_the_task_and_check_sees_it():
    store = TaskStore(FakeClient())
    row = store.create("run-3", "Logistics", "a supplier fire")

    run_task(row.task_id, store, lambda unit, situation: f"{unit} answered about {situation}")

    done = store.get(row.task_id)
    assert done.state == STATE_COMPLETED
    assert done.done
    assert done.result == "Logistics answered about a supplier fire"


def test_worker_failure_is_terminal_not_stuck_working():
    """A worker that raises must leave FAILED. Anything else leaves the model
    polling a task that will never change, which is worse than an error."""
    store = TaskStore(FakeClient())
    row = store.create("run-4", "Customer Comms", "recall")

    def boom(unit, situation):
        raise RuntimeError("leg unreachable")

    run_task(row.task_id, store, boom)

    failed = store.get(row.task_id)
    assert failed.state == STATE_FAILED
    assert failed.done
    assert "leg unreachable" in failed.error


def test_run_id_joins_the_units():
    """The run id is the only thread connecting legs that run in separate
    processes — `for_run` is what lets a check tool answer 'how are my three
    units doing' in one call."""
    client = FakeClient()
    store = TaskStore(client)
    for unit in ("Logistics", "Commercial", "Customer Comms"):
        store.create("run-5", unit, "same disruption")
    store.create("other-run", "Logistics", "unrelated")

    rows = store.for_run("run-5")
    assert len(rows) == 3
    assert {r.unit for r in rows} == {"Logistics", "Commercial", "Customer Comms"}


def test_rerunning_a_finished_task_is_a_no_op():
    """Async invoke is at-least-once, so the same task id can arrive twice. The
    second delivery must not re-run the leg — that would bill a second agent
    call and could overwrite a good answer with a worse one."""
    store = TaskStore(FakeClient())
    row = store.create("run-6", "Logistics", "fire")
    calls = []

    def runner(unit, situation):
        calls.append(unit)
        return "answer"

    run_task(row.task_id, store, runner)
    run_task(row.task_id, store, runner)

    assert calls == ["Logistics"], "a redelivered task re-ran the leg"
