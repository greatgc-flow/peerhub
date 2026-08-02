# peerhub — Phase 0 Compatibility Inventory and Freeze Draft

> Status: **draft; not yet frozen.** This is the first separately-authorized implementation artifact for `peerhub`. It authorizes observation, characterization, and contract review only. It does **not** authorize package source code, a database schema, or a cut-over in Engram.
>
> Baselines: `peerhub` `main` at `9cc5acc`; Engram's current `_sys/core/hub.py`, `_sys/core/hub_peer.py`, and `_sys/ai`/`.ai` state. The source-of-truth remains Engram until the Phase 6 strangler cut-over proves parity and rollback.

### Baseline binding and drift gate

This draft is bound to Engram commit `84fcb53e4cd74b8f8b6d69f9537d968992ef4cbb` and to the following worktree bytes, not merely a branch name:

| Input | SHA-256 |
|---|---|
| `_sys/core/hub.py` | `1a9f9c4a393989401aefeb60dbb3e24e8063ca558f1fcb595fb882a635a8d0b8` |
| `_sys/core/hub_peer.py` | `9639bbd549643674fade3cb94798ec5b503eb74d6e0bfa8d69b55b57df527cba` |
| `_sys/ai/protocol.json` | `d9eea98bae3ad726d54e6ae197a070b67dfeaf7fe8051d2fdd4c931c79b97d70` |
| `_sys/ai/orchestration.json` | `4165df0650f17747a867e0781c4d09596c17dac5b47a383242610aee3d7bb9e5` |
| `_sys/ai/model-registry.json` | `dad5c8ae4f47f638dc5cba28bf87c0d439fd7cf5bb9dfe63204f5b295dd192b6` |
| `_sys/ai/routing-config.json` | `520629e66625f2f1a2a10dbee224a1e15a42770f4ebb223b89267eb07e124901` |
| `_sys/ai/status_checks.json` | `1204e4b2518d17492aeba0e3d85edc601c2c5a84d9992fac049626d5c86d0f9a` |
| `_sys/ai/capability-declarations.json` | `9b26a6ff7c1671bcae06a969ec28cdcec5ca3f4f0026ecad860341a47e054c2c` |
| Ordered 90-action vector (`LF`-joined, trailing `LF`) | `2065c0b6de16cc39224bd3d364199383c2f625c1a6564e642fc853b76d76196d` |

The Phase 0 drift gate recomputes these digests before every characterization run and before completion. A changed action vector, CLI argument/public-output schema, Hub/adapter source, or listed configuration input invalidates affected transcripts and blocks Phase 0 completion. The inventory must then be regenerated and the changed contract re-reviewed; a branch move, a matching action count, or a passing unrelated test is not an override.

The protocol-document hash is an independent design-ratification artifact; it does **not** subsume any of these source/configuration bytes. PeerHub keeps three separately auditable revisions:

- **characterization build revision:** the pinned `hub.py`/`hub_peer.py` file digests and action/public-CLI schema digest. It says which legacy behavior transcripts characterize.
- **runtime configuration revision:** individually hashed protocol, orchestration, model-registry, routing, status-check, and capability-declaration inputs, followed by the canonical imported `PeerProfileBinding`/policy record digest. It says which desired configuration a decision used.
- **adapter/readiness revision:** adapter descriptor/parser digest, configured executable reference/fingerprint, transport limit, and readiness receipt digest. It says which runtime boundary was proved for an attempt.

At PeerHub startup, runtime configuration is sealed into its immutable configuration revision. Every command/attempt/round records all applicable revisions. A changed revision before a side effect is a deterministic `CONFIGURATION_STALE` result; the command may be re-planned, but it may not proceed by silently rereading a mixture of old and new configuration.

## 1. Migration objective and non-goals

`peerhub` will become the installable coordination package that owns the behavior now distributed through Engram's `hub.py`/`hub_peer.py`: dispatch, provider adaptation, routing, consensus, health/quarantine, session/lease lifecycle, rooms/mailboxes/handoff, IPC/audit, and governed mutations.

The migration is a strangler, not a rewrite-and-switch:

1. characterize existing behavior without changing it;
2. build `peerhub` behind its own tested contracts;
3. shadow existing decisions and compare evidence;
4. move one ownership boundary at a time with an explicit rollback path; and
5. retire an Engram path only after behavior, recovery, and evidence parity.

`peerhub` does not install, update, authenticate, bundle, or self-update vendor CLIs. Engram (or another host) supplies configured executable paths. There is no second live writer for a record during migration.

## 2. Compatibility scope: current public command inventory

The current `hub` CLI accepts 90 action names. Their exact draft disposition is in [`phase0/hub-actions-v1.csv`](phase0/hub-actions-v1.csv), whose action column is checked against the baseline vector above (90 rows, no duplicates, no missing action). The grouped map below is navigation; every `v1` command still needs its own golden transcript before the freeze.

| Domain | Existing actions | Target PeerHub boundary | Phase 0 disposition |
|---|---|---|---|
| Dispatch | `ask`, `ask-all`, `ask-coordinator` | `dispatch` + `application.api` | **v1 critical**; pipe/PTY/session/error/output compatibility is characterized first. |
| Session and lease | `init-session`, `end-session`, `lease-status`, `lease-sweep` | `dispatch` state plus coordination scope | **v1 critical**; owner-aware lease and resume behavior. |
| Coordination | `send`, `broadcast`, `mark-read`, `new-topic`, `clear-room`, `checkpoint`, `append-handoff`, `terminal-handoff`, `terminal-heartbeat`, `terminal-close`, `terminal-duty-sweep`, `context-fill` | `coordination` | **v1 critical**; this is a real Hub responsibility, not transport glue. |
| Routing/discovery | `discover`, `elect-leader`, `leader-yield`, `leader-claim`, `health-precheck`, `peer-status`, `model-status`, `profile-validate`, `freshness-sweep` | `routing`, `health`, `telemetry`, adapter readiness | **v1 critical**, with read-only/shadow decisions before routing cut-over. |
| Health/recovery | `health-update`, `health-check`, `health-sweep`, `peer-quarantine`, `peer-recover`, `report-error`, `transient-scan` | `health` + `telemetry` | **v1 critical**; recovery must authorize a probe, never manufacture GREEN/healthy evidence. |
| Consensus | `consensus-propose`, `consensus-vote`, `consensus-check`, `consensus-sweep`, `arbiter-review` | `consensus` | **v1 critical**; frozen electorate and immutable vote semantics. |
| Governed mutations | `broker-submit`, `broker-drain`, `broker-status`, `approval-request`, `file-lock`, `file-unlock`, `lock-status`, `artifact-claim`, `artifact-status`, `artifact-finalize` | `governance` + `state` | **v1 critical**; CAS, journal/effect separation, authorization, and recovery. |
| Proposals/feedback/lessons | `proposal-add`, `proposal-vote`, `proposal-list`, `feedback-add`, `feedback-list`, `feedback-resolve`, `lessons-list`, `lessons-propose`, `lessons-activate`, `lessons-retire`, `lesson-broadcast`, `lesson-sweep`, `lesson-inject`, `directive-add`, `directive-list`, `directive-clear` | `governance` and derived coordination projections | **v1 critical** for dedup/idempotency; existing directive policy may remain host-owned during the first slice. |
| Task/role/thread metadata | `task-checkpoint`, `task-status`, `task-failover`, `assign-role`, `release-role`, `role-status`, `thread-new`, `thread-append`, `thread-react`, `thread-promote`, `alert-raise` | `coordination` | **v1 compatibility**, sequenced after room/message core. |
| Diagnostics/administration | `status`, `check`, `check-gate`, `register-node`, `list-nodes`, `append-log`, `archive-file`, `update-status`, `preflight`, `context-hash`, `update-signatures`, `credit-status`, `credit-consume` | host integration / `ipc` / explicit extensions | **not silently dropped**. Each is classified as a PeerHub command, a host adapter command, or a deprecated compatibility wrapper before cut-over. Credit consume is human-authorized external action and remains opt-in. |

The final manifest must list every action individually, its typed v1 command name, mutability/effect tier, required idempotency key, state owner, success/error envelope, compatibility fixture, and retirement owner. Grouping here is navigation only.

### 2.1 Inventory completeness and audit procedure

Phase 0 completion must create a durable, reviewable inventory record, not
just maintain this CSV manually:

1. Extract the ordered `choices` vector from the pinned `hub.py` parser and
   canonicalize it as UTF-8, LF-delimited action names with a trailing LF.
2. Parse `phase0/hub-actions-v1.csv`; reject duplicate legacy actions,
   blank disposition/owner/target-command fields, and dispositions outside
   `required`, `compatibility-wrapper`, or an explicitly ratified
   `deprecated` value.
3. Perform a bidirectional exact-set comparison and ordered-vector digest
   comparison. A matching count is insufficient.
4. Capture `hub --help` and per-action argument-schema snapshots under the
   same characterization build revision; diff their normalized public flags,
   defaults, command names, exit-code/error behavior, and documented output
   fixtures.
5. Validate the required golden-fixture index in §4.4: every required domain
   has an enumerated fixture definition, a captured/redacted baseline
   transcript or explicit blocked-evidence record, and a digest. A fixture
   is not represented by a prose promise alone.
6. Join every action row through
   `phase0/action-fixture-policy-v1.csv`; require at least one known positive
   fixture ID and exactly one known invalid-input and authorization fixture
   ID for every action. Unknown/duplicate/uncovered domains fail validation.
7. Write a Phase 0 inventory receipt containing generation timestamp,
   Engram commit, all three revision classes, action vector digest, manifest
   digest, comparison result, fixture index/digests and validation result,
   and the tool/command
   version. Receipts are append-only audit evidence and are themselves
   covered by the digest-chain verification in §4.3.

Any failed comparison, unreadable baseline input, or action/schema not
represented by a fixture makes the compatibility set incomplete and blocks
Phase 1. The final receipt is an R:10 review input.

## 3. Current operational-state inventory

The current state has both durable workflow data and host-local observations. This table prevents a blind file-for-file migration. PeerHub's future SQLite records are authoritative only after an explicit import/cut-over; existing files remain authoritative until then.

| Current location | Current role | Planned disposition |
|---|---|---|
| `.ai/state.json`, `mailbox.json`, `nodes.json`, `task_registry.json`, `leases.json` | room/session/presence/lease/task coordination | Characterize shape and concurrent semantics; import under a one-time audited migration; no dual writer. |
| `.ai/ask_history.jsonl`, `operations.jsonl`, `operational_errors.jsonl`, `routing_metrics.jsonl` | append-only audit/telemetry | Preserve as provenance; map to immutable observations/outbox events with source digest and cursor. |
| `.ai/canary_budget.json`, `canary_cache.json`, `capability-reality.json`, `cli-reality-*.json`, `freshness_sweep_state.json`, `tool_discovery_cache.json` | provider/runtime observations and refresh controls | Host-provided evidence inputs first; import only facts with explicit ownership. PeerHub must not reinterpret absence as zero/healthy. |
| `_sys/ai/orchestration.json`, `model-registry.json`, `routing-config.json`, `status_checks.json`, `capability-declarations.json` | overlapping declarations/policy today | Characterize all writers and readers. A later governed import creates PeerHub's single operational `PeerProfileBinding`; files then become bootstrap/import inputs, not parallel live pins. |
| `_sys/ai/protocol.json`, `governance_params.json`, `lifecycle_policy.json`, `error-taxonomy.json`, `telemetry-config.json` | constitutional policy and host configuration | Freeze revision/digest into commands/rounds. Decide per fact whether PeerHub owns it or consumes it as host policy. |
| `_sys/ai/backlog.json`, `policy-decisions.json`, directives/lessons/proposals | human/governance records and projections | Characterize lifecycle/fingerprint rules. Do not bulk-import mutable projections as authoritative state. |

## 4. Required characterization transcripts

All transcripts are deterministic, secret-redacted fixtures. A fixture records command input, environment/profile revision, observable stdout/stderr/event sequence, exit/error code, state before/after digest, and process/lease evidence. It does not contain provider credentials or raw account data.

| Fixture family | Mandatory observations |
|---|---|
| Pipe dispatch | normal response, nonzero exit, spawn failure, output cap, hard deadline, cancellation, post-dispatch ambiguity, artifact input/output digest. |
| PTY dispatch | chunking/line normalization, silence deadline, graceful cancellation then tree termination, cleanup error attached to primary result, transcript/session evidence. |
| Session/lease | create, reuse, resume mismatch, two concurrent leases to one peer, independent renewal/close, stale sweep, process-birth identity mismatch. |
| Coordination | room create/end, direct message, broadcast, unread/read, checkpoint/handoff, terminal assignment/heartbeat/close, concurrent message ordering. |
| Consensus | propose, duplicate/idempotent vote, conflicting second vote, missing voter, unanimous finalization, dissent/rejection, timeout, frozen electorate/policy. |
| Health/routing | readiness success/failure/stale, quarantine/recover-probe/reopen, routing exclusions and deterministic draw, missing quota evidence, terminal exclusion. |
| Governance | request/plan/receipt, stale CAS, broker inbox duplicate import, effect success/failure/recovery, lock contention, proposal fingerprint dedup under concurrency. |
| CLI/JSONL | command validation, schema/version mismatch, stable error code, correlation/idempotency propagation, redaction and exit-code mapping. |

### 4.1 Observed baseline defect: short ask-ID collision

During Phase 0 review on 2026-07-27, a normal `hub.py ask` failed before
dispatch with `AskGuardRecord collision: ask-0f87 already has a record`.
The current source creates ask identifiers with `uuid.uuid4().hex[:4]`
(`_sys/core/hub.py::_short_id`), then intentionally fails closed if that
identifier already has an ask-guard record. This is a real 16-bit identity
collision surface, not a PeerHub assumption. Phase 1 must characterize the
current failure for compatibility purposes, but PeerHub v1 must use a
collision-resistant command/request identity and database uniqueness. It
must never overwrite an existing request or silently treat it as an
idempotent retry unless the caller supplied the same explicit idempotency
key and its payload digest matches.

### 4.4 Golden-fixture index and Phase boundary

Golden fixture **definitions and baseline captures are Phase 0 artifacts**.
They live under `docs/design/phase0/fixtures/` with a manifest that assigns a
stable fixture ID, domain, baseline revision, command/input, environment and
state preconditions, redaction declaration, expected observable outcome,
capture location, and SHA-256. Captures may be text/JSON transcript data;
they are not `peerhub` package source or an executable package test harness.

The required fixture domains are: `dispatch-pipe`, `dispatch-pty`,
`session-lease`, `coordination-room-mailbox-handoff`, `consensus`,
`health-recovery`, `routing`, `governance-broker-cas`, and `cli-jsonl`.
The following are the minimum, not a representative sample. Every legacy
action additionally has a successful validation/translation fixture and an
invalid-input/authorization fixture, linked from its final manifest row.

The stable IDs and full per-case outcome clauses are in
[`phase0/fixtures/CONTRACT.md`](phase0/fixtures/CONTRACT.md); the table below
is a count-level summary of that contract.

| Domain | Minimum fixture cases | Minimum count |
|---|---|---:|
| `dispatch-pipe` | normal delivered result; pre-spawn rejection; nonzero exit; output limit; hard deadline; post-`DISPATCH_INTENT` ambiguity/no replay | 6 |
| `dispatch-pty` | normal streamed result; chunk/line normalization; silence deadline; hard deadline; cancellation/tree cleanup; cleanup failure attached to primary result | 6 |
| `session-lease` | create; compatible resume; fingerprint mismatch; same-peer concurrent leases; wrong owner renew/close rejection; stale/identity-mismatch recovery | 6 |
| `coordination-room-mailbox-handoff` | room lifecycle; direct send/read; broadcast ordering; checkpoint/handoff; terminal heartbeat/assignment; concurrent conflict/retirement | 6 |
| `consensus` | proposal; idempotent same vote; conflicting vote rejection; missing/timeout electorate; unanimous finalization; dissent/arbiter separation | 6 |
| `health-recovery` | fresh probe/open; stale evidence; measured failure/quarantine; cooldown versus quarantine; admin probe authorization; failed/successful recovery probe | 6 |
| `routing` | eligible selection; capability/profile exclusion; missing usage; terminal exclusion; deterministic tie/draw audit; configuration/admission revision stale | 6 |
| `governance-broker-cas` | authorized commit; stale CAS; duplicate idempotent request; transaction crash/recovery; effect success/failure; lock/contention or saga compensation | 6 |
| `cli-jsonl` | valid read-only command; valid mutating command with key; malformed envelope; unsupported version; authentication/authorization rejection; stable error/exit mapping | 6 |

`BLOCKED_LIVE_CAPTURE` may replace only a live provider-effect case; it never
replaces the deterministic fake/simulated case in this table. A safety-sensitive
live effect which cannot be captured without spending quota or mutating user
state gets an explicit blocker record naming why, the safe fake/fixture
substitute, and the required later empirical test. It is not silently omitted.

Phase 1 converts this exact indexed baseline into executable characterization
and fault-injection tests. It must preserve the fixture ID and baseline
digest so a new implementation cannot redefine its own success condition.
Phase 0 is incomplete if an index row, baseline capture/blocker, digest, or
domain validation is missing.

### 4.2 PeerHub request identity and idempotency contract

PeerHub v1 has two non-interchangeable identities:

- `command_id` is a server-minted UUIDv4 (128 random bits). It is globally unique in the state store and never reuses the legacy four-hex-character form. Even at (10^{12}) commands, its birthday-bound collision probability is below (1.5 × 10^{-15}); the database unique constraint remains the correctness backstop, not probability alone.
- `idempotency_key` is an opaque caller-supplied UUIDv4 or equivalent 128-bit random value, scoped by authenticated/logical `client_id` and command type. A missing key is permitted only for declared read-only commands. Any state-changing external command receives or requires one before state changes.

On first acceptance, PeerHub atomically stores `(client_id, command_type,
idempotency_key, canonical_payload_sha256, command_id, receipt_id)`. A retry
with the same scope/key/payload digest returns the same command/receipt
anchor and current terminal or in-progress state; it never runs the effect a
second time. The same scope/key with a different digest is
`IDEMPOTENCY_PAYLOAD_MISMATCH`. Response bytes are not the binding: streaming
and recovery can change presentation while the command's normalized intent
must not. Effect workers receive the command/receipt anchor and their own
provider-safe idempotency material where an external system supports it.

### 4.3 Audit, recovery, and effect boundary

Phase 0 must freeze the following before Phase 1 code:

- **Atomic record:** a SQLite transaction commits the authoritative domain
  transition, immutable `TransitionReceipt`, and transactional outbox event
  together. The durable record is canonical JSON with schema version,
  correlation/command/receipt IDs, source and target revisions, policy and
  configuration revision, normalized outcome, evidence references, payload
  digest, timestamp, and monotonic audit sequence.
- **Integrity verification:** the audit is append-only at the application
  interface. Each event stores its canonical digest and predecessor digest;
  verification checks contiguous sequence, digest chain, foreign-key
  references, expected revision transitions, and SQLite `integrity_check`.
  This detects accidental/corrupt local history; it does not falsely claim
  protection from a privileged actor who can rewrite both database and
  verifier.
- **Crash recovery:** process startup scans non-terminal commands and effect
  intents. A committed transition is never rolled back merely because its
  effect worker died. It remains `COMMITTED_ENFORCEMENT_PENDING` until an
  idempotent worker reconciles it to `COMPLETED` or `EFFECT_FAILED`. A crash
  before the atomic transaction leaves no command record; a crash after it
  is recovered from the receipt/outbox. Ambiguous external effects are
  marked `MAY_HAVE_STARTED`/`UNKNOWN`, reconciled against provider evidence,
  and are never blindly replayed. Cross-record/import operations are named
  sagas with durable steps and compensations where possible, not claimed
  distributed transactions.
- **Failure/recovery path:** availability evidence, admission/quarantine
  policy, and coordination presence remain distinct. Administrative recovery
  records only `PROBE_AUTHORIZED`; reopening requires a fresh readiness probe
  bound to the current executable, adapter, and configuration revision.

The v1 command envelope carries `protocol_major` and `schema_version`.
One-shot CLI/JSONL invocation validates them before parsing a mutating
command. An unsupported major or schema receives a stable
`PROTOCOL_VERSION_MISMATCH` envelope/exit mapping with supported versions;
it cannot degrade into a best-effort parse or dispatch a peer.

## 5. Protocol v1 freeze requirements

Before package code, the team must approve a compact protocol document containing:

- command and event envelope fields, correlation ID, idempotency key, actor/client attribution, policy/configuration revision, and schema version;
- stable error code taxonomy and exit-code mapping;
- `EvidenceValue` states and freshness/error meaning;
- the three-layer dispatch result: process, provider-protocol, and task-completion assessment;
- state/transaction invariants: immutable event/outbox, compare-and-swap revision, one `(round_id, voter_id)` vote, one active proposal identity, and owner-aware lease lifecycle;
- the exact single operational owner for every configured peer/profile/model/effort pin (`PeerProfileBinding`), and bootstrap/import rules; and
- a host-adapter contract for vendor executable references, PTY/pipe transport, safe argv, redaction, and readiness receipts.

This consolidated protocol document is itself an R:10 artifact: it names the
reviewers, exact content hash, ratification date, and decision ID. A prior
R:10 on a related runtime-drift design is not a substitute for ratifying the
PeerHub v1 wire/error/state/session contract.

The protocol must not expose a broad public internal API beyond `Client` and v1 command/event schemas. Persistent `serve --stdio`, third-party adapter discovery, public conformance-kit packaging, and budget reservations remain explicitly deferred under `ARCHITECTURE.md`.

## 6. Phase 0 decisions requiring evidence

| Decision | Evidence needed | Decider |
|---|---|---|
| One database per user or per workspace | Existing `.ai` path usage, cross-workspace collision/lease tests, host invocation model | R:10 design decision |
| Usage evidence granularity and budget authority | Current quota collectors, real account/pool semantics, measured oversubscription evidence | Adapter/telemetry evidence; R:10 if authority is added |
| Health categories/thresholds/cooldowns | Current error and recovery behavior plus characterization fixtures | Health policy review |
| Default presentation of `DELIVERED_UNVERIFIED` | Existing user-facing output and completion fixtures | UX/contract review |
| Engram-to-PeerHub configuration import | Complete writer/reader inventory and a plan proving one live owner at every stage | R:10 migration decision |

The detailed, independently reviewable authority-transition rules for the last
row are in [`phase0/AUTHORITY-CUTOVER-CONTRACT.md`](phase0/AUTHORITY-CUTOVER-CONTRACT.md).
That document is a Phase 0 draft and does not authorize implementation,
database creation, or migration before its hash-bound R:10 ratification.

The compact v1 wire/error/evidence/state contract is
[`phase0/PROTOCOL-V1-FREEZE.md`](phase0/PROTOCOL-V1-FREEZE.md). It is likewise
a Phase 0 draft and is a separate hash-bound R:10 gate for all package code and
test scaffolding.

## 7. Entry and exit criteria

**Phase 0 entry:** this draft is reviewed against the actual Engram source and no package code is added.

**Phase 0 exit / compatibility freeze:**

1. Every current action has an individual disposition and a named owner.
2. Required golden transcripts run against a pinned Engram baseline and are redacted/reproducible.
3. v1 envelope/error/state invariants and the five decision records above are approved.
4. The consolidated v1 protocol document has a finalized R:10 binding with content hash and ratification date.
5. The migration ledger proves that each target fact has exactly one live write authority in every transition step.
6. Audit/recovery/version-mismatch contracts in §4.3 are represented by fault-injection and transcript requirements.
7. A peer review confirms the inventory did not invent behavior or omit coordination, recovery, configuration sealing, or governed-effect paths.

Only after these conditions are met may Phase 1 create package code or tests.
