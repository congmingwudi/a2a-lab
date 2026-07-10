# Results

Latency + transcript results per milestone. `scripts/matrix.py` appends
matrix runs below; manual measurements (action-timeout probes, managed vs
sdk first-turn latency) are recorded by hand with date + setup.

## Timeout probes (M6 — pending)

| Injected delay | Agentforce action outcome | Notes |
|---|---|---|
| 10s | — | |
| 30s | — | |
| 60s | — | |
| 90s | — | |

## Managed vs SDK backend latency (pending)

| Backend | Turn | p50 | p95 | Notes |
|---|---|---|---|---|
| managed | first (cold session) | — | — | includes container provisioning |
| managed | follow-up (warm session) | — | — | |
| sdk | first (warm server) | — | — | |
