# Incident / Problem / Change Guide

## Keep the three separate

- **Incident**: An event currently affecting users/operations — prioritize recovery
- **Problem**: A recurring or structural cause — prioritize root cause analysis/recurrence prevention
- **Change**: A fix or config/schema/catalog/procedure change — prioritize verification/promotion

One Incident can lead to multiple Changes, and multiple Incidents can be grouped into one Problem.

## Incident flow

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

## Problem flow

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

`5 Whys` does not require a single cause. When multiple causes are involved, such as concurrency, configuration, and provider drift, record them in a causal graph.

## Change flow

Every Change includes at least the following.

- reason/evidence
- blast radius
- rollback/forward-fix consideration
- linked requirement/tests
- release gate
- post-change observation

Restore the same traceability after an emergency change.
