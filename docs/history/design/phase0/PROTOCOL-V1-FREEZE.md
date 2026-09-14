# PeerHub Protocol v1 Freeze

> Status: **draft; implementation and test scaffolding are forbidden until
> R:10 ratification.** This is the v1 contract for embedded and one-shot
> CLI/JSONL clients, not a daemon protocol.

## 1. Ratification metadata

At freeze, record a UUIDv4 `document_id`, SHA-256 of the canonical UTF-8 body,
protocol `1.0`, schema `1.0.0`, R:10 round ID/date/frozen electorate, and the
SHA-256 of `ARCHITECTURE.md`, `PHASE0-COMPATIBILITY.md`, and the fixture
contract. A related Engram decision is not a substitute for this ratification.

## 2. Command, response, and event envelopes

Every command, regardless of embedded, CLI, or JSONL entry, has exactly:
`protocol_major`, `protocol_minor`, `schema_version`, server-minted UUIDv4
`command_id`, conditional caller UUIDv4 `idempotency_key`, caller UUIDv4
`correlation_id`, `client_id`, nullable `actor_id`, `scope`, typed `method`,
typed `params`, nullable expected policy/configuration revisions, and a
client timestamp (audit only; the server clock is authoritative).

`scope` is either `{workspace_id, home_id}` for a resolved workspace identity
or `GlobalScope`. Global scope is limited to declared inspection methods. Each
method declares its accepted scope and whether it is read-only. State-changing
methods require an idempotency key.

Every success returns the command ID, correlation ID, current/terminal state,
receipt reference when one exists, frozen revisions, and typed result. Every
failure returns this stable error envelope:

```text
code, phase, execution_certainty, retry_disposition,
message, details, correlation_id
```

`phase` is one of `VALIDATION`, `ADMISSION`, `PRE_SPAWN`, `POST_SPAWN`,
`ASSESSMENT`, or `EFFECT`; certainty is `NOT_STARTED`, `MAY_HAVE_STARTED`,
`STARTED`, or `TERMINAL`; retry is `SAFE`, `UNSAFE`, `CONDITIONAL`, or
`NEVER`. Consumers must not parse prose or vendor exception names for control
flow.

All outbox, audit, and stream events carry: protocol version, UUIDv4 event ID,
correlation ID, nullable request/round ID, monotonically increasing sequence
within the correlation stream, server timestamp, closed `kind`, typed payload,
evidence references, predecessor digest, and nullable recovery context.
Recovery context is `{sweep_id, sweep_timestamp, original_state,
recovery_action}` where action is `INTERRUPTED`, `INCOMPLETE_SAFE`, `FENCED`,
or `IDENTITY_MISMATCH`. Stream events are informational and cannot change a
terminal outcome.

The v1 event-kind registry includes dispatch acceptance/intention/start/chunks
(stdout, stderr, PTY), usage, vendor error, completion marker, progress,
attempt terminal, lease/session/artifact transitions; readiness/usage/admission
and route decisions; consensus creation/vote/decision; mutation/effect;
room/message/handoff/presence/terminal assignment; and authority/recovery
transitions. Adding a kind is a minor version change; removal or redefinition
requires protocol major 2.

## 3. Validation and version negotiation

One-shot input is processed in this order, stopping before any later stage:

1. Complete UTF-8 JSONL frame, then JSON parsing.
2. Protocol-major compatibility, then schema-version compatibility.
3. Required field/type validation, manifest method membership, scope kind.
4. Idempotency-key requirement and method parameter schema validation.
5. Actor/scope authorization, then admission and dispatch.

Errors are respectively `TRUNCATED_FRAME`/`MALFORMED_ENVELOPE`,
`PROTOCOL_VERSION_MISMATCH`, `SCHEMA_VERSION_UNSUPPORTED`,
`UNKNOWN_COMMAND`/`SCOPE_MISMATCH`, `MISSING_IDEMPOTENCY_KEY`/
`INVALID_PARAMS`, and `ACTOR_UNAUTHORIZED`/`SCOPE_UNAUTHORIZED`.
Version errors list supported majors/schemas. Unknown additive fields from a
higher minor are ignored; missing required fields are not. The embedded client
fails at construction on a major mismatch without submitting a command.

CLI exits are coarse only: 0 success/idempotency hit; 1 unexpected internal;
2 protocol/validation; 3 authorization; 4 admission; 5 pre-spawn; 6 execution
that may have started; 7 state/CAS. The structured error is authoritative.

## 4. Stable error taxonomy

V1 freezes these codes. New additive codes require a minor bump; removal or
changed semantics require a major bump.

| Family | Codes |
|---|---|
| Protocol | `PROTOCOL_VERSION_MISMATCH`, `SCHEMA_VERSION_UNSUPPORTED`, `MALFORMED_ENVELOPE`, `TRUNCATED_FRAME`, `DUPLICATE_ID_CONTENT_MISMATCH` |
| Validation | `UNKNOWN_COMMAND`, `INVALID_PARAMS`, `SCOPE_MISMATCH`, `MISSING_IDEMPOTENCY_KEY` |
| Identity/auth | `CLIENT_UNKNOWN`, `ACTOR_UNAUTHORIZED`, `SCOPE_UNAUTHORIZED` |
| Admission | `PEER_UNAVAILABLE`, `PROFILE_UNAVAILABLE`, `ROUTE_EXHAUSTED`, `ADMISSION_CLOSED`, `CONFIGURATION_STALE`, `POLICY_STALE` |
| Idempotency | `IDEMPOTENCY_HIT`, `IDEMPOTENCY_PAYLOAD_MISMATCH` |
| Execution | `SPAWN_FAILED`, `START_UNCERTAIN`, `PROCESS_TIMEOUT`, `SILENCE_TIMEOUT`, `PROCESS_KILLED`, `IDENTITY_MISMATCH`, `LEASE_EXPIRED`, `LEASE_OWNERSHIP_LOST`, `CANCELLATION_CLEANUP_FAILED`, `ARTIFACT_IDENTITY_UNPROVABLE` |
| State/CAS | `REVISION_CONFLICT`, `RECORD_NOT_FOUND`, `UNIQUE_CONSTRAINT_VIOLATED`, `EPOCH_STALE`, `CUTOVER_INPUT_DRIFT`, `CUTOVER_EPOCH_CONTENDED`, `MIGRATION_LOCK_LOST`, `WRITE_SCOPE_NOT_QUIESCED`, `PEERHUB_ERA_WRITES_PRESENT`, `WORKSPACE_IDENTITY_MISMATCH`, `FILESYSTEM_UNSUPPORTED` |
| Assessment | `PROTOCOL_ASSESSMENT_FAILED`, `COMPLETION_INCOMPLETE`, `COMPLETION_UNVERIFIED` |

`IDEMPOTENCY_HIT` returns the stored receipt/result and does not execute an
effect again. Authorization failures have no state or effect side effect.
`START_UNCERTAIN` and post-spawn timeouts are `MAY_HAVE_STARTED`/unsafe to
blindly retry. Assessment retry is conditional only when the frozen completion
contract explicitly declares `replay_safe`.
`ARTIFACT_IDENTITY_UNPROVABLE` is `PRE_SPAWN`/`NOT_STARTED`/`CONDITIONAL` with
CLI exit 5: it is safe to retry only after a new verified artifact binding.

## 5. Evidence, outcomes, and completion

`EvidenceValue[T]` has only `MEASURED`, `ABSENT`, `UNAVAILABLE`, `ERROR`, and
`STALE`. It includes source tag, provider ID/version, observed/captured times,
freshness TTL, content-addressable reference, and nullable value. Stale never
becomes measured; absent never means zero/unlimited/healthy; unavailable never
means healthy/routable; declared-unverified facts are not measurement.

An ask always carries independent `ExecutionOutcome`, adapter-only
`ProtocolAssessment`, and centrally-produced `CompletionAssessment`; its
effective status is derived, never a separately persisted fourth truth.
`CompletionContract` is frozen at admission and contains UUID, contract kind,
requirements, and `replay_safe`. Kinds are `DELIVERY_ONLY`,
`ARTIFACT_REQUIRED`, `SCHEMA_VALIDATED`, `FIELD_REQUIRED`, `CUSTOM_VERIFIER`,
and `VENDOR_RECEIPT`. Requirements identify an artifact/schema/field/verifier,
with optional digest, length, and schema reference.

Exit code zero with text is at most delivered-unverified. Verified completion
requires every frozen requirement. Adapters never decide semantic success, and
unverified/incomplete output never automatically degrades health without an
explicit versioned peer-causation policy.

## 6. Idempotency, state, recovery, and leases

The canonical payload digest is SHA-256 over UTF-8 without BOM, recursively
lexicographically sorted keys, no insignificant whitespace, canonical JSON
numbers, NFC strings, and explicit null fields. The idempotency identity is
`(client_id, command_type, idempotency_key)`. It binds this digest, command ID,
and receipt; same key/digest returns the existing state, while a changed digest
is `IDEMPOTENCY_PAYLOAD_MISMATCH`.

State transition, immutable receipt, and outbox events commit in one SQLite
`BEGIN IMMEDIATE` transaction. No transaction waits on process, provider,
filesystem, or network I/O. Mutable records have a CAS revision; evidence,
receipts, votes, round contracts, and events are append-only. Unique
constraints cover command ID, idempotency identity, `(round_id, voter_id)`,
and active proposal identity.

Outbox consumers checkpoint event IDs idempotently. A committed but unenforced
effect is `COMMITTED_ENFORCEMENT_PENDING`, never rolled back solely because a
worker died. Ambiguous external effects remain `MAY_HAVE_STARTED`/`UNKNOWN`
until reconciled; they are never blindly replayed.

Leases use UUIDv4 and renew/close with lease ID, owner instance, epoch, and
expected revision. Process actions require PID plus creation-time identity.
`RESERVED` and dispatch intent commit atomically. Expiry is not death proof:
a verified-live child is fenced before termination; an identity mismatch is
quarantined and never killed. Legacy leases also bind the authority epoch.

## 7. Actor, effects, artifacts, and coordination

Authorization occurs after validation and before any mutation or dispatch.
Sandboxed callers use a create-only immutable broker inbox; after validated
import, the database record is authoritative and repeat imports are harmless.
Only the application facade writes SQLite state; only the artifact materializer
creates staged files; runners start provider processes after intent/lease;
broker effect workers handle external effects. Adapters do none of those and
do not select peers, alter health, or decide task completion.

Artifact lifecycle is `DECLARED -> STAGED -> VERIFIED -> CONSUMED -> CLEANED`,
with stage/verification failures and `ORPHANED -> CLEANED` recovery. Staging is
create-new; verification requires digest and length; cleanup occurs only after
the process tree is terminal; `VERIFIED -> CONSUMED` commits with dispatch
intent.

Rooms, messages, handoffs, presence, and terminal duties use the same envelopes
and error taxonomy. V1 includes room create/close, message send/broadcast/read,
unread listing, checkpoint/handoff, terminal assign/heartbeat/close/sweep, and
presence update. Membership is explicit, broadcasts use a membership snapshot,
read state is per recipient, handoffs are immutable, and cross-feature work is
outbox-driven rather than a direct service import.

## 8. Fixture binding and exit gate

The fixture contract binds this protocol: CLI/JSONL fixtures cover envelopes,
version/auth/errors; pipe/PTy fixtures cover stream/outcome/artifact behavior;
session fixtures cover leases; coordination fixtures cover room semantics;
health/routing fixtures cover evidence; governance fixtures cover CAS,
idempotency, outbox, and recovery. Every legacy action retains its positive,
invalid-input, and authorization fixture linkage through the Phase 0 action
manifest. Fixture IDs and baseline digests are immutable within v1.

Phase 1 may begin only after this document's exact hash has R:10 approval and
all listed contract surfaces have a deterministic fixture or an explicit
blocked-live-capture record.

## 9. Required correction set (CX adversarial review)

This section overrides any conflicting draft wording above.

1. Submission contains caller-minted `client_request_id` and `correlation_id`,
   never `command_id`. After authorization/admission the server atomically
   mints `command_id`; pre-admission errors have null command ID and a server
   diagnostic ID. Reused `(client_id, client_request_id)` with changed intent
   is `DUPLICATE_ID_CONTENT_MISMATCH`.
2. Idempotency is RFC 8785 JCS SHA-256 after NFC strings and rejection of
   duplicate keys/non-finite numbers. Its projection is protocol major, schema,
   authenticated principal, canonical scope, method, typed params, expected
   revisions, and frozen completion contract; it excludes request/command/
   correlation IDs, timestamps, transport metadata, and ignored additions.
3. A server accepts only an explicitly supported minor/schema compatibility
   pair, emits the negotiated minor, and never emits a later event kind.
4. Errors always include protocol/schema, diagnostic ID, nullable command ID,
   and parseable request/correlation IDs. Idempotency hit is success; exit 7
   also covers idempotency conflict.
5. Events have UUID event ID plus monotonic workspace `outbox_position`; stream
   events have `stream_id` and contiguous `stream_sequence`. Correlation is
   tracing only. Consumers CAS-checkpoint outbox position and dedupe event ID.
   Chunks use `{encoding:'base64', data, byte_offset}`; JSONL diagnostics use
   stderr and clients never infer ordering from arrival time.
6. Only measured evidence carries a value. `ABSENT` requires a complete,
   authoritative observation and completeness receipt; partial, unauthenticated,
   interrupted, or scope-mismatched observation is unavailable/error, never
   absent. Stale may reference prior display evidence but `ABSENT`,
   `UNAVAILABLE`, `ERROR`, and `STALE` are unusable as measured routing, health,
   admission, quota, or authority facts.
7. Attempts have server-minted IDs, monotonic attempt numbers, and one active
   attempt per command. Completion assessment binds immutable attempts to the
   frozen contract; a post-`MAY_HAVE_STARTED` attempt needs reconciliation or
   explicit replay safety.
8. Admission atomically records command, idempotency, frozen contract, intent,
   lease/fence, and outbox. All provider/filesystem work is outside SQLite;
   completion CAS-checks command, attempt, lease token, epoch, and revision.
9. Lease generation has a database-monotonic fencing token and server UTC plus
   boot identity; every renew/close/outcome/cleanup/effect CAS-checks it.
10. Artifact staging retains the verified object identity/digest through runner
    binding; paths are never reopened after verification. Broker identity comes
    from host ACL/capability, and importer takes exclusive custody, verifies a
    bounded digest-complete file, then atomically records its import receipt.
11. SQLite additionally enforces `(client_id, client_request_id)` and
    `(command_id, attempt_number)`. The latter makes attempt history monotonic
    and permits at most one nonterminal attempt per command.
12. An outbox event uses workspace `outbox_position`; a stream event uses
    `stream_id` plus contiguous `stream_sequence`. Correlation ID is tracing
    metadata only. Responses/events include the negotiated minor/schema.
13. The lease CAS tuple for renew, close, outcome, cleanup, and effects is
    `(command_id, attempt_id, fencing_token, authority_epoch, revision,
    owner_instance_id, owner_process_birth_identity)`. Process birth identity
    is required for process-bound leases and an authenticated caller must match
    the persisted owner; expiration/reassignment advances the token first.
14. The artifact runner retains the verified handle/immutable descriptor and
    namespace protection until the consumer opens that same volume/file identity
    or the process tree terminates. If a pathname-only consumer cannot prove
    same-object acquisition, dispatch fails `ARTIFACT_IDENTITY_UNPROVABLE`.
    The broker importer retains exclusive custody through atomic receipt import;
    inbox fields never assert caller identity.
