# M1 implementation plan (R2 package, 88 requirements / 210 tests)

Source of truth: `docs/m1_spec/` (R2 FINAL package, validated by `docs/m1_spec/tools/validate_package.py`).
Read order: START_HERE.md, 06_GUIDES/TEST_SET/{README,RED_SEQUENCE,TEST_HARNESS_PORT}.md, then wave-specific specs.

## Working rules (all agents)

- Peer/agent communication in English only, terse; no restating the spec. Reference test ids (e.g. `CORE-003`) and file paths.
- TDD per wave: write RED tests for every catalog id of the wave (test function name contains the id, e.g. `test_core_003_...`; also
  `@pytest.mark.m1_id("CORE-003")`), see them fail for the right reason, then implement GREEN. Never weaken an oracle to pass.
- Test-only harness port per `TEST_HARNESS_PORT.md` lives in `tests/m1/harness/`; production names are not forced by tests.
- Core (`peerhub/m1/`) never imports extensions (`peerhub/extensions/`). Side-by-side: do not delete legacy v0 code.
- A wave is DONE only when: all its catalog ids exist and pass (live ids skipped unless run explicitly), full `pytest tests/m1 tests/unit/m1` green,
  `python tools/m1_traceability.py` reports no missing ids for waves <= current, `validate_package.py` PASS, review gate approved.
- Review gate per wave: ag.pro and/or cx.pro (READ_ONLY, terse English: VERDICT + blockers only). Run `peerhub diag --fresh` first;
  if a peer says "request was not admitted", that is usually stale health/telemetry (see T0).
- Commit per wave on `feat/m1-very-simple-renewal`; update `M1_PROGRESS_TRACKER.md`. No push/PR without user approval.

## Waves (from RED_SEQUENCE.md; ids by prefix, see test-catalog.json)

| Wave | Scope | Catalog prefixes |
|---|---|---|
| 0 | Meta / architecture / schemas / property validators | META, ARCH, SCH, PROP-007/008 |
| 1 | Core domain | CORE, IDEM, STR, OFF, remaining PROP |
| 2 | SQLite / multi-process / migration | SQL, MP, MIG, CON |
| 3 | Claim / session / execution certainty | CLM, CERT, BRG (claim + certainty subset) |
| 4 | Control / context continuity | CTL, CTX, BRG (rest) |
| 5 | Observation / Diag | OBS, DIA, READ |
| 6 | Fake-runtime E2E, fault injection | E, FLT, SEC |
| 7 | Package / compatibility / cutover | REL, IMP, remaining MIG |
| 8 | Real provider live canary (minimal quota, explicit opt-in) | LIVE |
| 9 | Scheduled soak (evidence only) | SOAK |

M1 target = waves 0-7 green and reviewed, wave 8 canary run once with minimum quota, wave 9 wired but not required to run.

## Tasks outside the catalog

- T0 (peerhub v0 quality): `peerhub ask` fails with the opaque `request was not admitted`. Surface the admission denial reason
  (health/telemetry state, gate, quota pacing) in the message and add a unit test. Seen 2026-10-03: cx.pro refused while health was UNKNOWN
  and telemetry stale; `peerhub diag --fresh` fixed it.
- T1: real runtime adapters (Claude/Codex/Agy subprocess) behind Session Bridge `RuntimeTarget` (needed by wave 8).
