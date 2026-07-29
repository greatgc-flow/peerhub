# SL-01..06 Session-Lease Classification Spec R1

Status: ratified CANDIDATE-tier evidence-scoping record. Produced by an
unlimited unanimous adversarial mutual-critique process between ag.deepthink
and cx.deepthink (2 rounds + Final Call ACK, 2026-07-29), reconciled by cc,
unanimous ACK from both peers (cx's ACK carried two narrow wording
clarifications, both incorporated below, neither changing the design). Does
not amend `PROTOCOL-V1-FREEZE.md`, authorize a cutover, or convert any status
this document does not explicitly name.

## Why this document exists

`PROTOCOL-V1-FREEZE.md` names "session fixtures cover leases" but otherwise
contains no frozen session-lease identity/ownership/fingerprint schema (unlike
the authority-cutover fence lease, whose CAS tuple is fully frozen). No other
doc in the corpus supplies one either. The six SL legacy captures
(`docs/design/phase0/fixtures/captures/SL-0{1..6}.json`) are real OBS-tier
evidence of concrete legacy defects, but do not themselves specify a
replacement schema. This is a genuine DP-06-caliber gap: unresolvable by
further reading, requiring original design work under unanimous ratification
before implementation.

## Process summary

Round 1: ag.deepthink and cx.deepthink independently derived a classification
from `CONTRACT.md`'s six one-liners, the frozen AC-04 CAS-tuple precedent, and
the six legacy capture records only -- explicitly barred from reading
`runner.py` or any `domain/*.py` module, per the DP-06 lesson. Both drafts
converged on the same defects but diverged materially on schema: ag merged
session and lease identity into one record and reused `effect_certainty`
directly for SL-06 uncertainty; cx kept them as two separate record types and
argued `effect_certainty` is a category error for lease-authority uncertainty.

Round 2 (cross-critique): each peer reviewed the other's full draft against
five identified divergences. ag conceded fully on four of five points and
proposed a compatible synthesis on the fifth (SL-06 keeps a dedicated
authority-certainty enum as primary, retains `effect_certainty` only as an
optional orthogonal field) -- cx had proposed the same shape independently.
No irreconcilable disagreement remained after round 2.

Final Call: cc synthesized the converged design and sent it back to both
peers for ACK. ag ACKed without reservation. cx ACKed with two narrow
wording corrections (both incorporated below): (1) `owner_peer_id` is a
persisted logical binding sourced from the request, but is only accepted
once authorized against the authenticated `owner_principal_id` -- it is not
itself trusted evidence; (2) a binding's own fingerprint failing to match its
own stored fingerprint is session-level corruption, not automatically an
SL-06 lease-scoped `LeaseRecoveryReceipt` (which requires a `lease_id`) --
SL-06 stays strictly scoped to lease recovery as CONTRACT.md states, and
session-binding self-corruption is out of scope for these six fixtures
(no SL one-liner requires it; not built here).

## Authority tagging (adopted, both peers unanimous, same framework as DP-06)

- `MUST` -- stated directly by `CONTRACT.md`'s six one-liners.
- `OBS` -- directly observed in a legacy capture record (`SL-01..06.json`).
- `CANDIDATE` -- a reasoned net-new proposal, explicitly unratified as *the*
  only correct schema, adopted here as the implementation baseline.
- `OPEN` -- genuinely unresolved; recorded as backlog, does not block a
  scoped `SPEC_FAITHFUL` fixture for one concrete scenario per ID.

## Ratified design

### Records

- **Session**: `session_id`, `binding` (object), `binding_fingerprint`
  (sha256 of JCS-canonicalized, NFC-normalized `binding`), `lifecycle_state`,
  `revision`, `created_at`. No owner/process/instance fields.
- **SessionLease**: `lease_id` (globally unique), `session_id`, `lease_kind`,
  `owner_peer_id`, `owner_principal_id`, `owner_instance_id`,
  `owner_process_birth_identity`, `fencing_token`, `revision`,
  `lifecycle_state`, `created_at`, `renewed_at`, `expires_at`, `closed_at`.

`session_id != lease_id`; neither is derived from the other. A session can
outlive one owner process and can have multiple concurrent lease records.

### Binding/fingerprint (CANDIDATE closed field set)

`workspace_scope_id`, `room_id`, `room_configuration_revision`, `peer_id`,
`protocol_major`, `resume_contract_revision`, `profile_id`,
`profile_revision`, `effective_tier`, `binding_schema_version`.

Excluded from the fingerprint: `session_id`, `lease_id`,
`owner_instance_id`, `owner_process_birth_identity`, lease expiry/heartbeat/
revision/fencing_token, request/command/correlation/idempotency IDs, mutable
health/load observations, human-readable reason strings. Compatibility is
exact equality of every field after server-side resolution; missing,
unknown, or unresolvable values are incompatible and are never defaulted
from the old session.

### Storage model

Flat authoritative table keyed by `lease_id`, with non-unique derived
indexes on `(session_id, owner_peer_id, lifecycle_state)` and
`(session_id, lease_kind, lifecycle_state)`. No nested/peer-keyed structure
defines uniqueness, ownership, renewal, or closure semantics -- a peer-keyed
view may exist only as a derived lookup index, never as the source of truth.

### Per-fixture disposition

- **SL-01** (MUST/OBS -> CANDIDATE resolved): fresh create persists one
  `Session` and one `SessionLease` atomically.
- **SL-02** (MUST/OBS -> CANDIDATE resolved): compatible resume requires
  exact fingerprint equality against the current resolved binding; returns
  the *existing* `session_id` (never overwritten); issues a *new* `lease_id`
  for the new owner process. Any prior lease stays independently active
  until closed/expired/recovered. Renewing an *existing* lease requires
  naming that `lease_id` and satisfying its full fence tuple -- resume never
  guesses which lease to renew.
- **SL-03** (MUST/OBS -> CANDIDATE resolved): mismatch returns
  `{code: SESSION_BINDING_MISMATCH, prior_session_id, mismatched_fields,
  stored_fingerprint, resolved_fingerprint, disposition}`. Default
  `disposition = REJECTED` (CANDIDATE default, not the only ratifiable
  choice). Re-planning is an explicit policy-selected action that creates a
  new session+lease; it never rewrites the old session's binding in place.
  Session-binding self-corruption (own fingerprint mismatches own stored
  fingerprint) is a distinct, out-of-scope concern per cx's Final Call
  correction -- not built as part of these six fixtures.
- **SL-04** (MUST/OBS -> CANDIDATE resolved): flat store as above; each
  lease independently owns revision/fencing_token/expiry/renewal/lifecycle/
  close-outcome/owner-instance/process-birth. Fixture proves: create L1+L2
  for the same session+peer; renew L1 leaves L2 byte-for-byte unchanged;
  close L2 leaves L1 active/renewable; stale CAS on either cannot mutate the
  other.
- **SL-05** (MUST/OBS -> CANDIDATE resolved): fence tuple = `(session_id,
  lease_id, fencing_token, revision, owner_peer_id, owner_principal_id,
  owner_instance_id, owner_process_birth_identity)`. `owner_principal_id`
  must be derived from authenticated transport/envelope context;
  `owner_peer_id` is recorded from the request but is accepted only once
  authorized against `owner_principal_id`, never trusted as self-asserted
  evidence on its own -- this directly fixes the observed legacy defect
  (peer `cx` heartbeating peer `ag`'s lease by self-asserting `--peer ag`).
  Every predicate is checked in one transaction before any mutation; a
  failed predicate mutates nothing and triggers no SL-06 transition.
- **SL-06** (MUST/OBS -> CANDIDATE resolved for one concrete scenario):
  dedicated `lease_authority_certainty` enum (`CONFIRMED_CURRENT`,
  `PRIOR_HOLDER_UNVERIFIED`, `FENCED_FOR_FUTURE_WRITES`), separate from and
  orthogonal to the existing `effect_certainty` vocabulary
  (`NOT_STARTED`/`MAY_HAVE_STARTED`, already used in DP/CJ modules), which is
  retained only as an optional/nullable field when an external process
  effect is actually attached to the lease. Recovery emits an atomic
  `LeaseRecoveryReceipt` (`recovery_receipt_id`, `session_id`, `lease_id`,
  `detected_at`, `recovery_actor_principal_id`, `trigger`,
  `mismatch_dimensions`, `evidence_digest`, pre/post
  `lifecycle_state`/`revision`/`fencing_token`, `certainty_before_policy`,
  `policy_id`/`policy_revision`, `decision`, `certainty_after_policy`,
  optional `external_effect_certainty`). Structural rule: an SL-05
  rejection must never itself trigger any SL-06 transition -- recovery only
  runs via a separately-authorized recovery operation that independently
  re-assesses evidence.

## OPEN backlog (recorded, non-blocking)

1. Full SL-06 recovery policy matrix (`trigger` -> `decision` mapping) is
   unratified; the SL-06 fixture fact-injects one concrete trigger+decision
   pair, not a general policy engine.
2. `lease_kind` taxonomy (whether `init-session` membership and
   `terminal-handoff` assignment share one `lease_kind` or two, and how this
   relates to CR-05's terminal-assignment ownership) is unresolved; fixtures
   fact-inject an explicit `lease_kind` without adjudicating the taxonomy.
3. Whether all session leases are process-bound or some are
   principal-bound-only is unresolved; SL-05 constructs a process-bound
   lease specifically (the OBS-grounded case) and leaves general
   applicability open.
4. Reject-vs-replan default (SL-03) is CANDIDATE, not the only ratifiable
   choice.
5. Session-binding self-corruption (distinct from a normal SL-03 mismatch)
   is out of scope for SL-01..06 as currently specified (cx's Final Call
   correction); if ever built, it needs its own session-level quarantine
   path, not an SL-06 lease receipt.

## Documentation-hygiene notes (non-blocking, not open questions)

- `CONTRACT.md`'s own header says "Status: draft," not "ratified." Every
  other completed fixture group this session (DP/CR/CS/RT/GB/CJ) has
  nevertheless treated its one-liners as binding scope, consistent with
  `RATIFICATION-PROVENANCE-INDEX-R1.md` tracking ratification as a separate
  event from this document's header.
- `SL-05.json`'s `artifacts.transcript` field says `CR-05.transcript.json`
  instead of `SL-05.transcript.json` -- a legacy capture metadata
  copy-paste artifact, not something either peer relied on for OBS claims.
