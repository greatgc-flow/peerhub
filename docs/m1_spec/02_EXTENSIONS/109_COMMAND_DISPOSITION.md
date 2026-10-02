# 기존 109 Leaf Commands 전수 Disposition

이 표는 **삭제 목록이 아니라 책임 이관표**입니다. N차 milestone 우선순위는 별도 논의합니다.

> 현행 근거: `greatgc-flow/peerhub` main HEAD `57a137cd6a0cc7e89124ea2b87b72995087627a5`의
> `docs/design/peerhub-production-call-map-R1.json`은 109 leaf commands를 기록합니다.
> 기존 107 표에서 누락되었던 `workspace reset`, `backup global`을 현재 call-map 순서대로 보완했습니다.

| # | Current command | Effect | Disposition | Target | Milestone |
|---:|---|---|---|---|---|
| 1 | `workspace init` | MUTATING | SUPERSEDE_M1 | M1 Infrastructure | M1 |
| 2 | `workspace reset` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Backup/Recovery | N |
| 3 | `status` | CONDITIONAL_MUTATION_EXTERNAL | SUPERSEDE_M1 | M1 View | M1 |
| 4 | `config paths` | READ_ONLY | SUPERSEDE_M1 | M1 Infrastructure | M1 |
| 5 | `config migrate` | MUTATING | FUTURE_EXTENSION | Compatibility/Migration | N |
| 6 | `config validate` | READ_ONLY | SUPERSEDE_M1 | M1 Infrastructure | M1 |
| 7 | `config init` | MUTATING | SUPERSEDE_M1 | M1 Infrastructure | M1 |
| 8 | `backup workspace` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Backup/Recovery | N |
| 9 | `backup global` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Backup/Recovery | N |
| 10 | `backup restore` | MUTATING | FUTURE_EXTENSION | Backup/Recovery | N |
| 11 | `adapter discover` | EXTERNAL_OBSERVATION | M1_EXTENSION | Observation Extension | M1 |
| 12 | `diag` | MUTATING_EXTERNAL | M1_EXTENSION | Diag Extension | M1 |
| 13 | `broadcast` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Orchestration | N |
| 14 | `health revalidate` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Health/Recovery | N |
| 15 | `health check` | CONDITIONAL_MUTATION_EXTERNAL | FUTURE_EXTENSION | Health/Recovery | N |
| 16 | `health precheck` | READ_ONLY | FUTURE_EXTENSION | Health/Recovery | N |
| 17 | `health sweep` | MUTATING | FUTURE_EXTENSION | Health/Recovery | N |
| 18 | `peer status` | READ_ONLY | M1_EXTENSION | Observation Extension/View | M1 |
| 19 | `peer quarantine` | MUTATING | FUTURE_EXTENSION | Health/Recovery | N |
| 20 | `peer recover` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Health/Recovery | N |
| 21 | `lease status` | READ_ONLY | M1_EXTENSION | Session Bridge View | M1 |
| 22 | `lease sweep` | MUTATING | FUTURE_EXTENSION | Resource/Recovery | N |
| 23 | `broker status` | READ_ONLY | FUTURE_EXTENSION | Governance/Effects | N |
| 24 | `gate check` | READ_ONLY | FUTURE_EXTENSION | Policy/Admission | N |
| 25 | `ask` | MUTATING_EXTERNAL | M1_EXTENSION | Session Bridge Extension | M1 |
| 26 | `statusline` | READ_ONLY | M1_EXTENSION | Observation View | M1 |
| 27 | `consensus propose` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | N |
| 28 | `consensus proposal-add` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | N |
| 29 | `consensus proposal-vote` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | N |
| 30 | `consensus list` | READ_ONLY | FUTURE_EXTENSION | Governance/Consensus | N |
| 31 | `consensus arbiter-review` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Governance/Consensus | N |
| 32 | `consensus vote` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | N |
| 33 | `consensus status` | READ_ONLY | FUTURE_EXTENSION | Governance/Consensus | N |
| 34 | `consensus ack` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | N |
| 35 | `consensus nack` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | N |
| 36 | `consensus correct` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | N |
| 37 | `consensus retract` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | N |
| 38 | `task create` | MUTATING | FUTURE_EXTENSION | Task/Workflow | N |
| 39 | `task claim-start` | MUTATING | FUTURE_EXTENSION | Task/Workflow | N |
| 40 | `task checkpoint` | MUTATING | FUTURE_EXTENSION | Task/Workflow | N |
| 41 | `task complete` | MUTATING | FUTURE_EXTENSION | Task/Workflow | N |
| 42 | `task fail` | MUTATING | FUTURE_EXTENSION | Task/Workflow | N |
| 43 | `task cancel` | MUTATING | FUTURE_EXTENSION | Task/Workflow | N |
| 44 | `task status` | READ_ONLY | FUTURE_EXTENSION | Task/Workflow | N |
| 45 | `lesson propose` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 46 | `lesson approve` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 47 | `lesson activate` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 48 | `lesson retire` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 49 | `lesson supersede` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 50 | `lesson quarantine` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 51 | `lesson broadcast` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 52 | `lesson status` | READ_ONLY | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 53 | `lesson sweep` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 54 | `directive add` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 55 | `directive migrate` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 56 | `directive clear` | MUTATING | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 57 | `directive list` | READ_ONLY | FUTURE_EXTENSION | Knowledge/Policy/Skills | N |
| 58 | `node register` | MUTATING | ABSORB_M1 | Core | M1 |
| 59 | `node list` | READ_ONLY | ABSORB_M1 | M1 View | M1 |
| 60 | `node bind-profile` | MUTATING | M1_EXTENSION | Observation/Catalog Extension | M1 |
| 61 | `node model-status` | READ_ONLY | M1_EXTENSION | Observation/Catalog View | M1 |
| 62 | `lock acquire` | MUTATING | FUTURE_EXTENSION | Resource Coordination | N |
| 63 | `lock release` | MUTATING | FUTURE_EXTENSION | Resource Coordination | N |
| 64 | `lock status` | READ_ONLY | FUTURE_EXTENSION | Resource Coordination | N |
| 65 | `artifact claim` | MUTATING | FUTURE_EXTENSION | Artifact | N |
| 66 | `artifact status` | CONDITIONAL_MUTATION | FUTURE_EXTENSION | Artifact | N |
| 67 | `artifact finalize` | MUTATING | FUTURE_EXTENSION | Artifact | N |
| 68 | `role assign` | MUTATING | FUTURE_EXTENSION | Role/Orchestration | N |
| 69 | `role release` | MUTATING | FUTURE_EXTENSION | Role/Orchestration | N |
| 70 | `role status` | READ_ONLY | FUTURE_EXTENSION | Role/Orchestration | N |
| 71 | `routing discover` | READ_ONLY | FUTURE_EXTENSION | Routing | N |
| 72 | `routing elect-leader` | MUTATING | FUTURE_EXTENSION | Routing | N |
| 73 | `routing import-capabilities` | MUTATING | FUTURE_EXTENSION | Routing | N |
| 74 | `leadership claim` | MUTATING | FUTURE_EXTENSION | Leadership/Orchestration | N |
| 75 | `leadership yield` | MUTATING | FUTURE_EXTENSION | Leadership/Orchestration | N |
| 76 | `leadership status` | READ_ONLY | FUTURE_EXTENSION | Leadership/Orchestration | N |
| 77 | `feedback add` | MUTATING | FUTURE_EXTENSION | Feedback/Ops | N |
| 78 | `feedback list` | READ_ONLY | FUTURE_EXTENSION | Feedback/Ops | N |
| 79 | `feedback resolve` | MUTATING | FUTURE_EXTENSION | Feedback/Ops | N |
| 80 | `error report` | MUTATING | FUTURE_EXTENSION | Operational Review | N |
| 81 | `error review list` | READ_ONLY | FUTURE_EXTENSION | Operational Review | N |
| 82 | `error review resolve` | MUTATING | FUTURE_EXTENSION | Operational Review | N |
| 83 | `alert raise` | MUTATING | FUTURE_EXTENSION | Notification | N |
| 84 | `room create` | MUTATING | ABSORB_M1 | Core | M1 |
| 85 | `room thread-new` | MUTATING | ABSORB_M1 | Core | M1 |
| 86 | `room create-thread` | MUTATING | ABSORB_M1 | Core | M1 |
| 87 | `room append-message` | MUTATING | ABSORB_M1 | Core | M1 |
| 88 | `room send` | MUTATING | ABSORB_M1 | Core | M1 |
| 89 | `room broadcast` | MUTATING | ABSORB_M1 | Core+Session Bridge | M1 |
| 90 | `room check-inbox` | READ_ONLY | ABSORB_M1 | Core | M1 |
| 91 | `room mark-read` | MUTATING | ABSORB_M1 | Core | M1 |
| 92 | `room promote-message` | MUTATING | FUTURE_EXTENSION | Collaboration UX | N |
| 93 | `room react` | MUTATING | FUTURE_EXTENSION | Collaboration UX | N |
| 94 | `room unreact` | MUTATING | FUTURE_EXTENSION | Collaboration UX | N |
| 95 | `room append-handoff` | MUTATING | ABSORB_M1 | Core | M1 |
| 96 | `room checkpoint` | MUTATING | ABSORB_M1 | Core | M1 |
| 97 | `room context-fill` | READ_ONLY | ABSORB_M1 | M1 View | M1 |
| 98 | `room update-status` | MUTATING | ABSORB_M1 | Core | M1 |
| 99 | `room clear` | MUTATING | ABSORB_M1 | Core | M1 |
| 100 | `room rebuild-session-bindings` | MUTATING | M1_EXTENSION | Session Bridge Extension | M1 |
| 101 | `room status` | READ_ONLY | ABSORB_M1 | M1 View | M1 |
| 102 | `duty claim` | MUTATING | FUTURE_EXTENSION | Duty/Resource Coordination | N |
| 103 | `duty heartbeat` | MUTATING | FUTURE_EXTENSION | Duty/Resource Coordination | N |
| 104 | `duty close` | MUTATING | FUTURE_EXTENSION | Duty/Resource Coordination | N |
| 105 | `duty sweep` | MUTATING | FUTURE_EXTENSION | Duty/Resource Coordination | N |
| 106 | `duty status` | READ_ONLY | FUTURE_EXTENSION | Duty/Resource Coordination | N |
| 107 | `session open` | MUTATING | M1_EXTENSION | Session Bridge Extension | M1 |
| 108 | `session heartbeat` | MUTATING | M1_EXTENSION | Session Bridge Extension | M1 |
| 109 | `session close` | MUTATING | M1_EXTENSION | Session Bridge Extension | M1 |
