# Pre-TDD Final Ratification (2026-08-26)

> **Purpose**: close every remaining open item across all 7 gap docs before implementation/TDD starts, per the standing "no ambiguity discovered mid-TDD" requirement. This is the terminal session's gate — read this doc's verdict as authoritative over any older "Unresolved" list in the individual gap docs; those lists predate this ratification pass.

## Method

The terminal compiled the full remaining open-item list from all 7 gap docs (53 items total, including one new cross-cutting finding) and dispatched it to `cx` with instructions to be **stingy about escalating to the user** — ratify anything resolvable by consistency with what's already confirmed elsewhere in this design set, defer anything that only becomes answerable during implementation, and reserve "genuine user decision" for actual business/authority/risk-tolerance judgment calls only. `cx` ratified 52 of 53 items outright; exactly one (approval authority model) was correctly flagged as needing the user, and the user has now answered it (below). The terminal cross-checked the highest-stakes ratifications for consistency before accepting this as final.

## The one user decision (RESOLVED)

**Q**: Who is the authoritative approver for gap-5's `AWAITING_APPROVAL` blocking gate (legacy `approval-request` was fire-and-forget to `human_interface_peer`; native peerhub upgrades this to a real blocking gate, so someone must be validated as the approver)?

**A (user, 2026-08-26)**: **Named/designated user** — this is fundamentally a single-operator environment; the approver is the one real human account holder, not a role, a room owner, or an external IdP. `role`/`room-owner`/`IdP` models are explicitly out of scope unless the operating model changes to multi-human later.

## Ratified defaults (52 items, adopted as design defaults — revisable later, not blocking)

Full item-by-item ratification (grounded in real-source consistency, e.g. reusing gap-4's `DutyLeaseCreateRequest` pattern for gap-2's coordinator lease, reusing gap-6's separate-target pattern for gap-3's message/membership targets, and applying the user's own "legacy superset-compatible changes may be applied freely" authorization to two real legacy-behavior upgrades):

| # | Gap | Item | Ratified default |
|---|---|---|---|
| 1 | 1 | Cutover style | Atomic cutover, rehearsed + reversible pre-cutover window |
| 2 | 1 | `.ai` state migration | Explicit import/receipt step, not a fresh start |
| 3 | 1 | In-flight work at cutover | Quiesce new work, let in-flight reach a recorded boundary, import or mark — never silently abandon |
| 4 | 1 | Old transcripts/handoffs | Read-only archive, consumed only via an explicit importer |
| 5 | 1 | "Equivalent" scope | Policy-outcome equivalence + materially equivalent persistence/audit observability |
| 6 | 1 | Rollback after legacy deletion | Release/tag backup + explicit restore procedure |
| 7 | 1 | Deletion-gate approver | Human authority, durable approval receipt required |
| 8 | 1 | Temporary shim state-writes | Pure translator only; all state writes go through PeerHub |
| 9 | 1 | Caller-absence evidence | Both static search AND runtime telemetry over a defined window |
| 10 | 2 | `f(N,risk)` for N>3 | **Already resolved** — real `protocol.md` itself says "undefined above N=3, default to N"; peerhub matches legacy exactly, no need to invent something legacy never had |
| 11 | 2 | Quorum loss after correction | No silent loss — a correction invalidates the vote and requires a fresh vote |
| 12 | 2 | Final Call mandatory scope | Mandatory for Tier-0/high-risk/unresolved-dissent; not required for ordinary low-risk quorum |
| 13 | 2 | Final Call ACK veto scope | Vetoes authority/safety/integrity/irreversible-effect violations; not cosmetic/schema preferences |
| 14 | 2 | Post-30min escalation mechanics | Explicit escalation intervals/deadlines/terminal states; never indefinite implicit waiting |
| 15 | 2 | `human_override` auth evidence | Real technical prerequisite — reject without verifiable authority evidence, not just a policy note |
| 16 | 2 | Coordinator lease type | Reuse `DutyLeaseCreateRequest` directly (gap-4) — same authority/lease shape |
| 17 | 2 | Audit history location | Operational state in `TargetState.state`; immutable audit history in an external append-only journal |
| 18 | 3 | Topic-change semantics | Creates a room *generation*; room identity preserved, thread semantics isolated per generation |
| 19 | 3 | Message ordering | Contiguous per-room sequence numbers, one authoritative ordering path; no global cross-room serialization |
| 20 | 3 | Edits/deletes/reactions v1 scope | Out of scope for v1 except immutable append/create + required redaction hooks |
| 21 | 3 | Room-membership CAS | Separate membership target with its own CAS; room state references membership revision |
| 22 | 3 | Retention/pagination/GC | Cursor pagination + bounded retention + indexed lookup + explicit GC states defined now; exact tuning deferred |
| 23 | 4 | `term` vs `authority_epoch` | Kept separate — term = leadership generation, epoch = stale-record fencing |
| 24 | 4 | Process-incarnation binding | Optional when available, fail-closed on conflicting identity |
| 25 | 4 | Election state location | Separate from `DutyLeaseSnapshot`; one dedicated lease record remains the liveness authority |
| 26 | 4 | Challenge-window score/overwrite discrepancy | **FIX it** — implement real score-based arbitration; do not knowingly port the documented toothless overwrite (covered by the user's superset-compat authorization) |
| 27 | 4 | AP-20 monopoly guard in duty-lease design | Add `consecutive_terms_held` + atomic create/renew check enforcing `< N` |
| 28 | 5 | `approval-request` fire-and-forget vs blocking | **Build the richer blocking `AWAITING_APPROVAL` gate** (superset-compat upgrade, not a straight port) |
| 29 | 5 | Approver authority model | **User decision, resolved above: named/designated user** |
| 30 | 5 | Approval expiry | Expires to `EXPIRED`, requires explicit re-request; never silent auto-reject or indefinite pending |
| 31 | 5 | Checkpoint data shape | Both artifact reference and opaque resume token supported, independently optional |
| 32 | 5 | `child_request_ids` parallelism | Parallel IDs permitted; dependencies/completion represented explicitly |
| 33 | 5 | Failure/failover history | Counters + last-event summary in task state; detailed history appended to the audit stream (consistent with item 17) |
| 34 | 6 | Proposal authority levels | Derived from the active voter set + gap-2's quorum function per R:5/8/10 level; no duplicate quorum logic |
| 35 | 6 | Lesson approval via ratified proposal | Permitted only where the proposal's authority class explicitly allows it; provenance link retained |
| 36 | 6 | Raw feedback 3-layer design | Introduce now (raw-event → normalized → delivery-pack) — **note: this is a real scope increase for v1 TDD**, flagged for awareness, not walked back |
| 37 | 6 | Lesson-broadcast semantics | Pack invalidation + next-dispatch eligibility; immediate notification is an optimization only |
| 38 | 6 | Lesson acknowledgement | Required + bounded-retry for high-severity lessons; delivery evidence only (no blocking ACK) for lower severity |
| 39 | 6 | Sweep authority | Mark-for-review only; retirement requires governance/human approval |
| 40 | 6 | Alert ownership | Explicit alert-class → gap-4-role policy table; default follows the role controlling the affected resource |
| 41 | 6 | Arbiter authority mapping | DIR-005 encoded explicitly: advisory-default / canonical-opinion for unresolved dissent+ties+high-risk / human-mandatory for irreversible unresolved conflicts |
| 42 | 6 | Alert → proposal requirement | Alerts may resolve operationally unless they change standing policy/authority/an irreversible decision |
| 43 | 6 | Cross-workspace lesson conflict | Global lessons are baseline; workspace lessons may narrow but not contradict without an approved exception proposal |
| 44 | 6 | Retention/archival layout | Separate append-only partitions per artifact type, retention metadata + archival receipts |
| 45 | 7 | Legacy diag text parity | Semantic parity required; byte-compatible layout not required |
| 46 | 7 | EXH contract status | Adapter-compat field, not a durable public contract |
| 47 | 7 | Credits domain model | Generic provider capability exposed through a PeerHub resource interface |
| 48 | 7 | Displayable credit fields | Counts/status/coarse expiry only; never provider identifiers or account/receipt data |
| 49 | 7 | `credit-consume` routing | Through the general mutation broker, with a compat adapter for the legacy command |
| 50 | 7 | `model-status` placement | Canonical under `diag --profiles`; legacy command kept as a compat alias |
| 51 | 7 | Token-accounting authority | Provider receipts authoritative for billed/token accounting; task-attempt events are operational corroboration/discrepancy evidence |
| 52 | 7 | Unavailable-field convention | Store a structured absence reason internally, render as `UNAVAILABLE` (formalizes what multiple gap docs already independently converged on) |

## The one shared implementation prerequisite (item 53 — NOT a pure design decision)

**Finding** (terminal, direct `peerhub` source read, this session): the governed-mutation broker has **zero listing/query capability** anywhere — confirmed at the abstract contract layer (`peerhub/state/contract.py`), the governance broker layer (`peerhub/governance/broker.py`, `get_target(target_id)` is an exact-key point lookup only), and the real concrete SQLite backend (`peerhub/persistence/sqlite_governance.py` — has real `list_*` methods, but only for outbox/effect-delivery bookkeeping, none for targets). Every domain built on `TargetState` (consensus rounds, rooms/threads, tasks, governance artifacts) currently cannot answer "list active X for this room" without already knowing the exact `target_id`.

**cx's recommendation (ratified by terminal)**: add a **generic indexed target projection at the broker/backend level** (e.g. a `kind`/`room_id`-indexed column + a real `list_targets(kind, room_id)` method added to the concrete backend, and to `GovernanceReadUnitOfWork`'s Protocol if kept backend-portable) rather than having each domain maintain its own separate index target. Rationale: one authoritative query surface avoids N independent index-consistency failure modes; per-domain indexes should stay an optimization layered on top only if the generic path proves insufficient for some domain's access pattern.

**This is not gap-2/3/5/6/7-specific design work — it's a small, shared, one-time piece of real peerhub infrastructure that should land BEFORE (or very early alongside) TDD starts on any of those five gaps**, since all of them depend on it for "list active X" behavior. Track it as its own implementation task, not folded into any single gap's TDD scope.

## Standards-interop check (added 2026-08-26, after this doc's original verdict)

Before starting TDD, the user asked whether A2A/MCP/other standard agent
protocols needed consideration. Full evaluation (real web-verified facts,
Position-A/Position-B debate, primary-source citation check) is in
`OSS-ADOPTION-STRATEGY-2026-08-15.md` Section 10. **Verdict: no impact.**
MCP was already resolved in that doc's original 2026-08-15 round
(adapter outside the kernel). A2A — never previously evaluated, and a
closer domain overlap with gap-2/gap-5 than MCP had — was evaluated fresh
and resolved the same way: an optional future adapter/projection layer
outside peerhub's authority kernel, zero schema changes needed to gap-5's
task `TargetState` or gap-2's consensus design. AGNTCY/OASF, ANP, and
Agent Skills were surveyed and confirmed correctly out of scope (agent
discovery/identity/knowledge-packaging concerns, not peerhub's
coordination-kernel domain). **This does not change the verdict below.**

## Verdict

**All 7 gap categories are now TDD-ready.** Every item that could be resolved by design-consistency reasoning has been; the single genuine business-judgment item has been decided by the user; the one real remaining prerequisite (broker listing capability) is a small, clearly-scoped, shared infrastructure task, not an open design question. Remaining "exact field name"/"exact schema micro-detail" items across the gap docs are correctly left for implementation time — forcing them now would be guessing without benefit, consistent with this session's own "safe to defer to implementation" bucket criterion.

No outstanding item was found that should block starting TDD.
