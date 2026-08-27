# hub.py-Replacement TDD Progress (2026-08-27, overnight session)

> Status doc, not a design doc — records what's actually implemented and tested, as of the commits listed below, so anyone picking this up doesn't have to reconstruct it from git log. Supersedes nothing; `HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md` remains the design-closure record.

## Execution dispatcher investigation

Investigation completed before implementation (2026-08-27). The README's typed boundary is real: `peerhub/application/api.py` defines `ApplicationAPI`, and `peerhub/client.py` defines `Client`. `Client.submit(Command)` reconstructs a strict `CommandEnvelope` and calls `ApplicationAPI.submit()`. `ApplicationAPI` has a real `method -> CommandDescriptor` registry, performs protocol/method/availability/idempotency/decode/auth validation, then invokes the descriptor's `handle` callable and encodes its result.

That dispatcher is not wired to the legacy translator or the five domain services. `ApplicationAPI._register_builtins()` registers only the existing dispatch admission/request/lease operations. The `SubmitDispatch`, `SubmitManyDispatch`, and `SubmitCoordinatorDispatch` classes in `peerhub/application/legacy.py` are translation targets, but no descriptors for `dispatch.submit`, `dispatch.submit_many`, or `dispatch.submit_coordinator` are registered. A repository-wide search also found no consumer that takes `TranslatedCommand` and calls `Client.submit()` (or `ApplicationAPI.submit()`). Therefore the three pre-existing backed legacy actions do not execute through this typed path; their historical working behavior is a separate legacy/hub dispatch path, not evidence of a peerhub `TranslatedCommand` execution path.

Conclusion: a real generic dispatcher exists at `ApplicationAPI.submit()`, but its handler-registration surface is currently unconnected to `LegacyTranslator` and the domain services. The clear, smallest supported path is to add descriptors to this existing registry and invoke it through `Client.submit()`; no new dispatcher framework is needed. Before broad wiring, this round will prove the path with the three consensus commands only, with an end-to-end test that translates and executes `consensus-propose` against the real consensus service. The remaining twelve translated commands remain explicitly follow-up work.

## What's real now

29 commits (`e60c4c4`..`39b46c9`), all independently verified by the terminal (full `pytest -q` + `pyright` after every commit, not just cx's own report — see "Verification discipline" below). Test suite: **1080 passed**, 1 known pre-existing unrelated failure (`test_generate_manifest.py`'s committed hub.py hash snapshot, stale before this session started) + occasional pre-existing flakiness in one real multi-threaded CAS-race test under full-suite load (`test_two_real_callers_race_at_attempt_creation_one_loses_cleanly`, passes cleanly in isolation, unrelated to any change this session).

**A complete 3-layer pass now exists for all 5 real domains** (consensus, task, lesson, room, duty-lease):
1. **Native service code**: `peerhub/governance/{consensus,rooms,tasks,lessons}.py` + `peerhub/dispatch/{duty_lease,terminal_duty}.py`.
2. **Native CLI**: `peerhub consensus|task|lesson|room|duty` — real, runnable commands with `--json` output.
3. **Legacy-name translation**: `LegacyTranslator` backs 15 of ~90 `LEGACY_CATALOG` actions (up from 3 at session start) — every legacy action for every domain built this session: `ask`/`ask-all`/`ask-coordinator` (pre-existing) + `consensus-propose`/`consensus-vote`/`consensus-check` + `task-checkpoint`/`task-status`/`task-failover` + `lessons-propose`/`lessons-activate`/`lessons-retire` + `new-topic`/`clear-room` + `leader-claim`/`leader-yield`/`terminal-handoff`/`terminal-heartbeat`.

**Important, confirmed distinction between layers 2 and 3**: the native CLI (layer 2) is fully wired end-to-end and actually runs services for real. `LegacyTranslator` (layer 3) is a genuinely SEPARATE subsystem — it only translates a legacy action name+args into a typed wire `Command` object; it was investigated directly and confirmed that no downstream dispatcher yet exists to actually execute a translated `Command` against a real service (except `ask`/`ask-all`/`ask-coordinator`'s own separate pre-existing path). Every legacy-translation commit this session is honestly scoped as translation-only for this reason — **finding or building that execution dispatcher is the single most valuable next piece of real, not-yet-started work**, since it's what would make all 15 backed legacy actions actually runnable end-to-end, not just correctly-named.

| Gap | Status | Real modules |
|---|---|---|
| **Increment 0** | Complete | `list_targets` on the broker (migration 0025); test-harness survey (no new code needed — `tests/fakes.py` already covers it) |
| **gap-2** consensus | Complete | `peerhub/governance/consensus.py` — propose, cast_vote, final_call_ack, mark_timeout, request_escalation, resolve, abandon |
| **gap-3** rooms/threads | Core + terminal-duty integration | `peerhub/governance/rooms.py` — create_room, create_thread, append_message, clear_room; `peerhub/dispatch/terminal_duty.py` — TerminalDutyService (thin wrapper over gap-4's DutyLeaseCoordinator) |
| **gap-4** duty-lease | Complete | `peerhub/dispatch/duty_lease.py` — create_lease, renew_lease, close_lease, expire_and_recover_lease, validate_lease_fence, AP-20 monopoly guard; migrations 0026/0027 |
| **gap-5** task lifecycle | Complete | `peerhub/governance/tasks.py` — create, claim_start, checkpoint, request_approval, approval_granted/rejected, request_failover, complete, fail, cancel |
| **gap-6** governance/lessons | Complete | `peerhub/governance/lessons.py` — propose, approve, activate, retire, supersede, quarantine, record_delivery_pending/complete |
| **gap-7** diagnostics | Read-path + row formatters | `peerhub/governance/activity.py` — list_active_{consensus_rounds,tasks,lessons}; `peerhub/telemetry/domain_rows.py` — format_consensus_row/format_task_row(_narrow)/format_duty_row |
| **gap-1** compat/cutover | Native CLI complete for 5 domains; 15/~90 legacy actions translate (wire-layer only, no execution dispatcher yet) | `peerhub consensus\|task\|lesson\|room\|duty` CLI; `peerhub/application/legacy.py`'s `LegacyTranslator` for 15 actions |

## What's NOT done yet (real, not hypothetical, gaps)

- **gap-1 — the single biggest piece of real, valuable, not-yet-started work**: **no execution dispatcher exists connecting a translated `Command` to a real service call** for any of the 15 newly-backed legacy actions — investigated directly and confirmed absent (except `ask`/`ask-all`/`ask-coordinator`'s own separate pre-existing path). This needs to be found (if it exists somewhere unexplored — check the `ApplicationAPI`/`Client` typed command boundary the root README mentions) or built before legacy callers could actually run end-to-end through this path, not just have their action names successfully translate. Also ~75 of ~90 `LEGACY_CATALOG` actions remain entirely untranslated (session/messaging/governance-proposal/health/routing actions outside the 5 covered domains).
- **gap-3**: the rest of the original command list beyond room/thread/message/clear-room/terminal-duty — `init-session`/`end-session`/`send`/`mark-read`/`new-topic`/`thread-react`/`thread-promote`/`terminal-close`/`append-handoff`/`checkpoint`/`context-fill`.
- **gap-7**: the formatters exist but are NOT wired into `collect_live_snapshot()`/the live `peerhub diag` render loop yet — they're pure, tested, unconnected functions.
- **gap-2**: `SessionLeaseCoordinator`-backed coordinator-lease reuse (ratification item 16) was never actually implemented — consensus rounds don't yet claim/renew a coordinator lease during their lifecycle.
- No real cross-domain integration tests (e.g. a task's `request_approval` creating a real governance approval flow that a consensus round could gate on) — each domain's tests are self-contained.
- 10 more legacy actions from `LEGACY_CATALOG` map to domains with real native services but weren't translated this session: `consensus-sweep`, `arbiter-review`, `lessons-list` (governance domain has read-only listing already via `activity.py`), plus whatever's covered by the ~75 fully-untranslated actions overlapping domains already built.

## Verification discipline (why every commit's message says "independently verified")

**cx's own sandboxed `pytest` was blocked by a Windows temp-directory ACL restriction in nearly every one of the ~13 rounds this ran across** — it could type-check with `pyright` in its own sandbox, but could almost never actually execute the tests it wrote. This was not a rare edge case; it was the norm. The terminal ran the real test suite after every single round from an unrestricted environment, and this caught real bugs cx's own "verification" never could have:

1. `TargetState.state` freezes JSON arrays to tuples — `== []` assertions failed against the real `== ()` (recurred in 2 separate increments before cx's fresh sessions stopped making it).
2. `MutationSubmission` has no `.target_id` — real field is `.receipt.target_id` (wrong attribute name, never checked against the real dataclass).
3. Migration 0025 and 0026 were both missing `PRAGMA user_version` bumps at first — silently left the schema version one behind after "applying" the migration.
4. `duty_lease.py`'s first refactor called `unit.get_duty_lease()` *after* `unit.commit()` on the same unit of work — raised `RuntimeError: SQLite unit of work is already finished`, a real regression the refactor introduced.
5. `SqliteUnitOfWork` was missing `release_duty_lease`/`insert_duty_recovery_receipt` forwarding methods entirely — `close_lease` raised `AttributeError` on first real use, undetectable without actually running it.
6. `domain_rows.py`'s first version had 18 real pyright errors (3 `reportPrivateUsage`, 15 `Any`-typing) — cx's own "targeted pyright: 0 errors" claim was checking a stale/different scope; a real run found them all.
7. The CLI's `_run_consensus` first version pre-checked `paths.database_path.exists()` and rejected every command including `propose` (a write op that should initialize a fresh workspace) -- `create_runtime()` already handles initialization, the manual guard was both redundant and wrong.
8. The CLI's `--json` output crashed with `TypeError: Object of type mappingproxy is not JSON serializable` -- `dict(target.state)` only converts the top level, frozen nested dicts inside it are untouched. Fixed once with a recursive `_json_safe()` helper, reused correctly in every subsequent CLI round.
9. **Recurred twice across the 5 CLI-wiring rounds**: optional argparse flags defaulting to `""` instead of `None`/the real service-method default (`room_id`, `resume_token`, `scope_kind`) -- each one a silent semantic divergence from what the same Python call with its real default produces, not a crash, so each one needed an explicit test assertion to catch, not just "does it run." By the 4th and 5th rounds (room, duty), cx's own dispatch-time signature review avoided the bug entirely -- explicit warnings citing the exact prior recurrence worked.
10. `peerhub duty`'s first version had 2 unused imports (`reportUnusedImport`), silently inflating `cli.py`'s pyright count from the 10-error baseline to 12 -- caught only because the terminal compares the ABSOLUTE error count against a known baseline every round, not just "pyright exit code."

**None of these would have been caught by trusting cx's self-report.** This is the concrete argument for why every commit in this sequence carries independent terminal-side verification, not just a peer's word — consistent with this whole session's standing "verify, don't just trust a green report" discipline.

## Recommended next steps (not a commitment, just the natural continuation)

1. **Find or build the `Command` execution dispatcher** — the single highest-leverage next step. Without it, all 15 backed `LegacyTranslator` actions translate correctly but cannot actually run. Start by tracing what actually consumes a `TranslatedCommand`/`Command` today (the `Client`/`ApplicationAPI` wire boundary the root README mentions) and whether it's a stub, a real-but-unconnected piece, or genuinely doesn't exist yet.
2. Wire `domain_rows.py`'s formatters into `collect_live_snapshot()` and the live `peerhub diag` render loop (investigated briefly this session — `collect_live_snapshot()` is oriented around P:'s own legacy peer-telemetry files, not a peerhub workspace's `GovernanceBroker` directly, so this needs real investigation into how/whether a workspace root reaches that function before implementing, not just a mechanical formatter hookup).
3. Continue `LegacyTranslator` coverage for the ~75 remaining `LEGACY_CATALOG` actions outside the 5 domains already covered (session/messaging/governance-proposal/health/routing).
4. gap-3's remaining session/handoff commands (`init-session`/`end-session`/`send`/`mark-read`/`thread-react`/`thread-promote`/`terminal-close`/`append-handoff`/`checkpoint`/`context-fill`).
5. A real cross-domain integration test or two, once enough surface exists to make one meaningful (e.g. task approval gated by a real consensus round).
