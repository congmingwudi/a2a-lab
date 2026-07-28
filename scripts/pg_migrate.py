"""Apply the hosted store's schema DDL as the table OWNER (D23, WS12).

    uv run python scripts/pg_migrate.py           # apply observability.pg.DDL
    uv run python scripts/pg_migrate.py --dry-run # print what would run

**Why this script exists.** `observability.pg.DDL` is the one definition of the
hosted schema, but until 2026-07-27 nothing could actually apply it to Aurora.
`ensure_schema()` had exactly one caller — `scripts/pg_backfill.py` — which
connects as `lab_writer`, and `lab.obs_briefs` is owned by the master role, so
every DDL statement failed with `must be owner of table` (SQLState 42501).
pg_backfill caught that and printed "assuming provisioned", which was true for
the CREATE TABLEs (the master had run them by hand at provisioning) and false
for anything added later.

WS12 is what surfaced it: the `kind` column was added to DDL, written by
`src/obs_mcp/tools.py`, read by the console — and never existed in Aurora. Three
layers of correct code over a column no code path could create.

So: DDL runs here, under the master secret, and nowhere else. The other two
identities keep the grants they should have — `lab_reader` reads, `lab_writer`
writes rows and cannot reshape tables.

Needs `A2ALAB_PG_MASTER_SECRET_ARN` (the RDS-managed master secret for the
cluster in `A2ALAB_PG_CLUSTER_ARN`):

    aws rds describe-db-clusters --db-cluster-identifier a2alab-obs \\
      --query 'DBClusters[0].MasterUserSecret.SecretArn' --output text
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from observability.pg import DDL as OBS_DDL  # noqa: E402
from observability.pg import SCHEMA, PgClient  # noqa: E402

MASTER_SECRET_ENV = "A2ALAB_PG_MASTER_SECRET_ARN"


def _all_ddl() -> list[str]:
    """Every schema this store holds, from the package that owns each one.

    The fan-out task table (WS11) is defined in `fanout_mcp.tasks` rather than
    alongside the observability tables, because that server ships as its own
    bundle and does not import `observability`. Collected here so there is
    still ONE command that migrates the store — the alternative is a second
    migration path nobody remembers to run, which is the D46 shape."""
    ddl = list(OBS_DDL)
    from fanout_mcp.tasks import DDL as FANOUT_DDL
    from fanout_mcp.tasks import DDL_INDEX as FANOUT_INDEX

    ddl.extend([FANOUT_DDL, FANOUT_INDEX])
    return ddl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the DDL, change nothing")
    args = ap.parse_args()
    load_dotenv()

    ddl = _all_ddl()
    if args.dry_run:
        for stmt in ddl:
            print(f"-- \n{stmt.strip()};")
        return 0

    cluster = os.environ.get("A2ALAB_PG_CLUSTER_ARN")
    master = os.environ.get(MASTER_SECRET_ENV)
    if not cluster or not master:
        print(
            f"set A2ALAB_PG_CLUSTER_ARN and {MASTER_SECRET_ENV} in .env — DDL must run as the\n"
            "table owner (the master role); lab_writer gets 'must be owner of table'."
        )
        return 1

    pg = PgClient(cluster_arn=cluster, secret_arn=master)
    who = pg.execute("SELECT current_user")[0]["current_user"]
    print(f"connected as {who}")

    applied = 0
    for stmt in ddl:
        first = " ".join(stmt.split())[:70]
        try:
            pg.execute(stmt)
            applied += 1
            print(f"  ok   {first}")
        except Exception as exc:  # noqa: BLE001 - report and continue; each stmt is independent
            print(f"  FAIL {first}\n       {type(exc).__name__}: {str(exc)[-200:]}")

    # Report the shape rather than trusting the statements: every DDL entry is
    # IF NOT EXISTS, so "ok" means "did not error", not "the column is there".
    cols = pg.execute(
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_schema = '{SCHEMA}' AND table_name = 'obs_briefs' ORDER BY 1"
    )
    print(f"{applied}/{len(ddl)} statements applied")
    print(f"{SCHEMA}.obs_briefs: {', '.join(c['column_name'] for c in cols)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
