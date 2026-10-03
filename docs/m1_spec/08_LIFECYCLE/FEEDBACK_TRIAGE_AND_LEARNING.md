# Feedback Triage / Learning Guide

## 입력 신호

- runtime defect / regression
- provider/model/CLI drift
- migration/storage anomaly
- performance/capacity degradation
- security/integrity anomaly
- usability/operational friction
- diagnostic/observation gap
- documentation/process drift
- upstream standard change
- feature request

## 분류 순서

```text
1. Evidence가 있는가?
2. 재현 가능한가?
3. M1 Core invariant인가, Extension인가, 운영/문서인가?
4. 현재 계약 위반인가, 새로운 요구인가?
5. 즉시 rollback/containment가 필요한가?
6. test/eval로 고정 가능한가?
7. 어떤 terminal disposition으로 닫을 것인가?
```

## terminal disposition

- `FIXED`
- `ROLLED_BACK`
- `MITIGATED`
- `ACCEPTED_RISK`
- `DEFERRED_WITH_TRIGGER`
- `INVALID_OR_NOT_REPRODUCED`
- `DUPLICATE_LINKED`
- `NO_CHANGE_REQUIRED`

`NOT_REPRODUCED`는 evidence 삭제가 아닙니다. 향후 재발 시 비교할 수 있도록 관찰 사실을 보존합니다.

`ROLLED_BACK`은 **해당 Change/Release item의 terminal disposition**일 수 있지만, 원인이 미해결인 Incident/Problem까지 자동으로 닫는 의미가 아닙니다. root cause가 남아 있으면 별도 Problem/next lifecycle item을 연결하고 그 링크 또는 N/A 사유가 있어야 `Closed`가 됩니다.

## 학습의 승격

```text
일회성 메모
→ 반복 evidence
→ Problem candidate
→ requirement/test/eval
→ implementation/process/catalog change
→ verified release
→ reusable Skill/Guide (절차라면)
```

절차적 학습은 Core 코드에 넣기 전에 Agent Skill/Guide로 흡수 가능한지 먼저 봅니다.
