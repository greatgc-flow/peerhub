# PeerHub Health & Quota Tracking Design

**Date:** 2026-08-16
**Status:** RATIFIED (round 1 ag.deepthink draft; round 2 cc.effort
independent critique found 2 real gaps + 3 minor fixes, all
independently re-verified against real source by the terminal; round 3
ag.deepthink revision closed every item, re-verified against source
again by the terminal before ratification). Implementation is gated on
Section 2.0's empirical canary passing first -- this is a design
ratification, not an implementation go-ahead.

## 1. Overview
This document outlines the design for PeerHub's native health, quota, and context telemetry tracking, satisfying the requirement to match `hub.py`'s `diag.py` capabilities (EXH, quota headroom, context utilization) for a PeerHub-orchestrated environment.

## 2. Data Sources & Collection Strategy

PeerHub adapters invoke the real underlying CLIs. To maintain an accurate picture of out-of-band usage (e.g. usage from other terminals not orchestrated by this PeerHub instance) without guessing, PeerHub will continue to rely on the CLI-native sources of truth via periodic polling rather than trying to perfectly intercept and accumulate tokens in the adapters.

### 2.0 Empirical Canary Precondition -- PASSED 2026-08-17
Before implementation begins, a concrete empirical canary (a real, once-run probe) MUST pass to prove that:
*   `claude.cmd /usage` correctly reads the credential/session store when invoked with PeerHub's specific environment (`CLAUDE_CONFIG_DIR`) and working directory (`cwd`). **PASS, with a required implementation detail**: under any non-TTY subprocess context (which peerhub always is -- it never runs attached to an interactive terminal the way P:'s hub.py does), `claude.exe` waits ~3s for piped stdin before proceeding ("Warning: no stdin data received in 3s, proceeding without it"). Combined with the real API call's own ~9.4s latency, this reliably exceeds a 12s deadline. Root-caused empirically (4 real probes: full-env copy, minimal-env, direct import from `P:\peerhub`, direct import from `P:\_sys\core` -- all failed identically; explicitly passing `stdin=subprocess.DEVNULL` succeeded in 9.39s). **Peerhub's real `TelemetryWorker` implementation MUST pass `stdin=subprocess.DEVNULL` explicitly on every `claude.cmd /usage` invocation, and should budget a deadline of at least 15s (not 12s) given ~9.4s of that is real network latency with little margin.**
*   `codex app-server` populates correctly under the same constraints. **PASS** -- real rate-limit JSON returned from a peerhub-context subprocess (JSONRPC handshake, background reader thread, deadline handling all worked as-is).
*   Antigravity's log path actually populates correctly when invoked via PeerHub's `agy_adapter.py` dispatch path. **PASS** -- a real dispatch through `RealAgyAdapter` was run from a peerhub context; `ag_statusline_stdin.log`'s mtime and content were directly confirmed to change (before/after diff), containing genuine quota-fraction data.

All 3 items empirically confirmed. Implementation may now proceed, with the `stdin=DEVNULL` + 15s-deadline requirement above carried into the Claude polling implementation.

### 2.1 Claude (`cc`)
*   **Quota/Headroom:** Sourced by executing `claude.cmd /usage` via a background telemetry worker, parsing the stdout for current session and weekly used percentages and reset times. To ensure it reads the correct credential/session store, the invocation MUST explicitly replicate `_sys/core/snapshot.py`'s binding of `env["CLAUDE_CONFIG_DIR"]` and `cwd=str(PORTABLE_ROOT)`. Furthermore, the telemetry worker must resolve the real binary instead of any heavy CLI wrapper that shadows it on PATH. **Canary-confirmed 2026-08-17: the subprocess call MUST also pass `stdin=subprocess.DEVNULL` explicitly** -- peerhub always runs non-TTY, and without this, `claude.exe` adds a ~3s stdin-wait on top of the real ~9.4s API latency, reliably blowing a 12s deadline. Use a 15s+ deadline for this specific probe, not the general-purpose 12s default.
*   **Context:** Sourced from `claude.cmd` session telemetry or computed directly in `claude_adapter.py` upon `AttemptTerminalObserved` via `UsageObserved` events.

### 2.2 Codex (`cx`)
*   **Quota/Headroom:** Sourced by opening a persistent `codex.cmd app-server` JSONRPC connection and calling `account/rateLimits/read`. This subprocess MUST have a background reader thread, a queue-based deadline, and an explicit Windows `taskkill /F /T` process-tree kill in a `finally` block to prevent orphaned process leaks (reproducing `_sys/core/snapshot.py`'s exact `_codex_rate_limits()` safety handling).
*   **Context:** Reading out-of-band context from `_sys/codex/config/state_5.sqlite` is explicitly deferred to a future increment (see Section 6) due to schema mismatch.

### 2.3 Antigravity (`ag`)
*   **Quota/Headroom & Context:** Sourced natively from `SYS_DIR/data/temp/ag_statusline_stdin.log` (the real file `gather_peer()` uses), which is populated by `ag`'s statusline hook. We must determine and state explicitly whether PeerHub's `agy_adapter.py` dispatch path actually triggers this same statusline hook/log write. If this data source is unavailable to PeerHub-orchestrated `ag` dispatches, we must explicitly route to `absent` per DIR-004 rather than assuming reuse.

### 2.4 When and Where to Record
A background `TelemetryWorker` (or a scheduled saga) will poll these CLI-native sources based on the `telemetry-config.json` TTLs (e.g. 60 seconds). It will emit `UsageObserved` and `ReadinessObserved` events to the dispatcher.

**Open architectural gap, found 2026-08-17 while implementing this
increment (all others, Sections 2.1-2.3 and 4, are done -- see
`docs/design/BACKLOG-CONSOLIDATED-2026-08-16.md`)**: this section never
specifies WHAT PROCESS actually hosts the "background" worker.
`peerhub` today has no persistent daemon/server concept at all --
`peerhub ask` and `peerhub status` are each a fresh, short-lived CLI
invocation with no long-running process for a periodic 60s-cadence
poller to live inside. Concrete options, none chosen yet (needs a
design decision, not a unilateral implementation choice, since it has
real operational implications):
1. **Poll-on-demand instead of scheduled**: `peerhub status --peer/--all`
   itself triggers a fresh poll (with its own TTL-based caching check
   against the existing projection's `updated_at`) rather than reading
   possibly-stale data written by a worker that was never running. Zero
   new process-lifetime concerns, but loses the "always fresh, ambient"
   telemetry the accord's/`diag.py`'s own always-on model provides.
2. **A genuinely new `peerhub daemon`/`peerhub serve` subcommand** that
   runs a persistent process (the worker loop lives here). Real new
   surface area: process supervision, start/stop lifecycle, likely a
   PID file or similar, cross-platform service-manager integration
   questions on top of everything already Windows-specific in this
   design.
3. **Piggyback on whatever process is already long-running** (e.g. if a
   future `dispatch_with_retries()`-driven session stays alive across
   multiple commands, or if peerhub gains some other persistent
   component) -- speculative, no such component exists today.
Recommend option 1 as the default unless there's a concrete reason
telemetry needs to be ambient rather than poll-on-demand -- it requires
no new process model and the existing `record_usage_observations()`
(already committed) plus each poll function's own `freshness_ttl`
parameter already provide exactly the staleness-aware building blocks
option 1 needs. Not implemented; flagged for the user's decision before
proceeding.
For per-dispatch context occupancy, the adapters (`claude_adapter.py`, `codex_adapter.py`, `agy_adapter.py`) will emit `SessionContextObserved` at the completion of a dispatch (`AttemptTerminalObserved`).

## 3. Data Model & Durability

PeerHub will durably project these observations so that CLI queries are fast and do not block on heavy subprocess polls.

*   **Existing Schema:** `session_context_observations` and `session_context_projections` already exist (landed in `0010_session_context_telemetry`).
*   **New Schema:** We will introduce a new migration, **`0024_telemetry_quota_tracking`** (updated 2026-08-16: `0023` was claimed first by the EvidenceArtifact increment, per this design's own canary gate leaving it unimplemented while EvidenceArtifact had no such gate -- re-verify the actual next-free number against `docs/migrations.md`/the migrations directory before implementation, don't trust this number in isolation), adding:
    *   `usage_observations` and `usage_projections` (mapping to `UsageMeasurement` in `telemetry/contract.py`). The projection's primary key MUST include `quota_pool_scope` because real Claude `/usage` outputs multiple concurrent pools (e.g., session/5h, week-all-models/168h, week-fable/168h); a single-valued PK would silently clobber rows.
    *   `readiness_observations` and `readiness_projections` (mapping to `ReadinessMeasurement`).

The telemetry projection builder will listen to the event log and update `usage_projections` / `readiness_projections` transactionally. The `peerhub status` command will exclusively read from these projections.

## 4. CLI Surface

We will expand the existing `peerhub status` command rather than creating a new subcommand. 

*   `peerhub status` will print the high-level summary (active leases, migration count, and overall readiness state).
*   `peerhub status --peer <peer_name>` (or `--all`) will output a table formatted identically to `diag.py`'s layout (e.g. `PEER`, `STATE`, `CONTEXT(used/win %)`, `TOTAL COST`, `SRC`, and the `POOL` dependency groups with EXH indexes).
*   The data will be fetched from `RuntimeContext.telemetry_service.get(...)`, which queries the projections.

## 5. Failure & Absence Handling

Following DIR-004 ("Measured-Only Claims — No Guessing, No Estimation"), PeerHub's telemetry service must fail closed on missing or stale data:
*   If a background poll times out or fails (e.g. `codex app-server` hangs), the resulting projection update must emit a `source_tag="absent"` or `"error"`.
*   If the data in the projection exceeds the freshness TTL (e.g. older than 60 seconds), it must not be shown as live; the CLI will degrade to `absent` or explicitly label it as `[stale]`. This staleness check (`now - updated_at > TTL`) MUST be enforced at READ time, not just tagged at write time by the poller, ensuring it fails closed even if the telemetry worker itself stops running completely.
*   We will never fabricate a metric based on "last known good" minus "estimated usage".
*   This aligns with the capability-lease enforcement discipline (`enforcement_ceiling=None` when unverified).

## 6. Scope Boundary (What is NOT covered)

*   **In-band Quota Accumulation:** We deliberately do not intercept and sum tokens inside the adapters to track global quota. We rely on polling the CLI-native stat files because it accurately captures out-of-band usage.
*   **Live Cross-Process Pool-Level Aggregation:** Creating a real-time, cross-machine token counting service is out of scope for this increment.
*   **Formal Multi-Agent Consensus for Quota:** Distributing quota usage mathematically between peers via broadcast/consensus (Primitive B) is not covered here.
*   **Codex Background-Polled Context Data:** Tracking out-of-band context occupancy for Codex (`model_context_window` / `total_tokens` from `state_5.sqlite`) is explicitly DROPPED from this increment's scope. The existing `session_context_observations` schema requires a live `SessionBindingKey` (which out-of-band dispatches lack), and `UsageMeasurement` / `ReadinessMeasurement` do not fit context occupancy semantics (e.g., no reset window). Implementing this requires a completely new table/dataclass shape, which is deferred to a future increment.
