# RED Sequence — Implementation Order

목표는 구현 편의가 아니라 가장 위험한 불변식을 먼저 실패시키는 것입니다.

## Wave 0 — Meta / Contract / Schema
- META-001..004
- ARCH-001..005
- SCH-001..013
- PROP-007/008
- 상태/예외/interaction validator 먼저 RED

## Wave 1 — Core Domain
- Peer identity
- Stream revision/OPEN→CLOSED/member integrity
- Record immutable/order/idempotency/canonicalization
- Offset CAS/monotonic/head bound/isolation

## Wave 2 — SQLite / Multi-process / Migration
- WAL/FK/UoW
- thread + **real multi-process** races
- busy/full/readonly/corrupt DB
- crash-safe ordered migration

## Wave 3 — Claim / Session / Execution Certainty
- claim expiry exact boundary + fencing
- NOT_STARTED / MAY_HAVE_STARTED / STARTED / TERMINAL transitions
- duplicate terminal/restart idempotency
- resume failure → bounded fresh generation

## Wave 4 — Control / Context Continuity
- pause/cancel intent before side effect
- bounded catch-up projection
- context/session loss recovery
- redirect/new-goal continuity P1

## Wave 5 — Observation / Diag
- freshness TTL + clock skew/out-of-order capture
- quota vs rate-limit / no auto routing
- probe isolation
- read-only snapshot under concurrent writer

## Wave 6 — Fake Runtime E2E
- restart/crash/restore/session loss/quota exhaustion
- extensions disabled Core path

## Wave 7 — Package / Compatibility / Cutover
- legacy importer
- 109/109 disposition guard
- runtime-data artifact contents
- clean install + Python/OS matrix

## Wave 8 — Real Provider Live
- CC/CX/AG canary
- advertised capability/version evidence

## Wave 9 — Scheduled Soak
- large Stream / many Peer/Stream cardinality
- evidence-only capacity trends until explicit SLO ratified

각 Wave는 다음 Wave 진입 전에 자기 requirement dimensions + state/exception/interaction coverage가 GREEN이어야 합니다.
