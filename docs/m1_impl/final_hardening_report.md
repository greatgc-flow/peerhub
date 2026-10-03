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
