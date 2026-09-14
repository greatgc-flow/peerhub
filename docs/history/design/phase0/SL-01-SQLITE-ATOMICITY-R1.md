# SL-01 SQLite Atomicity Proof R1

Status: implemented and reviewed, 2026-07-31. Extends
`SL-01-06-SESSION-LEASE-CLASSIFICATION-SPEC-R1.md` (unaffected otherwise).

## Why

`DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md` requires a genuine proof, not a
modeled claim, for atomicity assertions. SL-01 claimed "fresh create
persists one Session and one SessionLease atomically" via pure
claimed-state comparison -- a real partial-persistence bug would never
have been caught. This was flagged during the 2026-07-30 final
cross-review, deferred as a narrower-scope decision (implementing it is
a scope expansion, not a bug fix), and picked up here per explicit user
direction.

## Design (mirrors GB-01's already-ratified pattern in `broker.py`)

`SessionLeaseSubjectAdapter`'s SL-01 branch opens a real
`sqlite3.connect` against a path inside the fixture's isolated
`context.root`, creates `sessions`/`leases` tables, and runs a real
transaction: `BEGIN`, `INSERT` session, `INSERT` lease, `COMMIT`. Output
is read back from the persisted rows, not built from raw input facts.
The oracle (`SessionLeaseOracle`) is unchanged -- pure prediction only,
matching GB-01's own oracle/subject split.

A new input field `fault_point: "AFTER_SESSION_INSERT_BEFORE_LEASE_INSERT"`
plus two adapter parameters (`interrupt_after_session_insert`,
`commit_before_fault`) support a direct unit test
(`test_sl01_fault_point_rollback_and_partial_commit_probe`) rather than a
new `-NEG-02` fixture (no precedent for multiple negatives per base ID
exists in this codebase): it exercises the real rollback path (proves
zero rows persist in either table) and a simulated broken-adapter
partial-commit path (proves that if rollback discipline broke, the real
SQLite file would show 1 session row and 0 lease rows) -- the actual
atomicity proof, via a real database.

`SL-01-NEG-01`'s existing fault (lease_id relabeled to session_id) is
unchanged in what it tests; `FaultInjectedSessionLeaseAdapter` now
inherits `_sl01` from `SessionLeaseSubjectAdapter` rather than
duplicating it.

## Verification

Drafted by cx.deepthink from a fully specified brief (single dispatch,
no adversarial round needed -- this applies an already-ratified pattern
to a new module, not a genuinely ambiguous design question).
Independently verified by cc (transaction structure, class inheritance,
oracle purity preserved, forbidden-imports list correctly scoped) before
running the suite. Reviewed by ag.deepthink: clean ACK. 266/266 tests
green.
