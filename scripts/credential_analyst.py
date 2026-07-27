"""The lab's credential-health analyst — one Claude API call over measured data.

    uv run python scripts/credential_analyst.py run      # collect + analyse now
    uv run python scripts/credential_analyst.py latest   # print the last briefing

**Why an analyst at all**, when `expiry_report.py` already prints the dates. It
does not: it prints *dates*. The operational question is what to do about them,
and that is judgment across signals a threshold cannot join —

    "the CloudWatch key expires in 89 days and the Entra secret in 361, but
     the CloudWatch one is the failure that presents as an empty telemetry
     dashboard rather than an auth error, so it is the one to rotate first"

— which is the lab's D22 split applied to credentials: **deterministic
collection below, model interpretation above.** The collector never asks a model
for a date; the model never invents one.

**Why a plain API call and not a Managed Agent — this was built both ways.**
The first version (2026-07-27) was a Managed Agent, and it worked. Then the
shape was examined against what that abstraction is *for*:

| Managed Agents provides | used here |
|---|---|
| hosted tool execution (MCP, custom tools) | no — this analyst has no tools |
| scheduled deployments | no — see below, collection cannot be hosted |
| multi-turn session state | no — one shot: here is the data, interpret it |
| managed sandbox / environment | no |

None of it. What it cost was real: `agents.create` + `sessions.create` +
`events.send` + an event stream with idle detection, a setup step, a state file,
an agent object to version — and a bug that existed only because of the extra
surface (`sessions.messages` does not exist; the kickoff has to be streamed).
A single `messages.create` is the same model, the same prompt, the same data,
in one round trip with no setup.

**The test for when the heavier shape IS right** — and it is a real path, not a
hypothetical: the moment this needs to run *without a person*, or needs a tool.
Both hinge on the same dependency. Collection currently reads the operator's own
AWS SSO session, `az` login and `gcloud` ADC, so it can only happen on this
machine. Move the collector server-side (a Lambda with cloud-native credentials)
and a schedule becomes possible — which is exactly what Managed Agents are for,
and at that point this should go back. See the Details tab in the console's
Credentials view for the sketch of that variant.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab"))
BRIEF_FILE = STATE_DIR / "credential_brief.json"

SYSTEM_PROMPT = """You are the credential-health analyst for the A2A Interop Lab — a \
cross-platform agent-to-agent experiment rig spanning Salesforce Agentforce, Anthropic \
Managed Agents, AWS Bedrock AgentCore, Google Vertex AI Agent Engine and Microsoft Foundry, \
with its own hosted bridge, protocol shim and observability store on AWS.

You are given a MEASURED credential expiry report. Your job is judgment, not arithmetic.

Rules that make you useful rather than noisy:

1. NEVER invent, estimate or adjust a date. Every number you cite must appear in the report \
you were given. If something is missing, say it is missing.
2. Distinguish `measured` (the provider told us) from `declared` (a human wrote the date \
down). A declared date is an intention, and intentions drift.
3. Rank by CONSEQUENCE, not by days remaining. What breaks, who notices, and how the failure \
will present. A credential whose expiry looks like a broken feature rather than an auth error \
is more dangerous than one that fails loudly and sooner.
4. Say plainly when nothing needs doing. An analyst that always finds something to worry about \
trains its reader to stop reading.
5. Be specific about the fix. Name the command or the console path from the report's detail \
field. Do not invent a rotation procedure you were not given.

Answer in four short sections:
**Verdict** — one line: is anything at risk before the next demo?
**Act now** — items needing action within 30 days, most consequential first, each with what \
breaks and how to fix it. Say "nothing" if that is the truth.
**Watch** — items worth knowing about but not acting on.
**Blind spots** — what this report does NOT cover: credentials with no expiry (rotation is \
manual and unprompted), anything that errored, and anything `declared` rather than measured.
"""


def _client():
    from anthropic import Anthropic

    return Anthropic()


def _collect(fresh: bool) -> dict:
    """The measured half. Re-runs the collector unless asked not to."""
    from expiry_report import collect  # noqa: PLC0415

    path = STATE_DIR / "expiry.json"
    if not fresh and path.exists():
        return json.loads(path.read_text())
    report = collect()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    return report


def cmd_run(args) -> int:
    report = _collect(fresh=not args.cached)
    creds = report.get("credentials", [])
    print(f"collected {len(creds)} credentials (worst: {report.get('worst_status')})")

    lines = [
        "Here is today's MEASURED credential expiry report for the lab.",
        "",
        f"Collected: {report.get('generated_at')}",
        f"Thresholds: critical < {report['thresholds']['critical_days']}d, "
        f"warn < {report['thresholds']['warn_days']}d",
        "",
    ]
    for c in creds:
        left = "no expiry" if c["days_left"] is None else f"{c['days_left']}d"
        lines.append(
            f"- {c['name']} | status={c['status']} | left={left} | "
            f"expires={c['expires_at'] or '—'} | source={c['source']}"
            + (f" | error={c['error']}" if c.get("error") else "")
            + (f" | fix={c['detail']}" if c.get("detail") else "")
        )
    if args.context:
        lines += ["", f"Operator context: {args.context}"]
    lines += ["", "Analyse it per your instructions."]

    client = _client()
    resp = client.messages.create(
        model=args.model,
        # 14 credentials at 2000 truncated the Watch section mid-sentence;
        # a briefing that stops mid-thought reads as a failure, not a limit.
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "\n".join(lines)}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text:
        print("no analysis returned")
        return 1

    BRIEF_FILE.write_text(
        json.dumps(
            {
                "generated_at": report.get("generated_at"),
                "analysed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "model": args.model,
                "worst_status": report.get("worst_status"),
                "tokens": {
                    "in": getattr(resp.usage, "input_tokens", None),
                    "out": getattr(resp.usage, "output_tokens", None),
                },
                "brief": text,
            },
            indent=1,
        )
    )
    print("\n" + text + f"\n\n(saved to {BRIEF_FILE})")
    return 0


def cmd_latest(_args) -> int:
    if not BRIEF_FILE.exists():
        print("no briefing yet — run: uv run python scripts/credential_analyst.py run")
        return 1
    doc = json.loads(BRIEF_FILE.read_text())
    print(f"{doc['generated_at']}  (worst: {doc['worst_status']})\n")
    print(doc["brief"])
    return 0


def main() -> int:
    load_dotenv()
    sys.path.insert(0, str(REPO / "scripts"))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--model", default=os.environ.get("A2ALAB_ANALYST_MODEL", "claude-sonnet-5"))
    r.add_argument("--cached", action="store_true", help="use the last collection, do not re-query")
    r.add_argument("--context", default="", help="e.g. 'public demo on 2026-08-01'")
    r.set_defaults(fn=cmd_run)

    lat = sub.add_parser("latest")
    lat.set_defaults(fn=cmd_latest)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
