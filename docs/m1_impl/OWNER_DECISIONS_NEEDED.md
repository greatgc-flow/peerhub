# Items that need the spec owner (consolidated, 2026-10-04)

Status: M1 waves 0-9 implemented, 210/210 catalog ids present (live/soak/CI-only parts opt-in or skipped with machine-readable reasons).
Review state: per-wave reviews by cx.pro (waves 1-4) and ag.pro (waves 0-9); the consolidated cx.pro final review is PENDING (cx circuit was open on 2026-10-04: run `docs/m1_impl/CX_FINAL_CHECKLIST.md` prompt once cx recovers).

## A. Spec errata / gaps found (the frozen package was not edited)
1. PROP-002 contradicts TD-20/IDEM-001/002 (idempotency scope = stream+author+key). Implemented TD-20; PROP-002 asserts conflict within an unchanged scope only.
2. State-transition contract is not exhaustive (STATE_MACHINE_COVERAGE lists no control machine; certainty edges not listed). Implemented: forbid downgrades and TERMINAL overwrites (TD-11); evidence-backed forward edges allowed.
3. CTX-001 "after Offset" vs bridge fresh-generation bootstrap: implemented Offset = delivery only; fresh generations get a bounded ordered history suffix + pinned unseen redirects.
4. TD-11 vs pre-start failure: `about_to_invoke` marker sets MAY_HAVE_STARTED before invocation; adapter-reported PrespawnError is the single permitted return to NOT_STARTED.
5. ORIGINAL_LINKS.md differs from the generator output (REL-004 enforces STANDARDS_DECISION_TABLE.md only).

## B. Must be decided before release
6. Legacy importer scope (Q-W7-3): only event_log and consumer_offsets are imported; dispatch history, leases, governed targets are reported as unmapped.
7. Real adapters (Q-W8-1/2): no real session resume, no interrupt/terminate/steer for cc/cx/ag in M1 (pause/cancel only gate delivery). Is that acceptable for M1?
8. Cancel semantics (D-W4-7): cancel terminates the running delivery only; it does not skip earlier unread Records. Control precedence by Record position (D-W4-9).

## C. Safe defaults chosen (confirm or change later)
TTL default 300 s and per-kind overrides, negative age -> UNKNOWN, trust of wire captured_at, quick_check threshold 256 MiB, generation-bound sessions, exit codes 4/5/6/7, python 3.11-3.14 x ubuntu/windows matrix, jsonschema runtime dependency, soak scale levels and no capacity SLO, boundary budget change after restart conflicts (Q-W4-11), non-contiguous projection with pins (Q-W4-12), reconcile body convention.

## D. Housekeeping
- `tests/static/test_model_profiles_manifest.py::test_manifest_matches_schema` fails on the base commit too (pre-existing, unrelated).
- Branch is local only (not pushed); no PR opened.
