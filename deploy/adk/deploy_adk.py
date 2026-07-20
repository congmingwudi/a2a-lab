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
    "AF_SHIM_A2A_URL",
    "A2ALAB_TOKEN",
    "AF_SHIM_TIMEOUT_S",
    "A2ALAB_MAX_DELEGATION_DEPTH",
]

DISPLAY_NAME = "a2alab-adk-researcher"


def assemble_bundle() -> list[str]:
    """Filtered code bundle: interop/ + platforms/{adk,agentforce} only."""
    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "platforms").mkdir(parents=True)
    shutil.copytree(REPO / "src" / "interop", BUILD / "interop")
    (BUILD / "platforms" / "__init__.py").write_text("")
    for pkg in ("adk", "agentforce"):
        shutil.copytree(REPO / "src" / "platforms" / pkg, BUILD / "platforms" / pkg)
    # Relative paths from BUILD (the deploy chdirs there): the bundle keeps
    # the given path structure, so top-level names are what make
    # `import interop` / `import platforms` resolve in the runtime —
    # absolute paths break placement (learned from a failed first deploy).
    return ["interop", "platforms"]


def runtime_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in ENV_KEYS if os.environ.get(k)}
    env["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
    # The container FS: keep the trace layer writing somewhere writable —
    # these hops are ephemeral (the caller's client hop is the lab record).
    env["A2ALAB_TRACE_DIR"] = "/tmp/traces"
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
    args = parser.parse_args()

    load_dotenv(REPO / ".env")
    from platforms.adk.agent import build_a2a_agent

    if args.local:
        agent = build_a2a_agent()
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

    agent = build_a2a_agent()
    extra_packages = assemble_bundle()
    os.chdir(BUILD)
    config = {
        "display_name": DISPLAY_NAME,
        "description": "A2A interop lab — ADK/Gemini research agent (WS2)",
        "requirements": REQUIREMENTS,
        "extra_packages": extra_packages,
        "staging_bucket": staging,
        "env_vars": runtime_env(),
        "min_instances": 0,
        "max_instances": 2,
        "resource_limits": {"cpu": "1", "memory": "2Gi"},
        "container_concurrency": 3,
    }

    existing = os.environ.get("ADK_AGENT_ENGINE_ID")
    if existing:
        remote = client.agent_engines.update(name=existing, agent=agent, config=config)
        print(f"updated {existing}")
    else:
        remote = client.agent_engines.create(agent=agent, config=config)
        print("created new agent engine")

    resource_name = remote.api_resource.name
    a2a_url = f"https://{location}-aiplatform.googleapis.com/v1beta1/{resource_name}/a2a"
    write_env_var("ADK_AGENT_ENGINE_ID", resource_name)
    write_env_var("ADK_A2A_ENDPOINT", a2a_url)
    print(f"A2A endpoint: {a2a_url}")
    print("smoke test: uv run python scripts/matrix.py adk-a2a --runs 1 --no-record")


if __name__ == "__main__":
    main()
