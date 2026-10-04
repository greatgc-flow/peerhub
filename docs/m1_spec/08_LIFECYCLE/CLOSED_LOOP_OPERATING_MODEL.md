# Closed-loop Operating Model

## 1. 원칙

1. **Evidence first** — 의견보다 재현 가능한 Record, CI output, package hash, live canary 결과를 우선합니다.
2. **Invariant first** — 성능 목표보다 데이터/전달 정확성 불변식을 먼저 지킵니다.
3. **No silent repair** — corruption, uncertain execution, migration ambiguity를 임의 자동복구하지 않습니다.
4. **Every signal is classified** — 무시가 아니라 `FIX / ROLLBACK / MITIGATE / ACCEPT / DEFER / INVALID / DUPLICATE` 중 하나로 종료합니다.
5. **Every fix returns to RED** — 운영에서 발견된 defect는 가능한 경우 재현 테스트를 먼저 추가합니다.
6. **No-code / declarative first** — 임계치·rollout·catalog는 설정/JSON으로 두고 구현에 박지 않습니다.
7. **Core stays small** — 운영상 필요가 생겼다고 Core 객체를 즉시 늘리지 않습니다. 먼저 Extension/Skill/Catalog로 흡수 가능한지 검토합니다.

## 2. lifecycle

| 단계 | 핵심 질문 | 필수 산출물 | 실패 시 |
|---|---|---|---|
| L0 Intake | 왜 바꾸는가? | evidence + requirement/decision | classify/defer |
| L1 RED | 실패를 재현했는가? | failing test/eval 또는 test 불가 사유 | evidence 보강 |
| L2 Build | 최소 변경인가? | implementation + trace | L1 |
| L3 Verify | deterministic gate가 green인가? | test evidence | L2 |
| L4 Package | fresh install에서도 동일한가? | artifact + hash + clean smoke | L2/L3 |
| L5 Canary | 실제 CLI/provider에서 사실인가? | live evidence | hold/rollback |
| L6 Release | 배포 증적이 완전한가? | release evidence index | rollback/hold |
| L7 Observe | 실제 사용 중 불변식이 유지되는가? | stabilization observation | incident/triage |
| L8 Learn | 신호의 원인과 유형을 분류했는가? | feedback/problem record | reproduce |
| L9 Improve | 요구·test·docs/catalog가 함께 갱신됐는가? | linked change set | L1 |
| L10 Close | 재발 감시까지 끝났는가? | closure evidence | L7/L8 |

## 3. 즉시 중단 불변식

정확한 목록과 requirement 연결은 `closed-loop.json`의 `invariants[]`가 SSOT이며, 사람용 View는 `INVARIANT_CATALOG.md`입니다.

이 불변식들은 수치 SLO가 아니라 **zero-tolerance correctness contract**입니다. 하나라도 관측되면 해당 `on_breach`에 따라 release/canary를 멈추고 incident/rollback/hold 경로로 이동합니다.

## 4. 성능·용량은 설정값

Latency, backlog, observation staleness, uncertainty rate 등의 목표값은 패키지 문서에 고정하지 않습니다.

```text
baseline observation
→ environment-specific threshold candidate
→ ratify
→ config/catalog
→ alert / release gate
→ periodic recalibration
```

측정되지 않은 숫자를 SLO로 선언하지 않습니다.

## 5. 한 변경의 종료 조건

변경은 다음이 모두 참이어야 `Closed`입니다.

- source evidence/requirement가 연결됨
- regression 또는 conformance test가 연결됨; 불가능하면 사유 기록
- deterministic gate 통과
- 해당 시 real-provider/live gate 통과
- artifact/hash/clean-install 증적 존재
- 운영 signal이 terminal disposition으로 분류됨
- 문서/catalog/schema 영향 반영 또는 N/A 사유 존재
- rollback/mitigation 필요 여부 결정
- recurrence watch 결과 기록
