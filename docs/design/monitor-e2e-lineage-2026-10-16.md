# Monitor E2E Lineage and Updateability

**Status:** Reconciled design (Round 8 consensus); implementation awaits unanimous digest approval.  
**Review base:** `c717ee0097c7a1d89fa8d72616207d0dd0db7588` on `fix/monitor-detail-expansion`.  
**Scope:** Telemetry capture, SQLite persistence, read-model evaluation, JSON machine contract, and presentation boundaries.

## Core Lineage Decisions

1. **Quota and Credit Telemetry:**
   - Quota probes return `freshness_ttl` (default 60s) and collector contract identifiers (`provider_id`, `provider_version`). Telemetry capture preserves these in observation JSON metadata without relational schema migrations.
   - Historical rows without stored TTL retain the generic 300-second fallback labeled with `legacy_default` basis.
   - `monitor_read_model.py` expands its capacity query to include `reset_credit`: `WHERE r.kind IN ('quota', 'rate_limit', 'reset_credit')`. Reset credit observations are mapped to dedicated credit rows with count and expiry semantics; they are never ranked for urgency.
2. **Freshness Precedence and Reflow Expiry:**
   - *State Preservation:* Temporal freshness evaluation (TTL, elapsed age, clock skew) applies ONLY to stored `MEASURED` observations. Stored `ERROR`, `UNAVAILABLE`, and `ABSENT` states are preserved as recorded; they are never re-evaluated or upgraded to `MEASURED`.
   - *Clock Skew and Effective Clock:* Freshness clock and elapsed age are measured from `captured_at` when present, else `observed_at` (`peerhub/extensions/observation_model.py:160`). Source-clock skew is `skew_seconds = observed_at - captured_at` (`observation_model.py:136`). For `MEASURED` rows, negative age relative to the effective clock or unparseable timestamps evaluate to `UNKNOWN` ahead of TTL staleness.
   - *TTL Staleness:* For valid ages (`age >= 0`), if effective TTL is 0 or invalid, the observation evaluates to `STALE`. Otherwise, when `age + elapsed >= effective_ttl`, it evaluates to `STALE`; otherwise `MEASURED`.
   - *Reset Validity:* `resets_at` must be valid ISO-8601 UTC. If missing or invalid, or if `resets_at <= read_at`, last-known values are flagged with `expired: true`.
   - *Reset-Crossing Reflow:* In countdown/resize reflow, when pure temporal projection reaches or crosses reset (`read_at + elapsed >= resets_at`), any previously valid pre-reset last-known value transitions to `expired: true` and is immediately ineligible for display or headlining.
   - The renderer never parses raw timestamps; it consumes pre-calculated timestamps, ages, and eligibility flags.
3. **Historical Last-Known and JSON Compatibility:**
   - For stale observations, legacy `measurements: null` is explicitly preserved in JSON for backward compatibility.
   - A separate `last_known_measurements` object carries validated historical fields with precise definitions and types:
     - `observed_at`: string ISO-8601 UTC timestamp of upstream provider observation generation (when telemetry was produced upstream).
     - `captured_at`: string ISO-8601 UTC timestamp of actual client collection execution (when PeerHub ran the collector probe and captured the receipt; distinct from upstream generation time and database insertion time).
     - `window`: string window identifier (e.g. `'day'`, `'week'`, `'hour'`, `'rolling'`).
     - `window_seconds`: integer duration of the quota window in seconds (e.g. 86400, 604800).
     - `resets_at`: string ISO-8601 UTC timestamp of quota reset.
     - `remaining`: float fractional remaining in range `0.0..1.0` inclusive.
     - `expired`: boolean flag (`expired: false` when `resets_at > read_at`; `expired: true` when `resets_at <= read_at` or reset is invalid).
   - The renderer never ranks, headlines, or presents expired values (`expired: true`) as actionable.
4. **Urgency Ranking:**
   - Urgency headline ranks only fresh, reset-valid, fractional `MEASURED` pools.
   - Ties are broken deterministically by peer ID, then pool ID (no catalog dependency).
   - The headline ranking applies strictly to the loaded sample (not the full database). The lineage contract requires the `(loaded sample)` qualification at width >= 80, and explicitly permits the renderer to omit it only below width 80 because line 1 already displays loaded sample bounds (`N loaded`).
5. **Deferred Scopes:**
   - Quota catalog extraction and model/profile refresh workflows are deferred and removed from this PR series. Existing TOML files (`model-defaults.toml`, global, workspace) remain the authoritative runtime profile configuration.

## Coverage Dimensions and Precedence

Coverage encompasses seven distinct dimensions; source sample limits must never be conflated with delivery completeness or renderer viewport bounds.

| Coverage Dimension | Scope and Count | Source Cap / Signal | Owner |
|---|---|---|---|
| **Peer catalog sample** | Registered peers | 3 peers (sentinel 4) | `overview` |
| **Session-history sample** | Retained session records | 100 rows (sentinel 101) | Shared by `sessions`, `usage`, `context`, `runtime` |
| **Peer retained-count** | Per-peer retained sessions | 20 scopes/peer (3 peers) | `sessions` (`peer_sample_truncated` for 4th peer) |
| **Delivery-scope sample** | Streams/scopes with delivery receipts | 20 scopes/peer | `execution` |
| **Usage-scope telemetry** | Telemetry coverage across scopes | Measured/absent/unsupported | `coverage` |
| **Capacity-pool sample** | Stored capacity observations | 100 pools (sentinel 101) | `capacity` |
| **Renderer visibility** | Rows visible in viewport | Viewport-height dependent | Presentation only (not in JSON) |

### Coverage DTO and Precedence
- **Canonical DTO:** Every group exposes a structured coverage object: `dimension`, `read` (`complete`, `partial`, `unknown`, `unavailable`), `sample_size`, nullable `truncated`, and `reason`.
- **JSON Field-Domain Changes:** `read` supports `unknown`. `truncated` is nullable (null when coverage is unknown). Viewport visibility remains presentation-only and is excluded from JSON coverage.
- **Shared Session Coverage:** Derived groups (`usage`, `context`, `runtime`) always carry the shared `session_sample` DTO (`dimension=session_sample`) regardless of whether `sessions` is selected. The legacy rule ("sessions absent => UNKNOWN") is removed.
- **Precedence Rule:** Within each dimension, evaluate:  
  `ERROR / unavailable` > `partial` (query cap reached) > `complete`; `unknown` when evidence is missing.
- **Isolation:** Query failures or truncation in one group must not poison unrelated groups. Source completeness is reported locally in group summaries; `coverage` owns delivery/telemetry completeness only.

## Slicing and Delivery Sequence

Implementation proceeds via three staged PRs, each delivering RED tests before GREEN implementation:

1. **PR 1: Lineage & Read Model Correctness**
   - *Scope:* Persist probe TTL and collector metadata; evaluate `last_known_measurements` with `expired: true` on reset expiry or invalid reset; query and map `reset_credit`; implement pure temporal projection for reflow.
   - *Assignee:* CX (`cx.effort`).
2. **PR 2: Canonical Coverage & JSON Contract**
   - *Scope:* Emit canonical per-dimension coverage DTOs; share `session_sample` across derived groups; update JSON domain for `read=unknown` and nullable `truncated`.
   - *Assignee:* AG (`ag.effort`).
3. **PR 3: Presentation Layout & CLI Wiring**
   - *Scope:* Two-line capacity summary; round-robin fairness detail allocation in catalog order (`sessions`, `usage`, `context`, `capacity`, `runtime`); min-width 32 cutoff; session handle in inspection mode; wiring to `effective_preferences()`.
   - *Assignee:* CX writes, AG cross-reviews.
4. **Final Contract Review:**
   - *Assignee:* CC (`cc.effort`) performs final contract and pace review. Live evidence archive is deferred to a subsequent independent PR series.
