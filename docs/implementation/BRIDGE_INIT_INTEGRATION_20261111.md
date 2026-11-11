# Bridge initialization integration (#335)

Date: 2026-11-11. Review branch: `refactor/core-kernel-boundaries-20261111`.
Draft PR: #349. Main merge and release remain prohibited in this task.

## Applied, not just a proposal

The candidate preserved in `7374f19d54b75e5364d4c981181c313bd400e2ff`
is now applied to `peerhub/extensions/bridge.py`, with its 91-case regression
module and Windows platform-sensitive inventory entry. No runtime module,
public method, provider policy or database data migration was added.

Bridge blob: `5f85d0e232df6f49305cd7265d055d3c5ec423e9`.
Source SHA-256: `78a2d5a6ae9aa37efe2413ba2170c63195aa93fd9378b4e485b0f9c434306f05`.
These exactly match the locally verified candidate. Baseline blob was
`e1f52db4f9a7d39a76fa9480dc3de1d361c0bf2a`.

Under the existing serialized transaction, all 36 Bridge-owned trigger names
and complete SQL bodies are compared on every open. Matching definitions
avoid unnecessary DROP/CREATE. Damaged or legacy definitions are reconciled
atomically, quoted identifiers are escaped, and the single-slot legacy
registry retains its original migration window. Registry initialization and
projection repair still run on every open; no cached-version bypass exists.

## Verification provenance

The local environment cannot resolve github.com for a full Git clone. Local
validation used the published 0.24.7 sdist plus branch runtime changes. The
baseline Bridge Git blob was reconstructed and compared to the connector's
current remote blob; CoreStore and ClaimStore SHA-256 values also matched the
recorded runtime. This is focused validation, not a full local checkout.

Re-executed during this integration:

- Baseline on the new suite: **86 passed / 5 failed**, 22.44 seconds.
- Applied candidate: **91 passed / 0 failed**, 23.31 seconds.
- One local pytest marker warning; no authenticated provider calls.
- Uploaded Bridge blob matches the tested candidate bytes exactly.

The full checkout, strict typing, existing migrations/fault/architecture tests
and Windows execution are verified by the existing hosted CI. Results must
be recorded against this integration commit in PR #349 / issue #335; earlier
CI for `df2e52f...` does not certify the applied change. This document alone
does not assert that the newly triggered CI has passed.

## Scope and remaining evidence

Historical one-pair latency measurements and their limitations remain in
[candidate results](candidates/bridge-init-20261111/results.json). The
mechanism under test is warm initialization schema churn **72 -> 0**, not a
universal speed claim. Existing durable names, certainty/retry rules, session
reuse, claim fencing and read-only diagnostics remain unchanged.

Actual Claude/Codex/AGY executables and authenticated sessions are absent in
the local environment. No live G3 was run. #323's external-provider isolation
must not be inferred from offline runtime tests, and AGY writer-overlap
rejection is unchanged. Independent review and the full supported release
matrix remain separate from an ordinary PR CI pass.

Rollback: revert the runtime/test/inventory integration as a unit. No user
records, schema version, historical evidence, main branch or release need to
be rewritten. Do not reapply the historical candidate.patch to this branch.
