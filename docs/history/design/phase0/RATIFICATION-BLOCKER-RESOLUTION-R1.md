# Ratification Blocker Resolution R1

Status: active; Phase 0 ratification is blocked.  This ledger supersedes no
frozen file and makes no claim that a V1-only specification is captured.

## Canonical evidence status

| Status | Meaning |
|---|---|
| `LEGACY_CAPTURE` | digest-bound observation of legacy behaviour; may expose a defect |
| `V1_SPEC_ONLY` | deterministic required test, not yet a capture and not implementation evidence |
| `V1_CAPTURE` | future digest-bound fake-run result after the V1 runner exists |
| `BLOCKED_LIVE_CAPTURE` | permitted only for a genuinely unsafe/non-deterministic live case |

Current Phase 0 inventory: 35 canonical records; 19 contract IDs are
`V1_SPEC_ONLY`: DP-06, DT-01..06, HR-04..06, RT-04..06, GB-01/03/04/05,
CJ-02, CJ-05.  Existing records whose blocker names a missing V1 behavior are
`LEGACY_CAPTURE`, not a successful V1 capture.

## Required resolution order

1. Fix the fixture contract parent path and add a machine-readable status field
   to every record; inventory validation must require `V1_CAPTURE` only for
   Phase 0 exit.
2. Add a protocol terminology crosswalk: `IDEMPOTENCY_PAYLOAD_MISMATCH` is the
   frozen identifier; `PROCESS_TIMEOUT` is the frozen timeout identifier; JSON
   syntax parsing precedes version negotiation.
3. Publish a health supersession index: R3 controls receipt authenticity,
   generic-500 scope, recovery CAS, and quarantine authority. Runtime
   verification remains empirical legacy evidence only.
4. Bind every authority-cutover proof-matrix subject to an explicit fixture
   family or mark it `V1_SPEC_ONLY`; no cutover may be called executable before
   those bindings and captures exist.
5. Create the controlled-fake runner and host mutation broker only after the
   above documents are internally consistent; then produce the 19 `V1_CAPTURE`
   records.
6. Create a new ratification round with UUID, date, electorate, dependencies,
   and canonical hash policy.  Do not mutate existing frozen-file hashes to
   retroactively claim approval.

## CX sandbox rule

Availability evidence binds a `RuntimeRevision` including sandbox mode.  The
elevated host probe proves only that distinct revision; it does not prove an
unelevated production revision.  No proxy clearing, sandbox bypass, or blind
recovery is permitted by this ledger.
