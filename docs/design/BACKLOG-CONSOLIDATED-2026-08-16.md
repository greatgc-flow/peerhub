# Peerhub Consolidated Backlog (2026-08-16)

Corrected/extended by the terminal after independent verification against
the actual source (2026-08-16) -- see "Terminal corrections" at the
bottom for what changed from the first pass and why.

## AWAITING-USER-DECISION
- Inter-agent message monitoring (MAPLE-Guard pattern) - Requires explicit user decision on whether/when to schedule. [Source: HUB-REPLACEMENT-ROADMAP Cross-cutting, 2026-08-15 entry] [Size: architecture]
- Adaptive/structural loop termination vs. string-based stop signals - Requires explicit user decision on whether/when to schedule. [Source: HUB-REPLACEMENT-ROADMAP Cross-cutting, 2026-08-15 entry] [Size: architecture]

## SCHEDULED-DEFERRED
- Alembic runtime cutover (Increment 2) - **Already ratified 2026-08-11 as HOLD with named triggers** (not an open user decision -- 3-way dialectical ratification: do not implement Option 1 or Option 2 until a trigger fires; if one does, Option 1 -- freeze bespoke v19 as the schema floor -- is the ratified default). [Source: HUB-REPLACEMENT-ROADMAP Phase 2, "Increment 2 (runtime cutover) — RATIFIED 2026-08-11: HOLD, WITH NAMED TRIGGERS"] [Size: architecture]
- Durable response transcripts - Deferred with named trigger. [Source: HUB-REPLACEMENT-ROADMAP Phase 3 Broadcast] [Size: architecture]
- Parallel fan-out - Deferred with named trigger (blocked on measuring SQLite write contention). [Source: HUB-REPLACEMENT-ROADMAP Phase 3 Broadcast] [Size: architecture]
- Capability-lease enforcement-evidence prerequisites - Zero code, trigger-gated on machine-owned launcher evidence (4 named prerequisites). [Source: HUB-REPLACEMENT-ROADMAP Cross-cutting & CAPABILITY-LEASE-DESIGN ERRATA Section 8] [Size: architecture]

## UNSCHEDULED-READY
- CLI Ctrl-C cancellation-ladder wiring (T4) - `dispatch_and_execute()`/`dispatch_with_retries()` construct `ProcessSupervisor` internally, so the CLI has no live cancellation handle; needs a supervisor/cancellation hook routed through `ProcessSupervisor.begin_cancellation()`'s SOFT_CANCEL -> TERMINATE_TREE -> KILL_TREE ladder. Design already fully specified in the TODO comment itself -- no ambiguity left. **Confirmed present via direct TODO grep, was missing from the roadmap doc's own text entirely.** [Source: `peerhub/cli.py:146-151` TODO(Phase 3 increment 5) comment; the `workflows.py:748` line reference inside it predates 5C-3b's line-count changes and should be re-verified before implementation, not assumed accurate] [Size: small]
- 2 broken real-agy adapter integration tests - `test_real_agy_adapter_shells_out` and `test_real_agy_adapter_via_pipe` both fail with `TypeError: ApplicationWorkflows.dispatch_and_execute() missing 3 required keyword-only arguments: 'capability_lease_id', 'peer_instance_id', and 'current_policy_revision'` -- stale call sites from before the capability-lease increment 4 signature change. **Re-run and confirmed still failing today (2026-08-16), not a stale note.** [Source: `tests/integration/adapters/test_real_agy_adapter.py`, `tests/integration/adapters/test_real_agy_adapter_via_pipe.py`, both `@pytest.mark.slow`] [Size: hygiene fix]
- Tool-call parsing (peers that invoke their own tools mid-response) - Not yet handled. [Source: HUB-REPLACEMENT-ROADMAP Phase 3] [Size: small]
- Health/quota tracking equivalent to diag.py (T2) - Needs its own design pass. [Source: HUB-REPLACEMENT-ROADMAP Phase 3] [Size: architecture]
- Crash-linkage recovery (resuming an interrupted round after a coordinator crash) - Deferred as increment 4. [Source: HUB-REPLACEMENT-ROADMAP Phase 3 Broadcast] [Size: architecture]
- Phase 4 shadow-by-ownership-cluster validation - Not started. [Source: HUB-REPLACEMENT-ROADMAP Phase 4] [Size: architecture]
- Phase 4 same-revision comparison + rollback proof - Not started. [Source: HUB-REPLACEMENT-ROADMAP Phase 4] [Size: architecture]
- Phase 4 dedicated design pass (T7a) - Not started; no concrete "scoping memo" artifact found under this label anywhere in the repo -- treat as not yet begun, not merely undocumented. [Source: HUB-REPLACEMENT-ROADMAP Phase 4] [Size: architecture]

## BLOCKED
- ConsensusRound / Primitive B - Blocked gate; must be built before first R:10 decision is routed to peerhub, and blocked on durable response transcripts. [Source: HUB-REPLACEMENT-ROADMAP Phase 4] [Size: architecture]

## Items Not in Peerhub's Own Roadmap
- **Implement 3-Tier Context Partitioning (`EvidenceArtifact`)** - Mid-term (P2) backlog item targeting `peerhub/`. [Source: multi-ai-collaboration-accord-2026-08-15.md] [Size: architecture]
- **Deploy Windows-native Brokered Read-Only Reducers** - Mid-term (P2) backlog item targeting `peerhub/adapters/`. [Source: multi-ai-collaboration-accord-2026-08-15.md] [Size: architecture]
- **CLI Ctrl-C cancellation-ladder wiring** - genuinely absent from the roadmap doc's own text (confirmed by direct grep for "ctrl-c"/"cancellation-ladder" -- zero matches); the underlying TODO exists only in `peerhub/cli.py`. Now added above under UNSCHEDULED-READY.

## Terminal corrections (2026-08-16, after independent verification)
The first pass (ag.deepthink) miscategorized the Alembic cutover as
AWAITING-USER-DECISION; it is actually already ratified (moved to
SCHEDULED-DEFERRED above). It also flagged "T2/T4/T7a" as a single
vague item sourced from the dispatch prompt rather than the repo. Of
the three: T2 is just the already-listed "Health/quota tracking" item
(merged, not a separate gap); T4 (Ctrl-C wiring) is a real, fully-specified,
previously-untracked gap (added above with its real source citation);
T7a has no concrete artifact anywhere and is treated as simply
not-yet-started under Phase 4, not a distinct tracked item. A direct
`TODO`/`FIXME` grep across `peerhub/` and `tests/` found exactly one
hit (`peerhub/cli.py`, the T4 item) -- no other untracked source-code
TODOs exist.
