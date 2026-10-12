# PeerHub D-CTX — Round-1 Independent Proposal (Voice 1)

**Status:** PROPOSAL ONLY — independent Round-1 voice 1 of an eventual 2+; no implementation is authorized by this document
**Date:** 2026-09-13
**Author:** cx.deepthink
**Scope:** dispatch context, governance-write provenance, compatibility, threat model, rollout, and effort/risk

---

## 0. Recommendation in one paragraph

Adopt an **attempt-scoped dispatch credential**, but do not put its bearer token in a collection of environment variables. Make a protected, ephemeral context file the authoritative carrier; expose only non-secret hints and its path through a centrally composed environment, and append the same path in a PeerHub-owned prompt trailer as the measured fallback for CLIs that scrub unknown variables. At the application boundary, resolve caller-entered identity into a trusted `GovernanceWriteContext`; persist that context on the immutable mutation request and project a safe provenance summary onto votes and acknowledgements. Keep flag-only calls temporarily as `ASSERTED`, but never call an OS-account observation a verified peer identity. Snapshot a provenance requirement into each consensus round and move high-risk rounds to `DISPATCH_VERIFIED` after the carrier has passed per-CLI canaries.

The important distinctions are:

1. **Context transport is not identity verification.** Environment variables and a context file merely carry a credential.
2. **Identity verification is not authorization.** A credential bound to `cx` must not automatically authorize every governance method.
3. **OS-account verification is not peer or human verification.** All three peer processes run under the same local account.
4. **`CommandEnvelope.actor_id` is a claim, not proof.** Activating `ApplicationAPI` without adding a trusted proof channel would preserve the defect behind a typed facade.

---

## 1. Re-verification and new findings

Evidence labels in this document are:

- `[code]`: read directly from the current checkout.
- `[empirical_probe]`: observed by a command run for this review.
- `[probe_blocked]`: a valid probe was attempted but could not reach a model turn; it is neither pass nor fail.
- `[design_estimate]`: a forward-looking sizing or risk judgment, not a measured fact.

### 1.1 The reported integrity gap is present

| Finding | Current evidence |
|---|---|
| All three production adapters provide no dispatch context | `peerhub/adapters/agy_adapter.py:229`, `claude_adapter.py:279`, and `codex_adapter.py:485` each construct `InvocationPlan(..., environment_delta={}, ...)` `[code]`. |
| A nonempty delta would currently replace the child environment | `peerhub/application/workflows.py:896-898` copies the delta directly into `PipeRunnerConfig.env`; `peerhub/dispatch/pipe.py:403-409` passes it to `subprocess.Popen(env=...)`. In Python, a non-`None` `env` is the complete child environment, not a patch. The first naive D-CTX implementation would therefore risk dropping PATH, auth/config locations, and proxy/runtime settings `[code]`. |
| The command envelope has identity-shaped data but no proof | `CommandEnvelope` at `peerhub/core/protocol.py:382-404` contains protocol/schema IDs, request/correlation/client IDs, nullable `actor_id`, `scope`, method/params, idempotency, revisions, and timestamp. It has no authentication or provenance member `[code]`. |
| The typed boundary is built but bypassed | `ApplicationAPI` is instantiated at `peerhub/runtime.py:371`; there is no `Client(...)` instantiation under `peerhub/`. The only production call to its `submit` method is inside `Client` itself. Direct ask and broadcast construct envelopes but call `application_workflows.admit_request` directly (`direct_ask.py:557-590`, `broadcast.py:239-278`) `[code]`. |
| The typed boundary would not fix actor spoofing as written | `ApplicationAPI.submit` explicitly describes its auth as a skeleton and checks only `caller.client_id == cmd.submission.client_id` at `application/api.py:3036-3037`. Consensus decoding also accepts separate actor/proposer strings from `params`; it does not bind them to `envelope.actor_id` or `caller.principal` `[code]`. |
| Governance CLI paths bypass both typed boundary and caller identity | `_run_consensus` constructs `ConsensusService` directly at `cli.py:800`; propose and vote forward `parsed.proposer`/`parsed.actor` at `cli.py:859-881`. Task and room handlers follow the same direct-service pattern at `cli.py:995` and `cli.py:2153` `[code]`. |
| Consensus validates eligibility, not identity | `ConsensusService.cast_vote` only checks that the supplied `actor_id` is in the eligible sequence (`governance/consensus.py:516-520`), then keys the vote by that string (`:529`) `[code]`. |
| The durable generic write record cannot distinguish proof quality | `MutationRequest` has one required `actor_id` and no provenance (`governance/contract.py:145-160`); `mutation_requests` persists that string (`persistence/sqlite_governance.py:227-255`) `[code]`. |
| Local caller identity proves only an OS account | `LocalProcessCallerIdentityProvider` obtains the current process owner and returns `local-cli:<account>` (`core/identity.py:47-66`). This is useful audit evidence but cannot distinguish `cc`, `cx`, `ag`, or an interactive human under the same account `[code]`. |

I also reproduced the reported end-to-end failure in a fresh disposable workspace. From one PowerShell process I proposed a round requiring `cc,cx`, cast `agree` as `cc`, then cast `agree` as `cx`. All three commands exited 0; the second vote returned `phase="quorum_reached"`, `counted_votes=2`, `required_votes=2` `[empirical_probe]`.

### 1.2 The context source is not yet durable enough to bind correctly

The current direct-ask request already has an optional `room_id` (`application/direct_ask.py:79`), but its admitted `CommandEnvelope.scope` contains only `workspace_root` (`:565`). The broadcast path does the same (`application/broadcast.py:247`). Therefore a D-CTX issuer cannot derive the room from the immutable admitted request today; accepting a later function argument would recreate the same caller-assertion problem one layer down `[code]`.

There is also no single current object named “dispatch ID.” The durable identity is a tuple: server `command_id`, `attempt_id`, session `lease_id`, and `capability_lease_id`. The selected peer identity is likewise two fields, `selected_peer_instance_id` and `selected_profile_id`. A design that emits one ambiguous `PEERHUB_DISPATCH_ID` and one ambiguous `PEERHUB_PEER` would discard useful distinctions already present in the model `[code]`.

Finally, the current capability tier is only `READ_ONLY`, `WORKTREE_WRITE`, `GIT_MUTATE`, or `REMOTE_MUTATE` (`dispatch/capability.py:21-27`). It describes downstream effect authority, not governance authority. A `READ_ONLY` analysis peer may legitimately need to cast one vote, while a `WORKTREE_WRITE` peer should not thereby gain authority to create rooms, assign roles, or alter leadership. Governance grants must therefore be a separate axis `[code + design conclusion]`.

### 1.3 CLI environment inheritance remains unmeasured

The installed binaries during this review were Codex CLI 0.154.0, Claude Code 2.1.268, and Agy 1.2.2 `[cli_live]`. I attempted the same model-mediated probe against each: set a unique parent variable, ask the model to invoke its shell tool once, and return the value observed by that tool process.

- Codex and Claude initialized but could not reach a model turn because provider connections were blocked in this execution environment.
- Agy stopped at its eligibility check because the configured dead proxy refused the connection.

No shell tool ran, so all three results are `[probe_blocked]`, not `PASS`, `FAIL`, or evidence of scrubbing. This agrees with `docs/adapters/checkpoints/codex.md:537-569`, where the related Codex Windows child-environment checkpoint remains `TEST NEEDED`. D-CTX should not make correctness depend on a future favorable result.

---

## 2. Proposed model

### 2.1 Three distinct objects

#### A. `DispatchContextCredential` — durable issuer-side authority

Create one credential per dispatch **attempt**, after an attempt exists and before the process is spawned. Persist an immutable credential record with at least:

```text
credential_id
token_sha256
command_id
attempt_id
session_lease_id
capability_lease_id
workspace_home_id
canonical_workspace_root_digest
room_id? / task_id? / consensus_round_id?
selected_peer_kind
selected_peer_instance_id
selected_profile_id
governance_grants[]
issued_at
expires_at?
revoked_at?
policy_revision
```

`token_sha256` is sufficient for a uniformly random 256-bit bearer token; no reversible token is stored. The hash input should be domain-separated and include `credential_id`, but no slow password hash is needed for a high-entropy random value. The credential is valid only while all of these are true:

- its token matches;
- its command/attempt/capability/session bindings still match current durable records;
- the attempt is the currently authorized attempt;
- the session lease is active/renewed and not fenced, released, expired, or ownership-lost;
- the credential is not revoked and is inside any explicit expiry;
- the requested workspace, room/resource target, method, and actor match the frozen binding/grants.

Retries receive new credentials and new files. A credential from attempt 1 must not authenticate attempt 2. Resume is also a new dispatch attempt, not a session-long bearer authority.

#### B. `DispatchContextFile` — ephemeral carrier

Write an attempt-unique file such as:

```text
<workspace>/.peerhub/run/contexts/<attempt_id>/<credential_id>.json
```

Its bounded v1 payload contains the clear bearer token plus the non-secret credential fields needed by the CLI:

```json
{
  "schema": "peerhub.dispatch-context/v1",
  "credential_id": "...",
  "token": "base64url-256-bit-value",
  "workspace_home_id": "...",
  "workspace_root": "...",
  "room_id": null,
  "command_id": "...",
  "attempt_id": "...",
  "session_lease_id": "...",
  "capability_lease_id": "...",
  "peer_kind": "cx",
  "peer_instance_id": "cx",
  "profile_id": "cx.deepthink",
  "governance_grants": [],
  "issued_at": 0,
  "expires_at": null
}
```

Creation must be same-filesystem, exclusive/no-follow, atomic, and owner-only: mode 0600 on POSIX and an explicit current-user/SYSTEM DACL on Windows. Parent directories must reject symlinks/reparse-point redirection. The file is deleted after the terminal attempt state and bounded janitor cleanup removes verified-stale remnants. The database retains only the hash and safe metadata.

The token must never appear in `redacted_display`, command envelopes, argv, prompts, stdout/JSON results, telemetry, exception details, mutation state, or transition receipts. The file **path** is not a secret and may appear in the trusted prompt trailer.

#### C. `GovernanceWriteContext` — resolved, safe service input

The application layer converts a credential presentation or legacy/local call into an immutable context:

```text
effective_actor_id
claimed_actor_id?
authenticated_principal_id?
principal_evidence      = DISPATCH_CAPABILITY | LOCAL_OS_ACCOUNT | TRUSTED_SYSTEM | NONE | LEGACY
actor_binding           = VERIFIED | ASSERTED | NOT_APPLICABLE | UNKNOWN
credential_id?
command_id? / attempt_id? / lease_id? / capability_lease_id?
workspace_home_id
room_id?
governance_grants[]
verified_at
verification_policy_revision
```

Only this resolved object, not a raw string flag, crosses into a mutating governance service. For a valid dispatch credential, `effective_actor_id` is the frozen `selected_peer_instance_id`; `profile_id` is recorded separately and never substituted for it. An explicit `--actor`, `--voter`, `--proposer`, `--creator`, instance/profile pair, workspace, or room may be absent or equal the credential binding. Any contradiction is rejected. **Explicit flags do not override verified context.**

For a flag-only call, the peer actor remains `ASSERTED`. If the OS process owner is available, record it as `authenticated_principal_id` with `LOCAL_OS_ACCOUNT`; do not upgrade `actor_binding`. A peer-spawned shell and a human terminal have the same OS owner, so the evidence cannot prove human presence.

### 2.2 Governance grants are independent of filesystem capability tiers

A dispatch credential should carry an exact allowlist of method/resource grants, for example:

```text
consensus.vote:<round_id>
consensus.final_call_ack:<round_id>
room.message:<room_id>
task.checkpoint:<task_id>
```

Administrative methods such as consensus creation, task/room creation, role assignment, leadership mutation, lesson activation, quarantine, and recovery require explicit grants or a trusted system/local administrative route. A generic `peerhub ask` should not receive all governance grants merely because it has a token. The grant set must be derived from immutable admitted scope and dispatch purpose, frozen before spawn, and included in the credential digest.

This requires admission scope to carry optional room/task/round bindings now omitted by direct ask and broadcast. Those fields should enter the server-minted request snapshot before credential issuance; they must not be accepted only as late execution parameters.

---

## 3. Propagation and resolution

### 3.1 Environment: hints and locator, not the secret

The process receives these centrally protected variables:

| Variable | Meaning |
|---|---|
| `PEERHUB_CONTEXT_FILE` | Absolute path to the authoritative attempt context file. |
| `PEERHUB_WORKSPACE` | Canonical absolute workspace root; convenience only until credential verification. |
| `PEERHUB_WORKSPACE_ID` | Persistent `workspace_home_id`. |
| `PEERHUB_ROOM_ID` | Optional credential-bound room. |
| `PEERHUB_COMMAND_ID` | Server-minted command ID. |
| `PEERHUB_DISPATCH_ID` | Defined, not invented: exactly the current `attempt_id`. |
| `PEERHUB_LEASE_ID` | Current session lease ID. |
| `PEERHUB_CAPABILITY_LEASE_ID` | Current capability lease ID. |
| `PEERHUB_PEER_INSTANCE_ID` | Selected peer instance, the governance actor identity. |
| `PEERHUB_PROFILE_ID` | Selected profile, recorded separately from actor identity. |

Do not emit `PEERHUB_DISPATCH_TOKEN`. Putting the same bearer secret into both a file and environment increases leak surfaces (environment dumps, child diagnostics, crash reports) without solving the unknown-variable-scrubbing problem. If a compatibility alias `PEERHUB_PEER` is later desired, define it as `PEERHUB_PEER_INSTANCE_ID`; never let it ambiguously mean profile on one adapter and instance on another.

Adapters must not populate these fields. A general dispatch-context composer should combine authoritative request/attempt/lease data **after routing**. Conflicting adapter/caller values for the listed D-CTX keys are rejected; inherited values for those keys are stripped before the authoritative child values are overlaid, which also gives a nested dispatch its own context without inheriting its parent's credential hints.

The current `environment_delta` plumbing must be corrected at the same time. Build a complete child environment from one frozen/sanitized parent snapshot, overlay adapter-approved deltas, then overlay protected D-CTX hints. Pass that complete map to `Popen`. The readiness probe and real dispatch must use the same environment-policy revision; otherwise a readiness result would authorize an environment different from the one actually spawned.

### 3.2 File fallback and prompt trailer

Add a trusted, PeerHub-generated trailer after user content (or after the staged-prompt pointer):

```text
[PEERHUB DISPATCH CONTEXT]
Authoritative context file: <absolute path>
For PeerHub governance commands, allow PEERHUB_CONTEXT_FILE to resolve it.
If that variable is unavailable in a tool subprocess, pass:
  peerhub --context "<absolute path>" ...
Never print or copy the file contents.
```

The exact wording is less important than provenance: it must be composed by PeerHub outside user-supplied text and carry an attempt-unique path. To make the attempt ID available, attempt creation must occur before final prompt rendering/invocation planning. A planning failure after attempt creation must durably close that attempt as pre-dispatch failed rather than leave a reserved credential.

Until each CLI passes the environment canary, both locator paths are mandatory:

1. global `--context <path>` explicitly supplied in the model's tool command;
2. `PEERHUB_CONTEXT_FILE` implicitly read by the CLI.

No directory scan should guess the “current” context file. Concurrent dispatches make a well-known singleton file unsafe. If an explicitly selected or environment-selected context is malformed, stale, mismatched, or unverifiable, the CLI fails closed; it must not silently downgrade to asserted flags. A human can deliberately opt out by starting a separate flag-only command with no context selection.

### 3.3 Workspace bootstrap without circular trust

The CLI must read the small context file before choosing its workspace database. The file supplies a candidate root; the CLI canonicalizes it, verifies that the context file is beneath that root's owned run directory, opens that root's PeerHub database, checks the persistent `workspace_home_id`, and verifies the credential hash/bindings there. Only then is the root trusted. A simultaneous explicit `--workspace` must resolve to the same filesystem identity or the call is rejected.

This makes D-CTX compatible with the separately deferred P10 workspace-discovery decision without depending on P10. D-CTX context resolution is a credential-validation path, not a new general cwd/ancestor heuristic.

---

## 4. Governance write recording and enforcement

### 4.1 Canonical record

Add a required, typed `WriteProvenance` member to `MutationRequest` and include it in `mutation_payload()` / the idempotency digest. Persist its canonical JSON (plus an indexed assurance/binding column if query performance requires it) in `mutation_requests`. Historical rows are migrated to `principal_evidence=LEGACY`, `actor_binding=UNKNOWN`; they must never be retroactively described as asserted or verified.

Changing provenance while reusing an idempotency key must produce an idempotency-payload mismatch. Otherwise a verified retry could accidentally receive the receipt of an earlier asserted mutation, or vice versa.

The immutable mutation request is the provenance SSOT. Domain target state carries only the safe projection needed to interpret current state without reconstructing the ledger:

- every consensus vote and Final Call acknowledgement: actor binding, evidence class, credential ID, attempt ID, and mutation request/provenance reference;
- consensus proposal: proposer provenance and a frozen `vote_provenance_policy`;
- message/reaction/claim records: corresponding actor provenance summary;
- ordinary revisioned targets: `last_write_provenance_ref`, while full history remains in mutation requests.

Do not store the bearer token or token hash on a vote. `credential_id` is an audit reference; verification details remain in the credential and mutation ledgers.

### 4.2 Service boundary

Replace mutating service arguments such as `actor_id`, `creator_id`, `proposer_id`, and `voter` with `GovernanceWriteContext` (or accept both only in a short internal deprecation window). Services derive the effective actor from that context and project provenance into state. The generic governance broker rejects mutations lacking provenance, giving all 21 current production `MutationRequest(...)` construction sites a compile/test-visible migration obligation rather than leaving partial coverage to convention.

System-generated mutations use a distinct `TRUSTED_SYSTEM` context with a named subsystem principal and reason; they do not forge a peer dispatch credential. Read-only operations need no write provenance.

### 4.3 Consensus counting

At round creation, freeze a policy such as:

```json
{
  "schema": "peerhub.consensus-provenance-policy/v1",
  "minimum_actor_binding": "VERIFIED",
  "allowed_principal_evidence": ["DISPATCH_CAPABILITY"],
  "allow_tier0_override": true,
  "policy_revision": "..."
}
```

Quorum calculation counts a vote only when all existing eligibility/choice rules pass **and** its projected provenance satisfies this frozen policy. A denied credential presentation is rejected before mutation and written to a separate bounded security/audit event; it must not create an uncounted pseudo-vote that changes the round revision.

Human override remains a separate Tier-0 action with explicit provenance and reason. `LOCAL_OS_ACCOUNT` plus `--actor cx` is not a human override and is never a verified `cx` vote.

### 4.4 Role of `ApplicationAPI` and `Client`

Routing writes through the existing typed boundary is desirable consolidation, but it is neither necessary nor sufficient for the first integrity increment:

- Not sufficient: `actor_id`, `proposer_id`, and `voter` are currently caller data, and `ApplicationAPI` does not bind them to an authenticated subject.
- Not necessary: a shared application-layer `GovernanceWriteGateway` can resolve credentials and produce `GovernanceWriteContext` for current direct CLI handlers before a broad CLI migration.

Recommended sequence:

1. Introduce one credential/provenance resolver and require it at mutating services/broker.
2. Make both direct CLI handlers and `ApplicationAPI` descriptors call that same gateway.
3. Then migrate governance CLI commands to real `Client(runtime.application_api)` production calls and remove duplicate identity fields from command params. During transition, if both `CommandEnvelope.actor_id` and a legacy params actor exist, both are claims and must match the resolved actor.

The bearer presentation stays out-of-band in an authenticated submission context, analogous to an Authorization header; it is never serialized into `CommandEnvelope.params`. `CommandEnvelope.actor_id` may remain as an optional claimed/display identity, but authorization uses the resolved context.

This sequence closes the gap without making D-CTX depend on converting the entire roughly 4,500-line CLI at once, while still giving the currently orphaned boundary a real destination.

---

## 5. Compatibility and rollout

### Increment 0 — provenance ledger, behavior unchanged

- Add `WriteProvenance`, durable mutation-request storage, safe state projections, and historical `LEGACY/UNKNOWN` migration.
- Resolve OS process owner on existing local CLI writes, but record peer flags as `ASSERTED`.
- Display provenance in JSON/status diagnostics; do not change quorum counting yet.
- Add denied/mismatch audit events and secret-redaction tests.

This is additive and immediately removes the “no record of which vote was real” forensic gap, but it does **not** prevent impersonation. It should be described as auditability, not security closure.

### Increment 1 — credential carrier and verified consensus path

- Add attempt credential persistence, secure context-file lifecycle, protected environment composition, and prompt locator trailer.
- Bind room/task/round scope into admission and introduce separate governance grants.
- Make identity/workspace/room flags optional when valid context is present; reject every explicit contradiction.
- Support verified native votes, legacy-compatible proposal votes, and Final Call acknowledgements first because they directly determine quorum.
- Add an opt-in `verified_required` round policy and run the two-vote impersonation regression: the second actor claim must fail with the first credential, and no quorum is reached.

This is the minimum useful integrity increment. Merely setting environment variables is not.

### Increment 2 — high-risk enforcement

- After file fallback works for all real adapters and per-CLI environment results are recorded, make high-risk rounds freeze `DISPATCH_VERIFIED` as their default minimum.
- Asserted calls may remain available for normal-risk compatibility but are visibly lower trust and cannot satisfy high-risk quorum.
- Add explicit Tier-0 override semantics rather than treating any local OS account as a human.

### Increment 3 — full governance convergence

- Extend the gateway/context requirement to task, room, lesson, role, leadership, duty, recovery, file-lock, artifact, feedback, and operational-error mutations.
- Migrate CLI governance handlers into `Client/ApplicationAPI`; delete redundant param-level actor fields.
- Decide separately, with migration telemetry, whether asserted mutations remain supported, require `--allow-asserted-actor`, or are removed in the next major version.

Existing flag syntax can therefore remain initially. Context-present calls get safer and shorter; context-absent calls remain explicit and lower-trust. The only immediate behavior break should be deliberate and narrow: an invalid/stale selected context or a claim contradicting valid context fails instead of downgrading.

---

## 6. Required tests and acceptance evidence

### Carrier matrix

For each supported version/profile of Claude Code, Codex, and Agy, a real model turn must invoke the actual shell/tool subprocess and report a random marker that appeared only in the parent environment. Record binary version, platform, sandbox/profile, command, tool invoked, observed value, exit status, and timestamp. A model repeating a value present in the prompt is invalid evidence; the marker must not appear in the prompt.

Also prove the file fallback independently by removing the variable from the tool subprocess and invoking `peerhub --context <path> ...`. The direct attempts in this review remain `probe_blocked` and do not satisfy this gate.

### Security/integrity cases

- valid credential + omitted actor succeeds as its bound peer;
- valid credential + matching explicit actor succeeds;
- valid credential + different actor/profile/workspace/room/resource fails before mutation;
- wrong, malformed, expired, revoked, released, fenced, prior-attempt, and wrong-workspace tokens fail closed;
- `READ_ONLY` plus an exact vote grant can vote but cannot create a room/task or mutate a different round;
- a worktree-write capability without a governance grant cannot govern;
- asserted flag-only vote is recorded as asserted under compatibility policy and cannot count under verified policy;
- `LOCAL_OS_ACCOUNT` never upgrades a peer actor to verified;
- duplicate vote from two valid attempts of the same peer remains one peer vote while preserving both mutation attempts/audit history;
- provenance participates in idempotency hashing;
- tokens never appear in logs, CLI JSON, receipts, target state, transcripts, process display, or test failure snapshots;
- historical rows read as `LEGACY/UNKNOWN`;
- concurrent dispatch files never collide, and janitor cleanup cannot follow links/reparse points outside the run directory.

### Environment regression cases

- a nonempty context delta preserves the approved PATH/auth/config/proxy environment;
- the listed protected D-CTX keys from parent/caller/adapter cannot override authoritative values;
- readiness and dispatch record the same environment-policy revision;
- spawn/planning/materialization failure revokes the credential and cleans the file;
- retry rotates token, path, attempt binding, and grants.

---

## 7. Threat model

### Defended within this proposal

- accidental actor mix-ups caused by prose, copied commands, defaults, or multiple peer identities in one prompt;
- stale credentials from completed/fenced attempts;
- cross-workspace and cross-room accidental writes;
- one dispatched peer using its own credential to claim another peer identity;
- bearer-token overreach beyond frozen governance methods/resources;
- loss of forensic provenance on committed writes;
- silent downgrade from a bad credential to a caller-asserted flag.

### Explicitly not defended

- a hostile process running as the same OS user that can read another live process's files/environment/memory or the local SQLite database;
- a malicious administrator or repository owner rewriting the SQLite ledger;
- model/prompt compromise that exfiltrates its own live bearer token;
- vendor CLI compromise;
- cryptographic non-repudiation by a remote model provider.

The file carrier narrows casual leakage compared with an environment token and improves reliability, but its Windows DACL/POSIX mode is an **account boundary**, not a same-account process boundary. The token should therefore be called `DISPATCH_VERIFIED`, not “peer-authenticated” or “hostile-actor secure.”

### Stronger option, deferred

Hostile same-user resistance would require an OS-mediated broker: for example, governance writes over a local named pipe/socket whose server verifies the client process belongs to the dispatch's job/cgroup/process tree, or isolated per-peer OS identities/containers with per-principal signing keys. Windows named-pipe client PID plus Job Object membership could raise the bar, but ancestry races, handle inheritance, cross-platform parity, process injection, and recovery substantially increase complexity. Per-peer OS accounts or remote hardware-backed signing are stronger still and operationally much heavier.

I do not recommend that cost for D-CTX v1. Accept the accidental-confusion scope, minimize token lifetime/grants, and leave a versioned authentication-provider seam so a stronger verifier can later produce the same `GovernanceWriteContext` without changing service semantics or stored provenance shape.

---

## 8. Effort and risk

Measured surface: the current package has 21 production `MutationRequest(...)` construction sites, 20 direct broker-submit sites, 76 application command descriptors, zero production `Client(...)` instantiations, three real adapters, one common execution workflow, and one SQLite migration chain `[code count, 2026-09-13]`.

| Increment | Effort | Primary risk | Assessment |
|---|---|---|---|
| Increment 0: provenance ledger | Medium | Broad schema/constructor churn; accidentally omitting one write path | `[design_estimate]` Mechanically broad but conceptually contained. Requiring provenance in `MutationRequest` is the best completeness backstop. |
| Increment 1: credential + consensus path | Large | Attempt lifecycle ordering, token cleanup/redaction, workspace bootstrap, retry fencing, prompt composition, child-environment regression | `[design_estimate]` Highest design/test concentration; should ship separately from API/CLI restructuring. |
| Increment 2: high-risk policy | Medium, high consequence | Locking out real voters or counting downgraded votes | `[design_estimate]` Small code relative to Increment 1 but security-critical rollout. Requires live canaries and an explicit escape/override design. |
| Increment 3: all governance + typed-boundary migration | Large | Behavioral drift across many legacy-compatible commands and duplicated actor fields | `[design_estimate]` Valuable consolidation, not a blocker for the first consensus-integrity closure. |

Overall implementation risk is **high** because errors can either manufacture authority or deadlock governance. The safest delivery shape is four separately reviewable commits/releases with migration, redaction, and rollback tests at each boundary—not one D-CTX/CLI-refactor batch. Calendar estimates would be guesswork without an implementation plan and baseline test timings, so this proposal intentionally reports measured surface and relative effort rather than invented days.

---

## 9. Ratification decisions requested from the later dialectical pass

1. Ratify the context file as authoritative and environment variables as non-secret hints, or supply measured evidence strong enough to justify environment-only transport.
2. Ratify `selected_peer_instance_id` as the effective governance actor and `selected_profile_id` as separate provenance.
3. Ratify a separate governance-grant axis rather than deriving governance authority from filesystem capability tier.
4. Ratify that local OS ownership is audit evidence only and cannot satisfy verified peer quorum or Tier-0 human override by itself.
5. Ratify the compatibility sequence: legacy/flag-only writes remain `ASSERTED`, high-risk rounds become verified-first only after canaries, and invalid selected context never silently downgrades.
6. Ratify the application sequence: shared gateway first, production `Client/ApplicationAPI` migration second, with all credential material out of band from `CommandEnvelope`.

Until those decisions receive the required independent second proposal and ratifying pass, this document authorizes no code or policy changes.
