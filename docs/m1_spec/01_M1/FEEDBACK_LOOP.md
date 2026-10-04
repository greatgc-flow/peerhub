# PeerHub 선순환 Feedback Loop

개발/테스트 이후의 완전한 운영 loop SSOT는 `08_LIFECYCLE/`입니다.

```text
Evidence / Requirement
→ RED
→ Minimum Implementation
→ Deterministic Verification
→ Package / Clean Install
→ Real Runtime Canary
→ Release
→ Stabilization Observation
→ Feedback / Incident / Drift
→ Reproduce / Root Cause / Decision
→ Requirement + Regression + Evidence Update
→ Re-verify / Promote / Rollback
→ Closure + Recurrence Watch
↺
```

## Runtime defect

```text
Operational signal
→ preserve structured evidence
→ reproduce
→ root cause / contributing factors
→ requirement update
→ RED regression
→ fix
→ clean install + real CLI canary
→ release
→ recurrence watch
→ terminal disposition
```

## Vendor/Model change

```text
CLI/provider update
→ refresh-model-catalog Skill
→ official/live evidence
→ catalog candidate
→ JSON Schema validate
→ adapter compatibility test
→ changed-profile canary
→ release
→ observation
```

## Skill improvement

```text
failure/inefficiency
→ Skill candidate
→ eval dataset
→ version
→ compare
→ promote/rollback
→ operation evidence
```

## Standard update

```text
upstream standard release
→ official source
→ actual boundary impact?
  NO -> evidence 기록 + no architecture change
  YES -> contract/conformance RED -> adopt -> release -> observe
```

## 종료 규칙

`Done`이나 `Released`만으로 닫지 않습니다. `08_LIFECYCLE/README.md`의 **Done → Released → Operated → Closed** 네 단계를 사용합니다.
