# Wave 7 report (19 ids: REL-001..014, IMP-001..004, MIG-003)

Tests: `tests/m1/package/{test_rel_build_install,test_rel_matrix,test_rel_ci_dag,test_rel_legacy_dispositions,test_rel_gate_map,test_rel_validator_evidence,test_rel_007_evidence_bundle}.py`, `tests/m1/migration/{test_imp_legacy_importer,test_mig_003_cutover}.py`. Harness: `tests/m1/harness/{pkg_env,legacy_fixture,import_workers}.py`. Builds/venvs live under pytest's temp root (clean copy of the checkout via git file list); the real legacy schema (`SqliteStateStore`) backs the importer fixtures.

## RED (right reasons)
- IMP/MIG (stub raising NotImplementedError; module absent first): all IMP-* failed at the importer call; MIG-003 future-version message lacked actionable text; Diag interpreted a future schema; CLI used generic exit 1.
- REL-009: `peerhub.m1.wire` imports `jsonschema`, not a runtime dependency. REL-003: no `peerhub-m1` entrypoint. REL-002: installed `peerhub --help` crashed (UnicodeEncodeError, cp949 pipe). REL-006: publish needed only `build`; live job `continue-on-error`. REL-005b: live/soak not deselected by default addopts. REL-010/011: no CI matrix, `requires-python >=3.11` open-ended vs classifiers 3.11 only, no OS classifiers (and local 3.14 outside declared). REL-004: version/schema checks first failed on tooling (CRLF), real finding: ORIGINAL_LINKS.md drift (Q-W7-5).
- Characterization (already true, killed by probes): REL-008 (setuptools `find` includes namespace packages, so extensions/events ship), REL-012 (real parser == CSV == frozen map == md), gate map/DAG, REL-013/014, REL-005a, REL-007 (new tool, tests written first, ImportError RED).

## Production changes
`peerhub/m1/legacy_import.py` (importer), `schema_version.py` (pure contract; migrations asserts equality), `store.py` (`transaction()`, `_insert_record` extracted from append), `m1_cli.py` (`legacy-import dry-run|apply`, exit 6 schema / 7 refused, UTF-8 streams), `peerhub/_console.py` + legacy `cli.main`, Diag future-schema refusal (+ authorizer reads `user_version`), `tools/m1_release_evidence.py`, pyproject (entrypoint, jsonschema dep, classifiers, `<3.15`, dev deps, markers, addopts excludes live/soak), ci.yml matrix, publish.yml gate.

## Local matrix (honest)
Executed: Python 3.14 / windows-latest only (cell `py3.14-windows-latest` for REL-010 and REL-011, plus non-ASCII path + CR/LF/CRLF/non-BMP smoke). 14 other declared cells are skipped with reason `CI-ONLY[python=X;os=Y]: ...` and marker `ci_only(python, os)`; they were NOT run here.

## Mutation probes (foreground, restored, git diff checked): 15 run, 15 killed (2 survivors strengthened then killed)
Importer: foreign-stream marker check off (survived first: group mismatch fell through to a different conflict reason; test now asserts the reason), digest compare off, offset off-by-one, peer squat check off, dry-run creates target dir, stale plan accepted, constant plan digest, pending WAL accepted. MIG-003: non-actionable message (survived: other words matched; now asserts "upgrade peerhub"). Packaging: extensions package-data dropped, entrypoint dropped, open-ended requires-python. Workflow: publish needs/continue-on-error/always() fixtures inside REL-006; comparator drift fixtures inside REL-012.

## Gate
`tests/m1 tests/unit/m1` minus package/concurrency/fault/e2e: 430 passed; concurrency+fault+e2e+migration 126 passed 3x; package 46 passed + 14 CI-only skips (3 runs of 188-193 s). traceability --upto 7 ok (wave 7: 19/19); validate_package PASS (tracked evidence file unchanged).
Not done: LIVE/SOAK (waves 8-9), CI matrix cells other than 3.14/Windows, real-adapter import data, cx final review. Pre-existing failure outside scope: tests/static/test_model_profiles_manifest.py (Q-W7-8).

## Gate fixes (ag.pro BLOCK on 5465969; Q-W7-11..13)
RED first: offsets added after import were skipped (3 new IMP-003 tests failed against the old importer, then green after per-component idempotency incl. os._exit crash points before/after commit and resume == clean import); console tests cover PYTHONIOENCODING cp1252/ascii/utf-8/cp949, explicit error handler, default cp949 pipe, lossless JSON. Added REL-012 dispatch check, REL-006 textual YAML mutations + gate linkage.
Probes (foreground, restored): 8 run, 7 killed, 1 equivalent (INSERT OR REPLACE for offsets: the plan already excludes existing rows). Also killed: m1 schema package-data dropped, migrations sql package-data dropped, offset update skipped, legacy_ahead mislabelled, conflict ignored, force-utf-8, explicit error handler overridden.
Gate: 433 (tests/m1+unit/m1 minus package/concurrency/fault/e2e), concurrency+fault+e2e 76 x3, package 60 passed + 14 CI-only skips; traceability --upto 7 ok; validate_package PASS. Stray reviewer repro files (src.db, test_offset.py, tgt.db) removed from the repo root.
