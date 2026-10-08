# M1 Definition of Done

## Core
- concurrent/multi-process append canonical position 충돌 없음;
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
- MAY_HAVE_STARTED blind replay 금지;
- duplicate terminal/restart idempotency;
- double Bridge delivery/fencing 방지.

## Observation
- quota/rate 구분;
- stale read-time 판정 + TTL/clock-skew boundary;
- UNKNOWN honesty;
- shared resource pool;
- probe/quota failure가 Core/routing을 암묵 변경하지 않음.

## Diag
- strict read-only enforcement;
- concurrent writer에서도 consistent snapshot;
- optional-source failure isolation;
- corrupt authoritative store는 explicit failure, auto-repair 금지.

## Migration / Cutover
- 현재 Core schema 호환: 지원 버전 업그레이드, 미래/미지원 schema는 쓰기 없이 거부(MIG-001..003).

## Release
- package build/install/runtime-data completeness;
- Python/OS declared support matrix gate;
- real provider gate;
- docs/version/schema/catalog consistency;
- clean workspace smoke/restart.

## Recursive MECE Test Traceability

`06_GUIDES/TEST_SET/`가 machine-readable SSOT입니다.
현재 **85 requirements / 205 tests**이며 다음을 validator가 강제합니다.

- requirement coverage 100%
- required dimension coverage 100%
- OPEN state transition 0
- OPEN exception 0
- uncovered REQUIRED interaction 0
- orphan/dangling test reference 0

## Lifecycle closure

M1 Definition of Done은 publish로 끝나지 않습니다. `08_LIFECYCLE/`의 **Done → Released → Operated → Closed**를 적용합니다. 실제 운영 중 correctness invariant 위반이 없어야 하고, 발견된 signal은 terminal disposition 또는 다음 lifecycle item으로 연결되며 recurrence watch까지 기록되어야 합니다.

