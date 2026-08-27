# Gap 3 Design: Session/Room/Thread/Handoff Continuity (RATIFIED 2026-08-27 — all 16 open items closed; see bottom of file)

Status: first-round draft from `cx`, 2026-08-24. Covers `init-session`,
`end-session`, `send`, `mark-read`, `new-topic`, `clear-room`, `thread-new`,
`thread-append`, `thread-react`, `thread-promote`, `terminal-handoff`,
`terminal-duty-sweep`, `terminal-heartbeat`, `terminal-close`,
`append-handoff`, `checkpoint`, `context-fill`. Built within gap-1's
adapter boundary and gap-2's append-only-events-plus-projection pattern.
`cx` could not access `peerhub`/prior gap docs from its sandbox this round
but DID access `_sys/docs-v2/` (lifecycle.md/protocol.md) directly, so the
lifecycle rules cited below are grounded in real text, not guessed.

## 1. Native continuity model

**Room**: durable collaboration namespace (`room_id`, protocol/schema
versions, `status: OPEN|ARCHIVED|CLOSED`). NOT a peer-process session —
persists across terminal processes/reconnections. Owns sessions, threads,
requests/attempts, consensus rounds, terminal-duty leases, continuity
events.

**Session**: a bounded participation interval by a terminal/peer within a
room (`session_id`, `room_id`, `actor_id`, `terminal_instance_id`,
`status: ACTIVE|ENDED|EXPIRED|ABANDONED`, `resume_parent_id`,
`session_fingerprint`). Records membership/continuity, doesn't own
substantive work. Preserves existing lifecycle rules: startup does
init+health-update+context-fill+mailbox-check; resume governed by
staleness windows; failed resume retires old session, retries fresh once;
fingerprint drift forces retirement.

**Thread**: durable room-level append-only conversation stream, may span
multiple sessions/terminals, NOT nested under a session (`thread_id`,
`room_id`, `topic_key`, `status: OPEN|RESOLVED|ARCHIVED`,
`promoted_from_thread_id`). Messages append-only
(`message_kind: NOTE|QUESTION|ANSWER|REACTION|DECISION_POINTER`,
`idempotency_key`). Reactions are append-only events/uniquely-keyed rows,
not mutable counters. `thread-promote` creates a durable relationship to
a governance object (request/consensus round) — never silently converts
discussion into a binding decision.

**Relationship to requests/attempts/consensus** (gap-2's aggregates):
`room → {threads, requests→attempts, consensus_rounds→votes}`. Threads =
human-readable discussion/pointers; requests/attempts = execution state;
consensus rounds = governance state — none reduced to "just a thread
status." FK relations: `request.room_id`, `request.origin_thread_id`
(nullable), `attempt.session_id` (nullable — an attempt's durable identity
belongs to the request, not the originating session, so another session
can fail over/continue it). All continuity objects follow gap-2's pattern:
append-only events + materialized projections + idempotency keys +
optimistic-version/CAS + actor/session/terminal provenance on every event.

## 2. Terminal handoff and duty

A terminal = the currently active human-facing router for a room. Duty is
an **exclusive, leased room role** — not peer authority, not ownership of
substantive decisions. Terminal may receive human input, route/relay
losslessly, summarize output, run read-only status ops. **Must never
become the author/voter of governance state** (per terminal-transport/PRO-19
rules).

**Duty lease** (`terminal_duty`, PK `room_id`): `holder_actor_id`,
`holder_terminal_id`, `lease_id`, `acquired_at`, `renewed_at`,
`expires_at`, `status: ACTIVE|RELEASED|EXPIRED|REVOKED`, `fencing_token`.
Acquisition is transactional: read current row → reject if non-expired
lease exists → permit if absent/released/expired → increment fencing
token → append `duty.acquired` → materialize. **Every duty-scoped
mutation carries the current lease_id + fencing token; a stale terminal
must fail closed after handoff even if still running.**

**Handoff** transfers room continuity, not provider-process internals:
room identity+versions, current lease+fencing token, active
goal/request pointers, in-flight attempt states, unresolved consensus
rounds, open threads+cursors, pending issues/recent decisions, terminal
health/last-seen, last checkpoint position, explicit abnormal
conditions. Canonical transfer = durable event
(`terminal.duty_handoff_requested/accepted`, `terminal.duty_released`) +
materialized projection — the accepting terminal reads the projection
itself; **the outgoing terminal must never send a private authoritative
state blob that bypasses peerhub.**

**Silent-terminal sweep** (= native `terminal-duty-sweep`, analogous to
`consensus-sweep`): find leases past `expires_at` → append
`terminal.duty_expired` → mark holder session `STALE`/`ABANDONED` → fence
old lease → leave requests/attempts/threads/consensus durable → permit new
acquisition → emit audit+recovery pointer. **No automatic task replay on
expiry** — the new terminal explicitly inspects and resumes/fails-over via
the request/attempt state machine.

**Heartbeat** (`peerhub terminal heartbeat --room --lease --fence`):
renews lease, updates `last_seen_at`, rejects stale lease/fencing IDs, does
NO substantive work/replay — a lease renewal, not a peer health probe.

**Close** (`peerhub terminal close --room --lease --fence`): appends
`terminal.duty_released`, ends session if requested, retains all
room/thread/request/consensus history, refuses close from a
stale/non-holder terminal. Crash = expiry path; orderly exit = explicit
close.

## 3. Checkpoint and context-fill

SQLite durable state replaces the checkpoint file's role as authoritative
machine state (room/session lifecycle, duty leases, request/attempt
state, thread cursors, consensus state, event positions,
idempotency/fencing). **But**: the lifecycle SSOT explicitly defines a
rolling `handoff.md` (goal, recent work, pending issues, key decisions,
consensus history, active threads) — this human-readable continuity
projection remains useful (inspection, emergency recovery, portability,
debugging) and should NOT be declared obsolete without evidence.
**`SQLite event log + projections = authoritative state`;
`handoff projection = resumable human/context view`, never independently
authoritative.**

`peerhub continuity checkpoint`: records `continuity.checkpoint_created`
event, captures current room event sequence, materializes a compact
snapshot, optionally exports Markdown/JSON, optionally trims per policy
— must be idempotency-key-safe to repeat. Legacy `--trim` stays a
projection-maintenance option; trimming must never delete authoritative
event history without a separately ratified retention policy.

`peerhub continuity context-fill --room --session --sections
goal,decisions,pending,threads,consensus`: reads the durable projection at
a consistent event position, produces a bounded context envelope
(`room_id`, `session_id`, `as_of_event_seq`, version fields, `sections[]`,
truncation metadata, source position). Deterministic for the same
room-state+params; aligns with the existing ContextGate model (context
selected/pruned before an ask, durable state stays intact).

## 4. Native command surface + legacy mapping

Adapter follows gap-1's boundary + versioned JSON envelope.

| Legacy | Native | Compat behavior |
|---|---|---|
| `init-session` | `session.open` | Resolve/create room membership, new peerhub session, return context metadata. |
| `end-session` | `session.close` | Close caller's session; does NOT close the room unless explicitly requested. |
| `send` | `message.send` | Directed transient message, optional thread/resource pointer; doesn't duplicate substantive thread content. |
| `mark-read` | `inbox.cursor.advance` | Idempotent read-cursor advance. |
| `new-topic` | `thread.create` (+ optional session boundary) | Creates a room-level thread; adapter separately requests session retirement if legacy semantics require it. |
| `clear-room` | `room.projection.reset` or `room.archive` | **Must NOT delete durable history by default** — see caution below. |
| `thread-new` | `thread.create` | — |
| `thread-append` | `thread.message.append` | Idempotent, author/session provenance. |
| `thread-react` | `thread.reaction.append` | Append/toggle a uniquely-keyed reaction per declared semantics. |
| `thread-promote` | `thread.promote` | ~~Attach thread to a request/proposal/consensus round; no implicit vote/finalization.~~ **CORRECTED 2026-08-28** (see PEERHUB-BACKLOG-2026-08-27.md's Tier 3 ratification, item 3): this row was written without consulting `docs/design/PHASE1-PARITY-LEDGER-BATCH5-2026-08-20.md` §7, which specifies the REAL legacy behavior — promotes a mailbox MESSAGE (`msg_id`) into a durable thread, unrelated to consensus/proposal attachment. Verified directly against the parity ledger. The "attach to governance object" idea in this row was a guess, not a citation, and turned out wrong. |
| `terminal-handoff` | `duty.handoff` | Release/transfer duty lease via fencing + durable handoff events. |
| `terminal-duty-sweep` | `duty.sweep` | Expire stale leases, fence holders, no auto-replay. |
| `terminal-heartbeat` | `duty.heartbeat` | Renew lease, reject stale fencing tokens. |
| `terminal-close` | `duty.release` (+ optional `session.close`) | Clean release, optional session close. |
| `append-handoff` | `continuity.note.append` | Append structured continuity event/note, regenerate projection. |
| `checkpoint` | `continuity.checkpoint` | Event-positioned snapshot, optional export/trim. |
| `context-fill` | `continuity.context_fill` | Materialize bounded startup/ask context at a consistent event position. |

**`clear-room` caution**: highly ambiguous legacy command. Safe default:
`clear-room = start a fresh conversational projection boundary`, NOT
`clear-room = delete room data`. Adapter preserves the old room/threads,
creates a new topic/context epoch, retires provider sessions if legacy
behavior requires it. Destructive deletion should be a separate,
explicitly-named operation.

## 5. Failure/consistency rules

Append-only continuity events; projections rebuilt from events;
idempotency keys on all mutating commands; optimistic version/CAS;
fencing tokens for duty; journaled intent+commit records; fail-closed on
stale sessions/leases; no implicit replay after terminal failure; no
private channel bypassing room visibility; no terminal-origin governance
mutation; explicit actor/session/terminal provenance on every event. A
handoff completes only after the accepting terminal's lease+fencing token
are durably committed — if the outgoing terminal fails before acceptance,
the old lease eventually expires and the room stays recoverable.

## Open questions requiring user or peerhub-source confirmation (16)

1. Does peerhub already have a concrete SQLite schema/event naming convention for this area (needs checking against real peerhub source — `cx` didn't have access this round)?
2. Does "session" mean peerhub participation, provider conversation, terminal process, or all three?
3. Are rooms permanently retained, explicitly closed, or garbage-collected?
4. Can multiple terminals observe a room concurrently while only one holds active duty?
5. Is duty exclusive globally per room, or can routing/governance/execution have separate duties?
6. Exact timeout/sweep configuration for duty leases?
7. Does expired duty auto-trigger reassignment, or only make reassignment possible?
8. ~~What does legacy `clear-room` actually currently delete/reset~~ — **RESOLVED 2026-08-26**, see "CONFIRMED" section above: deletes nothing, mints a new room + resets pointer fields + clears live peer session bindings only.
9. ~~Exact legacy handoff fields and serialization rules~~ — **RESOLVED 2026-08-26** via direct read of `_HANDOFF_SECTIONS`/`_write_handoff`/`_parse_handoff`, `P:\_sys\core\hub.py:1255-1320`: exactly 6 fixed sections — `GOAL, RECENT_COMPLETED, PENDING_ISSUES, KEY_DECISIONS, CONSENSUS_HISTORY, ACTIVE_THREADS`. Storage tries a JSON form first (`{"sections": {...}}`), falls back to parsing Markdown (`## [SECTION]` headers, `- item` bullets, an empty section renders the literal placeholder `- (없음)`). Each of the 5 non-GOAL sections is trimmed to its own `HANDOFF_MAX_*` constant on every write — i.e. a bounded per-section ring buffer, not unbounded growth. Native mapping: these 6 sections become 6 fixed keys in a room/thread `TargetState.state` (or a dedicated projection), each independently length-capped; GOAL stays a single scalar, the rest stay capped lists.
10. Are thread reactions toggles, append-only events, or immutable acknowledgements?
11. Should `context-fill` return Markdown, structured JSON, or both?
12. Which continuity sections are mandatory at startup, and their size limits?
13. Is the human-readable handoff projection required for portability/export, or only compatibility?
14. Does `terminal-close` close only duty, the terminal session, or both?
15. Authoritative distinction between a room, a topic, and a thread in peerhub?
16. Are request/attempt/consensus aggregates already event-sourced in real peerhub source, or does gap 2 still need to define their event-store mechanics concretely?

## Proposed ratification statement (direction only)

Adopt: rooms as durable namespaces; sessions as bounded participation
intervals; threads as room-level append-only streams spanning sessions;
requests/attempts/consensus remain separate durable aggregates linked by
IDs/event pointers. Adopt exclusive fenced terminal-duty leases with
heartbeat renewal, explicit release, expiry-based sweep, no automatic
task replay after failure. Adopt SQLite/event projections as authoritative
state while retaining a generated human-readable continuity projection for
startup/recovery/export/compat. Adopt native continuity operations for
session lifecycle/threads/duty/checkpoints/context-fill, legacy commands
translated through gap-1's versioned compat envelope. **Defer** exact
schema names, timeout values, handoff-field
compatibility, and projection retention limits pending inspection of
peerhub's real source (the remaining 15 items above; item 8, legacy
`clear-room` semantics, is now resolved — see the CONFIRMED section above).

## RECONCILIATION AGAINST REAL SOURCE (2026-08-24)

Real `peerhub/dispatch/session_lease.py`/`heartbeat.py` confirmed via
direct read (see `HUB-REPLACEMENT-REAL-SOURCE-GROUNDTRUTH-2026-08-24.md`
and the gap-4 reconciliation for the `SessionLeaseCoordinator` type list).

**`SessionLeaseCoordinator` is an even more direct match for gap-3's
session-binding model than for gap-4's leadership concept** — strongest
evidence: `create_session_and_lease(...)` + `SessionBindingKey` +
`SessionBindingSnapshot` + `create_lease`/`renew_lease`/`close_lease` +
`expire_and_recover_lease`/`validate_lease_fence`.

| Gap-3 concept | Existing substrate |
|---|---|
| `init-session` | `create_session_and_lease(...)` |
| Session identity/binding | `SessionBindingKey` |
| Session state inspection | `SessionBindingSnapshot`/`LeaseSnapshot` |
| `end-session` | `close_lease(...)` (body-level confirmation still needed) |
| Session expiry | `expire_and_recover_lease(...)` |
| Stale-owner protection | `validate_lease_fence(...)` |

**Heartbeat substrate**: `HeartbeatWorker`/`LeaseRenewer`(Protocol)/
`HeartbeatFailure` is the natural substrate for `terminal-heartbeat` +
silent-terminal detection. `LeaseRenewer` being a `Protocol` suggests
dependency inversion (worker likely depends on an abstract
lease-renewal capability, not one concrete lease type) — makes a future
terminal-duty lease PLAUSIBLE but NOT proven generic; needs a body-level
read to confirm the protocol accepts a generic lease identifier (not
session-lease-only), whether the worker is parameterized by lease kind,
whether `HeartbeatFailure` distinguishes terminal/session/request
failures.

### Revised "Terminal handoff and duty" section

Terminal duty should be represented using the existing lease/session
machinery where applicable — `SessionLeaseCoordinator` for session
binding/`init-session`, `HeartbeatWorker`/`LeaseRenewer` for liveness
(feed `HeartbeatFailure` into the existing lease-expiry/recovery path,
don't build a parallel heartbeat mechanism). **If the current worker
doesn't support a distinct `terminal-duty` lease kind, the missing work
is a narrow lease-kind/adapter extension, NOT a new liveness
architecture.**

**Design work still needed above these primitives** (genuine gaps, not
covered by any real code found): durable room/namespace modeling,
append-only thread/conversation modeling, handoff-content schema/
ownership rules, explicit terminal-duty assignment/handoff semantics, a
`terminal-duty-sweep` command/orchestration entry point, the
no-auto-replay policy for a lost terminal.

### Room/thread — CONFIRMED genuine gap in the surveyed dispatch layer

Nothing in `session_lease.py`/`heartbeat.py`/`retry_authorization.py`/
`admission.py`/`artifact_coordination.py` covers a durable room namespace,
append-only thread/conversation stream, or thread
membership/ordering/archival/replay. **Not yet ruled out**:
`peerhub/persistence/` and `peerhub/application/` haven't been surveyed
— room/thread concepts could plausibly live there. Needs a targeted read
before calling this gap repository-wide (currently only "not in
dispatch/health/governance," not "not anywhere").

## CORRECTION (2026-08-24, same day): room/thread ARE named in real code

`HUB-REPLACEMENT-CRITICAL-FINDING-LEGACY-CATALOG-2026-08-24.md` found
`peerhub/application/legacy.py`'s `LEGACY_CATALOG` already assigns real
target names for room/thread: `coordination.room.clear`,
`coordination.topic.create` (= `new-topic`), `coordination.thread.create/
append/react/promote`, `coordination.terminal.*`,
`coordination.handoff.append`. **This corrects the prior reconciliation
round's conclusion that room/thread is "not found anywhere in peerhub"**
— it IS named/planned, just `INVENTORIED`/`NOT BACKED` (no handler, no
schema) like ~87 of ~90 catalog entries. The gap is real at the
IMPLEMENTATION and DATA-MODEL level (this doc's own room/session/thread
schema proposal is still needed work), not at the naming/discoverability
level.

## FIELD-LEVEL CONFIRMATION (2026-08-24, terminal): SessionBindingKey's real composite identity

```python
class SessionBindingKey:
    workspace_scope_id: str
    instance_id: str
    profile_id: str
    conversation_scope: str
```

A 4-part composite key. This answers part of gap-3's own identity
question for SESSIONS specifically: a session is uniquely identified by
`(workspace_scope_id, instance_id, profile_id, conversation_scope)`, not
a single ID field. `LeaseCreateRequest` (see gap-4's reconciliation doc
for full fields) confirms sessions are tightly bound to
`session_id`+`command_id`+`attempt_id` — i.e. a session-scoped lease is
inherently tied to ONE execution, not a general-purpose room/thread
membership concept. This reinforces (now with field-level evidence) that
room/thread — which need to persist and be READABLE independent of any
one command/attempt execution — are NOT the same kind of object as a
session lease, and need their own data model as this doc already
proposed, not a reuse of `SessionBindingKey`/`LeaseCreateRequest`.

## FINAL CONFIRMATION (2026-08-24, terminal): room/thread has ZERO schema anywhere in real peerhub — genuinely unimplemented at every layer

Checked all 24 real migrations in `peerhub/persistence/migrations/`
(`0001` through `0024_telemetry_quota_tracking.sql`) directly — no
room/thread table, column, or schema of any kind. The single grep hit
(`0018_capability_leases.sql`) is a coincidental use of "threads" as a
verb in a comment ("before increment 3 threads the state"), not a schema
reference. **This is now the 3rd and most authoritative confirmation**
(after: not in dispatch/health/governance modules; named in
`LEGACY_CATALOG` as `coordination.room.*`/`coordination.thread.*` but
`INVENTORIED`/`NOT BACKED`; now confirmed zero persistence-layer schema
either) — room/thread modeling is genuinely, completely undesigned at
every layer of real peerhub. This doc's own room/session/thread data
model proposal (see the top of this file) is real, necessary,
ground-up design work, not a reconciliation task.

## CONCRETE SCHEMA DESIGN (2026-08-26, cx): room/thread as `TargetState`, messages as SEPARATE per-message targets

**Decision: rooms and threads use the governed `TargetState` substrate
(unlike duty leases, which deliberately did NOT)** — reasoning from
access pattern, not just precedent: duty leases are high-frequency
liveness records whose primary operation is heartbeat renewal; rooms/
threads are durable collaborative aggregates needing authorization,
CAS-governed metadata, auditability, independent reads — their frequent
activity is append-oriented (messages), not heartbeat-oriented.

### Room `TargetState.state`

```json
{
  "kind": "room", "schema_version": 1, "room_id": "room-01", "topic_id": "topic-01",
  "title": "...", "status": "active", "created_at": "...",
  "created_by": {"instance_id": "instance-a", "profile_id": "cx.standard"},
  "participants": [{"instance_id": "...", "profile_id": "...", "role": "member", "joined_at": "..."}],
  "thread_ids": ["thread-architecture", "thread-implementation"],
  "session_bindings": [{"binding_id": "...", "instance_id": "...", "profile_id": "...", "session_id": "...", "role": "participant", "bound_at": "..."}],
  "message_projection": {"message_count": 12, "last_message_id": "message-12", "last_message_at": "..."},
  "retention": {"mode": "retained"}
}
```

Participant identity is explicitly the confirmed composite
`(instance_id, profile_id)`, not a bare profile/session ID.

### Thread `TargetState.state`

```json
{
  "kind": "thread", "schema_version": 1, "thread_id": "thread-architecture", "room_id": "room-01",
  "subject": "...", "status": "open", "created_at": "...",
  "created_by": {"instance_id": "...", "profile_id": "..."},
  "participant_keys": [{"instance_id": "...", "profile_id": "..."}],
  "message_projection": {"message_count": 3, "first_message_id": "message-101", "last_message_id": "message-103", "last_message_at": "...", "preview": "..."}
}
```

**Thread state holds only a projection, never full message bodies.**

### Messages — SEPARATE `TargetState` per message, NOT embedded

**Reasoning (revision-churn avoidance)**: embedding the full message
list inside room/thread state would mean every message (1) requires
reading+replacing the whole thread state, (2) increments the thread's
CAS revision, (3) increases conflict probability for concurrent senders,
(4) grows the record without bound, (5) couples message durability to
metadata mutation. **One independent `target_id: "message:<id>"` per
message instead**:

```json
{
  "target_id": "message:message-103", "revision": 1,
  "state": {
    "kind": "message", "schema_version": 1, "message_id": "message-103",
    "room_id": "room-01", "thread_id": "thread-architecture", "sequence": 3,
    "author": {"instance_id": "...", "profile_id": "..."},
    "created_at": "...", "message_type": "text", "body": "...",
    "reply_to": "message-102", "metadata": {}
  },
  "updated_at": "..."
}
```

Messages are immutable after creation (or an edit creates a new revision
under an explicit governed edit operation, if edits are ever needed).
Room/thread's `message_projection` updates opportunistically but is
**never the sole source of message truth**. Query model: `room target →
thread IDs; thread target → message query by (room_id, thread_id,
sequence); message targets → complete bodies/authorship`. **If strict
contiguous ordering is required, sequence assignment needs a separate
serialization/append-coordinator mechanism — that's a design dependency,
not a reason to embed messages in the thread target.**

### Room vs. topic vs. thread — resolved

- **Room** = durable collaboration CONTAINER — owns participants,
  session bindings, lifecycle, retention, the set of threads.
- **Topic** = the subject/continuity LABEL associated with a room —
  describes what the room is about, may carry across lifecycle
  transitions, but is NOT itself the message stream or execution
  session.
- **Thread** = a focused conversational/work SUBSTREAM within a room —
  own subject, participants, lifecycle, ordered message set.

`room → {topic_id/metadata, participants, session_bindings, thread_ids →
message targets}`. **A topic is not an alias for a thread** — a room has
one current topic and several threads; a thread discusses one aspect of
the topic. Whether a new topic creates a new room / mutates the existing
room / creates a room generation is a lifecycle-POLICY decision, not
inferable from the storage schema alone.

### CONFIRMED (2026-08-26, direct real `hub.py` source read): exact `clear-room` semantics

Read `action_clear_room()`, `P:\_sys\core\hub.py:3577-3605`, directly —
this was previously "not determinable from source alone" because that
read hadn't actually been done yet; it is fully determinable, and the
existing safe-default guess above is **confirmed correct**:

`clear-room` does **NOT** delete anything. It: (1) mints a brand-new
`room_id` (`_short_id("room-")`); (2) resets only the ephemeral pointer
fields on `state.json` — `room_id`, `members` (re-keyed to fresh
per-member session IDs via `_new_member_sids`), `mission`, `blocked`,
`phase="clear-room"`; (3) writes a **new** handoff file under
`sessions/{new_room}/` (GOAL=subject, one ACTIVE_THREADS line noting
`clear-room from {old_room}`); (4) calls `_clear_peer_sessions(pid,
"clear-room:{new_room}", ai_root)` for every routable peer — this clears
each peer's own *live session binding*, not any history. The **old**
room's directory and handoff file are never touched, moved, or deleted.

Native mapping this confirms: `clear-room` = allocate a new room
`TargetState` + reset an "active room pointer" field elsewhere (session/
duty-lease layer, not the room target itself) + release each peer's live
session binding for the new reason string. The old room's `TargetState`
is left exactly as-is — no archive/delete operation is needed to
replicate this legacy behavior; a real bug would be introduced if the
native version deleted or archived the old room, since legacy never did.

### Unresolved (needs more source access or explicit policy decision)

Whether message targets are physically stored in the same backend/table
as other `TargetState` records; whether the broker currently supports
dynamic target families for rooms/threads/messages at all (not yet
confirmed — this whole design assumes it does); whether message ordering
must be strictly contiguous/globally serialized; whether edits/deletes/
reactions/attachments/redactions are required; whether room-membership
changes need CAS on the room target or a separate membership target;
does a topic change create a new room, mutate the existing one, or
create a room generation; retention/pagination/indexing/garbage-collection
policy for message targets.

## RATIFIED (2026-08-27): the remaining 6 open questions, closed via a dialectical cc/cx round

Room/thread/message (this doc's earlier schema design) are already built
and out of scope here. Of the original 16 open questions, 10 were already
resolved earlier in this doc (clear-room, handoff fields, room/topic/
thread distinction, etc.) or answered by code actually built this session
(duty-lease exclusivity/timeout/sweep, via the real `DutyLeaseCoordinator`).
The remaining 6 went through one real critique round (cc drafted positions
grounded in this doc's own text + a direct read of legacy `_LIMITS` in
`P:\_sys\core\hub.py`; cx pushed back with a stronger alternative on one
item and concrete refinements on the rest; no unresolved disagreement
remained after one round). Ratified:

1. **Thread reactions** (open Q10): NOT a single mutable-status `TargetState`
   per reaction (cc's initial draft — rejected). Ratified per cx's
   counter-position: immutable, append-only reaction EVENTS keyed by
   `(message_id, actor_key, reaction_type, event_id)`, plus a maintained
   "current reaction" projection for fast "who currently reacted?" reads.
   Toggle-off emits a removal event and updates the projection; never a
   physical delete. Matches this doc's own §5 append-only-events-plus-
   projection pattern more closely than a CAS status flip would. Exact
   persistence mechanism for the "current" projection (its own dedicated
   target vs. computed on read via a broker scan, mirroring how room/
   thread sequence numbers are already computed) is left to implementation
   time, not a blocking design choice.
2. **`context-fill` result shape** (open Q11): one authoritative JSON
   envelope (`room_id`, `session_id`, `as_of_event_seq`, `sections`,
   truncation/provenance metadata) as the sole backend contract — no
   separate Markdown backend path. The CLI renders it as Markdown/plain
   text for human display; `--json` emits the same envelope raw. Per cx's
   refinement, sections are NOT opaque strings where truncation/item-count
   matters — list-shaped sections carry `{"items": [...], "truncated":
   bool}`, matching the `Mapping[str, JsonValue]` result-encoding
   convention already established this session for every read-only
   `CommandDescriptor`.
3. **Startup section set + limits** (open Q12): exactly the 6 legacy
   sections — `GOAL, RECENT_COMPLETED, PENDING_ISSUES, KEY_DECISIONS,
   CONSENSUS_HISTORY, ACTIVE_THREADS` (the §3 example command's 5-section
   list is a documentation bug missing `recent_completed` — corrected
   here, not intentional narrowing). Limits ported 1:1 as real legacy
   defaults, read directly from `P:\_sys\core\hub.py`'s `_LIMITS` dict, not
   reinvented: `handoff_max_chars=12000` (total text budget, applied as a
   post-render safety trim — legacy trims `RECENT_COMPLETED` first when
   over budget, see `hub.py:1316`), `handoff_max_completed=5`,
   `handoff_max_issues=3`, `handoff_max_decisions=3`,
   `handoff_max_consensus=10`, `handoff_max_threads=5` (all five are
   pre-render item-count caps — a list is sliced to its last N items
   before rendering), `GOAL` uncapped as a single scalar. Per cx's
   scope-discipline refinement: these are fixed protocol defaults for now,
   NOT workspace-configurable — a config-driven tuning knob is explicitly
   deferred future work, not part of this ratification.
4. **Handoff projection necessity** (open Q13): required as a first-class
   generated (never authoritative) capability — this doc's own §3 already
   argues it serves inspection/recovery/portability/debugging, not just
   legacy compat, and that reasoning is adopted as-is. Per cx's refinement:
   "required" means deterministic, versioned, and re-generatable on demand
   from the real authoritative event log/projections — NOT necessarily
   written synchronously on every single mutation. A stale or missing
   generated handoff file is a recoverable inconvenience, never data loss,
   since the event log/projections remain the actual source of truth.
5. **`terminal-close` scope** (open Q14): the doc's own command-surface
   table already answers this (`terminal-close → duty.release (+ optional
   session.close)`) — ratified as-is: default releases ONLY the duty
   lease; `--close-session` is an explicit opt-in, consistent with
   `end-session` never implicitly cascading to a room close elsewhere in
   this doc. Per cx's addition: if duty release succeeds but the optional
   session close fails, the command must report both outcomes
   independently (not collapse into one pass/fail) and remain safely
   retryable.
6. **Room retention policy** (open Q3): indefinite retention by default
   (`"retention": {"mode": "retained"}`, matching the already-built schema
   and the CONFIRMED real `clear-room` behavior, which never deletes the
   old room). Explicit archival is a human/governance action moving a
   room's `status` to `ARCHIVED` — never automatic. An automatic GC/expiry
   policy is explicitly deferred pending real evidence of storage growth
   becoming an operational problem, not designed speculatively now. Per
   cx's addition, ratified as required NOW regardless (cheap, non-blocking
   safety rails, not the GC algorithm itself): expose storage/record
   count + age metrics for observability; make the retention policy field
   explicit in schema/config even while only `retained` is supported;
   prohibit any implicit deletion via ordinary projection-maintenance
   code paths; reserve archival/GC as its own separately-ratified future
   operation with its own authorization/audit/recovery semantics.

These 6 are now unblocked for implementation under the same
architecture-before-implementation discipline this session has followed
throughout — see `docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md`
for the resulting implementation work.

## RATIFIED (2026-08-27): gap-7 `peerhub diag` unification (separate from gap-3, decided in the same round)

Ratified: option A3 — a `--domains` flag on the existing `peerhub diag`
command, not a merge into the default view (rejected: mixes two
unrelated telemetry audiences/refresh-rates/failure-modes into one
default output) and not a brand-new separate command (rejected for now:
adds a second surface to remember for a single flag's worth of value;
revisit only if governed-domain diagnostics grows into a substantial
dashboard with filtering/mutation-links of its own). Default `peerhub
diag` output is unchanged; `peerhub diag --domains` adds a governed-
domain section (consensus/task/duty rows via `domain_rows.py`'s existing
formatters); `--domains --json` combines with the existing `--json` mode.
Per cx's addition: domain-collection must be failure-isolated — if the
governance workspace/database is unavailable, that must not break the
existing peer-CLI-quota telemetry the rest of `diag` already provides.

## RATIFIED (2026-08-27): room-participation sessions (`init-session`/`end-session`) get a NEW dedicated coordinator, not a reuse of `SessionLeaseCoordinator` or `DutyLeaseCoordinator`

Closes the architecture gap recorded in
`docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md` item 7. One
critique round: cc drafted a position after directly confirming how
deeply `command_id`/`attempt_id` are baked into `SessionLeaseCoordinator`'s
real fence-validation logic (`peerhub/dispatch/model.py`, 10+ sites); cx
pushed back with more precise reasoning grounded in further direct source
reads (migrations, `contract.py`, `sqlite_dispatch.py`) and corrected one
overstatement (`DutyLeaseCoordinator` has no actual fencing-token field —
it compares lease/owner/term/authority_epoch, not a separate token).
Converged in one round.

**1. New coordinator, reusing patterns not domain models.** Working name
`RoomParticipationCoordinator` (cx's naming — `RoomSessionCoordinator`
was rejected as too easily confused with the existing provider/execution
"session" machinery). Real reasons a `SessionLeaseCoordinator` reuse is
wrong (stronger than "needs nullable fields," which overstates the
mechanical blocker — sentinel `command_id`/`attempt_id` values are
possible, migration 0003 has no FK forcing a real command/attempt to
exist): `command_id`/`attempt_id`/owner-identity/revision/fencing-token
are compared as *security-authoritative* fence dimensions
(`peerhub/dispatch/model.py`); every ACTIVE/RENEWED execution lease
requires real OS process-birth identity, which a human/browser/remote
room participant doesn't have; `close_lease()` doesn't retire the
`SessionBindingSnapshot`, so immediate reopening under the same key can
stay blocked until the old heartbeat timestamp naturally expires;
synthetic sentinel leases would inflate the dispatch-wide active-lease
count query, which has no lease-kind discriminator. A direct
`DutyLeaseCoordinator` reuse is also wrong: its real DB constraint is one
ACTIVE row per `(room_id, role)` — exclusive by design — but a room
naturally has many concurrently active participant sessions. The new
coordinator/table reuses the proven STRUCTURAL PATTERN both existing
coordinators already established (dedicated unit-of-work, clock, ID
source, timeout-based heartbeat renewal, partial-unique-index,
recovery-receipt shape) — not either one's literal domain model.

**2. Uniqueness key.** `(workspace_scope_id, room_id, actor_principal_id,
instance_id, profile_id)`, with a partial unique index applying only to
ACTIVE rows (so ended/expired/abandoned rows remain distinct historical
intervals, never blocking a fresh session). `session_fingerprint` is
compared DATA on the row, not part of the key — putting it in the key
would let fingerprint drift silently evade the retirement it's supposed
to trigger. `actor_id`/`actor_principal_id` must be bound to the
authenticated caller identity, never trusted merely because it arrived
as a command parameter.

**3. `instance_id` granularity — resolved now, not deferred.** cx
correctly flagged this as a real open premise (does `instance_id` mean
one terminal incarnation, or a stable peer identity that legitimately
spans multiple simultaneous tabs/processes, which would need an
additional `client_incarnation_id` in the key to avoid rejecting real
concurrency). Ratified: `instance_id` means one terminal incarnation,
matching how it's already used everywhere else in this exact codebase
(`DutyOwnerIdentity`, `SessionBindingKey`) with no separate incarnation
concept. Per the same "don't design speculatively" discipline already
applied to gap-3's retention/GC policy: if a real multi-tab/multi-process
concurrency need surfaces later, add `client_incarnation_id` then, with
real evidence driving the schema change — not now.

**4. Fencing — lightweight, not a DutyLease-style transplant.** An
immutable `session_id` plus a monotonic `session_generation` is the
fence: every heartbeat/end/resume carries the exact `session_id`, owner
identity, and `session_generation`; updates are conditional on that
identity + ACTIVE state + not-yet-expired; retirement/replacement and
close are transactional; an expired row is never resurrected by a late
heartbeat — recovery goes through the explicit create/resume lifecycle
instead. This is real protection against a stale caller without
synchronizing a changing fencing token on every heartbeat (which a full
dispatch-style fence-tuple would require).

**5. Independent from dispatch-attempt leases — confirmed, not just
assumed.** Ending a room-participation session must NEVER close or
cancel an in-flight `SessionLeaseCoordinator`-tracked dispatch-attempt
lease. Concrete consequences, all ratified as-is: cancelling an in-flight
dispatch requires its own explicit dispatch-cancel operation, never an
implicit side effect of session end; any link from an attempt back to
the room session that originated it is named `origin_room_session_id`
and is PROVENANCE, never ownership; if the terminal process itself dies,
the dispatch supervisor's own process-liveness path handles the
execution lease, not the room-session coordinator; a dispatch result that
completes after its originating session has ended must remain durably
discoverable through mailbox/context continuity, never silently
discarded with the connection; `terminal-close --close-session` (the
seam already left open in the ratified `terminal-close` implementation,
see `HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md` item 8) reports the
duty-close and the room-session-close outcomes independently, exactly
matching item 5's earlier terminal-close ratification.

**6. New items surfaced by this round, also ratified:**
   - **Heartbeat driver**: the command surface only ever specified an
     explicit heartbeat for terminal duty; non-duty room participants had
     no specified way to stay live. Ratified: a new native `session.heartbeat`
     command, mirroring `terminal.heartbeat`'s existing shape — explicit
     and simple, not inferred from unrelated activity (which would be
     implicit and fragile).
   - **Room `TargetState`'s `session_bindings` field**: would become a
     second, conflicting source of truth once a dedicated table is
     authoritative for sessions. Ratified: `session_bindings` is an
     explicitly-declared REBUILDABLE PROJECTION on the room target, kept
     opportunistically in sync — never authoritative — matching the exact
     same operational-state-vs-projection split already ratified for the
     handoff.md file and for `message_projection` on rooms/threads.
   - **`init-session` idempotency**: a compatible duplicate call (same
     identity tuple, no fingerprint drift, not expired) converges on
     returning the EXISTING session — an idempotent no-op, never an
     error. Fingerprint drift or expiry triggers one atomic
     retire-then-create-exactly-one-successor transaction. This reuses
     the exact proven transactional shape `SessionLeaseCoordinator.
     create_session_and_lease()` already implements for its own binding
     key (check existing → expired? → CAS-replace or reject) — not a new
     pattern.
   - **Events**: open/resume/retire/end/expire/abandon transitions are
     real append-only continuity events, matching gap-3's own general
     events-plus-projection discipline. Heartbeats themselves do NOT each
     emit an event — that would be excessive volume for a frequent
     operation — they just update the liveness row.

Implementation of `RoomParticipationCoordinator` (or whatever its real
final name becomes at build time) is now unblocked under the same
architecture-before-implementation discipline this session has followed
throughout.
