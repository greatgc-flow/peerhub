# M1 Definition of Done

## Core
- concurrent append canonical position 충돌 없음;
- idempotent replay;
- changed payload conflict;
- Offset CAS;
- restart durability.

## Peer/runtime
- same adapter kind N Peers;
- real Claude/Codex/Agy discovery/canary;
- session loss → fresh catch-up;
- session fingerprint change → fresh generation;
- pause before interrupt;
- MAY_HAVE_STARTED blind replay 금지;
- double Bridge delivery 방지.

## Observation
- quota/rate 구분;
- stale read-time 판정;
- UNKNOWN honesty;
- shared resource pool.

## Diag
- read-only enforcement;
- failure isolation.

## Release
- package build/install;
- real provider gate;
- docs/version/schema consistency;
- clean workspace smoke.
