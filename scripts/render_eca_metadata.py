"""Render the client-credentials ECAs' identifier-bearing metadata from .env.

Three kinds of file name the org or a person, which CLAUDE.md keeps out of the repo:

- `extlClntAppOauthPolicies/<name>_oauthPlcy.ecaOauthPlcy-meta.xml` names its
  run-as user in `clientCredentialsFlowUser` — a username in the lab org;
- `externalClientApps/<name>.eca-meta.xml` names a `contactEmail` (and a
  retrieve adds `orgScopedExternalApp`, `<org id>:<name>` — never rendered: the
  org generates it, plan/04-runbooks.md §"To add another");
- `extlClntAppOauthSettings/<name>_oauth.ecaOauth-meta.xml` gains `oauthLink`,
  `<org id>:<link id>`, on retrieve — also org-generated, also never rendered.
  What that file is FOR, the least-privilege scope set, lives in POLICIES.

So, like setup_jwt_eca.py, the recipe (POLICIES below) is checked in and the files
are RENDERED: both directories are gitignored, and this script writes them from
.env before a deploy.

    uv run python scripts/render_eca_metadata.py show             # print, write nothing
    uv run python scripts/render_eca_metadata.py write            # all six
    uv run python scripts/render_eca_metadata.py write obs shim   # just these

Then deploy as before, e.g.
`sf project deploy start -m ExtlClntAppOauthConfigurablePolicies:a2a_lab_obs_oauthPlcy`.
Each policy reads its run-as user from its own .env key and pre-authorizes its
own gate permission set, so only the integration user holding that set can be
its run-as (D86, D90). scripts/integration_users.py creates the users and writes
the keys.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "salesforce/force-app/main/default"
POLICY_DIR = META / "extlClntAppOauthPolicies"
APP_DIR = META / "externalClientApps"
SETTINGS_DIR = META / "extlClntAppOauthSettings"
CONTACT_ENV = "SF_ECA_CONTACT_EMAIL"


@dataclass(frozen=True)
class Policy:
    name: str  # ECA developer name
    run_as_env: str  # .env key holding clientCredentialsFlowUser
    scopes: str  # Metadata API labels, comma-separated: least privilege per caller (F6)
    # a2a_lab_app predates the others and was configured in Setup with a
    # 30-day inactivity refresh window; the five later apps were built from
    # metadata with a 365-day lifetime. Recorded as-is, not normalized.
    refresh: str = "lifetime"
    # The app file is rendered only where the repo has carried one (obs,
    # tab_embed); the other four were deployed once and never kept.
    description: str | None = None
    # The empty permission set the policy pre-authorizes instead of the System
    # Administrator profile (D86, D90); its integration user is the only holder.
    preauth_permission_set: str | None = None


POLICIES = {
    # One integration user per role (D90): briefs for the app (its token also does
    # the brief writer's CRM writes), one agent caller for the three Agent API
    # seams, obs, and a UI-licensed user for the embed.
    "app": Policy(
        "a2a_lab_app",
        "SF_BRIEFS_RUNAS_USER",
        "Api, Chatbot, SFApiPlatform",
        refresh="inactivity",
        preauth_permission_set="A2ALab_Briefs_Integration",
    ),
    **{
        k: Policy(
            f"a2a_lab_{k}",
            "SF_AGENT_CALLER_RUNAS_USER",
            "Chatbot, SFApiPlatform",
            preauth_permission_set="A2ALab_Agent_Caller",
        )
        for k in ("claude", "openai", "shim")
    },
    "obs": Policy(
        "a2a_lab_obs",
        "SF_OBS_RUNAS_USER",
        "Api",
        description=(
            "A2A lab: M11 observability harvest reading Data Cloud agent-session DMOs "
            "(per-caller identity, F6)."
        ),
        preauth_permission_set="A2ALab_Obs_Integration",
    ),
    "tab_embed": Policy(
        "a2a_lab_tab_embed",
        "SF_TAB_EMBED_RUNAS_USER",
        "Api, Web, RefreshToken",
        description=(
            "A2A lab: Tableau Next inline embed in the lab console. Client-credentials "
            "token, exchanged at /services/oauth2/singleaccess for a frontdoor URL (needs "
            "the `web` scope). Owner-only, WS19/M10."
        ),
        preauth_permission_set="A2ALab_Tab_Embed",
    ),
}


def render(p: Policy, run_as: str) -> str:
    if p.refresh == "inactivity":
        session = (
            "    <namedUserJwtSessionTimeoutType>UserSession</namedUserJwtSessionTimeoutType>\n"
        )
        refresh = "SpecificInactivity", "30"
    else:
        session = ""
        refresh = "SpecificLifetime", "365"
    preauth = (
        f"    <commaSeparatedPermissionSet>{p.preauth_permission_set}</commaSeparatedPermissionSet>"
        if p.preauth_permission_set
        else "    <commaSeparatedProfile>System Administrator</commaSeparatedProfile>"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppOauthConfigurablePolicies xmlns="http://soap.sforce.com/2006/04/metadata">
    <clientCredentialsFlowUser>{escape(run_as)}</clientCredentialsFlowUser>
{preauth}
    <externalClientApplication>{p.name}</externalClientApplication>
    <ipRelaxationPolicyType>Enforce</ipRelaxationPolicyType>
    <isClientCredentialsFlowEnabled>true</isClientCredentialsFlowEnabled>
    <isGuestCodeCredFlowEnabled>false</isGuestCodeCredFlowEnabled>
    <isTokenExchangeFlowEnabled>false</isTokenExchangeFlowEnabled>
    <label>{p.name}_oauthPlcy</label>
{session}    <permittedUsersPolicyType>AdminApprovedPreAuthorized</permittedUsersPolicyType>
    <refreshTokenPolicyType>{refresh[0]}</refreshTokenPolicyType>
    <refreshTokenValidityPeriod>{refresh[1]}</refreshTokenValidityPeriod>
    <refreshTokenValidityUnit>Days</refreshTokenValidityUnit>
    <requiredSessionLevel>STANDARD</requiredSessionLevel>
</ExtlClntAppOauthConfigurablePolicies>
"""


def render_app(p: Policy, contact_email: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ExternalClientApplication xmlns="http://soap.sforce.com/2006/04/metadata">
    <contactEmail>{escape(contact_email)}</contactEmail>
    <description>{escape(p.description or "")}</description>
    <distributionState>Local</distributionState>
    <isProtected>false</isProtected>
    <label>{p.name}</label>
</ExternalClientApplication>
"""


def render_settings(p: Policy) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ExtlClntAppOauthSettings xmlns="http://soap.sforce.com/2006/04/metadata">
    <commaSeparatedOauthScopes>{p.scopes}</commaSeparatedOauthScopes>
    <externalClientApplication>{p.name}</externalClientApplication>
    <isFirstPartyAppEnabled>false</isFirstPartyAppEnabled>
    <label>{p.name}_oauth</label>
</ExtlClntAppOauthSettings>
"""


def path_for(p: Policy) -> Path:
    return POLICY_DIR / f"{p.name}_oauthPlcy.ecaOauthPlcy-meta.xml"


def app_path_for(p: Policy) -> Path:
    return APP_DIR / f"{p.name}.eca-meta.xml"


def files_for(p: Policy) -> dict[Path, str]:
    out = {
        path_for(p): render(p, os.environ[p.run_as_env]),
        SETTINGS_DIR / f"{p.name}_oauth.ecaOauth-meta.xml": render_settings(p),
    }
    if p.description:
        out[app_path_for(p)] = render_app(p, os.environ[CONTACT_ENV])
    return out


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["show", "write"])
    ap.add_argument("policies", nargs="*", help=f"any of {', '.join(POLICIES)} (default: all)")
    args = ap.parse_args(argv)
    if unknown := [k for k in args.policies if k not in POLICIES]:
        ap.error(f"unknown policy: {', '.join(unknown)}")
    load_env()
    chosen = [POLICIES[k] for k in (args.policies or POLICIES)]
    need = {p.run_as_env for p in chosen} | (
        {CONTACT_ENV} if any(p.description for p in chosen) else set()
    )
    if missing := sorted(k for k in need if not os.environ.get(k)):
        sys.exit(f"set {', '.join(missing)} in .env (ECA run-as username / contact email)")
    for p in chosen:
        for path, body in files_for(p).items():
            if args.command == "show":
                print(f"--- {path.relative_to(ROOT)}\n{body}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
                print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
