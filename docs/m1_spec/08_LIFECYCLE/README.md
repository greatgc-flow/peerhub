# 개발·테스트 이후 Closed Feedback Loop

이 디렉터리는 **개발 완료를 종료점으로 보지 않습니다.**
M1은 아래 순환이 증적으로 닫힐 때 하나의 변경이 종료됩니다.

```text
Requirement / Evidence
→ RED
→ Minimum Implementation
→ Deterministic Verification
→ Package / Clean Install
→ Real Runtime Canary
→ Release
→ Stabilization Observation
→ Feedback / Incident / Drift
→ Triage + Reproduce
→ Root Cause / Decision
→ Requirement + Regression + Evidence Update
→ Re-verify / Promote / Rollback
→ Closure + Recurrence Watch
↺
```

## SSOT

- 기계 판독형 lifecycle: `closed-loop.json`
- invariant catalog: `INVARIANT_CATALOG.md` (View; SSOT는 `closed-loop.json`)
- lifecycle shape: `closed-loop.schema.json`
- release gate: `release-gates.json`
- test→gate traceability: `../06_GUIDES/TEST_SET/TEST_RELEASE_GATE_MAP.json`
- release gate shape: `release-gates.schema.json`
- 운영 신호 분류: `signal-routing.json`
- 운영 신호 shape: `signal-routing.schema.json`

문서들은 위 SSOT를 설명하는 사람용 Guide입니다.

## 읽는 순서

1. `CLOSED_LOOP_OPERATING_MODEL.md`
2. `RELEASE_PROMOTION_ROLLBACK.md`
3. `OPERATIONS_OBSERVABILITY_SLO.md`
4. `INCIDENT_PROBLEM_CHANGE.md`
5. `FEEDBACK_TRIAGE_AND_LEARNING.md`
6. `EVIDENCE_RETENTION_AND_AUDIT.md`
7. `DEPRECATION_EXTENSION_LIFECYCLE.md`
8. `POST_RELEASE_REVIEW.md`
9. 상황별 `RUNBOOKS/`
10. 실제 기록 시 `TEMPLATES/`

## 네 개의 완료 상태

| 상태 | 의미 |
|---|---|
| **Done** | 요구사항과 구현이 테스트를 통과 |
| **Released** | 패키지/실환경 gate를 통과하여 배포 |
| **Operated** | 안정화 관찰 기간 동안 핵심 불변식 위반 없이 실제 사용 증거 확보 |
| **Closed** | 피드백·위험·회귀·문서가 모두 분류되고 recurrence watch까지 종료 |

`Done != Closed` 입니다.
