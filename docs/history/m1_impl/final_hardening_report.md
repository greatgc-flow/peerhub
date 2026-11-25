# Final hardening: pragma-independent REPLACE guards

Finding: immutable tables were protected against INSERT OR REPLACE only under `recursive_triggers=ON` (per-connection; raw
connections such as scripts or the sqlite shell do not set it). Fix: a BEFORE INSERT trigger on each immutable table that aborts
when NEW collides with an existing row on any UNIQUE/PK constraint, so it works with the pragma OFF.

Audit (Core, spec STREAM_RECORD_OFFSET): only `records` is append-only; peers/streams/stream_members/offsets are mutable by design
(unchanged; positive control in tests). Guard `records_no_replace` is NEW migration v2 (`SUPPORTED_SCHEMA_VERSION`=2); atomic with the
existing runner, idempotent (IF NOT EXISTS), old v1 stores gain it on upgrade. Baseline v1 untouched; Core still exactly five tables.

Extensions (DROP+CREATE-on-init): bridge_evidence, bridge_session_events, bridge_deliveries, bridge_reconciliations, bridge_controls
(bridge.py), bridge_finalizations (bridge_claims.py); observations/resource_pools already guarded. Mutable by design, unguarded:
bridge_sessions, bridge_claims, delivery_claims, runtime_sessions.

Tests: `tests/m1/core/test_no_replace_guards.py` (raw connection, recursive_triggers OFF, INSERT OR REPLACE and REPLACE INTO, per-unique-key
collisions, unchanged digest, positive control, mutable-table control, v1->v2 upgrade, atomicity, idempotency). MIG runner tests updated
to the literal v2 sequence `[1, 2]`.

## Review round 2 (cx part 1)
1. Implicit-rowid replacement: `INSERT OR REPLACE ... (rowid, ...)` bypassed declared-key guards. Added `NEW.rowid IS NOT NULL AND EXISTS(rowid)` BEFORE INSERT guards: Core `records_no_rowid_replace` (migration v3, atomic/idempotent, v2 stores upgrade), bridge_deliveries/reconciliations/controls/finalizations/invocations/invocation_resolutions, resource_pools. Tables with an explicit INTEGER PRIMARY KEY (bridge_evidence/session_events seq, observations capture_seq) are guarded on that key (observations gained `observations_no_seq_replace`). Version sequence is now [1,2,3].
2. Post-construction mutation / unvalidated models: Peer/Stream/Offset use `validate_assignment=True`; `register_peer`, `create_stream`, `register_resource_pool` and observation `_insert` re-validate (`model_validate(model_dump())`) before the write transaction (append/cas/offsets already validate request data before it). Tests: tests/m1/core/test_model_revalidation.py.

## Final review round (6 reproduced defects)
1. Adapters (`extensions/adapters/base.py`): strict per-vendor completion. cc needs `result` with explicit `is_error=false` and subtype success; cx needs `turn.completed` after the last `agent_message` and no `turn.failed`/`error`; ag needs the WHOLE stdout to be one flat JSON object with a `response` string and no `is_error`/`error` marker. Anything else after spawn is `unparseable` -> uncertain (no response Record, no Offset advance). Tests: `tests/m1/adapters/test_w9_adapter_strict.py` (complete / incomplete / error_marked / garbage x cc/cx/ag); fake CLI gained `incomplete` and `error_marked` modes and emits `turn.completed`.
2. Prompt: `_prompt()` no longer truncates (the old 20,000 char slice is gone); the Bridge's injected CatchUpBudget decides. `deliver` fails BEFORE spawn with `prompt is N bytes, exceeding the <kind> inline limit of L bytes` (stdin providers 1,000,000; ag, whose prompt is an argv element, 30,000 on Windows / 120,000 elsewhere). The old test asserting truncation (`prompt_len < 25_000`) was inverted to the new contract.
3. Legacy import: peer ownership requires `metadata.legacy_import == {"source": <event_log|consumer_offsets>}` exactly (null/empty/extra/other = foreign -> conflict, no membership/Offset writes); already_imported classification recomputes the digest from the persisted kind/body/targets/reply_to/refs/metadata/created_at/author and compares to the legacy-derived digest (stored `payload_digest` alone is not trusted). `tests/m1/migration/test_w9_import_ownership.py`.
4. Extension stores: new `extensions/schema_guard.refuse_future_schema` (pure `schema_version` contract, read-only open) runs before any DDL/DML in ObservationStore, ClaimStore, Bridge and SessionBridgeStore. `tests/m1/migration/test_w9_extension_future_schema.py` (user_version 999 + foreign trigger: open fails with the actionable MIG-003 message; directory byte-digest unchanged).
5. Workflows: publish.yml now has jobs for every blocking gate (G0 `gate-g0-fast`, G1 `gate-g1-core`, G2 `gate-g2-m1`, G3 `live-validation` with `PEERHUB_M1_LIVE=1` and `-m live tests/m1/live`, G4 `build` + package tests, G7 `gate-g7-invariant`, G5 `release-evidence` running `tools.m1_release_evidence` over all gate JUnit files); publish needs all of them. The old `-m slow`/`-m e2e` selectors deselect all six M1 live tests, so `-m live` was added (also in live-protocol-gate.yml). REL-006 now derives the required gates from release-gates.json (blocking set must equal the job map), asserts closure/direct-needs/no bypass/DAG edges and runs per-gate mutated-YAML negative cases (continue-on-error, dropped need, removed job, always()).
6. Evidence tool: readiness is false when ANY blocking-gate (non-G6) catalog test is failed/error/missing/skipped (any priority; IMP-003 was P1) or ANY requirement is uncovered (previously only P0). Skips are accepted only with a machine-readable reason (`LIVE-OPT-IN[..]`, `LIVE-PROVIDER-UNAVAILABLE[..]`, `SOAK-OPT-IN[..]`, `CI-ONLY[..]`) and are reported as `not_verified` (`unverified_blocking_tests`, suite `not_verified` count), never as pass; they still keep readiness false.
