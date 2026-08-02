# Supersession and Crosswalk R1

## Health precedence

`RUNTIME-HEALTH-RECOVERY-ADDENDUM-R3-2026-07-28.md` governs receipt
authenticity, generic-500 scope, recovery CAS, and quarantine authority.
`RUNTIME-HEALTH-RECOVERY-DECISIONS-2026-07-28.md` is superseded on those
subjects. `RUNTIME-PROFILE-VERIFICATION-2026-07-28.md` is empirical legacy
evidence only, never a governing recovery contract.

## Protocol crosswalk

| Non-canonical term | Frozen term/rule |
|---|---|
| `IDEMPOTENCY_CONFLICT` | `IDEMPOTENCY_PAYLOAD_MISMATCH` |
| `HARD_TIMEOUT` | `PROCESS_TIMEOUT` |
| version check before parse | UTF-8 JSONL framing and JSON parsing, then version/schema negotiation |

## Future ratification metadata

New ratification, rather than retroactive frozen-file mutation, must bind a
document UUID, round UUID/date/electorate, dependency SHA-256 hashes, and a
canonical-hash policy. Phase 0 exit requires each contract ID to have
`V1_CAPTURE`; `LEGACY_CAPTURE` and `V1_SPEC_ONLY` remain non-exit evidence.
