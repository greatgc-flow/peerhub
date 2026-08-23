# hub.py → peerhub replacement gap audit (2026-08-23)

Status: fresh source-verified audit, superseding `HUB-REPLACEMENT-ROADMAP-2026-08-09.md`'s
phase-status claims as the current authority on replacement readiness. That
roadmap remains useful as historical/phase-sequencing context but should not
be trusted for "is X done" without cross-checking here or against real code
directly — it had drifted stale in multiple places (see "Roadmap accuracy"
below).

Triggered by: user request ("engram의 hub.py 를 모두 peerhub가 대체 할 수 있는지
검토해줘") after `P:\_sys\core\hub.py` was repaired the same day (an abandoned
git merge had deleted its 12 dependency modules from the working tree only;
`git merge --abort` restored it — see `reference_hubpy_stale_merge_broke_deps_2026_08_23.md`
in Claude's memory). Investigated by `cx`, independently, reading the live
`peerhub` source and `hub.py`'s real (today, live-verified) subcommand list.

## Verdict

**No — peerhub cannot fully replace hub.py today.**

peerhub's installed CLI (`pyproject.toml` entry point) exposes only 5
commands: `status`, `diag`, `broadcast`, `ask`, `statusline`. The application
layer underneath is substantially more capable (SQLite-backed durable state,
health/admission, routing, leases, capability enforcement, real Claude/
Codex/Agy adapters, session resume, retry/failover, artifact coordination,
governance broker primitives) — but that capability is library/API surface,
not an exposed, hub.py-compatible command interface. The implementation is
ahead of the user-facing replacement surface, not behind it.

## hub.py's real subcommand list (verified live, 2026-08-23)

```text
init-session, end-session, send, broadcast, mark-read, append-log,
archive-file, update-status, check, status, check-gate, ask, ask-all,
ask-coordinator, consensus-propose, consensus-vote, consensus-check,
consensus-sweep, register-node, list-nodes, health-update, health-check,
peer-status, context-fill, checkpoint, peer-quarantine, peer-recover,
new-topic, clear-room, preflight, context-hash, report-error, feedback-add,
feedback-list, feedback-resolve, artifact-claim, artifact-status,
artifact-finalize, leader-yield, leader-claim, elect-leader, discover,
assign-role, release-role, role-status, health-precheck, health-sweep,
freshness-sweep, terminal-handoff, terminal-duty-sweep, terminal-heartbeat,
terminal-close, append-handoff, task-checkpoint, task-status, task-failover,
approval-request, file-lock, file-unlock, lock-status, profile-validate,
lease-status, lease-sweep, model-status, transient-scan, directive-add,
directive-list, directive-clear, lessons-list, lessons-propose,
lessons-activate, lessons-retire, lesson-broadcast, lesson-sweep,
lesson-inject, thread-new, thread-append, thread-react, thread-promote,
alert-raise, proposal-add, proposal-vote, proposal-list, broker-submit,
broker-drain, broker-status, update-signatures, arbiter-review,
credit-status, credit-consume
```

## Command parity

### Covered or partially covered (peerhub has a real, verified equivalent — CLI or internal API)

| `hub.py` command(s) | peerhub equivalent | Assessment |
|---|---|---|
| `ask` | `peerhub ask` / `execute_direct_ask()` | Covered for direct single-peer dispatch; CLI/options and state semantics differ from hub.py's. |
| `ask-all` | `peerhub broadcast` / `BroadcastCoordinator.fan_out()` | Partial — no crash-resume entry point, not fully equivalent to legacy behavior. |
| `status` | `peerhub status` | Partial — not the same room/session/node dashboard. |
| `check`, `check-gate` | Health/admission APIs | Internal capability exists, no equivalent CLI command. |
| `health-update`, `health-check`, `peer-status`, `health-precheck`, `health-sweep` | `peerhub.health` services/contracts | Internal equivalents exist; CLI surface missing. |
| `register-node`, `list-nodes`, `discover` | Routing/peer-target resolution APIs | Partial internal capability; no compatible node-management CLI. |
| `profile-validate`, `model-status` | Adapter/profile resolution and telemetry | Partial/internal only. |
| `lease-status`, `lease-sweep` | Session/capability lease persistence | Internal mechanism exists; no CLI equivalent. |
| `file-lock`, `file-unlock`, `lock-status` | SQLite transactional state / lease primitives | Not demonstrated as a compatible general-purpose file-lock CLI. |
| `artifact-claim`, `artifact-status`, `artifact-finalize` | Artifact coordination/materialization APIs | Internal capability exists; no CLI. |
| `broker-submit`, `broker-drain`, `broker-status` | Governance broker | Internal capability exists; no CLI. |
| diag-like behavior | `peerhub diag` | Partial — different data model and output contract than legacy `diag.py`. |
| statusline-like behavior | `peerhub statusline` | Narrow — currently mainly Agy-oriented. |

### Genuinely missing (no equivalent exposed peerhub command found)

```text
init-session, end-session, send, mark-read, append-log, archive-file,
update-status, append-handoff, context-fill, checkpoint, peer-quarantine,
peer-recover, new-topic, clear-room, context-hash, report-error,
feedback-add, feedback-list, feedback-resolve, leader-yield, leader-claim,
elect-leader, assign-role, release-role, role-status, freshness-sweep,
terminal-handoff, terminal-duty-sweep, terminal-heartbeat, terminal-close,
task-checkpoint, task-status, task-failover, approval-request,
transient-scan, directive-add, directive-list, directive-clear,
lessons-list, lessons-propose, lessons-activate, lessons-retire,
lesson-broadcast, lesson-sweep, lesson-inject, thread-new, thread-append,
thread-react, thread-promote, alert-raise, proposal-add, proposal-vote,
proposal-list, update-signatures, arbiter-review, credit-status,
credit-consume
```

Also effectively missing as a **usable feature**, despite `peerhub` having a
`broadcast` command by coincidence of name: the entire legacy room-scoped
consensus protocol (`ask-coordinator`, `consensus-propose`, `consensus-vote`,
`consensus-check`, `consensus-sweep`) — no implemented replacement found for
voter-health filtering, quorum snapshots, collab-rate decision rules, or
forced escalation.

### Unclear — needs an explicit product decision, not assumed obsolete

`archive-file`, `append-log`, `context-fill`, `checkpoint`, `new-topic`,
`clear-room`, `feedback-*`, `leader-*`, `assign-role`/`release-role`/
`role-status`, `terminal-*`, `task-*`, `directive-*`, `lesson-*`,
`thread-*`, `alert-raise`, `credit-*`. These are embedded in the current
operational protocol and/or live state files (`.ai/*.json`, `.ai/*.jsonl`).
No command was confidently classified as safe to retire without consumer
evidence.

## Roadmap accuracy (`HUB-REPLACEMENT-ROADMAP-2026-08-09.md`)

Accurate: Phase 1 governance schema cleanup (marked COMPLETED) checks out.
Much of Phase 3's internal dispatch-loop work (session continuation,
streaming/decoding, failure classification, retry authorization, failover
routing, durable retry-loop state, concurrent-attempt handling, real
broadcast fan-out) has genuinely landed since the roadmap's last edit.

Stale/misleading:
1. Some sections still describe the outer orchestration loop as incomplete while later sections record T1 increments 5A-5C-3b as complete.
2. Implies the dispatch loop is "effectively complete" as if that means replacement-ready — but the public CLI is still only 5 commands. Implementation is ahead of the user-facing surface.
3. "Health/quota tracking: not started" is no longer accurate — `peerhub diag`, quota polling, and persisted usage projections now exist (exact parity with legacy `diag.py` still incomplete, but "not started" is wrong).
4. "Broadcast: designed" is stale — `BroadcastCoordinator.fan_out()` and `peerhub broadcast` are implemented; crash-linkage recovery remains deferred.
5. The roadmap does not reflect the post-2026-08-11 Phase 1 CLI crosswalk or later command-surface work. Do not use it as the current replacement-status authority without a fresh source-based check like this one.

## Blocking gap list for cutover (prioritized by cutover impact)

1. **Compatibility command surface / migration strategy** — either a compatibility CLI implementing the legacy `hub` commands, or an explicit, verified migration of every real caller. Most daily-driver commands currently have no peerhub equivalent at all.
2. **Consensus and coordinator workflows** — `ask-coordinator`, `consensus-propose/vote/check/sweep`. A fundamental coordination function, not cosmetic parity.
3. **Session/room/thread/handoff continuity** — `init-session`, `end-session`, `send`, `mark-read`, `new-topic`, `clear-room`, `thread-*`, `terminal-*`, `append-handoff`, `checkpoint`, `context-fill`.
4. **Health, quarantine, leadership, role operations** — `peer-quarantine`, `peer-recover`, `health-update`, `health-check`, `peer-status`, `elect-leader`, `leader-claim`, `leader-yield`, `assign-role`, `release-role`, `role-status`. Internal health model is substantial; operational commands are absent.
5. **Task lifecycle / failover CLI** — `task-checkpoint`, `task-status`, `task-failover`, `approval-request`. Internal retry/failover machinery is not equivalent to the legacy task protocol.
6. **Governance, learning, proposal, alert commands** — `directive-*`, `lesson-*`, `proposal-*`, `feedback-*`, `alert-raise`, `arbiter-review`. Underlying governance broker is not a replacement for the full operator-facing protocol.
7. **Legacy diagnostics/telemetry parity** — `peerhub diag` exists but parity with the legacy dashboard, session accounting, routing display, and health/quota semantics is undemonstrated.

## Additional cutover risks (beyond raw feature parity)

- **State migration**: legacy `.ai` JSON/JSONL files (room state, handoffs, leases, health records, proposal history, logs, credit data) are not automatically migrated into peerhub's SQLite schema.
- **State continuity**: active sessions and in-flight tasks cannot safely be assumed resumable across the two state models.
- **Hardcoded callers**: scripts, `.bat` wrappers, peer prompts, tests, and operational tooling invoke exact `hub` subcommands, flags, exit codes, output formats, and file locations.
- **Different identity/authorization models**: peerhub's authenticated-caller, capability-tier, admission, and lease semantics are stricter and structurally different from hub.py's.
- **Different failure semantics**: peerhub uses structured request/attempt states and execution certainty; callers expecting legacy exit codes or textual error patterns may mishandle failures.
- **Concurrency/process ownership**: peerhub has durable attempt coordination but is not yet a drop-in replacement for every legacy lock/heartbeat/terminal-duty/failover workflow.
- **Rollback requirements**: a safe cutover needs dual-run or shadow validation, migration receipts, replay tests, and a tested rollback path — not merely command-count parity.

## Bottom line

peerhub is now a credible replacement **kernel** for dispatch, persistence,
admission, retry, failover, and limited broadcast. It is **not yet** a
replacement for the full operational coordination system hub.py represents
today (consensus, session/room/thread continuity, health/leadership
operations, task lifecycle, governance/learning protocol, diagnostics
parity). Closing the gap is a real, multi-part implementation effort, not a
documentation or wiring task.

## Addendum: design-vs-implementation gap (same day, follow-up)

User asked: if everything already-designed were implemented, would that close the gap? `cx` checked each of the 7 blocking categories against this repo's actual design docs (`_sys/docs-v2/`, `docs/design/`). **Verdict: no.** All 7 categories still lack a ratified, peerhub-side replacement design (existing docs describe hub.py's own current architecture/contracts, not a peerhub-side specification for them). Diagnostics/telemetry has the strongest existing foundation (`_sys/docs-v2/ops/diag-telemetry-architecture.md`) but still isn't a complete replacement design. **Per the standing rule, implementation does not start while known design gaps remain open — Phase 2 is paused; next step is closing these 7 design gaps, not coding.**
