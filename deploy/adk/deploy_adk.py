"""Deploy the ADK research agent to Vertex AI Agent Engine with its native
A2A endpoint (WS2).

    uv run python deploy/adk/deploy_adk.py            # create or update
    uv run python deploy/adk/deploy_adk.py --local    # set_up() locally, no deploy

Creates on first run (then updates in place via ADK_AGENT_ENGINE_ID from
.env). Personal-account economics are deliberate: min_instances=0 (scale to
zero — cold starts are lab data, the console warm-up panel handles demos)
and 1 vCPU / 2Gi instead of the 4/4Gi defaults. A warm instance at the
default size would burn ~$250/month; this shape idles at $0.

Packaging: Agent Engine cloudpickles the A2aAgent and pip-installs
REQUIREMENTS in the runtime container; our code ships via extra_packages —
a filtered copy of src/interop + src/platforms (adk + agentforce only) is
assembled under deploy/adk/_build/ so the bundle exposes exactly the
packages the executor imports, nothing else.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv  # noqa: E402

BUILD = Path(__file__).resolve().parent / "_build"

# Installed inside the Agent Engine container at deploy time. google-adk
# pinned <2 to match the surface the A2A template/codelab targets.
REQUIREMENTS = [
    "a2a-sdk>=1.0.0",
    "google-cloud-aiplatform[agent_engines,adk]>=1.156",
    "google-adk>=1.29,<2",
    "httpx>=0.28,<1",
    "pyyaml>=6.0",
    # unpickling deps the SDK checks for explicitly
    "cloudpickle>=3",
    "pydantic>=2",
    # cross-hyperscaler cell (WS3): Entra SP auth for calling Foundry A2A
    "azure-identity>=1.20",
]

# Env the container needs: the Agentforce twin credentials (ask_agentforce
# runs IN the GCP container — same credential-locality contrast as the
# AgentCore twins, D26 insight) plus lab knobs. GOOGLE_CLOUD_* are reserved
# by the runtime and must not be set here.
ENV_KEYS = [
    "SF_MY_DOMAIN",
    "SF_CLIENT_ID",
    "SF_CLIENT_SECRET",
    "SF_AGENT_ID",
    "SF_ADK_AGENT_ID",
    "ADK_MODEL",
    "ADK_REAL_SEARCH",
    "AF_SHIM_A2A_URL",
    "AF_SHIM_TIMEOUT_S",
    "A2ALAB_MAX_DELEGATION_DEPTH",
    # cross-hyperscaler: Foundry incoming A2A + the Entra SP that calls it
    "FOUNDRY_A2A_ENDPOINT",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    # WS8: the orchestrator variant fans out from INSIDE the container, so it
    # needs the same per-leg target overrides the host reads from .env.
    "A2ALAB_LEG_EXPOSURE_TARGET",
    "A2ALAB_LEG_COMMERCIAL_TARGET",
    "A2ALAB_LEG_COMMS_TARGET",
]

DISPLAY_NAME = "a2alab-adk-researcher"

# WS8: the same deploy path serves a second, differently-shaped agent — the
# fan-out's logistics leg. Separate Agent Engine, separate display name,
# separate id in .env, because the point of a dedicated leg agent is that
# Cloud Logging attributes it separately.
ROLES = {
    "researcher": {
        "builder": "build_a2a_agent",
        "display_name": DISPLAY_NAME,
        "description": "A2A interop lab — ADK/Gemini research agent (WS2)",
        "id_var": "ADK_AGENT_ENGINE_ID",
        "endpoint_var": "ADK_A2A_ENDPOINT",
    },
    "orchestrator": {
        "builder": "build_orchestrator_a2a_agent",
        "display_name": "a2alab-supply-orchestrator-adk",
        "description": "A2A interop lab — fan-out orchestrator, ParallelAgent (WS8)",
        "id_var": "ADK_ORCHESTRATOR_ENGINE_ID",
        "endpoint_var": "ADK_ORCHESTRATOR_A2A_ENDPOINT",
    },
    "logistics": {
        "builder": "build_leg_a2a_agent",
        "display_name": "a2alab-logistics-agent",
        "description": "A2A interop lab — fan-out leg, Logistics BU (WS8)",
        "id_var": "ADK_LOGISTICS_ENGINE_ID",
        "endpoint_var": "ADK_LOGISTICS_A2A_ENDPOINT",
    },
}


def assemble_bundle() -> list[str]:
    """Filtered code bundle: interop/ + orchestration/ + platforms/{adk,agentforce}."""
    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "platforms").mkdir(parents=True)
    shutil.copytree(REPO / "src" / "interop", BUILD / "interop")
    # orchestration/ carries the leg agents' prompts (WS8). Shipping it keeps
    # the deployed agent's instructions identical to the ones the lab's own
    # tests and docs reference — one definition, not a copy that drifts.
    shutil.copytree(REPO / "src" / "orchestration", BUILD / "orchestration")
    # The orchestrator variant resolves legs by TARGET NAME, so the registry
    # needs its config inside the container. Shipped as a package-adjacent copy
    # and pointed at with A2ALAB_TARGETS_PATH (runtime_env) — the researcher
    # builds clients straight from endpoint env vars and never reads this.
    (BUILD / "labconfig").mkdir(parents=True, exist_ok=True)
    (BUILD / "labconfig" / "__init__.py").write_text("")
    shutil.copy(REPO / "config" / "targets.yaml", BUILD / "labconfig" / "targets.yaml")
    (BUILD / "platforms" / "__init__.py").write_text("")
    for pkg in ("adk", "agentforce"):
        shutil.copytree(REPO / "src" / "platforms" / pkg, BUILD / "platforms" / pkg)
    # Relative paths from BUILD (the deploy chdirs there): the bundle keeps
    # the given path structure, so top-level names are what make
    # `import interop` / `import platforms` resolve in the runtime —
    # absolute paths break placement (learned from a failed first deploy).
    return ["interop", "orchestration", "platforms", "labconfig"]


def runtime_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in ENV_KEYS if os.environ.get(k)}
    # Shim credential under its own name (same convention as the AgentCore
    # runtimes — A2ALAB_TOKEN stays a laptop-only variable).
    if os.environ.get("A2ALAB_TOKEN"):
        env["AF_SHIM_TOKEN"] = os.environ["A2ALAB_TOKEN"]
    env["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
    # WS6: the lab IdP's PUBLIC key — user-JWT verification in the engine
    # container (the signing key never leaves the laptop).
    pub = REPO / ".a2alab" / "lab_jwt_public.pem"
    if pub.exists():
        # escaped newlines for env-var safety; identity.public_key() unescapes
        env["A2ALAB_JWT_PUBLIC_KEY"] = pub.read_text().replace("\n", "\\n")
    # The container FS: keep the trace layer writing somewhere writable —
    # these hops are ephemeral (the caller's client hop is the lab record).
    env["A2ALAB_TRACE_DIR"] = "/tmp/traces"
    # where the bundled targets.yaml lands inside the container
    env["A2ALAB_TARGETS_PATH"] = "labconfig/targets.yaml"
    return env


def write_env_var(var: str, value: str) -> None:
    env_path = REPO / ".env"
    lines = env_path.read_text().splitlines()
    hit = False
    for i, ln in enumerate(lines):
        if ln.startswith(f"{var}="):
            lines[i] = f"{var}={value}"
            hit = True
    if not hit:
        lines.append(f"{var}={value}")
    env_path.write_text("\n".join(lines) + "\n")
    print(f".env: {var} set")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="set_up() locally, no deploy")
    parser.add_argument(
        "--role",
        default="researcher",
        choices=sorted(ROLES),
        help="which agent to deploy (researcher = WS2; logistics = WS8 fan-out leg)",
    )
    args = parser.parse_args()
    role = ROLES[args.role]

    load_dotenv(REPO / ".env")
    import platforms.adk.agent as adk_agent

    build = getattr(adk_agent, role["builder"])

    if args.local:
        agent = build()
        agent.set_up()
        print("local set_up() OK — A2aAgent builds and serves in-process")
        return

    import vertexai
    from google.cloud import storage
    from google.genai import types as genai_types

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    bucket_name = f"{project}-agent-staging"
    staging = f"gs://{bucket_name}"

    storage_client = storage.Client(project=project)
    if storage_client.lookup_bucket(bucket_name) is None:
        storage_client.create_bucket(bucket_name, location=location)
        print(f"created staging bucket {staging}")

    vertexai.init(project=project, location=location, staging_bucket=staging)
    client = vertexai.Client(
        project=project,
        location=location,
        http_options=genai_types.HttpOptions(api_version="v1beta1"),
    )

    agent = build()
    extra_packages = assemble_bundle()
    os.chdir(BUILD)
    config = {
        "display_name": role["display_name"],
        "description": role["description"],
        "requirements": REQUIREMENTS,
        "extra_packages": extra_packages,
        "staging_bucket": staging,
        "env_vars": runtime_env(),
        "min_instances": 0,
        "max_instances": 2,
        "resource_limits": {"cpu": "1", "memory": "2Gi"},
        "container_concurrency": 3,
    }

    existing = os.environ.get(role["id_var"])
    if existing:
        remote = client.agent_engines.update(name=existing, agent=agent, config=config)
        print(f"updated {existing}")
    else:
        remote = client.agent_engines.create(agent=agent, config=config)
        print("created new agent engine")

    resource_name = remote.api_resource.name
    a2a_url = f"https://{location}-aiplatform.googleapis.com/v1beta1/{resource_name}/a2a"
    write_env_var(role["id_var"], resource_name)
    write_env_var(role["endpoint_var"], a2a_url)
    print(f"A2A endpoint: {a2a_url}")
    smoke = "adk-a2a" if args.role == "researcher" else "adk-logistics-a2a"
    print(f"smoke test: uv run python scripts/matrix.py {smoke} --runs 1 --no-record")


if __name__ == "__main__":
    main()
