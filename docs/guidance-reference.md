# PeerHub Guidance & Session Reference

Reference documentation for PeerHub CLI outcome status codes, recovery guidance, and session lifecycle states.

## Execution Result Statuses

The table below maps actual result statuses (`result["status"]` returned by `peerhub ask`) to CLI exit codes, associated guidance codes, and safe recovery actions derived from `RESULT_STATUS_REGISTRY`.

| Status | Exit Code | Guidance Code | Meaning | Safe Action |
|---|---|---|---|---|
| `blocked_uncertain` | 1 | `ask_blocked_uncertain` | New request on this stream is blocked because an earlier record on the stream ended in an uncertain state. | Inspect stream health and uncommitted evidence via `peerhub diag health --stream STREAM_ID`. Abandon the blocked stream and continue on a new stream. |
| `delivered` | 0 | (none) | Request executed successfully and terminal response was committed to the stream. | None required. Continue subsequent turns on the same stream to reuse the active session. |
| `failed_not_started` | 1 | `ask_launch_failed` | Provider process failed before execution started (missing executable or OS spawn rejection). | Inspect peer adapter availability and PATH configuration via `peerhub diag health`. |
| `not_executed` | 1 | `ask_refusal` | Provider explicitly refused execution before performing tool calls (e.g. quota exhausted, auth failure, invalid model). | Inspect quota pools and reset times via `peerhub diag quota` or verify profile configuration via `peerhub profile list`. |
| `pending_earlier_record` | 1 | `context-dependent` | Cycle processed an earlier record on the stream rather than this prompt. Meaning and guidance depend on processed_status (earlier may have delivered, refused, failed, or ended uncertain). | Inspect processed_status: if earlier delivered, retry this queued prompt with the same request-id, prompt, binding and selector; check quota/health if refused/failed; abandon stream if uncertain. |
| `recovered_terminal` | 0 | (none) | Prior idempotent execution or replayed terminal response recovered. | None required. Prior committed result recovered. |
| `session_unavailable` | 2 | `ask_selector_error` | Session selector or handle validation failed before invocation could begin. | List valid sessions via `peerhub session list` or pass `--fresh` to allocate a fresh session. |
| `uncertain` | 1 | `ask_uncertain` | Request started execution but ended without terminal certainty (timeout or abort). | Inspect stream health via `peerhub diag health --stream STREAM_ID`. Do not retry or pass `--fresh` on this stream; abandon stream. |

### `pending_earlier_record` Case Matrix

When `peerhub ask` processes an earlier record instead of the requested prompt, it returns `pending_earlier_record` with exit 1. This does not by itself mean earlier work remains unfinished or uncertain: `processed_status` reports what happened to that earlier record during this cycle. The requested prompt remains queued. Guidance and safe recovery depend on that earlier outcome:

| Earlier `processed_status` | Guidance Code | Meaning | Safe Action |
|---|---|---|---|
| `delivered / recovered_terminal` | (none) | Earlier record delivered successfully; prompt was queued behind it and remains unexecuted in this cycle. | Retry peerhub ask on the same stream with the same request-id, prompt, binding and selector to advance the queued prompt; compatible retained sessions can be reused. |
| `not_executed` | `ask_refusal` | Earlier record was refused by the provider (quota, auth, invalid model). | Inspect quota via peerhub diag quota or verify profile configuration. |
| `failed_not_started` | `ask_launch_failed / ask_delivery_failed` | Earlier record failed before execution started (missing executable or pre-spawn policy check). | Inspect provider binary and PATH configuration via peerhub diag health. |
| `uncertain / blocked_uncertain` | `ask_blocked_uncertain` | Earlier record ended in an uncertain state, blocking further progress on this stream. | Inspect stream via peerhub diag health --stream STREAM_ID. Abandon stream; start a new stream for subsequent work. |

## Guidance Registry & Outcome Codes

The table below is generated from the PeerHub guidance registry (`peerhub/cli/recovery.py`). A guidance code can cover several CLI exit codes; use the context lists below to interpret the actual process exit. Context-dependent explain JSON contains `exits` without a scalar `exit_code`.

| Outcome Code | Exit Code | Meaning | Safe Recovery Action | Avoid |
|---|---|---|---|---|
| `ask_blocked_uncertain` | 1 | New request on this stream is blocked because an earlier record on the stream ended in an uncertain state. | `peerhub diag health --stream STREAM_ID` (Inspect the blocked stream and review uncommitted evidence) | Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims. Abandon the blocked stream and continue genuinely new work on a new stream with a summary of prior context. |
| `ask_cas_conflict` | 3 | A compare-and-swap revision check failed. | `peerhub diag health --stream STREAM_ID` (Inspect current stream state before retrying the original request) | Do not bypass revision checks or change the request ID to force another execution; resolve uncertain delivery first. |
| `ask_claim_held` | 1 | A concurrent ask operation or active lease currently holds the delivery claim for this peer and stream. | `peerhub diag health --stream STREAM_ID` (Inspect store connectivity and stream activity (note: diag health checks store and stream records, not active in-memory leases or claims)) | Do not delete claim tables or database files, do not force --fresh, and do not bypass claim contention with duplicate request IDs. |
| `ask_delivery_failed` | 1 or 4 (context-dependent) | Adapter invocation failed before execution started or failed critically during delivery. | `peerhub diag health` (Inspect peer adapter status and diagnostic health) | Do not loop blind retries or pass --fresh without verifying adapter flags and availability. |
| `ask_idempotency_conflict` | 2 | An idempotency key was reused with conflicting request data. | `peerhub record read --stream STREAM_ID` (Inspect the original request and reuse its prompt, binding, and session selector for a retry) | Do not change request IDs to bypass a conflict or retry uncertain execution blindly. |
| `ask_launch_failed` | 1 | Provider CLI executable could not be found or failed to spawn before execution started. | `peerhub diag health` (Inspect peer adapter availability and PATH configuration) | Do not loop retries or pass --fresh until the provider CLI executable is installed and reachable on PATH. |
| `ask_refusal` | 1 | The provider explicitly refused execution before performing any tool calls (task outcome NOT_EXECUTED). | `peerhub diag quota` (Inspect provider quota pools and reset times) | Do not blindly retry the prompt with a new request ID without resolving the quota, auth, or model configuration issue. |
| `ask_schema_version_error` | 6 | The selected store uses an unsupported schema version. | `peerhub --version` (Check the installed version and use a PeerHub version compatible with this store) | Do not edit schema version metadata or downgrade the store to bypass this check. |
| `ask_selector_error` | 1 or 2 (context-dependent) | Session selector or handle validation failed before invocation could begin. | `peerhub session list` (List available valid sessions and check session handles) | Do not invent session handles, do not combine --fresh with --session, and do not retry without correcting the session selector. |
| `ask_stream_peer_mismatch` | 2 | The target stream is already bound to a different ask target peer. | `peerhub stream list` (Inspect existing streams and select or create a stream for this peer) | Do not mix multiple target peers on the same ask stream or force --fresh to override stream peer binding. |
| `ask_uncertain` | 1 | Request started execution on the peer adapter but ended without terminal certainty (e.g. timeout or abort). | `peerhub diag health --stream STREAM_ID` (Inspect stream health and check for uncommitted or partial activity) | Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims. Continuing work on this stream is blocked to prevent split-brain execution; start a new stream for subsequent work. |
| `ask_unexpected_failure` | 1 or 2 (context-dependent) | An unclassified or unexpected error occurred during ask dispatch. | `peerhub diag health` (Inspect diagnostic evidence and stream state) | Do not retry destructive actions or invent unverified recovery commands. |

### Detailed Recovery Advice by Outcome Code

#### `ask_blocked_uncertain`

- **Exit Code**: 1
- **Meaning**: New request on this stream is blocked because an earlier record on the stream ended in an uncertain state.
- **Why**: Stream ordering requires resolving or abandoning uncertain prior work before appending new turns on the stream. Inspect stream health and uncommitted evidence before deciding whether to retry or start a new stream.
- **Safe Recovery Actions**:
  1. `peerhub diag health --stream STREAM_ID`: Inspect the blocked stream and review uncommitted evidence (Prerequisites: Provide the affected stream identifier.)
- **Avoid**: Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims. Abandon the blocked stream and continue genuinely new work on a new stream with a summary of prior context.
- **Documentation**: [docs/remote-ask.md](remote-ask.md)

#### `ask_cas_conflict`

- **Exit Code**: 3
- **Meaning**: A compare-and-swap revision check failed.
- **Why**: The stored revision changed before the guarded update could commit.
- **Safe Recovery Actions**:
  1. `peerhub diag health --stream STREAM_ID`: Inspect current stream state before retrying the original request (Prerequisites: Provide the affected stream identifier.)
- **Avoid**: Do not bypass revision checks or change the request ID to force another execution; resolve uncertain delivery first.
- **Documentation**: [docs/remote-ask.md](remote-ask.md)

#### `ask_claim_held`

- **Exit Code**: 1
- **Meaning**: A concurrent ask operation or active lease currently holds the delivery claim for this peer and stream.
- **Why**: Only one caller can execute an ask turn per peer and stream at a time to prevent concurrent dispatch races. Wait for the active turn to finish, or use a distinct --stream for genuinely separate parallel work.
- **Safe Recovery Actions**:
  1. `peerhub diag health --stream STREAM_ID`: Inspect store connectivity and stream activity (note: diag health checks store and stream records, not active in-memory leases or claims) (Prerequisites: Wait for the active turn or lease timeout to expire.)
- **Avoid**: Do not delete claim tables or database files, do not force --fresh, and do not bypass claim contention with duplicate request IDs.
- **Documentation**: [docs/design/a2a-inbound-server.md](design/a2a-inbound-server.md)

#### `ask_delivery_failed`

- **Exit Codes** (context-dependent):
  - `1`: Adapter invocation failure, rejected pre-spawn execution policy, or general execution error (failed_not_started status or adapter exception).
  - `4`: Storage layer failure (StorageReadOnlyError, StorageFullError, StorageCorruptError).
- **Meaning**: Adapter invocation failed before execution started or failed critically during delivery.
- **Why**: The adapter rejected pre-spawn execution policy (e.g. ag without --writable), failed pre-spawn validation, or encountered a storage/delivery error.
- **Safe Recovery Actions**:
  1. `peerhub diag health`: Inspect peer adapter status and diagnostic health (Prerequisites: None.)
- **Avoid**: Do not loop blind retries or pass --fresh without verifying adapter flags and availability.
- **Documentation**: [docs/remote-ask.md](remote-ask.md)

#### `ask_idempotency_conflict`

- **Exit Code**: 2
- **Meaning**: An idempotency key was reused with conflicting request data.
- **Why**: The stored request identity differs from the request supplied with the same key.
- **Safe Recovery Actions**:
  1. `peerhub record read --stream STREAM_ID`: Inspect the original request and reuse its prompt, binding, and session selector for a retry (Prerequisites: Provide the affected stream identifier.)
- **Avoid**: Do not change request IDs to bypass a conflict or retry uncertain execution blindly.
- **Documentation**: [docs/remote-ask.md](remote-ask.md)

#### `ask_launch_failed`

- **Exit Code**: 1
- **Meaning**: Provider CLI executable could not be found or failed to spawn before execution started.
- **Why**: The configured provider binary (codex, claude, or agy) is missing from PATH or the OS rejected process creation before any turn started.
- **Safe Recovery Actions**:
  1. `peerhub diag health`: Inspect peer adapter availability and PATH configuration (Prerequisites: Ensure the provider CLI is installed and on PATH.)
- **Avoid**: Do not loop retries or pass --fresh until the provider CLI executable is installed and reachable on PATH.
- **Documentation**: [docs/remote-ask.md](remote-ask.md)

#### `ask_refusal`

- **Exit Code**: 1
- **Meaning**: The provider explicitly refused execution before performing any tool calls (task outcome NOT_EXECUTED).
- **Why**: The provider reported quota exhaustion, invalid model selection, authentication failure, or another refusal before start.
- **Safe Recovery Actions**:
  1. `peerhub diag quota`: Inspect provider quota pools and reset times (Prerequisites: None.)
  2. `peerhub profile list`: Review available model profiles and configured bindings (Prerequisites: None.)
- **Avoid**: Do not blindly retry the prompt with a new request ID without resolving the quota, auth, or model configuration issue.
- **Documentation**: [docs/history/from-engram-repo/protocol-docs/ops/automatic-profile-routing-2026-06-20.md](history/from-engram-repo/protocol-docs/ops/automatic-profile-routing-2026-06-20.md)

#### `ask_schema_version_error`

- **Exit Code**: 6
- **Meaning**: The selected store uses an unsupported schema version.
- **Why**: This PeerHub version cannot read the selected store schema.
- **Safe Recovery Actions**:
  1. `peerhub --version`: Check the installed version and use a PeerHub version compatible with this store (Prerequisites: Verify the selected --db path is the intended store.)
- **Avoid**: Do not edit schema version metadata or downgrade the store to bypass this check.
- **Documentation**: [docs/migrations.md](migrations.md)

#### `ask_selector_error`

- **Exit Codes** (context-dependent):
  - `1`: Session lost error (SessionLostError) when retained session continuity is broken.
  - `2`: Selector syntax error, unsupported selector, conflicting flags, or session handle not found (session_unavailable status or invalid_arguments).
- **Meaning**: Session selector or handle validation failed before invocation could begin.
- **Why**: The specified session handle is invalid, tombstoned, incompatible with the runtime binding, or flags like --fresh and --session conflict.
- **Safe Recovery Actions**:
  1. `peerhub session list`: List available valid sessions and check session handles (Prerequisites: None.)
- **Avoid**: Do not invent session handles, do not combine --fresh with --session, and do not retry without correcting the session selector.
- **Documentation**: [docs/implementation/RETAINED_SESSIONS.md](implementation/RETAINED_SESSIONS.md)

#### `ask_stream_peer_mismatch`

- **Exit Code**: 2
- **Meaning**: The target stream is already bound to a different ask target peer.
- **Why**: Each ask stream maintains ordered single-peer session continuity and cannot be retargeted to another peer mid-stream.
- **Safe Recovery Actions**:
  1. `peerhub stream list`: Inspect existing streams and select or create a stream for this peer (Prerequisites: Use a distinct --stream per target peer.)
- **Avoid**: Do not mix multiple target peers on the same ask stream or force --fresh to override stream peer binding.
- **Documentation**: [docs/remote-ask.md](remote-ask.md)

#### `ask_uncertain`

- **Exit Code**: 1
- **Meaning**: Request started execution on the peer adapter but ended without terminal certainty (e.g. timeout or abort).
- **Why**: The provider or process exceeded timeout or was interrupted before producing terminal truth. Execution state remains uncertain.
- **Safe Recovery Actions**:
  1. `peerhub diag health --stream STREAM_ID`: Inspect stream health and check for uncommitted or partial activity (Prerequisites: Provide the affected stream identifier.)
  2. `peerhub diag health`: Check overall store and peer health (Prerequisites: None.)
- **Avoid**: Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims. Continuing work on this stream is blocked to prevent split-brain execution; start a new stream for subsequent work.
- **Documentation**: [docs/remote-ask.md](remote-ask.md)

#### `ask_unexpected_failure`

- **Exit Codes** (context-dependent):
  - `1`: Unhandled exception or unexpected runtime error during ask dispatch.
  - `2`: Invalid CLI arguments or unrecognized ask options (ValueError other than selector/session errors).
- **Meaning**: An unclassified or unexpected error occurred during ask dispatch.
- **Why**: An unhandled exception or internal error interrupted the ask command.
- **Safe Recovery Actions**:
  1. `peerhub diag health`: Inspect diagnostic evidence and stream state (Prerequisites: None.)
- **Avoid**: Do not retry destructive actions or invent unverified recovery commands.
- **Documentation**: [docs/remote-ask.md](remote-ask.md)

## Session Lifecycle & State Definitions

PeerHub manages durable session continuity for AI peer adapters (`cc`, `cx`, `ag`) via retained sessions in SQLite storage (`bridge_session_history`, `bridge_sessions`).

### Session States

- **`ACTIVE`**: The session record is in the active lifecycle state. Note that having `state = 'ACTIVE'` alone does not guarantee immediate turn reuse or resumption eligibility; actual eligibility also requires meeting retention age bounds (`0 <= now - last_seen <= MAX_AGE_S`), `resumable = 1`, bootstrap integrity, and valid native vendor session evidence (non-null `vendor_session_id` and `context_watermark`). A session remains `ACTIVE` across successful subsequent turns on the same stream, peer, and compatible binding.
- **`FRESH`**: A newly allocated session generation that has been created but has not yet completed a successful native vendor execution.
- **`LOST`**: Session continuity has been broken or tainted. This occurs when the adapter reports context loss, when a prior execution ends in an uncertain state (e.g. timeout or abort), or when a session resumption is rejected. A `LOST` session cannot be resumed. Note that marking a session `LOST` does not mean the next request automatically starts a new generation: an uncertain delivery can block the stream with `ask_blocked_uncertain`, and session selection during recovery can select another compatible retained session from history if available.
- **`TOMBSTONED`**: The session has been permanently retired because it exceeded the maximum retention age (`MAX_AGE_S` = 30 days) or the per-stream history capacity (`MAX_RETAINED` = 20 sessions). Tombstoned sessions cannot be resumed or resurrected.

### Resumability (`resumable`)

- **`resumable`**: A boolean flag (`0` or `1`) indicating whether the adapter and runtime target support multi-turn session resumption. CLI peers with native session continuation support (`cc`, `cx`, `ag`) operate with `resumable = 1`. If an adapter is not resumable, every turn executes in a fresh generation.

### Generation Semantics (`session_generation`)

The session generation counter (`session_generation`) tracks distinct conversation lineages within a stream and peer:

- **Generation Stays Constant (`gen = 1`)**: Within a retained session, the generation remains constant (e.g. `1`) across turns as long as the caller continues on the same stream and peer with compatible options. Subsequent asks on that stream automatically reuse the warm session without bumping the generation.
- **Generation Bumps (`gen -> gen + 1`)**: PeerHub allocates a new generation (incrementing `session_generation` via `bridge_session_counters`) in any of the following circumstances:
  1. **Binding Change**: When the effective runtime binding changes. For example, in Antigravity (`ag`), switching between read-only and `--writable` changes the CLI execution mode (`--mode accept-edits`), modifying the binding and bumping the generation.
  2. **No Eligible Compatible Retained Session**: When a prior session was marked `LOST` (e.g. context loss or resumption rejection) or tombstoned, and no other compatible retained session is eligible in history, the next eligible turn on an unblocked stream allocates a new generation. (Note: an uncertain delivery blocks subsequent turns on the stream with `ask_blocked_uncertain` rather than automatically bumping generations, and recovery can select another compatible retained session if available.)
  3. **Explicit `--fresh`**: When the caller explicitly requests `--fresh`, forcing a new session generation.
  4. **Expiration or Tombstoning**: When the retained session exceeds 30 days or is pruned due to quota.
  5. **Workspace or Fingerprint Mismatch**: When the workspace lineage or adapter binary fingerprint differs from the retained session.
  6. **Context Delta Overflow**: When the catch-up delta exceeds the context window budget (`delta_too_large`).
