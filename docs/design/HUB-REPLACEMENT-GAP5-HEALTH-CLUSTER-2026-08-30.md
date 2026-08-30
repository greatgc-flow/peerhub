# Gap 5 Design: Health Cluster Verification and the Freshness/Producer Gaps (RATIFIED)

Status: Final, post-critique ratified findings from ag and cc, 2026-08-30. Covers health-update, health-check, peer-status, health-sweep. Grounded in real P:\_sys\core\hub.py legacy behavior vs. peerhub's real evidence-based health authority model (peerhub/health/service.py).

**Bottom line: No new peerhub *domain service* is needed for any of these 4 actions.** However, this investigation surfaced two critical underlying gaps that block full operational parity.

## 1. health-update: Tractable, but blocked on a prerequisite

**Legacy Behavior:** `action_health_update` (hub.py:8022-8096) accepts direct peer self-reports of health via the explicit-status path (`ctx["source"] = "self"`, line 8064). But its AUTO mode (lines 8054-8060) derives health from real telemetry (e.g., jsonl_mb).
**Peerhub Conflict:** The explicit self-report path directly violates peerhub's architectural rule of deriving health authoritatively from independent evidence (specifically `ReadinessObserved`). This path is correctly rejected.
**Ratified Finding:** The action itself is NOT "permanently untranslatable." A peer *triggering* a re-evaluation of its own health is a legitimate operational need. However, this is currently **blocked on the health-evidence-producer gap** -- the exact same prerequisite recorded in the role-assignment ratification (see docs/design/PEERHUB-BACKLOG-2026-08-27.md). Peerhub can currently only produce `ReadinessObserved` evidence inside the bootstrap sequence (`create_runtime` via `build_direct_ask_admission_config`). Until a mechanism exists for a running peer to produce real readiness telemetry outside of bootstrap, health-update cannot be backed.

## 2. health-check / peer-status: Not just a simple view layer

**Legacy Behavior:**
*   `action_peer_status` (starts hub.py:8565) is a table printer, but its `_refresh_peer_health_live` call (line 8603) is **NOT read-only**. It actively persists `entrypoint_ok` and probes for the CLI binary; on missing-CLI, it sets `status="RED"`, `gate_open=False`, `last_failure_reason="cli_not_found"`.
*   `action_health_check` (hub.py:8107-8167) implements a `--recover` flag (mutation block at 8139-8155). This calls `_peer_effective_health(..., recover=True)`, which force-recovers a stale, non-RED peer to GREEN (clearing quarantine, reopening gates, resetting failures).

**Peerhub Conflict & Ratified Finding:**
*   **The CLI-presence probe is an undesigned gap.** Peerhub's `HealthService` never probes for binary presence. A pure view layer would silently drop this probe, leaving peerhub strictly less capable of detecting missing binaries.
*   **The --recover operator un-stick mechanism is missing.** Peerhub has read-side gates that block on STALE/QUARANTINED (e.g., `role_assignment.py:181-195`, `leadership.py:420-430`), but **nothing can currently clear a stale/quarantined projection short of a real fresh dispatch producing new evidence.** Legacy operators rely on --recover to un-stick these peers. Dropping it leaves peerhub less operable than legacy on this axis. This is a real, currently-undesigned operational gap.

## 3. health-sweep: The "lazy evaluation is universal" risk

**Legacy Behavior:** `action_health_sweep` (hub.py:11465-11480) iterates over enabled peers and forces the health-check --recover path to mutate stale peers to STALE. It also conditionally injects a handoff log (handoff injection 11477, conditional on `if not was_stale`).

**Peerhub Conflict & Ratified Finding:** The premise that "peerhub evaluates staleness lazily at read time" (rendering batch mutations obsolete) is currently only true at exactly two call sites (`role_assignment.py:183`, `leadership.py:425`).
Crucially, `HealthService.freeze_admission_snapshot()` (service.py:1260) applies **no freshness check at all**. It freezes `availability_state` verbatim, meaning admission snapshots can currently trust unboundedly stale evidence -- a real latent bug risk.

**Action Item:** The "lazy evaluation is universal" claim must be MADE true. The read-time freshness rule must be centralized inside `HealthService` (rather than duplicated at consumer call sites), ensuring `freeze_admission_snapshot` and all future consumers inherit it automatically. (Note: whether `RoomsService.checkpoint()` genuinely covers legacy's PENDING_ISSUES handoff injection remains unverified by this critique).

## Summary of Concrete Actionable Gaps
1.  **Centralize the read-time freshness rule** inside HealthService to close the latent stale-admission bug risk.
2.  **Solve the health-evidence-producer gap** (already blocking role-assignment) so running peers can produce ReadinessObserved evidence, unblocking both health-update (re-eval triggers) and the operator --recover un-stick capability.
