# PeerHub Continuous Improvement Feedback Loop

`08_LIFECYCLE/` is the SSOT for the complete operational loop after development/testing.

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
  NO -> record evidence + no architecture change
  YES -> contract/conformance RED -> adopt -> release -> observe
```

## Closure Rules

Do not close at `Done` or `Released` alone. Use the four stages in `08_LIFECYCLE/README.md`: **Done → Released → Operated → Closed**.
