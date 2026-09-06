# Repository Guidelines

> Read `CLAUDE.md` alongside this file: it carries the repository's definition of done (ADR entry, workstream item lines, console Details panes, deployment map, Dockerfile `COPY` and full-rebuild rules). This guide orients; that file gates. The two-agent loop both agents work under is `plan/16-joint-agent-workflow.md` (D79).

## Project Structure & Module Organization

Python 3.11+ code lives under `src/`. `interop/` owns canonical messages, protocol clients/servers, identity, and tracing; `platforms/` contains provider adapters. `bridge/`, `faces/`, `orchestration/`, and `fanout_mcp/` handle routing and delegation. `observability/` and `obs_mcp/` store and query telemetry. The FastAPI console and browser assets live in `src/console/`, including `static/index.html`.

Use `config/` for target/scenario definitions, `scripts/` for operational commands, `deploy/` for infrastructure, and `salesforce/` for Apex and Salesforce metadata. Tests live in `tests/unit/`, `tests/e2e/`, and `tests/live/`. Record decisions and implementation evidence in `plan/` and `build-notes/`.

## Build, Test, and Development Commands

- `uv sync`: install locked application and development dependencies; add `--extra openai` or another declared extra when needed.
- `uv build`: build distribution artifacts through Hatchling; verify package inclusion when adding modules.
- `uv run pytest`: run unit and local protocol loopback tests; live tests are excluded by default.
- `uv run pytest tests/unit/test_orchestration.py`: run a focused regression suite.
- `uv run ruff check src tests scripts`: check Python style and lint rules.
- `scripts/run_local.sh`: start the configured local stack, including the console on port 8200; it loads `.env` and reclaims service ports.
- `uv run pytest -m live`: explicitly run external-service tests with configured credentials.

## Coding Style & Naming Conventions

Use four-space indentation, type annotations, `snake_case` functions/modules, and `PascalCase` classes. Ruff's configured line length is 100. Keep protocol mappings at interoperability boundaries and provider behavior in adapters. Preserve canonical request/response contracts, trace correlation, and explicit partial-failure outcomes. Avoid unrelated formatting changes.

## Testing Guidelines

Use pytest and pytest-asyncio; name files `test_*.py` and functions `test_*`. Add regression coverage for changed behavior, including errors and trace evidence. Reuse isolated-trace fixtures and fake external clients. No numerical coverage threshold is configured; report actual tests, skips, and validation limits.

The independent standard-library JSONL harness is documented in `build-notes/codex/codebase-review/eval-harness/README.md`. Keep synthetic replay results distinct from live experiment evidence.

## Commit & Pull Request Guidelines

Recent commits use workstream prefixes, such as `WS10 SP1: fix broker trace attribution`. Follow that pattern when applicable and describe the concrete change. PRs should explain behavior, link relevant workstreams/issues, list validation, and include screenshots for console changes.

## Security & Configuration

Use `.env.example` as the configuration reference; preserve existing `.env` files or symlinks. Never commit credentials or account identifiers. Sanitize retained traces and distinguish missing telemetry or cost from measured zero.
