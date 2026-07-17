"""OpenAI log source placeholder (M9/M11).

There is nothing to pull: the Traces dashboard is ingestion-only (no read
API — openai/openai-agents-python#793) and Responses have no list endpoint.
The M9 OpenAI platform must capture at emit time (TracingProcessor tee +
response-id persistence); until it lands, this source exists so the
coverage panel states the gap honestly rather than omitting the platform.
"""

from __future__ import annotations

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore


class OpenAISource(PlatformLogSource):
    name = "openai"

    def harvest(self, store: ObsStore) -> HarvestResult:
        result = HarvestResult(
            platform=self.name,
            status="not-built",
            detail=(
                "No pull API exists (traces are write-only; responses have no "
                "list endpoint). Lands with M9: TracingProcessor tee at emit "
                "time + persisted response ids."
            ),
        )
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
