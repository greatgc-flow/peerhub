# M1 Definition of Done

## Core
- no canonical position conflicts during concurrent/multi-process append;
- idempotent replay / changed payload conflict;
- Stream revision CAS + CLOSED append rejection;
- Offset CAS + monotonic + head-bound;
- restart durability;
- disk-full/readonly/corrupt-store fail-closed;
- crash-safe ordered migration.

## Peer/runtime
- same adapter kind N Peers;
- real Claude/Codex/Agy discovery/canary;
- session loss/resume failure → bounded fresh catch-up;
- session fingerprint change → fresh generation;
- pause/cancel intent before runtime side effect;
- no blind replay of MAY_HAVE_STARTED;
- duplicate terminal/restart idempotency;
- prevent duplicate Bridge delivery/fencing.

## Observation
- distinguish quota/rate;
- determine staleness at read time + TTL/clock-skew boundary;
- UNKNOWN honesty;
- shared resource pool;
- probe/quota failures do not implicitly change Core/routing.

## Diag
- strict read-only enforcement;
- consistent snapshot even with concurrent writers;
- optional-source failure isolation;
- explicit failure for a corrupt authoritative store; no auto-repair.

## Migration / Cutover
- current Core schema compatibility: upgrade supported versions; reject future/unsupported schemas without writing (MIG-001..003).

## Release
- package build/install/runtime-data completeness;
- Python/OS declared support matrix gate;
- real provider gate;
- docs/version/schema/catalog consistency;
- clean workspace smoke/restart.

## Recursive MECE Test Traceability

`06_GUIDES/TEST_SET/` is the machine-readable SSOT.
There are currently **85 requirements / 207 tests**, and the validator enforces the following.

- requirement coverage 100%
- required dimension coverage 100%
- OPEN state transition 0
- OPEN exception 0
- uncovered REQUIRED interaction 0
- orphan/dangling test reference 0

## Lifecycle closure

The M1 Definition of Done extends beyond publication. Apply **Done → Released → Operated → Closed** from `08_LIFECYCLE/`. Correctness invariants must hold during actual operation. Every detected signal must lead to a terminal disposition or the next lifecycle item, with recurrence watch also recorded.

