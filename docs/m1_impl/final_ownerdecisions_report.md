# Final owner-decisions report (D-OWN-A2 / D-OWN-A4 + docs)

## Code
- `peerhub/extensions/bridge.py`: certainty-based about_to_invoke (MAY before invoke, revert on PrespawnError/control_halt) replaced by a separate marker.
  New append-only tables `bridge_invocations` (marker per invocation) and `bridge_invocation_resolutions` (reason), triggers: no update/delete/replace, resolution needs a marker.
  Created with CREATE IF NOT EXISTS on init (old stores gain the tables; trigger set re-installed each open; idempotent).
  `_mark_invocation` (fenced, certainty must be NOT_STARTED, no open marker), `_resolve_invocation` (fenced + scope, only while certainty NOT_STARTED and an open marker exists; reasons prespawn_failure / control_halt),
  `_promote_unresolved` (recovery at the start of every cycle: unresolved marker -> durable MAY_HAVE_STARTED + evidence + resolution `promoted_may_have_started`; read-only precheck so stale read-only cycles still write nothing).
  `_revert_prespawn` removed; the only certainty UPDATE is `_transition_in`, guarded by `_ALLOWED` (no downgrade). New fault point `bridge.after_invocation_resolved`.
- D-W3-8 marked superseded in DECISIONS.md. Adapters/Bridge audited: no path claims process termination on cancel (unsupported -> `runtime_outcome=unsupported`); test added, no code fix needed.

## Tests added/changed
- New `tests/m1/bridge/test_own_invocation_marker.py` (16 cases): os._exit crashes after marker/before invoke, after invoke before evidence (with positive controls), after resolution before ack (halt_crash_worker); pre-spawn failure + retry with Offset unchanged and new marker; pause/cancel before invoke; stale and foreign-scope callbacks; duplicate/illegal resolution; resolution refused after certainty advanced; append-only/no-replace guards (7 statements); old-store upgrade (marker tables dropped) idempotent.
- `test_cert_certainty.py`: independent FORBIDDEN literal (7 edges) beside LEGAL (9), asserting they partition the 16 pairs.
- Updated to new semantics: test_brg_delivery (marker evidence NOT_STARTED), test_w3_gate_fixes, test_flt_bridge (FLT-004/006: NOT_STARTED pending, MAY after recovery).
- Adapters: `test_t1_unsupported_cancel_never_claims_process_termination[cc|cx|ag]`. CI-only skip reasons now say UNVERIFIED.
- Mutation probes (try/finally restore, clean diff after): no promotion, downgrade edge allowed, resolve without scope check, halt not resolved, resolve ignoring certainty -> all KILLED (the last one survived first; test `resolution_refused_once_certainty_advanced` added).

## Docs
SPEC_FEEDBACK_RECORD.md (FB-001..005), LEGACY_IMPORT_SCOPE.md, ADAPTER_CAPABILITIES.md, OWNER_DECISIONS_NEEDED.md (resolved; nothing blocking), M1_PROGRESS_TRACKER.md, UNVERIFIED in README/ci.yml/skip reasons.

## Results
architecture 5, meta 6, schema 16, property 13, migration 53, core 53, integration 22, observation 32, diag 14, security 13, adapters 43, fault 43, e2e 12, soak 9 (+2 deselected), unit/m1 12, package 60 + 14 CI-only skips; bridge 95, control 134, concurrency 21, each 3x green.
traceability: no missing ids; validate_package PASS.

## Open issues
cx.pro consolidated review still pending; accepted-by-delegation items listed in OWNER_DECISIONS_NEEDED.md. Pre-existing unrelated tests/static manifest failure untouched.

## cx final review part 2 (HEAD 650e06c): 5 defects + 1 test gap, all reproduced RED then fixed
1. Pre-invoke fence: after the marker and gate, `_run_runtime` re-validates the claim (token/generation/lease) in a fence immediately before `runtime.deliver`. If lost, the runtime is never called, late evidence `fenced_before_invoke` is written and the cycle returns `fenced`. A lost claim cannot write a fenced resolution, so the marker stays unresolved and the new owner promotes it to MAY_HAVE_STARTED (conservative; a `lost_fence` resolution reason was not added because it could not be written by a fenced-out owner). Residual: a lease expiring between the check and the call is inherent and caught by the per-event heartbeat.
2. Attempt binding: `begin_attempt` authenticates the Record against the persisted Core row (exists, same Stream as the claim, addressed to the peer, not authored by it, position/author/kind/stream equal to the caller copy) and uses the persisted position; `RecordRejectedError` otherwise, nothing written.
3. RETRY authorization: the reconcile Record must address the delivery's peer (empty targets = broadcast, else must include it), in `reconcile_uncertain` and in both authorization triggers.
4. Invocation resolution: CHECK constraints (closed reason set prespawn_failure / control_halt / promoted_may_have_started, invocation_no >= 1, generation >= 0) plus a BEFORE INSERT trigger: it must be the latest (open) marker, the delivery certainty must match the reason (NOT_STARTED for prespawn/halt, advanced for promoted) and a persisted evidence row of the matching kind carrying that invocation_no must exist (evidence is now written before the resolution).
5. Certainty: BEFORE UPDATE trigger enforces forward-only edges (NS->MAY/ST, MAY->ST/TERM, ST->TERM) and rejects unknown values; BEFORE INSERT requires NOT_STARTED.
6. CTX-002 property test now has an independent oracle (exact kept set for record-only budgets; newest fitting pin must be kept for byte budgets; non-empty when something fits). Probes: always-empty projection and ignores-pins both KILLED.
Tests: `tests/m1/bridge/test_own_review2.py` (30 cases incl. 20 raw-SQL certainty pairs with an independent LEGAL literal, recursive_triggers OFF). Mutation probes (8, try/finally, clean diff): all KILLED.
