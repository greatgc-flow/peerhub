# DP/DT Controlled Baseline Mapping — 2026-07-28

## Execution

Host-controlled, provider-free execution:

`python -m pytest _sys/tests/unit/test_process_lease_supervision_c7.py -q -p no:cacheprovider`

Result: `17 passed in 1.54s`.  The test uses deterministic fake adapters,
mocked processes, or local Python child processes.  It did not invoke AG, CX,
CC, or any provider.

## Evidence mapping

| Fixture | Existing direct evidence | Status for the 54-fixture contract |
|---|---|---|
| DP-01 | local flushed pipe chunks complete with clean exit | partial: lacks separately persisted protocol/completion layers |
| DP-02 | denied pre-spawn produces `not_started` soft skip | baseline available |
| DP-03 | spawned nonzero process produces `execution_uncertain` soft skip | baseline available |
| DP-04 | none | requires a bounded-output fake process capture |
| DP-05 | none for pipe hard deadline | requires a controlled deadline capture |
| DP-06 | spawned failure distinguishes uncertain execution, but no injected post-intent crash | requires dedicated crash-after-intent capture |
| DT-01 | PTY escalation fake returns terminal result | partial: no ordered streaming transcript |
| DT-02 | none | requires chunk/line normalization capture |
| DT-03 | none | requires silence-vs-hard deadline capture |
| DT-04 | PTY hard-timeout path soft-skips with uncertain execution | baseline available |
| DT-05 | none | requires bounded PTY cancellation/termination capture |
| DT-06 | none | requires PTY crash/partial-output uncertainty capture |

## Guardrail

Passing legacy regressions is supporting evidence only.  It does not satisfy a
Phase 0 fixture until an individual digest-bound record and transcript prove
the required PeerHub v1 outcome.  In particular, legacy process supervision
does not provide the V1 protocol-layer receipt or the V1 cancellation contract.
