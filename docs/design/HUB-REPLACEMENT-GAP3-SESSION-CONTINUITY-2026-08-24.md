# Gap 3 Design: Session/Room/Thread/Handoff Continuity (DRAFT — architecture proposed, 16 items need ratification)

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
| `thread-promote` | `thread.promote` | Attach thread to a request/proposal/consensus round; no implicit vote/finalization. |
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
8. What does legacy `clear-room` actually currently delete/reset (needs checking real hub.py behavior)?
9. Exact legacy handoff fields and serialization rules?
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
schema names, timeout values, legacy `clear-room` semantics, handoff-field
compatibility, and projection retention limits pending inspection of
peerhub's real source (the 16 items above).
