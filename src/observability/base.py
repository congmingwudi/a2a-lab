"""Harvester seam: one PlatformLogSource per platform (M11.2).

harvest(store) pulls whatever the platform exposes into the obs store and
returns a HarvestResult. Sources must degrade honestly: a platform whose
prerequisites aren't met (no Data Cloud, no API key) reports status
"blocked" with the reason — the console's coverage panel renders exactly
that, keeping the matrix in plan/05-observability.md honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from observability.store import ObsStore


@dataclass
class HarvestResult:
    platform: str
    status: str  # ok | blocked | error | not-built
    detail: str = ""
    sessions: int = 0
    events: int = 0
    errors: list[str] = field(default_factory=list)


class PlatformLogSource:
    name: str = "abstract"

    def harvest(self, store: ObsStore) -> HarvestResult:  # pragma: no cover - interface
        raise NotImplementedError
