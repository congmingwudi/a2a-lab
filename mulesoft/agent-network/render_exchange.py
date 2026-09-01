#!/usr/bin/env python3
"""WS10 SP1 — render the committed exchange.json.template into exchange.json.

The Agent Fabric descriptor requires Exchange GAV coordinates
(organizationId / groupId / assetId / version). `groupId` and
`organizationId` are the MuleSoft ORG ID — an account identifier that must
never be committed (root CLAUDE.md: no environment identifier is hardcoded
anywhere in the repo). So the template carries a ${A2ALAB_MULE_ORG_ID}
placeholder for both, and this script substitutes the real value read from
.env at build time. The rendered exchange.json is gitignored — the same
pattern as the gitignored A2ALab_GCP.externalCredential-meta.xml.

`agent-network project build` reads exchange.json literally (no variable
substitution), and `project publish` checks the session has access to the
org named by groupId — so the real id must be present before those run.
Run this once in the worktree before build/publish/deploy:

    uv run python mulesoft/agent-network/render_exchange.py
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "exchange.json.template"
OUTPUT = HERE / "exchange.json"
PLACEHOLDER = "${A2ALAB_MULE_ORG_ID}"


def render() -> Path:
    load_dotenv()
    org_id = os.environ.get("A2ALAB_MULE_ORG_ID")
    if not org_id:
        raise SystemExit(
            "A2ALAB_MULE_ORG_ID is not set — add the MuleSoft org id to .env "
            "(it must never be committed; see mulesoft/README.md)."
        )
    text = TEMPLATE.read_text().replace(PLACEHOLDER, org_id)
    OUTPUT.write_text(text)
    return OUTPUT


if __name__ == "__main__":
    out = render()
    # Confirm the render without echoing the org id.
    print(f"rendered {out} (org id substituted from .env)")
