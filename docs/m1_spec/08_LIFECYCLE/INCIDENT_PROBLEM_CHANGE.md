# Incident / Problem / Change Guide

## 세 개를 분리합니다

- **Incident**: 지금 사용자/운영에 영향을 주는 사건 — 복구 우선
- **Problem**: 반복 가능하거나 구조적인 원인 — root cause/재발 방지 우선
- **Change**: 수정·설정·schema·catalog·procedure 변경 — 검증/승격 우선

하나의 Incident가 여러 Change를 만들 수 있고, 여러 Incident가 하나의 Problem으로 합쳐질 수 있습니다.

## Incident 흐름

```text
Detect
→ preserve evidence
→ classify severity/impact
→ contain
→ restore service/safe state
→ decide rollback/forward-fix
→ link Problem if structural
→ post-incident learning
```

## Problem 흐름

```text
Evidence cluster
→ reproducibility
→ 5 Whys / causal chain
→ root cause vs contributing factors
→ requirement gap?
→ regression test/eval
→ minimum corrective change
→ release
→ recurrence watch
```

`5 Whys`는 반드시 하나의 단일 원인을 만들기 위한 도구가 아닙니다. 동시성·설정·provider drift처럼 복수 원인이 있으면 causal graph로 남깁니다.

## Change 흐름

모든 Change는 최소한 다음을 가집니다.

- reason/evidence
- blast radius
- rollback/forward-fix consideration
- linked requirement/tests
- release gate
- post-change observation

Emergency change도 사후에 동일한 traceability를 복구합니다.
