# Feedback Triage / Learning Guide

## Input signals

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

## Classification order

```text
1. Is there evidence?
2. Is it reproducible?
3. Does it concern an M1 Core invariant, an Extension, or operations/docs?
4. Is it a current contract violation or a new requirement?
5. Is immediate rollback/containment needed?
6. Can it be captured in a test/eval?
7. Which terminal disposition will close it?
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

`NOT_REPRODUCED` does not mean deleting evidence. Preserve observed facts for comparison if the issue recurs.

`ROLLED_BACK` can be **a terminal disposition for that Change/Release item**, but does not automatically close an Incident/Problem with an unresolved cause. If the root cause remains, link a separate Problem/next lifecycle item; that link or an N/A rationale is required for `Closed`.

## Promoting lessons learned

```text
one-time note
→ repeated evidence
→ Problem candidate
→ requirement/test/eval
→ implementation/process/catalog change
→ verified release
→ reusable Skill/Guide (if procedural)
```

Before putting procedural lessons into Core code, first consider whether an Agent Skill/Guide can capture them.
