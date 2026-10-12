# R4/P4b duty gateway-gap design

**Date:** 2026-09-20  
**Status:** design only; no implementation is included in this document.

## Decision summary

All four mutating `duty` actions can be migrated to the ApplicationAPI
gateway without introducing a non-JSON wire type.  Three have already
registered command/handler paths (`heartbeat`, `close`, and `sweep`), but
they are not yet safe CLI replacements because their shared lease encoder
silently narrows the current `--json` receipt.  `claim` needs a new command
and descriptor, and a small service/command extension to preserve its
existing `--heartbeat-timeout-ms` behavior.

`duty close --close-session` should remain one composite gateway command,
using the existing `TerminalCloseCommand` handler as its basis.  It is not
transactionally atomic today and cannot become so merely by routing it
through ApplicationAPI.  The implementation must preserve the current
explicit partial-success result and CLI exit status of 2 when duty closes
but session closure fails.

No action needs to remain permanently direct.  The prerequisite is a
lossless duty-lease result encoder plus the small claim and close behavior
work detailed below.

## Current construction and gateway inventory

`create_runtime()` constructs the concrete
`DutyLeaseCoordinator(state_store, clock=context.clock, ids=context.ids)`,
then constructs `TerminalDutyService(duty_lease_coordinator)` and
`RoomParticipationCoordinator(...)`.  It supplies all three to
`ApplicationAPI`, which calls `register_duty_handlers(...)`.  Thus
`runtime.duty_lease_coordinator` is a `DutyLeaseCoordinator`, not an
adapter or an opaque proxy.

The existing registered commands are:

| CLI action | Existing command method | Current registration |
| --- | --- | --- |
| `duty claim` | none | no command or descriptor |
| `duty heartbeat` | `coordination.terminal.heartbeat` | registered |
| `duty close` | `coordination.terminal.close` | registered, including the session-close composition |
| `duty sweep` | `coordination.terminal.duty_sweep` | registered |

The existing registrations have `Mutability.MUTATING`, `ScopeKind.ANY`, and
`IdempotencyPolicy.DOMAIN_ATOMIC_REQUIRED`.  Gateway submission through the
shared CLI helpers therefore uses the normal asserted-client path:
`_cli_submission()` supplies the stable CLI client ID and
`_submit_via_gateway()` supplies the matching `RequestContext.client_id`.
The proposed call-map value for each migrated mutating action is
`"authorizer_behavior": "ASSERTED_ONLY"`.

## Action-by-action mapping

### `duty claim`

The direct CLI call maps to this existing service method:

```python
TerminalDutyService.claim_terminal_duty(
    self,
    room_id: str,
    owner: DutyOwnerIdentity,
    owner_principal_id: str,
    authority_epoch: int,
) -> DutyLeaseSnapshot
```

It is a single service call and all logical command inputs are JSON-safe:
`room_id`, `instance_id`, `profile_id`, and `owner_principal_id` are
strings; `authority_epoch` and the CLI timeout are integers.  The
`DutyOwnerIdentity` value is already naturally represented on the wire as
the two explicit strings and reconstructed by the handler.

This action does **not** fit the current registered pattern unchanged:

1. There is no `TerminalClaimCommand` or `coordination.terminal.claim`
   descriptor.
2. The direct CLI deliberately constructs `TerminalDutyService` with
   `default_heartbeat_timeout_ms=parsed.heartbeat_timeout_ms`.  `claim`
   accepts `--heartbeat-timeout-ms` (default `60_000`), while the existing
   method signature and existing runtime service have no per-call timeout
   argument.  Routing a command to the current method would silently turn a
   user-selected non-default timeout back into 60 seconds.

Recommended implementation shape: add `TerminalClaimCommand` with
`room_id`, `instance_id`, `profile_id`, `owner_principal_id`,
`authority_epoch`, and `heartbeat_timeout_ms`, with method
`coordination.terminal.claim`.  Extend the terminal service with an
optional per-call timeout (for example,
`heartbeat_timeout_ms: int | None = None`) and have the handler call that
method.  The service uses its existing default when the argument is `None`.
That preserves the existing public CLI behavior while retaining
`TerminalDutyService` as the owner of the literal `"terminal-duty"` role.
Duplicating `DutyLeaseCreateRequest` construction in the handler would also
work mechanically, but would bypass that service boundary and duplicate its
role/default policy.

For asserted gateway metadata, `owner_principal_id` is the best available
`actor_id`: it is the current CLI's explicit principal argument and is the
identity persisted on the lease.

### `duty heartbeat`

The exact service call is:

```python
TerminalDutyService.send_heartbeat(
    self,
    lease_id: str,
    room_id: str,
    owner: DutyOwnerIdentity,
    term: int,
    authority_epoch: int,
) -> DutyLeaseSnapshot
```

It already has `TerminalHeartbeatCommand` and a matching
`coordination.terminal.heartbeat` descriptor.  The command's six wire
parameters are strings/integers and reconstruct the `DutyOwnerIdentity`
exactly.  It therefore fits the Command/handler/gateway pattern after the
shared result-encoder correction described in [Wire-shape preservation](#wire-shape-preservation).

No owner-principal CLI argument is available for this action.  Use the
existing `profile_id` as the asserted `SubmissionMetadata.actor_id` for
consistent request attribution.  This does not alter current domain fence
checks; absent a credential, the gateway authorizer checks the matching CLI
client IDs, not a new relation between the submission actor and lease
fields.

### `duty close`

The duty half maps to:

```python
TerminalDutyService.close_terminal_duty(
    self,
    lease_id: str,
    room_id: str,
    owner: DutyOwnerIdentity,
    term: int,
    authority_epoch: int,
) -> DutyLeaseSnapshot
```

The ordinary close is a single service call with JSON-safe string/integer
parameters, and `TerminalCloseCommand` plus
`coordination.terminal.close` already exists.  Its registered handler
returns a composite result even when `close_session` is false
(`session_close.status == "not_requested"`), so the CLI must deliberately
unwrap the lossless duty lease for ordinary `close --json` in order to
preserve the current flat output contract.

The `--close-session` form adds this independent second call:

```python
RoomParticipationCoordinator.end_session(
    self,
    request: RoomSessionEndRequest,
) -> RoomSessionSnapshot
```

`RoomSessionEndRequest` contains `session_id: str`,
`session_generation: int`, `workspace_scope_id: str`, `room_id: str`,
`actor_principal_id: str`, and `owner: DutyOwnerIdentity`.  Each maps to a
string/integer command field; no custom JSON serializer is needed.

The direct CLI first releases duty, then ends the room session in a separate
transaction.  On an `InvalidMutationError`, `RecordNotFoundError`, or
`ValueError` from session closure it prints:

```json
{
  "duty_close": {"status": "ok", "lease": "..."},
  "session_close": {"status": "failed", "reason": "..."}
}
```

and returns exit code 2.  The duty release is intentionally retained.  A
later retry is supported because `close_terminal_duty()` recognizes the
same fenced released lease and returns it before retrying the independent
session close.

The existing gateway `close_terminal()` handler already implements this
ordering and packages the same conceptual result, so the command is the
right composition boundary.  There are two differences to correct during
migration:

1. It currently catches `Exception`, whereas the direct CLI catches only
   `InvalidMutationError`, `RecordNotFoundError`, and `ValueError`.  Align
   the handler's catch set with the direct path so unrelated programming or
   infrastructure failures do not become synthetic partial successes.
2. A returned composite result makes `CommandOutcome.ok` true today even
   when `session_close.status == "failed"`.  The gateway CLI branch must
   inspect that result, print the structured partial-success result, and
   return 2 for that status.  Do not turn the session failure into a raised
   gateway error: doing so loses the successful duty-close receipt that the
   current CLI deliberately reports.

Use `profile_id` as the outer asserted submission actor, just as heartbeat
does.  The separate `actor_principal_id` remains preserved verbatim in the
session request and continues to be the session fence identity.

### `duty sweep`

This action maps directly to the concrete coordinator method:

```python
DutyLeaseCoordinator.sweep_expired_leases(
    self,
    role: str,
    *,
    recovery_actor_principal_id: str,
    trigger: str,
    evidence_digest: str,
    policy_id: str,
    policy_revision: str,
) -> tuple[DutyLeaseSnapshot, ...]
```

`TerminalDutySweepCommand` and
`coordination.terminal.duty_sweep` already match this signature.  It is a
bulk operation, but it remains a single coordinator call and returns a
tuple of snapshots that the handler can encode as a JSON list.  It fits the
existing pattern after the shared result-encoder correction.

All named recovery inputs are `str` in the parser, command, and method:

| Field | CLI default / requirement | JSON representation |
| --- | --- | --- |
| `recovery_actor_principal_id` | required | string |
| `trigger` | optional; defaults to `"HEARTBEAT_TIMEOUT"` | string |
| `evidence_digest` | required | string |
| `policy_id` | required | string |
| `policy_revision` | required | string |

`role` is likewise a string and defaults to `"terminal-duty"`.  None of
these parameters is a dataclass, enum, bytes value, or nullable field at
the command boundary; there is no JSON coercion risk.  Use
`recovery_actor_principal_id` as the asserted submission actor.

## Wire-shape preservation

The reason duty was correctly excluded from the earlier mechanical CLI
migration is concrete output information loss, not an inability to encode
the domain data.

The direct `_duty_lease_payload()` emits these nine fields:

```text
lease_id, room_id, role,
owner { instance_id, profile_id },
owner_principal_id, authority_epoch, term, state,
heartbeat_expires_at
```

The current `register_duty_handlers()` `encode_lease()` emits only:

```text
lease_id, room_id, role, state, term, authority_epoch
```

Consequently, a naïve migration would silently drop exactly:

- `owner` (and therefore both `DutyOwnerIdentity.instance_id` and
  `DutyOwnerIdentity.profile_id` from the result);
- `owner_principal_id`; and
- `heartbeat_expires_at`.

This affects heartbeat results, normal-close results, the nested
`duty_close.lease` in composite close output, and every item in sweep
output.  `authority_epoch` and `term` are already encoded as JSON integers
and are not dropped.  The full internal `DutyLeaseSnapshot` has additional
fields (`challenge_until`, `created_at`, `updated_at`, and
`consecutive_terms_held`), but the direct CLI does not currently expose
them, so preserving the nine-field direct receipt—not expanding it—is the
compatibility requirement.

Before routing any duty CLI branch, replace or extend the shared handler
encoder to emit the same nine-field JSON-safe representation as
`_duty_lease_payload()`.  It should live at a non-CLI application/dispatch
boundary (or be a small shared pure adapter), rather than importing a CLI
formatter into a handler.  Then:

- heartbeat can print that result directly;
- sweep can return `{ "expired_count": n, "leases": [...] }` with the
  complete payload per lease;
- close with `--close-session` can nest that complete payload under
  `duty_close.lease`; and
- close without `--close-session` can have the CLI preserve its current
  flat nine-field output by unwrapping the composite handler result.

The claim timeout is a separate *parameter* preservation risk.  JSON can
represent `heartbeat_timeout_ms` as an integer, but the present command and
service signature provide nowhere to carry a non-default value.  The
proposed claim command/service extension is therefore required before
migration.

## Composite-close options

### A. One composite command and one handler (recommended)

Use the existing `TerminalCloseCommand` and
`coordination.terminal.close` handler, with the two corrections above.

Advantages:

- preserves the present order, partial-success receipt, and retry rule in
  one domain-facing operation;
- produces one gateway authorization decision, one submission identity, and
  one command receipt for what users invoke as one CLI action;
- avoids making the CLI reconstruct a cross-service protocol that already
  exists in the handler.

Costs and limits:

- the two writes remain independent transactions; ApplicationAPI does not
  provide cross-coordinator atomicity;
- the handler needs a documented composite-result convention and the CLI
  needs explicit status-to-exit-code mapping; and
- the partial-success result must remain observable rather than being
  flattened into a generic gateway failure.

### B. Two sequential commands in the CLI

Submit terminal close and then a `coordination.session.close` command.

Advantages:

- each handler remains a single-service call;
- each sub-operation has its own independent gateway outcome.

Costs:

- requires two authorizer submissions, two idempotency keys, and CLI-side
  compensation/reporting logic;
- reproduces the same non-atomic failure window while scattering its
  semantics across the CLI;
- changes the established single composite duty receipt unless the CLI
  reassembles it; and
- risks a visible behavior drift if the second command fails after the first
  succeeds.

This is a valid general orchestration approach, but it is inferior here
because the project already has a dedicated composite handler whose job is
precisely this two-step, retryable operation.

### C. New durable workflow/saga coordinator

A persisted workflow could record progress and make retries explicit.
That would be appropriate if recovery of partially completed close actions
needed autonomous background processing or cross-process observability.
It is materially beyond R4/P4b routing: it adds persistent protocol state
and does not make the two existing database transactions atomic.  It should
not be introduced merely to close this gateway gap.

## Recommended implementation sequence

1. Add a lossless duty-lease result encoder used by heartbeat, close, and
   sweep.  Add compatibility tests that assert all nine current direct CLI
   fields, including nested close and sweep lease receipts.
2. Add `TerminalClaimCommand` and its descriptor.  Thread
   `heartbeat_timeout_ms` through a backward-compatible
   `TerminalDutyService.claim_terminal_duty` extension; test a non-default
   timeout, not only the default.
3. Route claim, heartbeat, and sweep through `_cli_submission()` and
   `_submit_via_gateway()`, retaining their current human and JSON output.
4. Route close through its existing composite command.  Preserve flat JSON
   for normal close; preserve composite JSON for `--close-session`; return
   2 on `session_close.status == "failed"`; and test the retry/partial
   success case.
5. Add one happy-path gateway CLI test and one monkeypatched
   `RequestContext` asserted-client-mismatch rejection test for each action.
   Include claim with a non-default timeout, sweep with its defaulted and
   explicit string metadata, close without a session, close with a session,
   and duty-succeeds/session-fails.
6. Update the four `duty` mutating entries in
   `docs/design/peerhub-production-call-map-R1.json` to
   `"gateway": "APPLICATION_API_GATEWAY"` and
   `"authorizer_behavior": "ASSERTED_ONLY"`.  `duty status` remains a
   direct read composition and is outside this migration.

With those bounded extensions, all four actions migrate now.  Without the
lossless encoder, `claim` timeout propagation, and composite-close exit
handling, they should remain direct: each omission would be a real behavior
or receipt regression rather than an acceptable implementation detail.
