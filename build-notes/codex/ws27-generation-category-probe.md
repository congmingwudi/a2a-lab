# WS27 D107 generation-category tenant probe

Date: 2026-11-03. Controlled owner: `a2alab-memprobe`. Registered service identity:
`A2ALab_Memory_Case`. The probe created no customer data and cleaned up every marker it successfully
created.

| Category | Trace | Result |
|---|---|---|
| `A2ALAB_EXPERIMENT_GENERATION` | `5644b7ad795b48d1b6fae5a7ffc84e91` | Initial GET returned HTTP 403 `Access forbidden`; no write attempted. |
| `PROFILE_FACT` | `8d974375635c4a7089481fee0b0eea0c` | Initial GET returned the same HTTP 403; no write attempted. |
| `sys_PROFILE_FACT` | `18b9227232ba46efa495ac9b01496583` | GET/create/read-back/delete all succeeded and cleanup verified, but the raw marker appeared in GetContext and saved prompt material. |
| `sys_PROFILE_FACT` after filtering implementation | `e3c30627c7f945a99a54b6e7535f20a3` | GET/create/read-back/delete and cleanup succeeded. The raw tenant context contained the marker, while `render_context` and `saved_memories` both excluded it. |

Go/no-go conclusion: custom category storage is a **no-go** in this tenant. D107 uses a strictly
validated `v1:<UTC timestamp>:<nonce>` control row in supported `sys_PROFILE_FACT`, excludes all
reserved marker rows from semantic-memory management, and strips valid marker tokens at both model
prompt paths. Ordinary profile facts are not treated as malformed control rows; only `v1:`-prefixed
rows enter marker validation.

Re-run:

```sh
uv run python scripts/customer_memory_generation_probe.py --apply
```
