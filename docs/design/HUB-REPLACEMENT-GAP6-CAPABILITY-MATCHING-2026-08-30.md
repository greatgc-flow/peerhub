# Gap 6 Design: Capability Matching and Leader Election (NOT READY FOR IMPLEMENTATION)

Status: BLOCKED / NOT YET READY. Research by `ag.deepthink`, independent critique by `cx.deepthink` (fresh session), 2026-08-30. Covers `elect-leader` and capability scoring.

**Overall Verdict: Do not implement the initial proposal unchanged. It contains real gaps that must be resolved first.**

## 1. Original Research Summary (Core Algorithm)
The core legacy `_matching_peers` logic (`hub.py:3418-3438`) works by evaluating exact and substring capability matches, penalizing missing needs, and weighting health, cost, and historical use.
- Empty needs default to a score of 1.
- Exact capability matches score maximum points; substring matches score slightly less.
- Node IDs and aliases do not support substring matching.
- Unmatched peers are removed from the candidate pool.

## 2. Critique Corrections & Material Gaps

### Scoring Factors and Deny-Lists
- **Configuration-Driven Maxima:** Scoring constants (e.g., capability=10, continuity=2, console=1, cold-start=1, GREEN=3/YELLOW=1/STALE=-5, cost low=0/mid=1/high=2) are NOT hardcoded; they are driven by `protocol.json` (lines 27-42).
- **Health Exclusions:** Legacy *only* excludes `RED` peers. `STALE` candidates stay eligible but take a -5 penalty (hub.py:3408-3411). The proposed strict 5-state deny-list (UNAVAILABLE/STALE/QUARANTINED/COOLDOWN/RECOVERY_REQUIRED) is overly restrictive for `STALE`. Missing projections (`None`) must fail OPEN, matching legacy's behavior of treating `UNKNOWN` as eligible.
- **Quota Exclusions:** Quota margins range from +3 to -3, and zero-remaining-margin acts as a **hard-exclude**, not just a score penalty (hub.py:3452-3472).
- **Recent-Use Penalty:** The history penalty always evaluates exactly the last 2 history entries (hub.py:3404-3407, 3473-3477). This is structurally distinct from the configurable AP-20 coordinator monopoly threshold.

### Capability Sources
The proposal's "needs" sources were imprecisely mapped:
1. `profile.capabilities` comes from `health.json` via `_peer_effective_health` (hub.py:3408-3416), not the orchestration declaration.
2. `workload.capability_registry` comes from `_sys/ai/protocol.json` (lines 184-218).
3. `roles_registry` comes from `orchestration.json` (lines 501-522).
**Peerhub currently possesses NO implemented functional catalog.** `adapters.contract.Capability` is transport-only (SESSION, STREAM, GRACEFUL_CANCEL) and cannot be overloaded. `PeerRegistryService` lacks fields for aliases, enabled states, or functional capabilities.

### Service Placement
A new application-level `CapabilityMatchingService` is correct but must be structured as three distinct layers to preserve architectural boundaries:
1. An authoritative functional-capability/config owner.
2. A **pure** matching/ranking reducer living in the routing domain.
3. An application coordinator that reads registry, health, leadership, and quota states and invokes the pure reducer. (This preserves `RoutingService`'s strict "pre-supplied facts, no sibling calls" rule).
*Note:* Reading `LeadershipService.get_current_leader()` requires decoding nested dicts (`state["leader"]["peer_node_id"]`); a typed snapshot read protocol is required.

### Dropped-Factor Handling
The proposal silently dropped cost, quota margin, console fit, cold start, and health (converting graded health to a binary gate).
- Hardcoding dropped factors to 0 violates peerhub's evidence rules (`peerhub/core/evidence.py:36-43`), which forbid converting absent evidence into a synthetic zero. Unsupported factors must either be omitted entirely from a labeled "native-v1" formula or carry explicit `ABSENT` component states.
- **Quota is NOT absent:** Peerhub already persists quota projections with used/remaining fractions (`telemetry/contract.py:79-87`). A native quota factor must be built, not deferred.
- **Tie-breaking:** Dropping cost removes legacy's final lower-cost tie-break (hub.py:3488). A deterministic tie-breaking key must be ratified.

### Fixture/Fallback Staleness
The `fix-elect-leader-rec-01` ledger fixture (`PHASE1-PARITY-LEDGER-BATCH3-2026-08-20.md:79-96`) proves empty matches fall back to `default_proposer`. However, its `"cc"` fallback is stale; the live `orchestration.json` sets `default_proposer` to `"rotating"`. Because `LeadershipService.claim_leadership()` resolves via `PeerRegistryService.get_node()`, a literal `"rotating"` fallback will raise a `RecordNotFoundError` unless special resolution semantics are designed. Furthermore, the legacy routing metric/selection audit trail required by the ledger was omitted.

### Schema, Conventions, and Data Sources
- `score_peers()` is purely read-only and requires a `READ_ONLY` command descriptor, not a mutation receipt.
- `elect-leader` is mutating and should delegate directly to `LeadershipService.claim_leadership()` (using its existing bounded CAS loop), rather than inventing a secondary mutation loop.
- **Effort is a dead parameter:** Peerhub's production adapters only expose `*.standard` profiles. `supports_reasoning_effort` is a boolean flag, and tier fields are display-only. A real effort-quality-floor data source must be designed before effort parity is possible.
- **Richer Result Type:** `CapabilityScore(node_id, capabilities, score)` is too thin for `discover`, which requires status, cost tier, model tier, ordered capabilities, ranking score, and evidence provenance to explain an election.
- String edge cases (case-insensitive exact match, symmetric substring matching, "medium" effort alias, unknown-effort-silently-mid, empty-needs-scores-1) must be deliberately preserved or explicitly rejected.

## 3. Concrete Blocking Checklist (cx's Minimum Hardening Requirements)
Implementation CANNOT begin until the following are designed and ratified:
1. Ratify an authoritative functional capability/config owner.
2. Design a real effort-quality data source.
3. Establish explicit missing-evidence semantics (do not invent synthetic zeros).
4. Ratify a deterministic ranking tie-break key.
5. Define a richer score/evidence result type capable of backing `discover`.
6. Validate fallback semantics for `default_proposer` (`"rotating"`) resolution.
7. Design an election audit/receipt shape that matches the ledger fixture's routing metric requirements.
