# Wave 5 report (30 ids: OBS-001..018, DIA-001..010, READ-001/002)

Production: `peerhub/extensions/observation_model.py` (models, wire parsing, honesty, read-time freshness; pure), `observation.py` (append-only store, pool registry, capture), `diag.py` (ReadonlyDiag, own module). Removed `observation_and_diag.py`; CLI `diag health` and unit tests updated. Tests: `tests/m1/observation/{test_obs_capture,test_obs_freshness,test_obs_pools,test_obs_e2e}.py`, `tests/m1/diag/test_dia_diag.py`, `tests/m1/concurrency/test_dia_snapshot.py`, `tests/m1/core/test_read_boundaries.py`, ARCH-004 rewritten (module level). Harness: `tests/m1/harness/observation.py` (capture/latest/list_for_resource_pool/render_diag/persist_wire), `tests/m1/fakes/observation.py` (FakeObservationSource, SequentialIdSource). Fake runtimes only.

## RED
Stubs first (every method NotImplementedError; data models real). 47 failed / 8 passed in the new files + ARCH. Reasons: NotImplementedError (44), FileNotFoundError (extension schema copy missing, drift test), ARCH-004 assertion (old `observation_and_diag.py` still present / class-level check). The 8 passes: READ-001/002 (4, already implemented in Wave 1: characterization, see Q-W5-12) and 4 unrelated architecture tests.

## GREEN notes / bugs found by tests
- Test-side bugs fixed after GREEN: offset CAS argument order (revision, position); writer harness id collision (production correctly rejected a duplicate `obs-1` as immutable evidence); Diag report age depends on read_at (tests pass a fixed read_at).
- First mutation pass showed the capture-ordinal tie-break was not distinguishable because the lookup index already returns ties newest-first; OBS-018 now swaps the index for an unrelated one (scan + sort) so the plan cannot hide a missing tie-break.
- Diag latest-per-key ordering was untested (inverted ordering survived); added a Diag-vs-store ordering test.

## Mutation probes (foreground, restored, git diff checked): 27 run, 26 killed, 1 equivalent survivor
Killed: latest by observed_at instead of capture time; TTL `>=` -> `>`; negative age treated fresh; capture-ordinal tie-break removed (after the fix above); no BEFORE INSERT replace trigger; legacy triggers kept on reopen (weak kill: trigger-exists error) and drop-only-known-names (strong kill); honesty check off; semantic check off; pool validated after the probe; BaseException swallowed; Diag without single transaction (DIA-008 and DIA-006); no query_only read-back; observations section not isolated; log errors not isolated; non-read-only URI; `import subprocess` in Diag (ARCH-004); section errors not isolated; wire captured_at unchecked; pool conflict ignored; latest ignores pool filter; list ignores subject filter; Diag newest-selection inverted (first survived, killed after adding the ordering test).
Survivor: `query_only` removed from store read connections (equivalent: no read path writes; defence in depth).

## Decisions (spec)
TD-13 (freshness clock, age >= TTL stale), TD-23 (ordinal tie-break), TD-07 (section isolation), TD-15 (fail closed, no repair), OBS-008 immutable evidence (INSERT OR REPLACE blocked even with recursive_triggers OFF; triggers dropped/recreated on reopen). D-W5-1..4 in DECISIONS.md. D-W0-2 (wire persistence assertions for Observation/Resource Pool) and D-W0-4 (module split, module-level ARCH-004) closed.

## Open questions
Q-W5-1..13 in OPEN_QUESTIONS.md; OWNER items: TTL numbers (Q-W5-2), negative-age policy (Q-W5-3), wire captured_at trust (Q-W5-4), honesty key list (Q-W5-6), source-declared semantics (Q-W5-7), subject binding (Q-W5-8).

## Gate
Per directory green: architecture 5, meta 4, schema 16, property 13, migration 12, core 35, integration 22, observation 31, diag 12, bridge 78, control 134, unit/m1 12; concurrency 21 (3x green, includes DIA-008 x4); the wave-5 new tests also 3x green. traceability --upto 5 ok (wave 5: 30/30); validate_package PASS.
Not done: Diag views of bridge/control tables, real adapters (T1), wheel check of extension schema package-data (Wave 7).
