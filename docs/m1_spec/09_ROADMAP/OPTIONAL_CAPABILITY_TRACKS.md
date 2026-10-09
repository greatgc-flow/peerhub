# M4+ Optional Capability Tracks

M4 and beyond are not a mandatory sequence.

| Track | Name | Default State | Activation Rationale |
|---|---|---|---|
| M4-A | Advanced Health & Recovery | OFF | Repeated stuck/dead runtime and manual recovery costs |
| M4-B | Resource Coordination | OFF | Actual quota/concurrency contention |
| M4-C | Advanced Governance | OFF | Organizational approval/separation of duties/compliance needs |
| M4-D | Role / Duty / Leadership | OFF | Unable to coordinate responsibilities purely with capability+policy |
| M4-E | Notification | OFF | Repetitive operational burden requiring human polling |
| M4-F | UI / TUI / Web | OFF | Insufficient visibility/usability with only CLI/API |
| M4-G | Enterprise / Multi-user | OFF | SSO/tenant/audit/secret requirements |
| M4-H | Advanced Eval / Optimization | OFF | Measured value in optimizing route/model selection |
| M4-I | HA / Federation | OFF | Unacceptable single-node failure cost |
| M4-J | Extension Ecosystem | OFF | Need for third-party extension demand/deployment |
| M4-K | Advanced Second Brain | OFF | Demand for temporal/entity/decision lineage |
| M4-L | Connectors / Business Apps | OFF | Repetitive demand for specific external system integration |

## Common Activation Gate

1. Operational evidence exists.
2. Document root causes using the 5 Whys.
3. Record that it cannot be solved with a combination of existing M1~M3 capabilities.
4. Confirm it can be isolated as an Extension without new Core concepts.
5. Define the Requirement + RED test + rollback/disable path first.
6. Deploy as opt-in.

## Dependency Principles
All Optional Tracks are opt-in by default after M3 baseline completion. They are not sequential milestones to each other but reuse existing capabilities. `roadmap.json.depends_on_capabilities` is not a DAG to enforce implementation order but explicitly states **need-based capability dependencies**. Tracks that do not require the dependency can be bypassed.
