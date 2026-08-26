# hub.py-Replacement TDD Progress (2026-08-27, overnight session)

> Status doc, not a design doc — records what's actually implemented and tested, as of the commits listed below, so anyone picking this up doesn't have to reconstruct it from git log. Supersedes nothing; `HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md` remains the design-closure record.

## What's real now

18 commits (`e60c4c4`..`e9dfbb7`), all independently verified by the terminal (full `pytest -q` + `pyright` after every commit, not just cx's own report — see "Verification discipline" below). Test suite: **1068 passed**, 1 known pre-existing unrelated failure (`test_generate_manifest.py`'s committed hub.py hash snapshot, stale before this session started, tracked separately, not a peerhub regression).

**First real end-to-end CLI path now exists**: `peerhub consensus propose|vote|status` (see gap-1 row below) — a real, runnable command, not just Python/test-callable code. This is the first CLI-layer work in this whole TDD pass.

| Gap | Status | Real modules |
|---|---|---|
| **Increment 0** | Complete | `list_targets` on the broker (migration 0025); test-harness survey (no new code needed — `tests/fakes.py` already covers it) |
| **gap-2** consensus | Complete | `peerhub/governance/consensus.py` — propose, cast_vote, final_call_ack, mark_timeout, request_escalation, resolve, abandon |
| **gap-3** rooms/threads | Core + terminal-duty integration | `peerhub/governance/rooms.py` — create_room, create_thread, append_message, clear_room; `peerhub/dispatch/terminal_duty.py` — TerminalDutyService (thin wrapper over gap-4's DutyLeaseCoordinator) |
| **gap-4** duty-lease | Complete | `peerhub/dispatch/duty_lease.py` — create_lease, renew_lease, close_lease, expire_and_recover_lease, validate_lease_fence, AP-20 monopoly guard; migrations 0026/0027 |
| **gap-5** task lifecycle | Complete | `peerhub/governance/tasks.py` — create, claim_start, checkpoint, request_approval, approval_granted/rejected, request_failover, complete, fail, cancel |
| **gap-6** governance/lessons | Complete | `peerhub/governance/lessons.py` — propose, approve, activate, retire, supersede, quarantine, record_delivery_pending/complete |
| **gap-7** diagnostics | Read-path + row formatters | `peerhub/governance/activity.py` — list_active_{consensus_rounds,tasks,lessons}; `peerhub/telemetry/domain_rows.py` — format_consensus_row/format_task_row(_narrow)/format_duty_row |
| **gap-1** compat/cutover | Started | `peerhub consensus propose\|vote\|status` real CLI subcommands wired to `ConsensusService`, real `--json` output. Legacy-name translation (`consensus-propose` → `consensus propose`, per `LEGACY_CATALOG`) and every other domain's CLI wiring (rooms/tasks/lessons/duty-lease) not started — this is still the largest remaining gap by volume |

## What's NOT done yet (real, not hypothetical, gaps)

- **gap-1**: the actual legacy-command-translation CLI layer (LEGACY_CATALOG → native calls). Nothing implemented.
- **gap-3**: the rest of the original command list beyond room/thread/message/clear-room/terminal-duty — `init-session`/`end-session`/`send`/`mark-read`/`new-topic`/`thread-react`/`thread-promote`/`terminal-close`/`append-handoff`/`checkpoint`/`context-fill`.
- **gap-7**: the formatters exist but are NOT wired into `collect_live_snapshot()`/the live `peerhub diag` render loop yet — they're pure, tested, unconnected functions.
- **gap-2**: `SessionLeaseCoordinator`-backed coordinator-lease reuse (ratification item 16) was never actually implemented — consensus rounds don't yet claim/renew a coordinator lease during their lifecycle.
- No CLI wiring anywhere yet — every module above is a Python service class, callable from tests, not from `peerhub`'s real CLI (`peerhub/cli.py` untouched this session).
- No real cross-domain integration tests (e.g. a task's `request_approval` creating a real governance approval flow that a consensus round could gate on) — each domain's tests are self-contained.

## Verification discipline (why every commit's message says "independently verified")

**cx's own sandboxed `pytest` was blocked by a Windows temp-directory ACL restriction in nearly every one of the ~13 rounds this ran across** — it could type-check with `pyright` in its own sandbox, but could almost never actually execute the tests it wrote. This was not a rare edge case; it was the norm. The terminal ran the real test suite after every single round from an unrestricted environment, and this caught real bugs cx's own "verification" never could have:

1. `TargetState.state` freezes JSON arrays to tuples — `== []` assertions failed against the real `== ()` (recurred in 2 separate increments before cx's fresh sessions stopped making it).
2. `MutationSubmission` has no `.target_id` — real field is `.receipt.target_id` (wrong attribute name, never checked against the real dataclass).
3. Migration 0025 and 0026 were both missing `PRAGMA user_version` bumps at first — silently left the schema version one behind after "applying" the migration.
4. `duty_lease.py`'s first refactor called `unit.get_duty_lease()` *after* `unit.commit()` on the same unit of work — raised `RuntimeError: SQLite unit of work is already finished`, a real regression the refactor introduced.
5. `SqliteUnitOfWork` was missing `release_duty_lease`/`insert_duty_recovery_receipt` forwarding methods entirely — `close_lease` raised `AttributeError` on first real use, undetectable without actually running it.
6. `domain_rows.py`'s first version had 18 real pyright errors (3 `reportPrivateUsage`, 15 `Any`-typing) — cx's own "targeted pyright: 0 errors" claim was checking a stale/different scope; a real run found them all.

**None of these would have been caught by trusting cx's self-report.** This is the concrete argument for why every commit in this sequence carries independent terminal-side verification, not just a peer's word — consistent with this whole session's standing "verify, don't just trust a green report" discipline.

## Recommended next steps (not a commitment, just the natural continuation)

1. gap-1's compat CLI layer — the largest remaining real gap, and the one every legacy caller actually depends on.
2. Wire `domain_rows.py`'s formatters into `collect_live_snapshot()` and the live `peerhub diag` render loop.
3. gap-3's remaining session/handoff commands.
4. A real cross-domain integration test or two, once enough surface exists to make one meaningful (e.g. task approval gated by a real consensus round).
