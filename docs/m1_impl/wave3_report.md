# Wave 3 report (22 ids: CLM-001..003, CERT-001..003, BRG-002..007, BRG-010..019)

Production: `peerhub/extensions/bridge.py` (Bridge: session mapping, certainty ledger, fenced delivery cycle, terminal finalizer, reconcile.retry); `bridge_claims.py` extended with `renew`, `fenced(token)` (Wave-2 kernel reused, not duplicated). Extension tables: bridge_sessions, bridge_session_events, bridge_deliveries, bridge_evidence (append-only triggers), bridge_reconciliations.
Tests: tests/m1/bridge/{test_clm_claims,test_cert_certainty,test_brg_sessions,test_brg_delivery}.py; fakes tests/m1/fakes (FakeRuntimeTarget, CrashInjector; no real CLI); harness tests/m1/harness/bridge.py (+ bridge_workers.py child processes); helpers tests/m1/bridge_helpers.py (raw-SQL oracles). 54 new tests.

## RED
Tests were run against a stub Bridge (all methods NotImplementedError) and the Wave-2 ClaimStore without `renew`: 51/51 then-existing tests failed (the 3 later tests were added after mutation probes). Reasons: `NotImplementedError` on delivery_cycle/begin_attempt/current_mapping (BRG, CERT), `ClaimStore has no attribute 'renew'` (CLM-001), crash-process tests exit code 1 instead of the injected 17. Honest caveat: CLM-002 and the negative halves of CLM-001/003 exercise Wave-2 kernel behaviour that already worked; their RED reason is only the missing harness/bridge surface. Bugs found during GREEN by the tests: terminal-immutability trigger fired on the same-tx result write; duplicate `response_appended` evidence under concurrent duplicate callbacks (BRG-014).

## Coverage of STATE_MACHINE_COVERAGE.json
BridgeClaim STM-020..026 (acquire, renew, takeover before/at expiry, stale ack/finalize, terminal finalize); ExecutionCertainty STM-030..038 allowed AND forbidden: full 4x4 matrix (16 cases) vs a literal oracle, plus DB triggers; SessionGeneration STM-040..045.

## Mutation probes (production broken, test fails, restored): 26 killed
Certainty edges: STARTED->NOT_STARTED, MAY->STARTED, TERMINAL->STARTED, NOT_STARTED->TERMINAL allowed; blind replay of uncertain attempt; claim expiry `<`->`<=` (acquire) and `>=`->`>` (check); renew bumping generation; conflicting terminal treated as duplicate; response append unfenced; Offset ack unfenced; fresh generation not incremented; fingerprint ignored; session attempts bound +1; prespawn -> MAY_HAVE_STARTED; start evidence not persisted; terminal-immutable trigger removed; evidence append-only trigger removed; recovered terminal re-runs runtime; random response idempotency key; Offset CAS-mismatch handling removed; catch-up omitted; mid-delivery heartbeat removed; reconcile `consumed_attempt` not written. First round let response-append/ack unfenced survive: added CLM-003 test where the claim lapses exactly between committed steps (hook), then killed.
Surviving (equivalent): `begin_attempt` consumed-authorization guard removed (unreachable, run_cycle only inspects the newest attempt; kept as defence in depth, behaviour covered by CERT-003 double-authorize test).

## Decisions (spec)
TD-04/19/25 (fencing on append, finalize, ack; stale callbacks = evidence only), TD-11 (never downgrade), TD-26 (reconcile.retry = new attempt, old row immutable), TD-12 deferred to Wave 4. Open questions Q-W3-1..7 in OPEN_QUESTIONS.md (e2e marker deselected by addopts, reconcile body shape, extra forbidden edges, FRESH/binding, catch-up, terminal shape, control records).

## Gate
pytest tests/m1 tests/unit/m1: 188 passed; bridge+concurrency set 3x: 71 passed each; traceability --upto 3 rc=0 (wave 3 22/22); validate_package PASS.
Not done: handle_control (Wave 4), FLT-xxx fault matrix over bridge (Wave 6), real adapters (T1).
