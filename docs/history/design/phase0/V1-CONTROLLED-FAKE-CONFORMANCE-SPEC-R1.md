# V1 Controlled-Fake Conformance Spec R1

This is the TDD boundary for the remaining DP/DT fixtures.  It intentionally
does not reuse legacy Hub outcomes as V1 proof.

## Fake adapter contract

The adapter accepts a deterministic event script and exposes only:
`INTENT_PERSISTED`, `SPAWNED(pid,birth)`, `CHUNK(bytes,t)`, `EXIT(code)`,
`SILENCE(t)`, `HARD_DEADLINE(t)`, `CANCEL_ACK`, `TREE_STATE`, and
`CLEANUP_ERROR`.  The runner appends every event to an isolated journal before
reducing state.  No script may launch a provider or touch live Hub state.

## Required cases

| Fixture | Script | Assertions |
|---|---|---|
| DP-06 | INTENT_PERSISTED then injected runner crash | recovery is `MAY_HAVE_STARTED`; no automatic replay; journal digest retained |
| DT-01 | CHUNK(a), CHUNK(b), EXIT(0) | ordered stream events and terminal receipt |
| DT-02 | split UTF-8 plus `\r`/`\n` boundaries | canonical text/line event order independent of read chunking |
| DT-03 | output then silence; separate hard-deadline script | `SILENCE_TIMEOUT` differs from `HARD_TIMEOUT` |
| DT-04 | HARD_DEADLINE, ignored first cancel, bounded termination | cancellation ladder and uncertainty receipt |
| DT-05 | tree identities then cancellation | every identity terminated or explicit cleanup uncertainty |
| DT-06 | partial output, primary failure, CLEANUP_ERROR | primary terminal state unchanged; cleanup error attached |

Each test writes a fresh isolated root, a canonical transcript, before/after
state digests, and a fixture record.  These tests are the first PeerHub source
tests after Phase 0 evidence and design are ratified.
