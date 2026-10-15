# PeerHub improvement design and pre-TDD specification — 2026-10-15

## Objective and decision rules

This design takes the findings in [the cross-peer review](PEERHUB_REVIEW_2026_10_15.md) to the point immediately before writing tests. No implementation or test changes are included here. The order weighs user-data safety, confirmed impact, and the size of the state-machine or compatibility change. A risk hypothesis must first become a deterministic failing test; a performance change must have a workload benchmark that measures the claimed cost.

## Priority and implementation batches

| Order | Item | Priority / confidence | Why this order | Batch boundary |
| ---: | --- | --- | --- | --- |
| 1 | Explicit database selection precedence | P1, reproduced | A command can mutate a different database than the operator selected. Contract is simple and cross-command. | Small isolated CLI/config fix. |
| 2 | Cancel during spawn-to-STARTED window | P1, serious code path; not yet reproduced | A durable `nothing_running` result can mislead the operator while work continues. Fixing it touches durable state and process ownership. | Bridge/control state-machine change, kept separate from other changes. |
| 3 | MCP scoped reads and privileged record writes | P1 for embedded hosts that rely on registered scopes; not CLI-exposed | MCP is a library-level surface, but `register_session` documents scoped capabilities. Reads bypass those scopes and appends can emit controls. | MCP API compatibility/security change. |
| 4 | Snapshot-consistent integrity audit | P2, code path confirmed | Can falsely report corruption during ordinary concurrent writes; isolated read-transaction change. | Diagnostics only. |
| 5 | Bridge-aware stream health | P2, code path confirmed | Users can see `OK` while a subsequent ask is blocked. Requires an honest combined operational projection. | Read-only diagnostics and CLI output contract. |
| 6 | Human output terminal-control sanitization | P2, reproduced | Stored text can forge rows or issue terminal controls. Scope is human renderers; JSON remains exact. | CLI rendering only. |
| 7 | Monitor overrun schedule | P2, simulated | Current loop can run back-to-back after slow collection. Define fixed-rate/skipped-slot contract. | Monitoring cadence only. |
| 8 | Per-ask ClaimStore schema DDL | P2, repeated schema churn reproduced; contention impact unmeasured | Recreates four triggers on every ask and takes a write transaction. Avoid schema writes on the hot path. | Bridge claim persistence/migration behavior. |
| 9 | Targeted single-stream health and N+1 listing reads | P2/P3, query shapes confirmed; scale impact unmeasured | Clear query inefficiency, but size the benefit before broad optimization. | Separate diagnostics and projection optimization changes. |
| 10 | Windows process-tree exit classification | P3, race plausible | Preserve certainty about descendants while avoiding a false failure for an exited process. | Process supervision only. |
| 11 | Vendor session ID option-shaped values | P3, input gap confirmed | Prevent IDs such as `--last` being parsed as CLI flags. Small validation change. | Adapter validation only. |
| 12 | Session eligibility visibility and uncertain-delivery resolution | P2/P3 design work | Important recovery/UX gap, but it requires a product contract and auditable operator intent. Do not conflate with routine fixes. | Separate design/feature proposal before implementation. |

Items 2 and 3 should not be combined into one patch: they have different owners, contracts, and rollback criteria. Items 4–8 are individually testable and should remain independently reviewable. Performance item 9 follows correctness because the measured current store is small and no scale claim is established.

## TDD-ready contracts and test specifications

### 1. Database selection

**Contract:** an explicitly supplied `--db PATH` selects `PATH` even when `PEERHUB_DB` is set. If `--db` is absent, the existing environment → workspace discovery → default behavior remains. A missing explicitly selected path is never redirected; read-only commands fail closed, while documented initialization commands may create the selected path. Provenance output reports `explicit` for the explicit CLI choice.

**Test-first scenarios:**

- `test_explicit_db_precedes_peerhub_db`: set environment to database A, pass `--db` database B, run a mutation, and assert B changed while A did not.
- `test_explicit_default_path_precedes_peerhub_db`: explicitly pass the default-looking `.peerhub/core.db`; ensure it is distinguishable from an omitted flag and wins over environment.
- `test_missing_explicit_db_does_not_fall_back_to_env_or_discovery`: both alternate stores exist, selected path does not; a read-only command reports unavailable without opening either alternate. An explicit `peer register` initialization creates only the selected path and leaves the env store unchanged.
- `test_env_db_used_when_cli_db_omitted`: preserve environment behavior when no CLI option was supplied.
- `test_empty_peerhub_db_is_rejected_or_treated_as_unset`: never interpret an empty environment value as the current directory or an unintended database.
- `test_workspace_discovery_used_only_when_cli_and_env_absent`: preserve nearest-store behavior.
- `test_explicit_db_and_workspace_relative_paths_have_stable_precedence`: cover `--workspace`, relative paths, symlink/case-normalized paths on Windows, and ensure store provenance matches the actual selected file.
- Cover CLI JSON and human error output and all entry points sharing store selection (`ask`, core mutation, monitor/diagnostics). Avoid duplicate tests where one command-level test plus the common selector contract proves propagation.

**Design seam:** argparse must preserve whether the option was supplied. A default string equal to `DEFAULT_DB_PATH` cannot distinguish omitted from explicitly supplied default. Represent omission separately (for example `None` plus resolved default) and keep explicit source/provenance in `StoreSelection`. Update the documented precedence and current test that intentionally encodes environment-over-explicit precedence.

### 2. Cancel between spawn and durable STARTED

**Contract to settle before coding:** once a cancel control record is durably accepted while an invocation marker is open, it cannot be finalized as `nothing_running` solely because Bridge has not yet persisted `STARTED`. The accepted target is immutable and remains bound to the same delivery/session/claim generation; the active owner must apply the pending intent when the execution becomes addressable. If no owner can reach the execution, report a durable pending/unavailable state rather than claiming it stopped. A kill is not proof that external side effects did not happen; the delivery remains uncertain unless existing evidence proves otherwise. Repeated handling is idempotent and must not retarget another delivery.

**Test-first scenarios:**

- `test_cancel_during_runtime_start_window_is_applied_to_spawned_execution`: fake runtime signals after process registration but before yielding `started`; another thread submits/handles cancel; release runtime; assert the same delivery remains the target, its active owner applies terminate, and the durable result is never `nothing_running`.
- `test_cancel_before_spawn_still_prevents_invocation`: ensure no new process is started and invocation marker resolves using the existing safe pre-start path.
- `test_cancel_after_started_retains_existing_behavior`: preserve current established termination and certainty outcome.
- `test_replayed_cancel_does_not_terminate_later_delivery`: replay accepted control after first target is terminal; assert write-once result and unchanged target identity.
- `test_lost_claim_during_pending_cancel_never_targets_unfenced_execution`: concurrency/fencing case; assert no runtime effect after the Bridge loses ownership.
- `test_cross_process_control_reports_unavailable_runtime_owner_explicitly`: a second Bridge process can persist intent but cannot address the first process's in-memory runtime handle; assert honest pending/unavailable semantics until the owning process observes intent. Do not persist an irreversible `failed` outcome that hides an active local process.

**Design gate:** use a deterministic event/barrier, not sleeps. Confirm how `Bridge.handle_control` can race with `cycle()` and whether the active execution handle can be recovered safely. Do not change certainty transitions or auto-ack a killed run in this batch.

CC's independent review recommends a deterministic hook after the invocation gate and before the first runtime event. This is a test seam, not itself the design fix. Its central correction is that the control target is decided once; therefore pending cancellation must be persisted for the open invocation rather than selecting a different target after `STARTED`.

### 3. MCP sessions, read scope, and privileged writes

**Contract decision required:** clarify whether `register_session(... allowed_streams=...)` is an access-control boundary or only an append constraint. This design assumes it is a boundary. Every stream-scoped read and write must validate an active session and its stream scope. Unscoped sessions retain current broad access only if the host explicitly registers that capability. Control records (`control.cancel`, `control.pause`, `control.redirect`) require a distinct explicit capability; ordinary append capability cannot issue them. Resource listing must not disclose streams outside scope.

**Test-first scenarios after the contract is approved:**

- `test_scoped_session_cannot_read_other_stream_slice`: authorized stream succeeds; other stream raises `MCPSessionInvalidError`; no content is returned.
- `test_scoped_session_cannot_read_other_stream_resource`: same for `resources/read`.
- `test_scoped_session_resource_list_only_contains_allowed_streams`: no IDs/titles for disallowed streams.
- `test_scoped_session_cannot_read_work_or_search_skills_without_capability`: `get_work_tree` and `search_skills` are currently sessionless too; define whether they are scoped by stream or need separate resource capabilities.
- `test_missing_or_revoked_session_cannot_read_or_append`: cover each read/write port and revoked status.
- `test_default_append_capability_rejects_control_kinds`: ordinary capability cannot append control records; explicitly authorized control capability can only issue permitted effects/targets.
- `test_stream_scope_and_author_scope_are_both_enforced`: preserve existing append author binding and add read checks.
- `test_unscoped_host_session_compatibility`: pin explicitly documented broad host behavior if retained.

**Compatibility note:** current `resources/read` has only a URI parameter. Decide how trusted session identity is conveyed (transport-bound context is preferred; a caller-supplied capability ID is weaker). Do not silently add a session field without updating MCP schemas, client docs, and compatibility tests.

Prefer one MCP-specific `AccessScope`/capability policy called by every stream/resource/tool port. Keep its authorization facts independent of the CLI and Core model; the Core package should not learn about MCP clients. Add tests that exercise the policy through public JSON-RPC dispatch, not only direct helper calls.

### 4. Snapshot-consistent integrity audit

**Contract:** all database reads contributing to one `diag integrity` result use one read-only SQLite snapshot. A concurrent commit may appear wholly before or wholly after the audit snapshot, never split across expected and actual projection reads. Audit remains strictly non-mutating.

**Tests:**

- `test_integrity_projection_comparison_uses_single_snapshot`: controlled hook between expected/actual queries commits an observation+rollup pair; result remains OK for the pinned snapshot.
- `test_integrity_still_reports_preexisting_projection_drift`: retain existing drift detection and exit code.
- `test_integrity_audit_remains_read_only`: assert query-only mode/unchanged logical state and no sidecar/table creation on missing or read-only store paths.
- `test_integrity_snapshot_releases_after_report`: writer can commit after audit exits; no leaked read transaction.

### 5. Bridge-aware stream health

**Contract:** preserve Core stream status (`OPEN`/`CLOSED`) separately from Bridge delivery status. Report whether a peer/stream is blocked by unresolved certainty, with the blocking delivery identity, peer, certainty, and concise resolution hint. If an unresolved delivery blocks the next ask, command status cannot be `OK`; the CLI exit code must be documented and stable. Read-only command must not reconcile, prune sessions, or mutate records.

**Tests:**

- `test_stream_health_reports_started_unacked_delivery_as_blocking`: fake runtime leaves STARTED; health reports `BLOCKED_UNCERTAIN` (or approved equivalent) and correct delivery ID.
- `test_stream_health_reports_open_stream_without_delivery_block_as_healthy`.
- `test_stream_health_reports_closed_core_state_independently_of_bridge_state`.
- `test_stream_health_multiple_peers_identifies_only_blocked_peers`.
- `test_stream_health_never_mutates_bridge_or_core_state`.
- CLI tests assert human/JSON shape and exit code for healthy, degraded, missing stream, and unavailable Bridge schema.

### 6. Human CLI rendering

**Contract:** all values from persisted/user-controlled text are rendered as one visible line with terminal control characters escaped or replaced. Cursor metadata and row boundaries are generated only by the renderer. JSON output preserves original values via JSON escaping. Width/truncation behavior stays deterministic.

**Tests:**

- `test_page_renderer_escapes_ansi_and_c0_controls` with ESC, CR, TAB, BEL, and backspace in each displayed field.
- `test_page_renderer_cannot_forge_cursor_or_rows` with newline plus `next_cursor:` and table delimiters.
- `test_page_renderer_preserves_zero_and_false_values`: `0` and `False` must not collapse to an empty cell.
- `test_json_output_preserves_control_characters_as_escaped_data`.
- `test_terminal_safe_rendering_does_not_change_unicode_or_valid_spacing`.
- Cover peer, stream, and session list/show paths; share a sanitizer only if it has no hidden terminal-width side effects.

### 7. Monitor overrun schedule

**Contract:** interval is a fixed-rate cadence anchored to monotonic time. If a cycle overruns one or more slots, skip expired slots and wait until the next future slot; never run catch-up probes back-to-back. Document that actual probe runtime can exceed interval and that missed slots are skipped.

**Tests:**

- `test_monitor_waits_until_next_future_slot_after_probe_overrun`: fake monotonic times, interval 60, cycle duration 75; start times 0, 120, 240.
- `test_monitor_does_not_sleep_when_probe_finishes_before_deadline`: sleep remaining interval precisely.
- `test_monitor_skips_multiple_elapsed_slots`: duration >2 intervals; next start aligns to first future slot.
- `test_monitor_cycle_count_and_shutdown_are_unchanged`: preserve count semantics and interruptible shutdown.
- Replace the existing no-sleep assertion for a 75-second overrun with the new cadence oracle.

### 8. ClaimStore DDL on ask path

**Contract:** constructing a ClaimStore against the current schema with the current trigger set performs no DDL and does not change `PRAGMA schema_version`. New database creation and upgrade/legacy repair still install exactly the required triggers. Future-schema rejection remains before any mutation. Initialization does not weaken append-only protections.

**Tests:**

- `test_reopening_claim_store_does_not_change_schema_version`: initialize once, capture version and trigger SQL/names, construct again; version and trigger definitions remain byte/semantically stable.
- `test_fresh_claim_store_installs_expected_trigger_set`.
- `test_legacy_claim_store_repairs_only_known_obsolete_triggers` with explicit legacy fixtures.
- `test_future_claim_schema_refuses_before_ddl`: preserve existing refusal tests and assert schema version/triggers/tables unchanged.
- `test_finalization_triggers_still_reject_update_delete_replace`.
- `test_concurrent_claim_store_open_does_not_hold_unnecessary_writer_lock`: two independent connections/processes; establish lock behavior without brittle wall-time assertion.
- Benchmark separately: repeated `ask` setup with warm DB and concurrent writer, report schema version change, lock wait, and setup p50/p95. Do not use a CI timing threshold as correctness oracle.

**Design preference:** use an explicit extension migration/version or an idempotent compare-before-DDL path. Do not blindly skip trigger verification, because existing code intentionally replaces unknown/legacy trigger sets. Preserve exact schema compatibility and `refuse_future_schema` ordering.

### 9. Query and projection efficiency

**Single-stream health contract/tests:** target only the requested stream and its member/offset/evidence rows; result must match the full projection for that stream and avoid loading unrelated streams/records. Instrument query parameters or use a populated multi-stream fixture and a query trace; avoid asserting implementation-private helper names.

**N+1 work/skills contract/tests:** list/search results are produced from one consistent database snapshot with a bounded query count independent of item count. Compare full returned values against the current getters; test zero, one, and many items, ordering, missing associated data, and malformed JSON error policy. Measure connection/query counts rather than unstable absolute milliseconds.

**Benchmark:** compare 10/1,000/100,000 records across 1/100/1,000 streams; compare item lists of 1/100/1,000 entries. Capture wall time, SQL count, connections, peak memory. Use realistic Windows and Linux runs because the reported CI time is longer on Windows. Avoid expanding scope to connection pooling unless these measurements show it is necessary.

### 10–12. Remaining test-first contracts

- **Windows process tree:** fake `taskkill` result for parent-not-found while a descendant remains alive; result must remain unconfirmed. Parent missing and no descendants verified can be classified as complete. Test success, access denied, timeout, parent exit race, child survival, and fallback. Never report confirmed termination based on parent poll alone.
- **Session IDs:** accepted vendor session IDs begin with an alphanumeric and then use the documented allowed alphabet; reject `--last`, `-x`, whitespace, shell punctuation, empty, and overlength values at both adapter output and persisted Bridge boundary. Accept valid punctuation already supported. Ensure argv remains a sequence with no shell.
- **Session eligibility projection:** compute read-only eligibility reasons for exact handle: peer/stream/workspace/fingerprint/model/binding mismatch, expired age, tombstoned/lost state, and uncertain blocker. Redact secret binding fields. Do not prune or mutate as a side effect of list/show.
- **Uncertain delivery resolution:** design explicit operator choices (confirm not executed/retry authorization, accept outcome/ack, abandon) with evidence, identity, actor, timestamp, and irreversible audit. Require the exact stream/peer/delivery ID and expected certainty/revision; stale or mismatched confirmation fails. Never infer not-executed merely from process kill.

Two additional MCP protocol/data-contract items surfaced in CC's follow-up and should be triaged after access scope is defined: caller-supplied `created_at` is accepted by append, so document it as authored time or replace it with server-owned time while retaining idempotent replay behavior; `is_initialized` is set by `initialize` but dispatch does not enforce initialization. Add protocol tests only if PeerHub intends to require the MCP initialize lifecycle. These are not the same severity as cross-stream reads.

## Core file size, decomposition, and dependencies

### What the line counts show

At the reviewed commit, the actual `peerhub/core` package totals about 1,321 physical lines. Its largest files are `store.py` (581), `models.py` (258), and `migrations.py` (233), with smaller workspace/wire/read-only helpers and schemas. The 1,838-line `peerhub/extensions/bridge.py` is not in `peerhub.core`; it is the largest stateful orchestration module. Other large files are adapter runtime/parser (1,209), quota probes (1,048), skills (925), and CLI dispatcher (861). Treating every long file as a Core issue would misidentify the architectural boundary.

### `peerhub/core/store.py`: mostly justified cohesion, with one safe seam

`CoreStore` holds the database lifecycle, restore-lineage guard, preflight, Core connection setup, read/write units of work, and five entity operation groups (peer, stream, record, offset). The shared transaction and validation helpers keep multi-table invariants together: stream membership, append position/idempotency, close-state enforcement, and offset CAS/head bounds. The 581 lines are large enough to make navigation harder, but not evidence that the class needs a repository-per-table rewrite. A compatibility facade can retain `CoreStore` while pure row codecs or error classification move behind it; leave transaction owners and multi-table mutations together.

**Recommended:** preserve `CoreStore` as the public facade and preserve its connection/UoW/write boundaries. If file navigation becomes a demonstrated maintenance problem, extract only pure, stable seams behind facade delegation: storage error classification/exception definitions, row codecs, and perhaps read-only projection queries. Keep append, CAS, and cross-table mutations in one transactional owner. Add new internal modules to both architecture gates and keep `peerhub.core.store.CoreStore` imports/API unchanged. Do not perform a line-count-driven refactor in the same PR as correctness fixes.

**Not recommended without evidence:** one class/module per table; generic repository base classes; moving transaction creation into mixins; splitting `_tx` from every method without explicit UoW ownership. These patterns can obscure transaction scope, multiply connection openings, and make invariants harder to audit.

### Bridge is a genuine decomposition candidate, but not a safe opportunistic edit

`Bridge` spans roughly 1,452 method lines in one class and combines delivery orchestration, evidence/certainty transitions, recovery, control, session selection/retention, and projection helpers. These are distinct concepts, but the durable state machine intentionally connects them. Candidate later boundaries are pure selectors/serializers, read-only projections, session-selection policy, and control-record validation/application behind a narrow internal port. Keep transition ownership and state-changing workflows in one explicit coordinator until characterization tests prove a narrower owner. Decomposition should preserve SQL trigger invariants, ordering, fences, and certainty transitions exactly. Do not move cancellation/control code before the cancel contract is fixed.

`IncrementalEventParser` in `adapters/base.py` is also a substantial, separable concern (roughly 443 lines) from subprocess lifecycle (`CliRuntimeTarget`). Its parsing behavior can be characterized with the existing adapter tests and moved to an internal parser module if navigation or test ownership justifies it. Keep vendor-specific parse rules explicit; a large generic parser can be harder to understand than three small provider adapters.

First add/confirm behavioral characterization at public API boundaries, then extract one cohesive area at a time while keeping the public `Bridge` API stable. The repository's architecture tests explicitly inspect allowed modules/imports; update `tests/continuity/test_m2_m3_exit_gates.py` and `tests/communication/architecture/test_arch_extension_graph.py` together for any new extension module. Avoid circular imports and ensure the Core dependency direction remains intact.

### Commonization and external components

PeerHub already uses standard-library `sqlite3` for deliberate SQLite transactions/triggers, Pydantic for typed validation, `jsonschema` for contracts, and `psutil` for process/platform support. No evidence shows an absent general-purpose dependency is the source of the identified issues.

- **SQLAlchemy/SQLModel:** would add an abstraction and dependency but would not eliminate SQLite write locks, schema-version churn, or N+1 queries automatically. It risks obscuring existing explicit transaction/trigger behavior. Do not introduce for these findings.
- **Alembic:** potentially useful only if extension-schema migration ownership and ordering are being redesigned. The repo already has guarded, ordered migrations and future-schema refusal; one trigger-set hot-path issue does not justify wholesale migration replacement.
- **Pydantic/jsonschema additions:** no need; already present and not a performance bottleneck indicated by evidence.
- **SQLite utility commonization:** there are repeated read-only connection/snapshot patterns across `diag.py`, `ask.py`, and Core's `read_uow`, but their architecture constraints differ: diagnostics cannot import `CoreStore`; some reads need one snapshot; ask reads only selected metadata; Core applies restore-lineage checks. A small dependency-neutral helper is worth considering only after mapping these contracts and updating architecture gates. Do not funnel all three through a generic abstraction that hides read-only mode, snapshot pinning, or restore checks.
- **External process library:** no evidence supports replacement of current argv/no-shell bounded runner; process-tree behavior is platform-sensitive and must retain explicit certainty semantics.

The best near-term efficiency gains are algorithmic: stop repeated schema DDL, filter diagnostics to the requested stream, and remove N+1 connection/query patterns. These need measurements at representative cardinalities before claiming user-visible speed gains.

## Review and release gates for implementation

Before each implementation batch, turn the matching scenarios above into failing tests and confirm each fails for the intended reason. Then change the smallest owned module set, run the focused tests, deterministic full suite and pyright as required by repository guidance, and update architecture gates when module boundaries move. No provider quota/live tests are needed for these changes unless an adapter/runtime contract is modified; the cancel and process-tree changes should use deterministic fake runtimes and controlled OS subprocess tests. After correctness passes, run the specified non-gating benchmarks and report workload, platform, sample count, and distributions.

## Peer review status

The original delivered reviews from `cc`, `cx`, and `ag` are the cross-peer baseline. A bounded second-round critique from `cc.standard` delivered. It agrees on the database precedence, cancel race, MCP scope, repeated DDL, diagnostic truthfulness, and monitor cadence. It corrected the cancel priority to P1, identified falsy-value loss in `_print_page`, noted MCP `get_work_tree`/`search_skills` and `created_at`/initialization-contract gaps, and cautioned that the Windows process-tree claim is only a hypothesis and must preserve uncertainty. The follow-up `cx.effort` and `ag.effort` calls ended uncertain without reviewer text. Their output is not treated as consensus and the listed open MCP capability and state-machine decisions remain pre-implementation gates.

The GitHub line inventory was produced by script from tracked UTF-8 files and its category sums were recomputed locally; CC did not independently rerun this inventory. Likewise, the `schema_version` 6→14→22 experiment is recorded from the prior isolated disposable-database reproduction, not independently rerun by CC.
