# Owner decisions (updated 2026-10-04 after consultation)

Status: M1 waves 0-9 implemented, 210/210 catalog ids present. NOTHING IS BLOCKING on the owner.
Consultation (cx + ag consensus, DECISIONS.md "D-OWN-*"): A1, A3, B6, B7, B8 and C accepted; A2 and A4 accepted and IMPLEMENTED per cx (D-OWN-A2/A4).

## Resolved
| Item | Resolution |
|---|---|
| A1 PROP-002 vs TD-20 | Resolved: TD-20 implemented; erratum logged in `SPEC_FEEDBACK_RECORD.md` FB-001 |
| A2 transition contract not exhaustive | Resolved and implemented: forbid downgrades and TERMINAL overwrite, NOT_STARTED->TERMINAL forbidden (FB-002) |
| A3 CTX-001 "after Offset" | Resolved: documented departure (FB-003) |
| A4 pre-start failure vs durability | Resolved and implemented: separate persisted invocation marker, no certainty downgrade anywhere (FB-005, D-OWN-A4) |
| A5 ORIGINAL_LINKS drift | Logged (FB-004); package stays frozen |
| B6 legacy importer scope | Retired 2026-10-08 (importer removed) |
| B7 real adapters | Accepted: no real resume/interrupt/terminate/steer in M1; fallback fresh generation + catch-up; `ADAPTER_CAPABILITIES.md` |
| B8 cancel semantics | Accepted (D-W4-7, D-W4-9) |
| C safe defaults | Accepted; all 8 Python/OS matrix cells are now VERIFIED-CI (green run https://github.com/greatgc-flow/peerhub/actions/runs/37189754726; evidence docs/m1_impl/matrix_evidence/ci-run-37189754726.json); a cell without run evidence stays UNVERIFIED (TD-18); windows py3.11-3.13 also VERIFIED-LOCAL (docs/m1_impl/matrix_evidence/windows-py3.11-3.13.json). The live-provider-validation job (self-hosted) is still pending/not run in hosted CI |

## Still needs the owner
Nothing blocking. Accepted by delegation, for later confirmation (no code change expected unless the owner disagrees):
1. C defaults: TTL 300 s, negative age -> UNKNOWN, wire captured_at trust, quick_check 256 MiB, exit codes 4/5/6/7, soak scale levels without capacity SLO, boundary-budget change after restart conflicts, non-contiguous projection with pins, reconcile body convention.
2. Supported matrix Python 3.11-3.14 x ubuntu/windows (narrowing means editing pyproject + ci.yml together).
3. B6/B7/B8 as accepted-by-delegation.

## Housekeeping
- cx.pro consolidated final review is still PENDING (`CX_FINAL_CHECKLIST.md`).
- `tests/static/test_model_profiles_manifest.py::test_manifest_matches_schema` fails on the base commit too (pre-existing, unrelated).
- Branch is local only (not pushed); no PR opened.
