# PeerHub 선순환 Feedback Loop

## Runtime defect

```text
Operational signal
→ structured evidence
→ reproduce
→ root cause
→ Record/Bridge/Observation requirement update
→ RED regression
→ fix
→ real CLI canary
→ release
→ recurrence check
→ FIXED / accepted risk / deferred
```

## Vendor/Model change

```text
CLI/provider update
→ refresh-model-catalog Skill
→ official/live evidence
→ catalog candidate
→ JSON Schema validate
→ adapter compatibility test
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
```

## Standard update

```text
upstream standard release
→ official source
→ actual boundary impact?
  NO -> record, no architecture change
  YES -> contract/conformance RED test -> adopt
```
