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
- ~~CLI Ctrl-C cancellation-ladder wiring (T4)~~ - **FIXED AND COMMITTED 2026-08-16 (`98586ff`).** `peerhub ask` runs the dispatch on a background thread; on interrupt the CLI now calls the live `ProcessSupervisor.begin_cancellation()` (SOFT_CANCEL -> TERMINATE_TREE -> KILL_TREE) via a `cancellation_hook` threaded through `execute_direct_ask()`/`dispatch_and_execute()`, with a bounded poll for the narrow race where the interrupt lands before the supervisor exists. Two issues caught only by independent re-verification, not the implementer's own report: a false "pyright clean" claim (4 real errors, fixed by removing the `Optional` type structure that caused them rather than silencing); and a pre-existing CLI test that encoded the old "not implemented" behavior and broke on the intentional message change (only the new test file had been run, not the full suite) -- updated to assert the new behavior. Full suite independently re-run: 961 passed, pyright clean.
- ~~2 broken real-agy adapter integration tests~~ - **FIXED AND COMMITTED 2026-08-16 (`a81dc9e`).** Confirmed two genuinely unrelated bugs, not one: `test_real_agy_adapter_shells_out` had a stale decoder-event-count assertion (fixed to check both `SESSION_IDENTITY`/`ASSISTANT_TEXT` events precisely); `test_real_agy_adapter_via_pipe` had the stale keyword-arg `TypeError`, which once fixed surfaced two further latent bugs in shared test routing helpers (a profile mismatch and a peer_kind mismatch), both fixed via optional overrides that don't change any other test's default behavior. Full suite independently re-run by the terminal: 960 passed; the only slow-test failure (`test_peerhub_facts.py::test_real_peer_versions_match_the_shipped_contracts`) is an unrelated, genuine ag CLI version drift (1.1.12->1.1.13) correctly caught by the drift-detection tool itself, not caused by this fix.
- Tool-call parsing (peers that invoke their own tools mid-response) - Not yet handled. [Source: HUB-REPLACEMENT-ROADMAP Phase 3] [Size: small]
- ~~Health/quota tracking equivalent to diag.py (T2)~~ - **DESIGN RATIFIED 2026-08-16**, moved to Implementation-Ready-Pending-Canary below.
- Crash-linkage recovery (resuming an interrupted round after a coordinator crash) - Deferred as increment 4. [Source: HUB-REPLACEMENT-ROADMAP Phase 3 Broadcast] [Size: architecture]
- Phase 4 shadow-by-ownership-cluster validation - Not started. [Source: HUB-REPLACEMENT-ROADMAP Phase 4] [Size: architecture]
- Phase 4 same-revision comparison + rollback proof - Not started. [Source: HUB-REPLACEMENT-ROADMAP Phase 4] [Size: architecture]
- Phase 4 dedicated design pass (T7a) - Not started; no concrete "scoping memo" artifact found under this label anywhere in the repo -- treat as not yet begun, not merely undocumented. [Source: HUB-REPLACEMENT-ROADMAP Phase 4] [Size: architecture]

## BLOCKED
- ConsensusRound / Primitive B - Blocked gate; must be built before first R:10 decision is routed to peerhub, and blocked on durable response transcripts. [Source: HUB-REPLACEMENT-ROADMAP Phase 4] [Size: architecture]

## Items Not in Peerhub's Own Roadmap
- ~~Implement 3-Tier Context Partitioning (`EvidenceArtifact`)~~ - **DESIGN RATIFIED 2026-08-16**, moved to Implementation-Ready-Pending-Canary below.
- ~~Deploy Windows-native Brokered Read-Only Reducers~~ - **DESIGN RATIFIED 2026-08-16**, moved to Implementation-Ready-Pending-Canary below.
- **CLI Ctrl-C cancellation-ladder wiring** - genuinely absent from the roadmap doc's own text (confirmed by direct grep for "ctrl-c"/"cancellation-ladder" -- zero matches); the underlying TODO exists only in `peerhub/cli.py`. Now added above under UNSCHEDULED-READY.

## Implementation-Ready (design ratified, 2026-08-16 sequential detailing pass)
- **Health/quota tracking (T2)** - `HEALTH-QUOTA-TRACKING-DESIGN-2026-08-16.md`. Gated on an explicit empirical canary (proving CLI polling works identically from inside peerhub's own process) before implementation starts. Migration `0024_telemetry_quota_tracking` (was `0023` in the original doc; EvidenceArtifact landed first and took `0023`, doc updated).
- ~~EvidenceArtifact / 3-Tier Context Partitioning~~ - **INCREMENT 1 DONE (`baa6a04`), INCREMENT 2 DONE (`d744ce9`), both 2026-08-16.** Data layer (dataclass + migration `0023`) and Claude-adapter offload/substitution wiring both landed and independently re-verified (967 passed, pyright clean). Codex and agy adapter wiring are separate, not-yet-started increments (the design doc scoped Claude + Codex explicitly; agy wiring wasn't scoped in the ratified doc at all and would need its own small design check first).
- **Windows-native Brokered Read-Only Reducers** - `WINDOWS-BROKERED-REDUCERS-DESIGN-2026-08-16.md`. Gated on 3 empirical preconditions before implementation: process-spawn privilege availability, `agy.exe`'s real write footprint (Low IL is system-wide, not worktree-scoped -- an unresolved risk of breaking `agy` outright), and a network-egress policy decision. Would close 3 of the 4 capability-lease errata Section 8 prerequisites for `READ_ONLY` tier if the preconditions check out.

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
