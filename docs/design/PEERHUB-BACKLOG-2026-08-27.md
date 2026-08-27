# peerhub Backlog (consolidated, 2026-08-27)

> Single source of truth for "what's left." Supersedes hunting across `docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md`, the README's status lists, and per-gap design docs to answer "what's next" — those documents remain the detailed record of *why* each decision was made; this one is the current, flat *what's outstanding* list, organized by how ready each item is to pick up. Update this doc's tiers as items move, rather than letting the README's own "Designed but not built" / "Explicitly deferred" / "Not yet implemented" lists drift out of sync again (see the "Known drift caught while writing this doc" note at the bottom — that's exactly the failure mode this doc exists to prevent).

## Where things stand right now

**Update (same day, Tier 2 round): 27 of the 90 now backed.** `thread-react` is done (see the Tier 2 table below) -- the reaction event/projection model, reachable through `peerhub room react|unreact`, `LegacyTranslator`, and `ApplicationAPI`.

**Prior update (same day, Tier 1 backlog round): 26 of the 90 now backed** (up from 24) -- `terminal-close --close-session` completed (duty-close and session-close now reported as two independent outcomes, verified end-to-end manually including the partial-failure path: duty released even when session-close fails), `terminal-duty-sweep` implemented (new `DutyLeaseCoordinator.sweep_expired_leases()` + a new cross-room expired-lease query), and `thread-append` wired to the already-existing `RoomsService.append_message()`. `lesson-broadcast` was investigated and deliberately left unbacked -- real legacy behavior broadcasts messages to every other room member, while `LessonService.record_delivery_pending()` only marks one peer's delivery as pending; a regression test (`test_legacy_lesson_broadcast_stays_unbacked_without_broadcast_semantics`) documents this is intentional, not an oversight. Tier 1 is now fully closed except that one confirmed mismatch, which moves to Tier 3 (needs its own design round for a real per-peer-vs-broadcast decision) rather than staying in Tier 1.

Original state before this round: 24 of the 90 `LEGACY_CATALOG` actions translate and execute end-to-end, across 6 real domains with full CLI + native service + legacy-translation + execution-dispatcher coverage: consensus, task, lesson, room (partial), duty-lease, room-participation-session. Full detail: `docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md`. This doc inventories everything still unbacked plus the older, pre-TDD-phase backlog items from the README that never got folded into the TDD tracking.

## Tier 1 — Ready now (real backing method already exists, zero new design)

These need only the same mechanical wiring pattern used repeatedly this session (`LegacyTranslator` branch + `CommandDescriptor` registration + CLI subcommand + tests) — no new service code, no dialectical round.

| Legacy action | Native method | Status |
|---|---|---|
| ~~`terminal-close` (`--close-session` half)~~ | `coordination.terminal.close` (extended) | **DONE.** `RoomParticipationCoordinator.end_session()` wired in; duty-close and session-close report as two independent outcomes (`{"duty_close": {...}, "session_close": {...}}`); verified end-to-end manually, including the partial-failure path (duty released, session-close failure reported separately, CLI exit code 2). |
| ~~`terminal-duty-sweep`~~ | `coordination.terminal.duty_sweep` | **DONE.** New `DutyLeaseCoordinator.sweep_expired_leases(role, ...)` + a new cross-room `list_expired_duty_leases` query, iterating the existing per-lease `expire_and_recover_lease()`. Real test proves selectivity (only the expired lease transitions, the active one is untouched). Does not touch room sessions or replay work, as scoped. |
| ~~`thread-append`~~ | `coordination.thread.append` | **DONE.** Wired to the pre-existing `RoomsService.append_message()`. |
| `lesson-broadcast` | `coordination.lesson.broadcast` | **Investigated, deliberately left unbacked** — moved to Tier 3 below. Real legacy behavior broadcasts a message to every other room member; `LessonService.record_delivery_pending(lesson_id, peer_id, ...)` only marks ONE peer's delivery as pending, a genuine cardinality mismatch, not a naming coincidence. A regression test (`test_legacy_lesson_broadcast_stays_unbacked_without_broadcast_semantics`) documents this is intentional. |

Also worth a **quick verification pass** (not full implementation) before assuming they're Tier 1:
- `thread-new` (`coordination.thread.create`) vs. the already-backed `new-topic` (`coordination.topic.create`) — both may resolve to the same `RoomsService.create_thread()` call; confirm they're genuinely distinct legacy semantics before wiring, or wire `thread-new` as a straight alias.
- `broadcast` (`coordination.message.broadcast`) vs. the existing `BroadcastCoordinator`/`peerhub broadcast` — confirm these are actually the same concept (a room/thread message fan-out) before assuming `BroadcastCoordinator.fan_out()` backs it; the naming overlap could be coincidental (peer-dispatch fan-out vs. room-message fan-out are different things).

## Tier 2 — Ratified design, needs new (but scoped) component code

From the gap-3 dialectical round (`docs/design/HUB-REPLACEMENT-GAP3-SESSION-CONTINUITY-2026-08-24.md`, "RATIFIED (2026-08-27)" sections) — the *design* questions are closed, but the components themselves aren't built.

| Item | What's ratified | What's still missing |
|---|---|---|
| ~~`thread-react`~~ | Immutable append-only reaction events + a maintained "current reaction" projection (not a mutable CAS status flip) | **DONE.** `RoomsService.react()`/`unreact()` write immutable `reaction-event:<id>` targets plus a CAS-updated `reaction-state:<message>:<actor>:<type>` projection, following the room/thread/message `TargetState` family exactly. Reachable through `peerhub room react\|unreact`, `LegacyTranslator` (`thread-react` with an `action: ADD\|REMOVE` field), and `ApplicationAPI`. One real bug caught by the terminal's manual end-to-end smoke test (not unit tests): the first round wired ADD everywhere but left `unreact()` completely unreachable from any external interface (no CLI subcommand, no `action` field on the wire command, the API handler hardcoded `s.react(...)`) -- fixed in a follow-up round, independently re-verified with a fresh manual CLI round-trip (ACTIVE → REMOVED → ACTIVE). |
| `context-fill` | One JSON envelope (`room_id`, `session_id`, `as_of_event_seq`, `sections`, truncation metadata); list-shaped sections carry `{"items":[...],"truncated":bool}` | The read-aggregation service that actually pulls current room/thread/task/consensus/lesson state into that shape at a consistent event position — doesn't exist yet. |
| `checkpoint` + `append-handoff` | The 6 legacy handoff sections (`GOAL`, `RECENT_COMPLETED`, `PENDING_ISSUES`, `KEY_DECISIONS`, `CONSENSUS_HISTORY`, `ACTIVE_THREADS`) with real ported legacy limits (12000 chars total / 5 / 3 / 3 / 10 / 5 items); required as a generated (never authoritative), deterministic, re-generatable projection | The handoff-projection generator itself — reads durable state, produces the 6 sections, supports Markdown/JSON export. Nothing built. |
| `room.session_bindings` wiring | Ratified as an explicit rebuildable projection on the room `TargetState`, not authoritative | Not actually regenerated/kept in sync by anything yet — `RoomParticipationCoordinator` deliberately does not touch it (see the explicit comment in `peerhub/dispatch/room_session.py`). |

## Tier 3 — Needs its own dialectical round before implementation

Real open design questions, not yet debated. Do not implement before ratifying, per this session's standing architecture-before-implementation rule.

- `send` (`coordination.message.send`) — "directed transient message, optional thread/resource pointer, doesn't duplicate substantive thread content" per the gap-3 doc's own table, but never actually debated: what makes it "transient" vs. a thread message, does it persist at all, who can address whom.
- `mark-read` (`coordination.message.mark_read`) — described as "idempotent read-cursor advance," looks simple, but the underlying read-cursor data model doesn't exist yet (per-participant, per-thread position tracking) — confirm it's actually simple before assuming so.
- `thread-promote` (`coordination.thread.promote`) — "attach thread to a request/proposal/consensus round; no implicit vote/finalization" is a direction, not a mechanism; what the actual link field/target-type validation looks like was never nailed down the way reactions/context-fill were.
- `update-status` (`coordination.mission.update`) — not in the gap-3 doc's command table at all; likely belongs to a different, entirely undesigned "mission/status" concept.
- `check` (`coordination.message.check`) — undiscussed.
- `arbiter-review` (`consensus.arbiter.review`) — flagged during the `approval-request`/`consensus-sweep`/`lessons-list` batch as having no clean 1:1 existing method on `ConsensusService`; DIR-005's arbiter-authority mapping is policy-ratified (ratification item 41) but the actual command mechanics aren't.
- `lesson-broadcast` (`coordination.lesson.broadcast`) — confirmed cardinality mismatch (see Tier 1 table above): legacy behavior sends to every other room member, `LessonService` only has a per-peer delivery-marker method. Real question: does `LessonService` need a new `broadcast_delivery_pending(lesson_id, room_id)`-style method that internally discovers room members and calls the existing per-peer primitive N times, or is this actually a room/messaging-layer concern (send N individual messages) rather than a `LessonService` one? Resolve which layer owns "discover room members" before designing the method shape.

## Tier 4 — Entirely new domains, no design work started

~50 of the 66 remaining actions cluster into domains that were part of `hub.py`'s original 7 functional categories but never got their own gap-N design doc this session (unlike consensus/session-room-thread/duty-lease/task-approval/governance-learning, which each did). Roughly:

- **Health / admission / routing** (~18 actions): `register-node`, `list-nodes`, `health-update`, `health-check`, `peer-status`, `peer-quarantine`, `peer-recover`, `health-precheck`, `health-sweep`, `freshness-sweep`, `elect-leader`, `discover`, `assign-role`, `release-role`, `role-status`, `check-gate`, `lease-status`, `lease-sweep`. This is `hub.py`'s "health/leadership/duty-lease" category minus the duty-lease part (which IS built). No `TargetState` schema, no service, no design doc.
- **Governance artifacts / feedback / proposals / locks** (~19 actions): `append-log`, `archive-file`, `report-error`, `feedback-add/list/resolve`, `artifact-claim/status/finalize`, `proposal-add/vote/list`, `broker-submit/drain/status`, `file-lock/unlock`, `lock-status`. Part of `hub.py`'s "governance/learning" category beyond lessons (which IS built).
- **Lessons residual** (3 actions) — checked against the real `LessonService` (peerhub/governance/lessons.py) while writing this doc: `lesson-broadcast` looks like a Tier 1 candidate — `record_delivery_pending(lesson_id, peer_id, *, delivery_method="broadcast", ...)` already exists with a `delivery_method` parameter defaulting to exactly this concept, worth wiring next; `lesson-sweep` and `lesson-inject` have no matching method (`propose`/`approve`/`activate`/`retire`/`supersede`/`quarantine`/`record_delivery_pending`/`record_delivery_complete` is the complete real method list) — stay Tier 4, undesigned.
- **Configuration / telemetry / peerhub-internal** (7 actions): `profile-validate`, `model-status`, `transient-scan`, `preflight`, `context-hash`, `status`, `update-signatures` — miscellaneous, likely each a small independent utility, not one coherent domain.
- **Host-level / P:-environment-specific** (5 actions, **worth questioning whether these belong in peerhub's domain model at all**): `directive-add/list/clear`, `credit-status`, `credit-consume` — these read like they're about the P: portable environment's own host integration, not a generic peerhub concept. Resolve "does peerhub own this or does it stay host-side" before designing, not after.
- **Alert** (1 action): `alert-raise` — standalone, undesigned.

## Tier 5 — Pre-TDD-phase backlog (from the README, predates this session's TDD work, still real)

These were never part of the gap-1..7 design/TDD tracking; they're infra/ops-level items with their own already-stated deferral triggers, carried forward here so they don't get lost:

- **Health/quota tracking's periodic background polling (`TelemetryWorker`)** — awaiting a user decision on the process-host model (poll-on-demand vs. daemon).
- **Windows-native Brokered Read-Only Reducers** — blocked pending a policy call on required OS privileges.
- **Alembic runtime cutover** — ratified HOLD; revisit only if peerhub adopts SQLAlchemy ORM or is about to become the primary dispatch path.
- **Formal multi-peer consensus voting machinery (Primitive B)** — deferred until the first `r10_requires_finalized_for` decision class is actually routed to peerhub. (Note: `ConsensusService` now built this session covers propose/vote/resolve for peerhub's OWN governance rounds — this deferred item is about a *different* thing, formal Primitive B voting machinery for external decisions; don't conflate the two when picking this up.)
- **Durable response transcripts for broadcast** — deferred until a dispatch-layer durability mechanism is ratified.
- **Capability-lease enforcement evidence** (claiming positive enforcement in adapter receipts) — deferred until a machine-owned launcher, plan-bound digest, empirical negative probe, and post-plan corroboration gate all exist.
- **Parallel fan-out** — deferred, blocked on measuring real SQLite write contention.
- **Phase 4 shadow-by-ownership-cluster validation** + same-revision comparison/rollback proof — not started.
- **Crash-linkage recovery** (resuming an interrupted round after a coordinator crash) — not started.
- **Detailed per-vendor error-taxonomy mapping, PTY transport** — deliberately out of scope for the current adapter slice.
- **Shadow-mode validation** (routing a subset of real traffic through peerhub alongside `hub.py` for comparison before cutover) — not started; `hub.py` remains the authoritative system for real multi-peer coordination work today.

## Known drift caught while writing this doc

The README's "24 of the 90 ... action count" line needed a correction during the last implementation round — a running tally had silently drifted stale (stuck at "18" for a few rounds after a commit had already pushed it to 19) because updates were manual and scattered across README + the TDD progress doc + inline commit messages. This backlog doc doesn't eliminate that risk by itself, but centralizing "what's left" in one place makes drift easier to catch on the next audit pass. If updating this doc, prefer running `grep -c "if call.action ==" peerhub/application/legacy.py`-style direct verification over incrementing a remembered number.

Also stale, not yet fixed: README line "None of this design work has been implemented yet" (under the "hub.py-replacement design phase" paragraph) — written before the TDD phase started and now inaccurate; most of the design work it refers to (gap-2, gap-4, gap-5, gap-6, half of gap-3) has since been implemented. Needs a README pass, not fixed as part of writing this backlog doc.
