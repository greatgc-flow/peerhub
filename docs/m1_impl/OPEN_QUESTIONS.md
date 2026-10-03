# M1 open questions (catalog ambiguities; oracles never weakened)

- Q-W0-1 (META-001..004): spec package was already validated, so these were GREEN on first run (no RED phase). They guard future catalog edits only.
- Q-W0-2 (SCH-011/012/013): "API persistence through a transaction fixture" is only defined for Peer/Stream in the harness (`persist_wire`);
  Record/Offset mutants are checked at `parse_wire` (no wire-level persistence port yet; Record append goes through `append_record`). Revisit in Wave 1.
- Q-W0-3 (SCH-007/008): Observation / Resource Pool have no Core model (extension-owned); validated schema-only.
- Q-W0-4 (ARCH-001): "Core package entrypoints" interpreted as every module under `peerhub/m1/`; the CLI composition root (imports Diag) was moved to `peerhub/m1_cli.py`.
- Q-W0-5 (ARCH-004): ReadonlyDiag shares a module with ObservationStore (writer); the rule is applied to the `ReadonlyDiag` class (ctor deps, names, SQL verbs, `mode=ro`) plus module imports. Splitting the module is advisable in Wave 5.
- Q-W0-6 (SCH-005/PROP-008): digest covers `kind + body` only (metadata/targets excluded); TD-20/TD-idempotency scope to be confirmed in Wave 1 (IDEM).

## Wave 1
- Q-W0-2 / Q-W0-6: RESOLVED in Wave 1 (record/offset persistence port via persist_wire; digest scope = TD-02, see wave1_report.md).
- Q-W1-1 (PROP-002 vs IDEM-001/002): catalog says author/stream_id change under same key must conflict; TD-20 scopes keys by (stream, author). Implemented TD-20; PROP-002 asserts conflict for 7 fields and independence for author/stream. Needs spec owner confirmation.
- Q-W1-2: CORE-009/TD-02 do not say what created_at is when the client omits it; chose "absent projects as null, server fills stored value".
- Q-W1-3: mutation of members on a CLOSED Stream is allowed (TD-09 only forbids append and reopen). Confirm.
- Q-W1-4: Peer upsert (create_peer on existing id) updates display_name/adapter_ref/metadata but never created_at; no conflict semantics specified.
- Q-W1-5: no record body size limit configured (PROP-009); an explicit limit would need a decision.

## Wave 2
- Q-W2-1 (CON-006/007/008, MP-004, SQL-008/010): these Bridge-flavoured ids are mapped to wave 2 (waves.json) but Bridge claims are Wave 3 (CLM). Built a minimal extension-owned fenced claim kernel (`peerhub/extensions/bridge_claims.py`: table `bridge_claims`, token = workspace gen + claim gen + owner, TD-19 `now >= expires_at`, TD-25 `guard` inside Core write tx). Wave 3 extends it; the legacy wall-clock `session_bridge.py` claim code is untouched and not used.
- Q-W2-2 (SQL-002 "migration metadata"): ARCH-002 requires the Core table set to be exactly 5 tables, so schema version is stored in `PRAGMA user_version`, not a metadata table. Confirm.
- Q-W2-3 (MIG-001/002 "N -> N+1"): production has only baseline v1 (version 0 = empty or unversioned Wave-1 schema). MIG-002 tests v0->v1 on a raw-SQL legacy fixture and a test-supplied v1->v2 step (table/index change); MIG-001 uses the test-supplied v2 with an in-process raise and a real killed child process (os._exit at `migration.before_commit`). No invented production N+1 migration.
- Q-W2-4 (CON-002 "bounded busy failure explicit/retryable"): store busy timeout is 30 s, so zero busy failures occur in practice; the test tolerates only `sqlite3.OperationalError` "locked/busy" and asserts every success is durable and unique, integrity_check ok, foreign_key_check empty.
- Q-W2-5 (CON-010): oracle "same serialization order" tested with deterministic hook orderings (advancer-first -> `OffsetBeyondHeadError`, nothing moved, retry succeeds; writer-first -> CAS accepted at head) plus 20 free-running real-thread rounds.
- Q-W2-6 (heartbeat on an expired but not-yet-taken-over claim): treated as stale (an expired claim cannot be renewed, FLT-007 fences finalize after expiry). CLM-001..003 in Wave 3 should confirm the exact-boundary heartbeat/takeover race.
- Test seams added to CoreStore (not behaviour changes): `fault_hook(point)` (append.before_commit, offset.before_head_check), `guard(conn)` kwarg on append_record / advance_offset_cas, `connect()`, `read_uow()`; TD-14 `SchemaVersionError` for future versions.
