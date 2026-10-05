# 109 Command Disposition

> **2026-10-05 status:** This is the frozen **legacy v0 `peerhub` CLI** 109-command disposition baseline. Current main also installs a separate `peerhub-m1` surface. This table is migration/compatibility evidence, not the target architecture or complete current installed CLI inventory. See `07_AUDIT/CURRENT_M1_IMPLEMENTATION_SNAPSHOT_20261005.json`.

| # | Command | Effect | Disposition | Target | Milestone | Reason |
|---:|---|---|---|---|---|---|
| 1 | `workspace init` | MUTATING | SUPERSEDE_M1 | M1 Infrastructure | M1 | Create/bind workspace and new minimal store |
| 2 | `workspace reset` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Backup/Recovery | M2 | v0.10.0 lifecycle feature; dry-run by default and --apply uses a pre-reset safety snapshot; preserve as future Backup/Recovery, not M1 Core |
| 3 | `status` | CONDITIONAL_MUTATION_EXTERNAL | SUPERSEDE_M1 | M1 View | M1 | Supersede current conditional-mutation semantics with read-only summary |
| 4 | `config paths` | READ_ONLY | SUPERSEDE_M1 | M1 Infrastructure | M1 | Resolve workspace/config paths |
| 5 | `config migrate` | MUTATING | SUPERSEDE | Generic Extension Host / Migration | M2 | Legacy config migration is superseded by explicit owned-schema/config migration contracts; do not recreate a generic legacy config domain. |
| 6 | `config validate` | READ_ONLY | SUPERSEDE_M1 | M1 Infrastructure | M1 | Validate minimal configuration |
| 7 | `config init` | MUTATING | SUPERSEDE_M1 | M1 Infrastructure | M1 | Initialize minimal configuration only |
| 8 | `backup workspace` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Backup/Recovery | M2 | Preserve proven SQLite online-backup lessons |
| 9 | `backup global` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Backup/Recovery | M2 | v0.10.0 global configuration backup; preserve fail-safe backup lessons as future Backup/Recovery, not M1 Core |
| 10 | `backup restore` | MUTATING | FUTURE_EXTENSION | Backup/Recovery | M2 | Preserve proven SQLite online-backup lessons |
| 11 | `adapter discover` | EXTERNAL_OBSERVATION | M1_MODULE | Observation Module | M1 | Discover CLI/adapters and emit structured observations |
| 12 | `diag` | MUTATING_EXTERNAL | M1_MODULE | Diag Module | M1 | Rewrite as strictly read-only observer; no refresh/persist/repair |
| 13 | `broadcast` | MUTATING_EXTERNAL | SUPERSEDE | Orchestration | M3 | Absorb fan-out/partial/deadline/idempotency lessons; supersede the legacy BroadcastCoordinator implementation with bounded M3 orchestration. |
| 14 | `health revalidate` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Health/Recovery | M4-A | M1 observes facts; admission/quarantine/recovery is later policy |
| 15 | `health check` | CONDITIONAL_MUTATION_EXTERNAL | FUTURE_EXTENSION | Health/Recovery | M4-A | M1 observes facts; admission/quarantine/recovery is later policy |
| 16 | `health precheck` | READ_ONLY | FUTURE_EXTENSION | Health/Recovery | M4-A | M1 observes facts; admission/quarantine/recovery is later policy |
| 17 | `health sweep` | MUTATING | FUTURE_EXTENSION | Health/Recovery | M4-A | M1 observes facts; admission/quarantine/recovery is later policy |
| 18 | `peer status` | READ_ONLY | M1_MODULE | Observation Module/View | M1 | Structured Peer snapshot view |
| 19 | `peer quarantine` | MUTATING | FUTURE_EXTENSION | Health/Recovery | M4-A | Mutating peer health policy is outside M1 |
| 20 | `peer recover` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Health/Recovery | M4-A | Mutating peer health policy is outside M1 |
| 21 | `lease status` | READ_ONLY | M1_MODULE | Session Bridge View | M1 | Reduced active-delivery/session-claim view; do not keep generic lease domain |
| 22 | `lease sweep` | MUTATING | FUTURE_EXTENSION | Resource/Recovery | M4-B | Generic lease sweeping not needed by minimal Bridge claim |
| 23 | `broker status` | READ_ONLY | FUTURE_EXTENSION | Governance/Effects | M3 | Governance effect delivery not M1 |
| 24 | `gate check` | READ_ONLY | FUTURE_EXTENSION | Policy/Admission | M3 | Policy gates not M1 |
| 25 | `ask` | MUTATING_EXTERNAL | M1_MODULE | Session Bridge Module | M1 | Deliver Record to one Peer runtime and append response Record |
| 26 | `statusline` | READ_ONLY | M1_MODULE | Observation View | M1 | Presentation-only projection |
| 27 | `consensus propose` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 28 | `consensus proposal-add` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 29 | `consensus proposal-vote` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 30 | `consensus list` | READ_ONLY | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 31 | `consensus arbiter-review` | MUTATING_EXTERNAL | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 32 | `consensus vote` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 33 | `consensus status` | READ_ONLY | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 34 | `consensus ack` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 35 | `consensus nack` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 36 | `consensus correct` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 37 | `consensus retract` | MUTATING | FUTURE_EXTENSION | Governance/Consensus | M4-C | Consensus is an optional workflow over Streams/Records |
| 38 | `task create` | MUTATING | SUPERSEDE | Work Projection | M2 | Map lifecycle intent to work.created over ordered Records; do not recreate the legacy Task engine. |
| 39 | `task claim-start` | MUTATING | SUPERSEDE | Orchestration | M3 | Basic work.started semantics are absorbed by M2 Work; exclusive claim/coordinator/attempt ownership is superseded by bounded M3 orchestration, not carried into M2. |
| 40 | `task checkpoint` | MUTATING | SUPERSEDE | Work Projection | M2 | Map to work.checkpointed + Artifact refs; Session/provider resume state remains Session Bridge-owned. |
| 41 | `task complete` | MUTATING | SUPERSEDE | Work Projection | M2 | Map to deterministic work.completed transition over ordered Records. |
| 42 | `task fail` | MUTATING | SUPERSEDE | Work Projection | M2 | Map to deterministic work.failed transition; retry policy stays outside Work truth. |
| 43 | `task cancel` | MUTATING | SUPERSEDE | Work Projection | M2 | Map to deterministic work.cancelled transition. |
| 44 | `task status` | READ_ONLY | SUPERSEDE | Work Projection | M2 | Replace legacy Task status with rebuildable Work projection. |
| 45 | `lesson propose` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 46 | `lesson approve` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 47 | `lesson activate` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 48 | `lesson retire` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 49 | `lesson supersede` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 50 | `lesson quarantine` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 51 | `lesson broadcast` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 52 | `lesson status` | READ_ONLY | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 53 | `lesson sweep` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 54 | `directive add` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 55 | `directive migrate` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 56 | `directive clear` | MUTATING | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 57 | `directive list` | READ_ONLY | SUPERSEDE | Skill/Catalog/Policy | M2 | Historical global lesson/directive surface is superseded by on-demand Skill, General Catalog and declarative Policy; do not recreate the old domain. |
| 58 | `node register` | MUTATING | ABSORB_M1 | Core | M1 | Peer register/identify |
| 59 | `node list` | READ_ONLY | ABSORB_M1 | M1 View | M1 | Peer list |
| 60 | `node bind-profile` | MUTATING | M1_MODULE | Observation/Runtime Target Catalog Module | M1 | Model/profile binding outside Core |
| 61 | `node model-status` | READ_ONLY | M1_MODULE | Observation/Catalog View | M1 | Model/profile status from structured catalog/observations |
| 62 | `lock acquire` | MUTATING | FUTURE_EXTENSION | Resource Coordination | M4-B | File/resource locking optional; worktree separation preferred for M1 |
| 63 | `lock release` | MUTATING | FUTURE_EXTENSION | Resource Coordination | M4-B | File/resource locking optional; worktree separation preferred for M1 |
| 64 | `lock status` | READ_ONLY | FUTURE_EXTENSION | Resource Coordination | M4-B | File/resource locking optional; worktree separation preferred for M1 |
| 65 | `artifact claim` | MUTATING | SUPERSEDE | Artifact / Work | M2 | Immutable artifacts are not claimed; in-progress ownership belongs to Work/resource policy. Preserve only useful ownership lessons. |
| 66 | `artifact status` | CONDITIONAL_MUTATION | SUPERSEDE | Artifact | M2 | Replace with read-only immutable artifact metadata/integrity view. |
| 67 | `artifact finalize` | MUTATING | SUPERSEDE | Artifact | M2 | Replace with staged digest/verify/atomic-ingest Artifact flow. |
| 68 | `role assign` | MUTATING | FUTURE_EXTENSION | Role/Orchestration | M4-D | PeerHub must not assign AI behavior roles in M1 |
| 69 | `role release` | MUTATING | FUTURE_EXTENSION | Role/Orchestration | M4-D | PeerHub must not assign AI behavior roles in M1 |
| 70 | `role status` | READ_ONLY | FUTURE_EXTENSION | Role/Orchestration | M4-D | PeerHub must not assign AI behavior roles in M1 |
| 71 | `routing discover` | READ_ONLY | SUPERSEDE | Routing | M3 | Absorb immutable request/evidence/deterministic decision concepts; supersede legacy routing dependencies with M1 Observation + M2 Catalog/Eval inputs. |
| 72 | `routing elect-leader` | MUTATING | FUTURE_EXTENSION | Role/Leadership | M4-D | Historical leader election belongs to optional Role/Duty/Leadership, not required M3 Routing. |
| 73 | `routing import-capabilities` | MUTATING | SUPERSEDE | Capability Catalog / Routing | M3 | Declared capabilities belong to M2 Catalog; M3 Routing consumes them. Supersede legacy routing-owned capability import. |
| 74 | `leadership claim` | MUTATING | FUTURE_EXTENSION | Leadership/Orchestration | M4-D | Leadership policy not M1 |
| 75 | `leadership yield` | MUTATING | FUTURE_EXTENSION | Leadership/Orchestration | M4-D | Leadership policy not M1 |
| 76 | `leadership status` | READ_ONLY | FUTURE_EXTENSION | Leadership/Orchestration | M4-D | Leadership policy not M1 |
| 77 | `feedback add` | MUTATING | SUPERSEDE | Eval/Telemetry + Lifecycle Feedback | M2 | Feedback becomes evidence/signal feeding Eval and the closed lifecycle loop; no separate Feedback domain engine. |
| 78 | `feedback list` | READ_ONLY | SUPERSEDE | Eval/Telemetry + Lifecycle Feedback | M2 | Read feedback/eval evidence projection; no separate Feedback authority. |
| 79 | `feedback resolve` | MUTATING | SUPERSEDE | Eval/Telemetry + Lifecycle Feedback | M2 | Disposition closes the lifecycle signal; learned procedure/fact routes to Skill/Catalog/Policy as appropriate. |
| 80 | `error report` | MUTATING | SUPERSEDE | Eval/Telemetry / Operational Evidence | M2 | Record operational evidence and regression signal; automatic remediation is M4-A. |
| 81 | `error review list` | READ_ONLY | SUPERSEDE | Eval/Telemetry / Operational Evidence | M2 | Read operational evidence projection; no separate operational-review authority. |
| 82 | `error review resolve` | MUTATING | SUPERSEDE | Eval/Telemetry / Lifecycle | M2 | Resolve to regression/requirement/risk disposition; recovery action remains M4-A when needed. |
| 83 | `alert raise` | MUTATING | FUTURE_EXTENSION | Notification | M4-E | Notification policy deferred |
| 84 | `room create` | MUTATING | ABSORB_M1 | Core | M1 | Stream create |
| 85 | `room thread-new` | MUTATING | ABSORB_M1 | Core | M1 | Stream create/child-stream convention; no Thread domain |
| 86 | `room create-thread` | MUTATING | ABSORB_M1 | Core | M1 | Stream create/child-stream convention; no Thread domain |
| 87 | `room append-message` | MUTATING | ABSORB_M1 | Core | M1 | Record append |
| 88 | `room send` | MUTATING | ABSORB_M1 | Core | M1 | Record append with targets |
| 89 | `room broadcast` | MUTATING | ABSORB_M1 | Core+Session Bridge | M1 | One Record addressed to Stream members; Bridge delivers |
| 90 | `room check-inbox` | READ_ONLY | ABSORB_M1 | Core | M1 | Read Records after Offset |
| 91 | `room mark-read` | MUTATING | ABSORB_M1 | Core | M1 | Advance Offset; does not mean work completed |
| 92 | `room promote-message` | MUTATING | FUTURE_EXTENSION | Collaboration UX | M4-F | Optional social/UX semantics over Records |
| 93 | `room react` | MUTATING | FUTURE_EXTENSION | Collaboration UX | M4-F | Optional social/UX semantics over Records |
| 94 | `room unreact` | MUTATING | FUTURE_EXTENSION | Collaboration UX | M4-F | Optional social/UX semantics over Records |
| 95 | `room append-handoff` | MUTATING | ABSORB_M1 | Core | M1 | Record kind/namespace; no Handoff subsystem |
| 96 | `room checkpoint` | MUTATING | ABSORB_M1 | Core | M1 | Record kind/namespace; no Checkpoint subsystem |
| 97 | `room context-fill` | READ_ONLY | ABSORB_M1 | M1 View | M1 | Bounded catch-up projection over Records |
| 98 | `room update-status` | MUTATING | ABSORB_M1 | Core | M1 | Record kind=status; current status is projection |
| 99 | `room clear` | MUTATING | ABSORB_M1 | Core | M1 | Append context-boundary/cancel record; never delete durable history |
| 100 | `room rebuild-session-bindings` | MUTATING | M1_MODULE | Session Bridge Module | M1 | Reconcile provider-session mappings |
| 101 | `room status` | READ_ONLY | ABSORB_M1 | M1 View | M1 | Read-only Stream projection |
| 102 | `duty claim` | MUTATING | FUTURE_EXTENSION | Duty/Resource Coordination | M4-D | Terminal duty/leadership policy deferred |
| 103 | `duty heartbeat` | MUTATING | FUTURE_EXTENSION | Duty/Resource Coordination | M4-D | Terminal duty/leadership policy deferred |
| 104 | `duty close` | MUTATING | FUTURE_EXTENSION | Duty/Resource Coordination | M4-D | Terminal duty/leadership policy deferred |
| 105 | `duty sweep` | MUTATING | FUTURE_EXTENSION | Duty/Resource Coordination | M4-D | Terminal duty/leadership policy deferred |
| 106 | `duty status` | READ_ONLY | FUTURE_EXTENSION | Duty/Resource Coordination | M4-D | Terminal duty/leadership policy deferred |
| 107 | `session open` | MUTATING | M1_MODULE | Session Bridge Module | M1 | Supersede with minimal Peer+Stream session generation/claim |
| 108 | `session heartbeat` | MUTATING | M1_MODULE | Session Bridge Module | M1 | Supersede with minimal active bridge claim heartbeat |
| 109 | `session close` | MUTATING | M1_MODULE | Session Bridge Module | M1 | Close provider-session mapping/claim |
