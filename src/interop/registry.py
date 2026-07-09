"""Target registry: config/targets.yaml maps a target name to
{platform, protocol, endpoint, auth}. The bridge, shims, custom tools, and
the matrix harness all resolve targets here — adding a platform is one
directory under src/platforms/ plus one entry in targets.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_TARGETS_PATH = Path("config/targets.yaml")


@dataclass
class Target:
    name: str
    platform: str  # claude | agentforce | openai | ...
    protocol: str  # rest | mcp | a2a | agentforce-api
    endpoint: str | None = None
    auth: dict[str, Any] = field(default_factory=dict)
    status: str = "native"  # native | via-bridge | via-shim | blocked-beta
    options: dict[str, Any] = field(default_factory=dict)


def _expand_env(value: Any) -> Any:
    """Expand ${VAR} references in strings so targets.yaml can point at
    .env-provided endpoints and secrets without duplicating them."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class Registry:
    def __init__(self, targets: dict[str, Target]):
        self.targets = targets

    @classmethod
    def load(cls, path: str | Path = DEFAULT_TARGETS_PATH) -> "Registry":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        targets = {}
        for name, spec in (raw.get("targets") or {}).items():
            spec = _expand_env(spec)
            targets[name] = Target(
                name=name,
                platform=spec["platform"],
                protocol=spec["protocol"],
                endpoint=spec.get("endpoint"),
                auth=spec.get("auth") or {},
                status=spec.get("status", "native"),
                options=spec.get("options") or {},
            )
        return cls(targets)

    def get(self, name: str) -> Target:
        if name not in self.targets:
            raise KeyError(
                f"unknown target '{name}' — known targets: {sorted(self.targets)}"
            )
        return self.targets[name]

    def client_for(self, name: str):
        """Instantiate the RemoteAgentClient for a target."""
        target = self.get(name)
        if target.protocol == "rest":
            from interop.clients.rest import RestClient

            return RestClient(target.endpoint, auth=target.auth, target_name=name)
        if target.protocol == "mcp":
            from interop.clients.mcp import McpClient

            return McpClient(target.endpoint, auth=target.auth, target_name=name)
        if target.protocol == "a2a":
            from interop.clients.a2a import A2AClient

            return A2AClient(target.endpoint, auth=target.auth, target_name=name)
        if target.protocol == "agentforce-api":
            from platforms.agentforce.client import AgentforceClient

            return AgentforceClient.from_target(target)
        raise ValueError(f"no client for protocol '{target.protocol}'")
