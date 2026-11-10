# WS27 customer-memory ingest 403 probe — 2026-11-03

Purpose: distinguish Claude critique F1's content-filter hypothesis from the original
closed-session-reuse hypothesis without writing Maya or Jordan data.

## Safety and scope

- Owner: dedicated synthetic `a2alab-memprobe` owner.
- Agent registration: `A2ALab_Memory_Case`, matching the customer-memory experiment.
- Conversations: three isolated opaque `case_conversation` ids.
- Content: synthetic `example.invalid` data only. The safety reviewer rejected replaying the full
  hosted business payload, so no customer trace content was copied into the tenant.

## Results

| Arm | Trace | Result |
|---|---|---|
| Fresh session, synthetic `https://probe.example.invalid/supplier-api` plus `/v1/orders` and `/v1/inventory` patterns | `8254320eebe7432fbc09cf99e665ba2d` | **403**, bare nginx HTML |
| Fresh session, same synthetic message with URLs and API paths replaced by neutral placeholders | `44243dc24a9c4cba9d6a62e3a9f13b2b` | **accepted** |
| Benign write, close session, then a second benign write into that closed session | `811667c7fa1440a3b2e6455836860fb0` | **accepted** |

## Conclusion

The three-way result confirms F1. Closed-session reuse is supported by the tenant and is not the
cause of the observed correction failure. URL/API-path content triggers an upstream edge filter;
removing those patterns makes the same write shape succeed. The implementation must retain D94's
one-session-per-chat model and handle the narrow HTML-403 content refusal explicitly.

The safe behavior is not to classify every 403 as content filtering. Only a 403 with the observed
HTML/nginx response shape qualifies. On that response, retry once with deterministic memory-ingest
normalization that converts URLs and slash-form API paths into readable non-URL text. JSON 403s
remain ordinary authorization/governance failures and are never retried as content.
