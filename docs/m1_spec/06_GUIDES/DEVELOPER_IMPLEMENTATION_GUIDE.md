# Developer Implementation Guide

## Sequence
1. Core contracts + RED tests.
2. fresh SQLite minimal store.
3. fake Session Bridge.
4. concurrency/crash/idempotency.
5. real CC/CX/AG adapters.
6. session continuity/control.
7. Observation.
8. status/peers/logs.
9. Readonly Diag.
10. real live release gate.

## Reuse
SQLite UoW/CAS, binary resolution, Windows process cancellation, session fingerprint,
quota parsing, real provider canary experience.

## Rewrite
Peer/Stream/Record/Offset and new extension ports.

## Prohibited
Do not just rename existing governance/routing/task services and move them to M1.

## TDD execution

Follow Wave 0→6 in `06_GUIDES/TEST_SET/RED_SEQUENCE.md` for implementation order. In particular, do not advance actual provider adapter implementation before SQLite concurrency/crash is GREEN.
