Plan document, 2026-10-12. Reference, not an instruction file.

# Blocked-Stream Root-Cause Elimination & Recovery Plan (2026-10-12)

## Status (2026-10-13)

PRs 1-3 are merged and included in the 0.16.0 source candidate: termination
confirmation (#95), drain-and-truncate (#97), and provider refusal classification
(#98). PRs 4-6 remain pending. The evidence table below is the original incident
snapshot; the seven historical deliveries have not been resolved by this release.
Automatic timeout settlement and `stream resolve` are planned, not shipped.

PR 3 classifies explicit pre-tool cx/cc errors and recognized read-only ag stderr
refusals (including invalid model selection). Successful answer text never
settles a refusal. Post-tool or incomplete evidence stays uncertain; writable
ag lacks pre-execution proof and remains guarded. Refusal outcomes retain
`STARTED` certainty, consume the prompt, and invalidate the session generation.

## 1. Problem and Evidence Table
Deliveries: 107 total; 100 TERMINAL, 7 stuck at STARTED (`acked=0`, `blocked_uncertain`). 6 writable, 1 read-only.
Planned timeout settlement requires enforced isolation plus confirmed termination
(PR 5); the current source does not automatically settle timeouts.

| Delivery Execution ID | Peer | Mode | Cause | Outcome |
|---|---|---|---|---|
| `ag:21968:13889ac7` (`ag-impl-peerhub`) | ag | Writable | Total timeout (1700s elapsed; killed by process runner) | Stuck at `STARTED` (`no_terminal`, `blocked_uncertain`) |
| `cx:18044:c435bac2` (`cx-mem-1`) | cx | Read-only | Output cap hit (>1MB; pump killed process) | Stuck at `STARTED` (`runtime_error`, `blocked_uncertain`) |
| `ag:19976:9309b8e0` (`ag-hand-2`) | ag | Writable | Total timeout (480s elapsed; killed by process runner) | Stuck at `STARTED` (`no_terminal`, `blocked_uncertain`) |
| `ag:10040:26ed10a4` (`ag-xd-1`) | ag | Writable | Total timeout (380s elapsed; killed by process runner) | Stuck at `STARTED` (`no_terminal`, `blocked_uncertain`) |
| `ag:11648:15895773` (`ag-ci-1`) | ag | Writable | Total timeout (330s elapsed; killed by process runner) | Stuck at `STARTED` (`no_terminal`, `blocked_uncertain`) |
| `cx:13884:0859ea8e` (`cx-a4`) | cx | Writable | Provider usage-limit refusal after `turn.started` (exit 1) | Stuck at `STARTED` (`runtime_error`, `blocked_uncertain`) |
| `cx:11936:c89ca222` (`cx-pyr`) | cx | Writable | Provider usage limit ("You’ve hit your usage limit", exit 1) | Stuck at `STARTED` (`runtime_error`, `blocked_uncertain`) |

## 2. Root Causes
1. **Output Cap Process Kill**: `BoundedProcess._pump` set `_exceeded` at 1MB and killed the process tree, converting a presentation/buffer cap into execution uncertainty.
2. **Undifferentiated Provider Refusals**: Providers emit envelopes (`thread.started`) triggering `started` before dispatch. Nonzero refusal exits become generic `RuntimeTargetError` where 300-char excerpts hide the cause, leaving Bridge trapped in `STARTED` despite zero tool execution.
3. **Discarded Termination Confirmation**: `BoundedProcess.wait()` and `abort()` discard `kill_process_tree` results. On timeout, Bridge records `no_terminal` while retaining `STARTED`. `reconcile_uncertain` accepted only `MAY_HAVE_STARTED`, permanently locking streams.
4. **Opaque Write-Policy Persistence**: Bridge persists only an opaque binding hash; recovery cannot identify whether an invocation ran with enforced read-only isolation or writable permissions.
5. **False Read-Only Assumption**: Omitting `--writable` flags (`cc`, `ag`) only alters default tool approvals; providers do not enforce OS-level sandbox isolation.

## 3. Adapter Enforcement Table
Exact invocation templates from `peerhub/extensions/adapters/base.py` (excluding optional `--model`/`--effort`):

| Adapter | Mode | Exact CLI Flags Passed | Provider Enforced? | Enforcement Class |
|---|---|---|---|---|
| `cx` | read-only | `exec --skip-git-repo-check -s read-only --json -` | Yes: `-s read-only` enforces shell/filesystem sandbox | Enforced (sandbox) |
| `cx` | writable | `exec --skip-git-repo-check -s workspace-write --json -` | No: allows workspace file edits | Not enforced (writable) |
| `cc` | read-only | `-p - --output-format stream-json --verbose` | No: omitting `--permission-mode acceptEdits` defaults tool permissions; allows ambient approvals | Not enforced |
| `cc` | writable | Above + `--permission-mode acceptEdits` | No: auto-accepts file edits | Not enforced (writable) |
| `ag` | read-only | `-p <prompt> --output-format json` | No: omitting `--mode accept-edits` soft-denies approvals, but headless mode auto-allows file reads/writes | Not enforced |
| `ag` | writable | Above + `--mode accept-edits` | No: auto-accepts file edits | Not enforced (writable) |

Resume provenance: `cc` adds `--resume ID`; `ag` adds `--conversation ID`; `cx` uses `exec resume ID --skip-git-repo-check --json -` (strictly inherits initial sandbox; `-s` forbidden).

## 4. Fixes F1–F8
- **F1 Output Cap Drain-and-Truncate**: Drain both pipes to process exit; truncate retained text in buffer at `max_bytes`; set `output_truncated: true`; never kill for volume. Bounded incremental parsing ensures completion, refusal, and usage events beyond the cap are still captured; oversized individual events are bounded; truncated JSON prefixes are rejected as completion evidence. Existing total `timeout_s` remains sole bound. User-visible metadata: `"output_truncated": true`.
- **F2 Provider Refusal Classification (NOT_EXECUTED)**: Classify quota, auth, and usage-limit rejections via semantic parsing of structured error events before excerpting. Record task outcome as `NOT_EXECUTED` while preserving process certainty `STARTED` (never convert to `PrespawnError`, never alter/delay `started`: frozen M1 certainty monotonicity). Requires complete evidence excluding preceding tool execution; refusal after tools remains uncertain. Covers variants across `cx`, `ag`, `cc` (curly apostrophes `\u2019`, stderr, quoted user text). Acknowledge prompt (`acked=1`), invalidate session generation, leave stream reusable.
  - *User-visible NOT_EXECUTED message*: `Task outcome: NOT_EXECUTED (provider refusal: usage limit / quota exhausted; no side effects occurred)`
- **F3 Quota Pre-Flight Refusal**: Refuse before invocation when fresh committed `MEASURED` Observation evidence shows pool exhaustion. Print reset time. Stale, unknown, predictive, conflicting, or expired evidence never refuses; never probe implicitly.
  - *User-visible quota pre-flight refusal*: `Refusal: provider quota exhausted for pool <pool_id>; resets at <reset_iso>. Prompt not submitted.`
- **F4 Timeouts & Termination Evidence**: Retain total deadline `timeout_s` as sole bound (no extra nested CPU/wall-clock bounds). Propagate process-tree termination confirmation (`kill_process_tree` result) into durable evidence. Automatic settlement requires confirmed termination AND persisted enforcement covering all mutation channels (`cx` read-only); writable asks retain guard.
- **F5 Persist Effective Permissions**: Persist write policy, sandbox provenance, resume provenance (sandbox inheritance), shell/MCP/network restrictions, and adapter enforcement class (`enforced` vs `not_enforced`) in delivery metadata before invocation. Read-only intent alone does not establish absence of external effects.
- **F6 Audited Resolution Command**: Single CLI command `peerhub stream resolve --stream S --delivery D --as abandoned --reason TEXT [--evidence REF] [--accept-unknown-effects]`. Exact delivery only; requires confirmed termination or verified containment; writable asks require `--accept-unknown-effects`. Atomically fences claim, appends audit record, acks only the failed prompt (`acked=1`), invalidates session generation, prevents replay; never records success; idempotent and crash-safe; no bulk cleanup, no force-clear, no time-based release.
  - *User-visible blocked message*:
    ```text
    Stream '<stream_id>' is blocked by uncertain delivery '<delivery_id>' (certainty: STARTED).
    Before resolving, inspect the workspace for side effects:
      1. Check git status and workspace diffs (git status alone is not proof)
      2. Check for untracked files and external service mutations
      3. Verify no lingering background processes are running
    To resolve, run:
      peerhub stream resolve --stream <stream_id> --delivery <delivery_id> --as abandoned --reason "<reason>" [--accept-unknown-effects]
    ```
  - *User-visible abandonment audit record*:
    `control.reconcile: {"decision": "ABANDON", "stream_id": "<stream_id>", "delivery_id": "<delivery_id>", "reason": "<reason>", "evidence_ref": "<evidence_ref>", "accepted_unknown_effects": <bool>, "operator_peer_id": "<operator>", "timestamp": "<iso_timestamp>"}`
- **F7 Historical Delivery Resolution**: Inspect the 7 stuck deliveries individually (complete traces, containment, diffs, untracked files, processes). Resolve `cx-mem-1`, `cx-a4`, `cx-pyr` via runbook; 4 `ag` timeouts resolved after operator workspace confirmation using F6.
- **F8 Operational Process**: Set `--timeout-seconds` comfortably above realistic task duration; decompose large writable tasks into smaller steps (documented in README recipe).

## 5. Command Specification & Example Session
Syntax: `peerhub stream resolve --stream <ID> --delivery <DID> --as abandoned --reason "<TEXT>" [--evidence <REF>] [--accept-unknown-effects]`
- Pre-conditions: Caller author peer distinct from target peer; delivery in `blocked_uncertain`; process tree dead/contained; writable requires `--accept-unknown-effects`.
- Effects: Acquires claim fence; appends `control.reconcile` (decision `ABANDON`); marks `acked=1`; bumps session generation; consumes failed prompt without replay.

Example session:
```powershell
# 1. Ask fails and traps stream in blocked_uncertain:
$ peerhub ask ag "Refactor models" --stream impl --writable --timeout-seconds 120
Stream 'impl' is blocked by uncertain delivery 'ag:10040:26ed10a4' (certainty: STARTED).
Before resolving, inspect the workspace for side effects:
  1. Check git status and workspace diffs (git status alone is not proof)
  2. Check for untracked files and external service mutations
  3. Verify no lingering background processes are running
To resolve, run:
  peerhub stream resolve --stream impl --delivery ag:10040:26ed10a4 --as abandoned --reason "<reason>" --accept-unknown-effects

# 2. Operator verifies containment and side effects:
$ git status
$ Get-Process agy -ErrorAction SilentlyContinue

# 3. Operator resolves the delivery:
$ peerhub stream resolve --stream impl --delivery ag:10040:26ed10a4 --as abandoned --reason "Timeout inspected: child killed, no uncommitted diffs" --accept-unknown-effects
Resolved delivery 'ag:10040:26ed10a4' on stream 'impl' as abandoned. Session invalidated; stream unblocked.

# 4. Next ask starts cleanly on a fresh generation:
$ peerhub ask ag "Refactor models (chunk 1)" --stream impl --writable --timeout-seconds 300
```

## 6. Certainty and Outcome Model
- **Certainty Monotonicity (INV-004, TD-26)**: Certainty strictly advances `NOT_STARTED` -> `STARTED` -> `TERMINAL`. Once spawned, `STARTED` is never downgraded to `NOT_STARTED` or `MAY_HAVE_STARTED`. Reclassifying post-spawn refusals as `PrespawnError` is forbidden.
- **Why STARTED is Never Downgraded**: Downgrading post-spawn certainty erases process execution history, breaks claim fences, masks potential ambient side effects, and enables duplicate command execution via catch-up.
- **State Storage**:
  - `bridge_deliveries`: retains `certainty = STARTED`, updates `acked = 1`, records resolution disposition (`abandoned`).
  - `bridge_evidence`: appends durable events (`termination_confirmed`, `refusal_detected`, `reconcile_registered`, `abandoned`).
  - `records`: appends `control.reconcile` (decision `ABANDON`); never fabricates a `response` record.
  - `bridge_sessions`: increments `session_generation` to isolate subsequent turns from poisoned provider state.

## 7. Test List
Pre-fix fault injection & regression tests:
1. `test_output_drain_and_truncate`: >1MB stdout drains cleanly, flags `output_truncated: true`, preserves valid terminal completion.
2. `test_output_drain_events_beyond_cap`: terminal/usage JSON events emitted after 1MB cap are parsed and recorded.
3. `test_parser_bounds_oversized_events`: single events >256KB fail safely; truncated JSON prefixes are rejected as completion evidence.
4. `test_kill_tree_unconfirmed_blocks`: surviving child orphan keeps delivery in `blocked_uncertain`.
5. `test_kill_tree_confirmed_terminates`: verified process-tree kill propagates `termination_confirmed: true` into evidence.
6. `test_quota_refusal_pre_tool_not_executed`: early refusal with zero tool calls records `NOT_EXECUTED`, preserves `STARTED`, acks prompt, unblocks stream.
7. `test_quota_refusal_post_tool_uncertain`: refusal after tool execution remains guarded in `blocked_uncertain`.
8. `test_quota_refusal_variants`: handles curly apostrophes (`\u2019`), stderr messages, and quoted prompt strings without misclassification.
9. `test_quota_preflight_refusal`: fresh `MEASURED` exhausted quota snapshot rejects prompt before spawn; stale/unknown snapshot proceeds.
10. `test_enforced_readonly_auto_settlement`: sandboxed `cx` read-only timeout auto-settles; `cc`, `ag`, and writable timeouts remain blocked.
11. `test_stream_resolve_idempotent_audit`: `stream resolve` appends durable audit record, rejects live processes, requires `--accept-unknown-effects` for writable.
12. `test_stream_resolve_no_replay`: resolved failed prompt is not replayed by subsequent ask; next ask runs on fresh session generation.
13. `test_concurrent_resolution_fenced`: concurrent or stale resolution attempts are safely rejected by claim fencing.

## 8. PR Order, Files, and Acceptance
1. **PR 1: Termination Evidence Propagation — COMPLETE (#95; 0.16.0)**
   - Files: `peerhub/extensions/adapters/process.py`, `peerhub/extensions/bridge.py`, `tests/communication/test_adapters_process.py`.
   - Acceptance: `wait()` and `abort()` capture `kill_process_tree` results in `ProcessResult.termination_confirmed`; Bridge persists confirmation in evidence.
2. **PR 2: Output Drain-and-Truncate & Bounded Event Parser — COMPLETE (#97; 0.16.0)**
   - Files: `peerhub/extensions/adapters/process.py`, `peerhub/extensions/adapters/base.py`, `tests/communication/test_output_cap.py`.
   - Acceptance: Outputs >1MB drain without process kill; buffer truncates at `max_bytes`; terminal/usage events beyond cap parse cleanly; `output_truncated: true` recorded.
3. **PR 3: Structured Provider Refusal Classification (NOT_EXECUTED) — COMPLETE (#98; 0.16.0)**
   - Files: `peerhub/extensions/adapters/base.py`, `peerhub/extensions/bridge.py`, `tests/communication/test_provider_refusal.py`.
   - Acceptance: Pre-tool quota/auth/limit errors classify as `NOT_EXECUTED`; `STARTED` certainty preserved; prompt acked; stream reusable on fresh generation.
4. **PR 4: Quota Pre-Flight Refusal — PENDING**
   - Files: `peerhub/extensions/ask.py`, `peerhub/cli/app.py`, `tests/communication/test_quota_preflight.py`.
   - Acceptance: Fresh committed exhausted quota evidence refuses prompt before subprocess spawn; stale or unknown evidence proceeds.
5. **PR 5: Enforced-Policy Metadata & Automatic Settlement — PENDING**
   - Files: `peerhub/extensions/adapters/base.py`, `peerhub/extensions/bridge.py`, `tests/communication/test_readonly_enforcement.py`.
   - Acceptance: Persistence of write policy, sandbox provenance, and enforcement class; confirmed dead `cx` read-only timeouts auto-settle; non-enforced modes retain guard.
6. **PR 6: Audited Stream Resolution CLI & Historical Recovery Runbook — PENDING**
   - Files: `peerhub/cli/app.py`, `peerhub/extensions/bridge.py`, `docs/design/plan-stuck-streams-2026-10-12.md`, `README.md`, `tests/communication/test_stream_resolve.py`.
   - Acceptance: CLI `peerhub stream resolve --as abandoned` fences claims, validates termination, appends audit record, requires `--accept-unknown-effects` for writable; runbook resolves the 7 historical deliveries.

## 9. Risks
1. **Masked Side Effects**: Non-enforced read-only configurations (`cc`, `ag`) could execute mutations via ambient approvals. Mitigated by restricting auto-settlement to provider-enforced sandboxes (`cx` only); all others require explicit operator abandonment.
2. **Brittle Refusal Parsing**: Parsing text errors across provider CLI versions risks misclassification. Mitigated by semantic event parsing before excerpting, strict verification of zero tool calls, and defaulting to uncertainty on ambiguity.
3. **Resource Exhaustion on Runaway Processes**: Draining infinite output loops could consume CPU. Mitigated by strictly bounding pump queues and retaining total `timeout_s` as an absolute wall-clock deadline.
4. **Child Process Orphans**: On Windows, child processes might survive parent termination. Mitigated by requiring verified `kill_process_tree` confirmation before any resolution or abandonment.

## 10. Non-Goals
1. **No Automatic Settlement for Writable Deliveries**: Writable timeouts and crashes never auto-settle; operator review is strictly required.
2. **No Certainty Downgrading**: Never downgrade `STARTED` to `NOT_STARTED` or `MAY_HAVE_STARTED`. Monotonicity is frozen.
3. **No Fabricated Responses**: Abandoned or refused deliveries never record fabricated success or response records.
4. **No Bulk Cleanup or Force-Clear CLI**: No `--all-stuck` or `--force` flags. Every resolution is exact-delivery and audited.
5. **No Schema Migration**: Existing SQLite schemas are preserved; breaking changes or additive nullable columns only.
6. **No Implicit Retries**: Retrying an abandoned prompt requires a separate fresh request on a fresh session generation; failed prompts are never replayed implicitly.
