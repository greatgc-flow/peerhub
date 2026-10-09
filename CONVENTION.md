# PeerHub conventions

## Ownership

The communication Core owns only Peer, Stream, Record and Offset. It never imports extensions. Keep Task/Work, Memory, Routing, Orchestration, policy and provider-specific runtime behavior in replaceable extensions.

Use role-based package and file names: `core`, `extensions`, `cli`, and a capability's name. Development milestones belong in roadmap and audit records. Existing immutable Record kinds, schema IDs and durable table names are compatibility contracts; renaming a Python module is not permission to rewrite them.

The CLI translates public input into Core or extension operations. It has no legacy runtime proxy. The v0 runtime and importer are removed; v0 source is archived on branch `legacy/v0-main-final`.

## Durability and evidence

Records are authoritative. Views and search indexes are rebuildable projections. Preserve response-before-Offset ordering, fenced execution claims, invocation markers, and the ban on blind replay after uncertain execution.

Observation evidence is append-only. Quota and rate limits are distinct. Unknown evidence is never zero, healthy or unlimited. Derive freshness at read time.

Diagnostics use a read-only snapshot and do not collect observations, initialize storage, repair state or contact providers. Collection is an explicit command. Store discovery is read-only and never renames a live database or its WAL files.

## Tests

Keep tests with their capability group: `tests/communication`, `tests/continuity`, or `tests/collaboration`. Shared communication fixtures and subprocess fakes live under `tests/communication/harness`.

Default tests require no authenticated provider or network access. Real provider tests require the declared opt-in and a live/slow/e2e marker. Use `catalog_id` to retain frozen requirement IDs without making development milestones part of the current test layout.

Prefer meaningful independent oracles: persisted rows, subprocess results, transaction boundaries, source checksums and package contents. Do not fabricate successful evidence for unavailable or skipped gates.

## Changes and validation

Keep strict type checking, bounded subprocess time/output, explicit errors and documented JSON/exit contracts. Run tests appropriate to the change, Pyright and the parser-derived command inventory check. Preserve frozen specifications and historical evidence; record source-path changes in the current implementation documentation.

Version changes, tags, publishing and production promotion are separate operations with candidate-matched evidence. Preserve user data and report the recovery path when retiring source files.
