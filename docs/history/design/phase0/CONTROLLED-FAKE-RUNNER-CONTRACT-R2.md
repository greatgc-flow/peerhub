# Controlled-Fake Runner Contract R2

Status: proposed bootstrap contract. This document is not a Phase 0 exit
approval, a production feature specification, or authority-cutover approval.

## 1. Authorized surface

The first PeerHub source work may implement only a provider-free,
controlled-fake runner. It consumes deterministic event scripts and produces
isolated evidence artifacts. It MUST NOT invoke a provider, mutate live Hub
state, perform authority cutover, or implement the host mutation broker.

## 2. Isolation and determinism

Every test creates a fresh root owned by the test. The runner receives an
injected monotonic clock, deterministic ID source, and event script. It must
not read wall-clock time, ambient process state, credentials, or live Hub
files. Process identities are represented by the script and must include a
stable identity token when a tree is involved.

## 3. Journal and reducer

For each input event, the runner MUST append one canonical JSON record to its
local journal before reducing any derived state. A simulated interruption
between append and reduction is a supported test input. Recovery may reduce
the durable journal, but MUST NOT automatically replay an uncertain external
dispatch. The journal, reduced state, transcript, and fixture record are all
contained in the fresh root.

## 4. Event vocabulary and terminal semantics

The minimum common event vocabulary is:

`INTENT_PERSISTED`, `SPAWNED(identity)`, `CHUNK(bytes,t)`, `EXIT(code)`,
`SILENCE(t)`, `PROCESS_DEADLINE(t)`, `CANCEL_ACK`, `TREE_STATE`, and
`CLEANUP_ERROR`.

`SILENCE_TIMEOUT` and `PROCESS_TIMEOUT` are distinct terminal classifications.
`PROCESS_TIMEOUT` replaces the retired term `HARD_TIMEOUT`. Cleanup evidence
is attached to, and never overwrites, the primary terminal result. JSON
framing/parsing precedes version or schema negotiation. Idempotency mismatch is
named `IDEMPOTENCY_PAYLOAD_MISMATCH`.

## 5. Required output artifacts

Each fixture execution MUST emit:

1. a canonical transcript with ordered events;
2. a raw-byte SHA-256 transcript digest;
3. before/after state digests;
4. a machine-readable fixture record containing fixture ID, status, artifact
   paths, all digests, and terminal classification; and
5. an explicit `V1_CAPTURE` only when the fixture completed under this
   contract.

All other statuses use the vocabulary in
`RATIFICATION-BLOCKER-RESOLUTION-R1.md`; `V1_SPEC_ONLY` is never presented as
captured evidence.

## 6. Acceptance boundaries

The runner is useful only when it can produce digest-bound V1 evidence for the
19 currently missing fixture IDs. Its existence changes no Phase 0 gate state.
Phase 0 exit remains blocked until every required V1 capture and all
authority-cutover proof bindings exist and receive their own ratification.

## 7. Supersession rule

For the controlled-fake runner only, this R2 contract supersedes the ambiguous
event names in `V1-CONTROLLED-FAKE-CONFORMANCE-SPEC-R1.md`. R1 remains a
historical design input and is not modified.
