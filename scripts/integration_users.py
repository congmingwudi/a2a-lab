"""The lab's dedicated Salesforce integration users, one per seam (D86, D90).

Every External Client App the lab uses runs as a user whose whole bundle is what
that seam does, not as the org admin. A user is a license, a profile that
carries no data access, and a short list of (permission set license,
permission set) pairs, ending with the app's GATE: an empty permission set that
the app's OAuth policy pre-authorizes instead of the System Administrator
profile, so only the user holding it can be the app's run-as (`sub`). The
bundles are measured, not assumed: plan/03-results.md records what each one was
cut down to and why.

    uv run python scripts/integration_users.py status
    uv run python scripts/integration_users.py <user> [--username U] [--apply]

Every write lands in the PRODUCTION org, so the default is a dry run that
prints the steps. `--apply` creates the user if needed, assigns whatever the
bundle is missing, and writes the username to the user's `.env` key. It is
idempotent and refuses to adopt an existing user on any other profile.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from setup_jwt_eca import load_env

INTEGRATION = ("Salesforce Integration", "Minimum Access - API Only Integrations")
PLATFORM = ("Salesforce Platform", "Standard Platform User")


@dataclass(frozen=True)
class UserSpec:
    name: str
    env_key: str  # .env key the username is written to; the apps render from it
    local: str  # username local part; the domain is the operator's
    last_name: str
    alias: str  # 8 characters at most
    license: str
    profile: str
    # (permission set license or None, permission set), in assignment order:
    # a license is assigned before the permission set that requires it.
    bundle: tuple[tuple[str | None, str], ...]
    used_by: str


USERS = {
    "memory": UserSpec(
        name="memory",
        env_key="SF_MEMORY_RUNAS_USER",
        local="a2alab.memory",
        last_name="A2ALab Memory",
        alias="a2amem",
        license=INTEGRATION[0],
        profile=INTEGRATION[1],
        bundle=(
            ("GenieDataPlatformStarterPsl", "GenieUserEnhancedSecurity"),  # Data Cloud User
            ("ContextServiceRuntimePsl", "ContextServiceRuntimePsl"),  # agentic runtime APIs
            # ContextServiceAdminPsl is not here: the Integration license refuses it
            # ("user license doesn't support it", measured 2026-10-14), and nothing
            # showed the runtime path needs it.
            (None, "A2ALab_Memory_Integration"),  # gate: a2a_lab_memory (JWT bearer)
        ),
        used_by="a2a_lab_memory: the bridge's Agentic Memory calls (plan/18)",
    ),
    "agent_caller": UserSpec(
        name="agent_caller",
        env_key="SF_AGENT_CALLER_RUNAS_USER",
        local="a2alab.agentcaller",
        last_name="A2ALab Agent Caller",
        alias="a2acall",
        license=INTEGRATION[0],
        profile=INTEGRATION[1],
        # The Agent API runs the agent as its own agent user (bypassUser), so the
        # caller needs no object access at all.
        bundle=((None, "A2ALab_Agent_Caller"),),  # gate: a2a_lab_claude/openai/shim
        used_by="a2a_lab_claude, a2a_lab_openai, a2a_lab_shim: Agent API sessions only",
    ),
    "briefs": UserSpec(
        name="briefs",
        env_key="SF_BRIEFS_RUNAS_USER",
        local="a2alab.briefs",
        last_name="A2ALab Briefs",
        alias="a2abrf",
        license=INTEGRATION[0],
        profile=INTEGRATION[1],
        bundle=(
            # Object permissions on the Integration license need this PSL.
            ("SalesforceAPIIntegrationPsl", "A2ALab_Brief_Writer"),
            (None, "A2ALab_Briefs_Integration"),  # gate: a2a_lab_app
        ),
        used_by="a2a_lab_app: the brief writer's CRM writes, and local Agent API sessions",
    ),
    "obs": UserSpec(
        name="obs",
        env_key="SF_OBS_RUNAS_USER",
        local="a2alab.obs",
        last_name="A2ALab Obs",
        alias="a2aobs",
        license=INTEGRATION[0],
        profile=INTEGRATION[1],
        bundle=(
            ("GenieDataPlatformStarterPsl", "GenieUserEnhancedSecurity"),  # Data Cloud User
            (None, "A2ALab_Obs_Integration"),  # gate: a2a_lab_obs
        ),
        used_by="a2a_lab_obs: STDM DMO harvest (M11) and the console's OTel session trace",
    ),
    "tab_embed": UserSpec(
        name="tab_embed",
        env_key="SF_TAB_EMBED_RUNAS_USER",
        local="a2alab.tabembed",
        last_name="A2ALab Tab Embed",
        alias="a2atab",
        # A frontdoor UI session needs a UI-capable license; Integration is API-only.
        license=PLATFORM[0],
        profile=PLATFORM[1],
        bundle=(
            ("TableauNextConsumerPsl", "TableauUser"),  # Tableau Next Consumer
            (None, "A2ALab_Tab_Embed"),  # gate: a2a_lab_tab_embed (JWT bearer)
        ),
        used_by="a2a_lab_tab_embed: the console's inline Tableau Next dashboard (WS19)",
    ),
}


def plan_steps(
    spec: UserSpec, username: str, exists: bool, psls: set[str], pss: set[str]
) -> list[tuple[str, ...]]:
    """The `sf` assignments the bundle is missing (the create step is the caller's)."""
    steps: list[tuple[str, ...]] = []
    for psl, ps in spec.bundle:
        if psl and psl not in psls:
            steps.append(("org", "assign", "permsetlicense", "-n", psl, "-b", username))
        if ps not in pss:
            steps.append(("org", "assign", "permset", "-n", ps, "-b", username))
    return steps


def default_username(spec: UserSpec, operator_username: str) -> str:
    return f"{spec.local}@{operator_username.split('@', 1)[1]}"


def ensure(spec: UserSpec, username: str | None, apply: bool) -> None:
    """Create (or reconcile) one integration user and its bundle; idempotent."""
    from setup_agentic_memory import assigned, operator_user, sf_json, soql
    from setup_jwt_eca import set_env_value

    op = operator_user()
    username = username or default_username(spec, op["Username"])
    rows = soql(f"SELECT Id, IsActive, Profile.Name FROM User WHERE Username = '{username}'")
    create: tuple[str, ...] | None = None
    if not rows:
        prof = soql(f"SELECT Id, UserLicense.Name FROM Profile WHERE Name = '{spec.profile}'")
        if not prof or prof[0]["UserLicense"]["Name"] != spec.license:
            sys.exit(f"no '{spec.profile}' profile on the {spec.license} license")
        # Locale and time zone copy the operator's, so nothing org-specific is
        # written here; the email is the operator's, who receives its notices.
        values = (f"Username='{username}' LastName='{spec.last_name}' FirstName='Integration' "
                  f"Alias={spec.alias} CommunityNickname='{spec.local}' Email='{op['Email']}' "
                  f"ProfileId={prof[0]['Id']} TimeZoneSidKey={op['TimeZoneSidKey']} "
                  f"LocaleSidKey={op['LocaleSidKey']} LanguageLocaleKey={op['LanguageLocaleKey']} "
                  f"EmailEncodingKey={op['EmailEncodingKey']}")  # fmt: skip
        create = ("data", "create", "record", "-s", "User", "-v", values)
        psls, pss = set(), set()
    else:
        if rows[0]["Profile"]["Name"] != spec.profile:
            sys.exit(f"{username} exists with profile {rows[0]['Profile']['Name']!r}, "
                     f"not {spec.profile!r}; refusing to reuse it (D86)")  # fmt: skip
        psls, pss = assigned(username)
    steps = ([create] if create else []) + plan_steps(spec, username, bool(rows), psls, pss)
    print(f"{spec.name} integration user: {username} ({'exists' if rows else 'new'})")
    for st in steps:
        print("  sf " + " ".join(st[:3]) + (f" {st[4]}" if st[0] == "org" else " User"))
    if not steps:
        print("  nothing to do")
    if not apply:
        print("(dry run — re-run with --apply to write these in the production org)")
        return
    for st in steps:
        sf_json(*st)
    set_env_value(spec.env_key, username)
    print(f"done; wrote {spec.env_key} to .env")


def cmd_status(_args) -> None:
    from setup_agentic_memory import assigned, operator_user, soql

    domain_user = operator_user()["Username"]
    for spec in USERS.values():
        username = default_username(spec, domain_user)
        rows = soql(f"SELECT Profile.Name FROM User WHERE Username = '{username}'")
        if not rows:
            print(f"{spec.name:13} missing      {spec.used_by}")
            continue
        psls, pss = assigned(username)
        missing = plan_steps(spec, username, True, psls, pss)
        state = "ok" if not missing else f"{len(missing)} missing"
        print(f"{spec.name:13} {state:12} {spec.used_by}")


def main(argv: list[str] | None = None) -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("user", choices=["status", *USERS])
    ap.add_argument("--username", help="default: <local part>@<the operator's domain>")
    ap.add_argument("--apply", action="store_true", help="create/assign (default: dry run)")
    args = ap.parse_args(argv)
    if args.user == "status":
        cmd_status(args)
    else:
        ensure(USERS[args.user], args.username, args.apply)


if __name__ == "__main__":
    main()
