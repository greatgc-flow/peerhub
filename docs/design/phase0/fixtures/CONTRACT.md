# Phase 0 Golden Fixture Contract v1

> Status: draft. This is the normative fixture checklist referenced by
> `../../PHASE0-COMPATIBILITY.md`. Phase 0 may not substitute a representative
> sample for an ID in this table.

Each fixture record uses its ID below and contains the pinned baseline
revisions, input/preconditions, redaction declaration, expected observable
outcome, capture or `BLOCKED_LIVE_CAPTURE` record, and SHA-256. A blocker may
replace a live provider effect only; it cannot replace the deterministic
fixture marked here.

| ID | Domain | Required baseline outcome |
|---|---|---|
| DP-01 | dispatch-pipe | A valid pipe command reaches a delivered terminal result and records process/protocol/completion layers separately. |
| DP-02 | dispatch-pipe | Validation/pre-spawn rejection creates no provider process or ambiguous dispatch state. |
| DP-03 | dispatch-pipe | Nonzero process exit preserves exit evidence and does not claim verified task success. |
| DP-04 | dispatch-pipe | Output cap stops/categorizes a bounded process without exposing unbounded output. |
| DP-05 | dispatch-pipe | Hard deadline yields the specified timeout/cleanup outcome. |
| DP-06 | dispatch-pipe | Crash after dispatch intent becomes MAY_HAVE_STARTED/UNKNOWN and is not automatically replayed. |
| DT-01 | dispatch-pty | A valid PTY command produces ordered streamed output and terminal evidence. |
| DT-02 | dispatch-pty | Chunk and line normalization preserves defined event ordering. |
| DT-03 | dispatch-pty | Silence deadline is distinct from hard deadline. |
| DT-04 | dispatch-pty | Hard deadline follows bounded cancellation/termination policy. |
| DT-05 | dispatch-pty | Cancellation terminates the process tree or records identity/cleanup uncertainty. |
| DT-06 | dispatch-pty | Cleanup error is attached to, and never masks, the primary outcome. |
| SL-01 | session-lease | A fresh create persists independent session and owner-aware lease identity. |
| SL-02 | session-lease | A compatible resume uses the frozen binding/fingerprint. |
| SL-03 | session-lease | A fingerprint/configuration mismatch rejects or re-plans; it never silently resumes. |
| SL-04 | session-lease | Two same-peer concurrent leases remain independently renewable and closable. |
| SL-05 | session-lease | Wrong owner/process-birth renewal or close is rejected without mutation. |
| SL-06 | session-lease | Stale or identity-mismatched lease recovery records its uncertainty and applies the policy. |
| CR-01 | coordination-room-mailbox-handoff | Room/session lifecycle opens and closes with explicit membership state. |
| CR-02 | coordination-room-mailbox-handoff | Direct send/read preserves message identity and unread/read transition. |
| CR-03 | coordination-room-mailbox-handoff | Broadcast has deterministic recipient/order semantics. |
| CR-04 | coordination-room-mailbox-handoff | Checkpoint/handoff records scope and immutable source reference. |
| CR-05 | coordination-room-mailbox-handoff | Terminal assignment/heartbeat/close respects current owner identity. |
| CR-06 | coordination-room-mailbox-handoff | Concurrent conflict or retirement follows one serialized transition/result. |
| CS-01 | consensus | A proposal freezes an explicit round contract/electorate/policy revision. |
| CS-02 | consensus | Repeating the same vote is idempotent. |
| CS-03 | consensus | A conflicting second vote is rejected and original vote remains immutable. |
| CS-04 | consensus | Missing or timed-out electorate produces the defined unresolved/terminal result. |
| CS-05 | consensus | A unanimous electorate produces exactly one final decision event. |
| CS-06 | consensus | Dissent and any arbiter opinion stay separate; effective outcome is derived. |
| HR-01 | health-recovery | Fresh readiness evidence produces the defined open/admission projection. |
| HR-02 | health-recovery | Expired evidence becomes stale, never silently healthy. |
| HR-03 | health-recovery | Measured integrity/provider failure reaches the correct degradation/quarantine policy. |
| HR-04 | health-recovery | Cooldown and quarantine are distinct transitions with distinct exit conditions. |
| HR-05 | health-recovery | Administrative recovery authorizes a probe but cannot write healthy/open directly. |
| HR-06 | health-recovery | Failed and successful current-fingerprint recovery probes produce their respective outcomes. |
| RT-01 | routing | An eligible request selects a candidate with complete decision/evidence audit. |
| RT-02 | routing | Capability/profile mismatch excludes the candidate with a reason. |
| RT-03 | routing | Missing usage evidence remains absent/unavailable, never zero/unlimited. |
| RT-04 | routing | Terminal/excluded candidate is not automatically selected. |
| RT-05 | routing | Tie/stochastic draw is deterministic from the request ID and auditable. |
| RT-06 | routing | Changed configuration or admission snapshot produces CONFIGURATION_STALE/re-plan before dispatch. |
| GB-01 | governance-broker-cas | Authorized request commits target transition, receipt, and outbox atomically. |
| GB-02 | governance-broker-cas | Stale expected revision/CAS fails without partial mutation. |
| GB-03 | governance-broker-cas | Same idempotency key/payload returns its prior receipt; changed payload is rejected. |
| GB-04 | governance-broker-cas | Crash recovery reconciles committed enforcement-pending work from journal/outbox. |
| GB-05 | governance-broker-cas | Effect success and failure become distinct durable receipts. |
| GB-06 | governance-broker-cas | Lock contention or saga compensation preserves one authoritative transition. |
| CJ-01 | cli-jsonl | A valid read-only envelope returns typed result without requiring idempotency key. |
| CJ-02 | cli-jsonl | A valid mutating envelope carries client/command/idempotency/policy/configuration identity. |
| CJ-03 | cli-jsonl | Malformed envelope fails before mutation/dispatch with stable error. |
| CJ-04 | cli-jsonl | Unsupported protocol major/schema fails before parse/dispatch with supported-version evidence. |
| CJ-05 | cli-jsonl | Authentication/authorization rejection has no state/effect side effect. |
| CJ-06 | cli-jsonl | Error envelope and process exit mapping are stable and redacted. |

The final action manifest must link every legacy action to at least one
positive fixture and one invalid and authorization fixture in this contract
or a strictly additive companion contract. The machine-readable link is
`../action-fixture-policy-v1.csv`: every `legacy_action` in
`../hub-actions-v1.csv` resolves through its required `domain` to one or
more `positive_fixture_ids`, exactly one `invalid_fixture_id`, and exactly
one `authorization_fixture_id`. Semicolon separates positive IDs. The
inventory validator rejects a missing/unknown domain, blank linkage, unknown
fixture ID, duplicate policy domain, or an action row not covered by exactly
one policy row. Thus all 90 actions inherit a positive, malformed-input, and
authorization-rejection fixture; no action may be treated as "representative"
or untested by omission. Missing links fail Phase 0 completion.
