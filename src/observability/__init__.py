"""M11: cross-platform agent execution logs, harvested and cached locally.

Each platform gets a PlatformLogSource that pulls that platform's *interior*
view of the executions the lab drove (sessions, steps, LLM calls) into the
obs_* tables of traces/lab.db — the same SQLite file the sqlite TraceSink
writes — so lab-trace ⋈ platform-log joins are plain SQL. See
plan/05-observability.md for the honest per-platform capability matrix.
"""

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore


def make_obs_store(*, force_sqlite: bool = False):
    """The ONE place that decides which observability store you get (D49).

    Postgres is the source of truth — storage, dashboard, and the analysis
    briefs the Managed Agent writes. It used to be decided in two places with
    OPPOSITE defaults: `scripts/obs_harvest.py` defaulted to sqlite while the
    console hardcoded sqlite, so the hosted harvest filled Aurora and the local
    harvest filled `traces/lab.db` while the console only ever read the latter.
    Nothing errored; the dashboard just showed the wrong copy.

    `A2ALAB_OBS_STORE=sqlite` still selects the local file, for working on a
    harvested snapshot with no AWS session. Falling back when Postgres is not
    configured is deliberate: a fresh checkout with no Aurora must still run.
    """
    import os

    if force_sqlite or os.environ.get("A2ALAB_OBS_STORE", "postgres").lower() == "sqlite":
        return ObsStore()
    from observability.pg import PgClient, PgObsStore

    if not PgClient.configured():
        return ObsStore()
    return PgObsStore()


__all__ = ["HarvestResult", "PlatformLogSource", "ObsStore", "make_obs_store"]
