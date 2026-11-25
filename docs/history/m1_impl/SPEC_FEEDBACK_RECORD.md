# Spec Feedback Record (M1 implementation -> frozen spec package)

Format: `docs/m1_spec/08_LIFECYCLE/TEMPLATES/FEEDBACK_RECORD.md`. The frozen `docs/m1_spec` package is NOT edited; these records are the
errata channel. Detected 2026-10-04 (implementation waves 0-9 + owner consultation). Source: audit (implementation vs. catalog).
Common fields: Boundary per item; Reproducibility: reproduced; Release/rollback relation: none (docs/process only); Recurrence watch: re-check at the next spec revision.

## FB-001 PROP-002 contradicts TD-20 / IDEM-001 / IDEM-002
- ID: FB-001 | Signal class: spec contradiction | Boundary: Docs
- Evidence: test-catalog.json PROP-002 oracle "any semantic field change (incl. author, stream_id) conflicts under the same key" vs TD-20 (idempotency scope = stream + author + key).
- Impact: a changed author or stream with the same key is a NEW scope, not a conflict; implementing PROP-002 literally would break TD-20.
- Current contract violation: yes (internal inconsistency of the package). Decision: implemented TD-20; PROP-002 asserts conflict only for mutations inside an unchanged scope. Owner consultation: confirmed (A1).
- Linked requirement/tests: REQ-IDEM-002/003, IDEM-001/002, PROP-002 (tests/m1/property). Proposed spec change: restate the PROP-002 oracle as "within the same scope".

## FB-002 State-transition contract is not exhaustive
- ID: FB-002 | Signal class: spec gap | Boundary: Docs / Bridge
- Evidence: STATE_MACHINE_COVERAGE.json lists no control machine and no explicit certainty edge table; TD-11/TD-26 give only monotonicity text.
- Decision: "unlisted" is not "forbidden" (D-W3-3). Forbidden = any certainty downgrade (D-OWN-A2/A4: none anywhere, the pre-start state is kept by a separate invocation marker) and any overwrite of TERMINAL; NOT_STARTED -> TERMINAL is forbidden; MAY_HAVE_STARTED -> STARTED/TERMINAL allowed on late evidence only. Owner consultation: accepted (A2/A4).
- Linked tests: CERT-001 (full 4x4 matrix with independent LEGAL and FORBIDDEN literals), BRG-011/012, tests/m1/bridge/test_own_invocation_marker.py. Proposed spec change: add the edge table and the control machine to STATE_MACHINE_COVERAGE.

## FB-003 CTX-001 "after Offset" vs the implemented fresh-generation bootstrap
- ID: FB-003 | Signal class: spec wording vs behaviour | Boundary: Bridge
- Evidence: CTX-001 action "fresh-session projection after Offset".
- Decision: the Offset governs DELIVERY only (D-W4-8b). A fresh generation is bootstrapped from the Stream history up to (excluding) the delivered Record, in canonical order, deduplicated, with omission and pin metadata (pinned unseen redirects), all inside ONE total TD-12 budget; boundary metadata records `after_position` 0 and truncation. The oracle ("canonical ordered suffix fitting budget, truncation marked, no reorder") still holds. Owner consultation: confirmed (A3).
- Linked tests: CTX-001.., tests/m1/control/test_ctx_catchup.py. Proposed spec change: replace "after Offset" with "bounded ordered suffix before the delivered Record".

## FB-004 ORIGINAL_LINKS.md differs from the generator output
- ID: FB-004 | Signal class: package drift | Boundary: Docs / Process
- Evidence: `03_STANDARDS/ORIGINAL_LINKS.md` vs `generate_docs.py` output (trailing blank line, platform newlines); validate_package.py does not check it; REL-004 enforces STANDARDS_DECISION_TABLE.md only. The package is checksum-frozen, so nothing was changed.
- Decision: documented only (Q-W7-5). Proposed fix: regenerate in the next package revision and have the validator compare both files.

## FB-005 BRG-011 / TD-11 consistency (pre-spawn failure vs durable marker)
- ID: FB-005 | Signal class: spec ambiguity | Boundary: Bridge
- Evidence: BRG-011 "pre-spawn failure stays NOT_STARTED, Offset unchanged, retryable" and TD-11 "durable certainty never downgrades" are only jointly satisfiable if the crash-safety marker is NOT the certainty itself. An earlier implementation set MAY_HAVE_STARTED before invoking and reverted it (a downgrade; superseded D-W3-8).
- Decision (D-OWN-A2/A4): certainty stays NOT_STARTED while an invocation is pending; a separate persisted invocation marker blocks replay; recovery promotes an unresolved marker to MAY_HAVE_STARTED; a fenced proven pre-spawn failure or control halt resolves the marker without any certainty change. Implemented with append-only tables bridge_invocations / bridge_invocation_resolutions and an upgrade path for existing stores.
- Linked tests: BRG-011, BRG-012, BRG-008. Proposed spec change: state the marker semantics in TD-11.
