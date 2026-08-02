# Phase 0 Controlled-Fake Fixture Runner

This directory contains provider-free Phase 0 test-evidence tooling. It is not PeerHub product source and does not invoke providers, access the network, read credentials or live Hub files, perform authority cutover, mutate configuration, or implement the host mutation broker.

The normative contract is [`CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md`](../../docs/design/phase0/CONTROLLED-FAKE-RUNNER-CONTRACT-R2.md).

## Run the example

Run from the repository root:

    python tools/phase0_fixture_runner/run_fixture.py --event-script tools/phase0_fixture_runner/examples/spawn-exit-0.json --fixture-id EXAMPLE-01 --out-root .phase0-fixture-example

`--out-root` is the exact fresh run root. It must not already exist. The runner writes `event-script.json`, `journal.jsonl`, `state-before.json`, `state-after.json`, `transcript.json`, `transcript.sha256`, and finally `fixture-record.json` inside that root.

A successful example produces a fixture record whose status is `V1_CAPTURE`. Unsupported events, contract violations, failed expectations, and contained runner errors produce distinct non-capture statuses. A genuine runner crash or invalid CLI invocation exits nonzero.

## Event-script format

A schema-version-1 script contains:

- `clock`: one injected, nondecreasing integer monotonic value per event.
- `ids`: one injected run ID followed by one unique event ID per event.
- `events`: ordered event objects.
- `interrupt_after_append`: optional zero-based index used to simulate a crash after the indexed journal append and before in-memory reduction. It must identify the final scripted event.
- `expect`: optional deterministic assertions checked before `V1_CAPTURE` is emitted.

The supported event names are exactly:

- `INTENT_PERSISTED`
- `SPAWNED`
- `CHUNK`
- `EXIT`
- `SILENCE`
- `PROCESS_DEADLINE`
- `CANCEL_ACK`
- `TREE_STATE`
- `CLEANUP_ERROR`

`SPAWNED` carries an `identity` object with a stable `token`. `TREE_STATE` carries an `identities` array whose members also have stable tokens. `CHUNK.bytes` is canonical Base64, and the `t` values on `CHUNK`, `SILENCE`, and `PROCESS_DEADLINE` must equal the corresponding injected clock value.

The runner appends and flushes one canonical JSONL journal record before validating or reducing each event. Recovery rebuilds state idempotently from that journal and records `external_dispatch_replayed: false`; an interrupted nonterminal attempt becomes `START_UNCERTAIN` with `MAY_HAVE_STARTED` certainty and requires explicit reconciliation.

## Tests

Run the stdlib-only tests from the repository root:

    python tools/phase0_fixture_runner/test_runner.py
