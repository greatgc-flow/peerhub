# PeerHub improvement design and pre-TDD specification — 2026-10-15

## Objective and decision rules

This design takes the findings in [the cross-peer review](PEERHUB_REVIEW_2026_10_15.md) to the point immediately before writing tests. No implementation or test changes are included here. The order weighs user-data safety, confirmed impact, and the size of the state-machine or compatibility change. A risk hypothesis must first become a deterministic failing test; a performance change must have a workload benchmark that measures the claimed cost.

## Priority and implementation batches

| Order | Item | Priority / confidence | Why this order | Batch boundary |
| ---: | --- | --- | --- | --- |
| 1 | Explicit database selection precedence | P1, reproduced | A command can mutate a different database than the operator selected. Contract is simple and cross-command. | Small isolated CLI/config fix. |
| 2 | Cancel during spawn-to-STARTED window | P1 consequence if concurrent control is supported; not yet reproduced | The code has a race window, but whether an accepted cancel can miss the registered process needs deterministic characterization. Fixing it touches durable state and process ownership. | First add a race test; only design a state change if it fails. |
| 3 | MCP scoped reads and privileged record writes | P2, host-contract dependent; library-level, not CLI-exposed | MCP registration documents scopes, while stream reads and resource reads bypass them. Control writes need an explicit capability contract. | Clarify which host capabilities are promised, then update MCP API compatibly. |
| 4 | Snapshot-consistent integrity audit | P2, code path confirmed | Can falsely report corruption during ordinary concurrent writes; isolated read-transaction change. | Diagnostics only. |
| 5 | Bridge-aware stream health | P2, code path confirmed | Users can see `OK` while a subsequent ask is blocked. Requires an honest combined operational projection. | Read-only diagnostics and CLI output contract. |
| 6 | Human output terminal-control sanitization | P2, reproduced | Stored text can forge rows or issue terminal controls. Scope is human renderers; JSON remains exact. | CLI rendering only. |
| 7 | Per-ask ClaimStore schema DDL | P3, repeated schema churn reproduced; user-visible cost unproven | Recreates four triggers on every ask and takes a write transaction. This is maintenance/lock hygiene; do not frame it as a runtime bottleneck without contention evidence. | Small initialization cleanup only if it preserves legacy-trigger repair. |
| 8 | Targeted single-stream health and N+1 listing reads | P2/P3, query shapes confirmed; scale impact unmeasured | Clear query inefficiency, but size the benefit before broad optimization. | Separate diagnostics and projection optimization changes. |
| 9 | Vendor session ID option-shaped values | P3, input gap confirmed | Prevent IDs such as `--last` being parsed as CLI flags. Small validation change. | Adapter validation only. |
| 10 | Session eligibility visibility and uncertain-delivery resolution | P2/P3 design work | Important recovery/UX gap, but it requires a product contract and auditable operator intent. Do not conflate with routine fixes. | Separate design/feature proposal before implementation. |

Items 2 and 3 should not be combined into one patch: they have different owners, contracts, and rollback criteria. Item 2 is characterization first, not an approved state-machine rewrite. Items 4–7 are individually testable and should remain independently reviewable. Performance item 8 follows correctness because the measured current store is small and no scale claim is established.

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
- `test_explicit_db_and_workspace_precedence_matches_documented_contract`: cover the documented `ask --workspace` interaction and ensure store provenance matches the selected file. Do not add path alias/symlink matrices unless canonical path identity is an explicit supported contract.
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

**Contract decision required:** clarify whether `register_session(... allowed_streams=...)` is an access-control boundary or only an append constraint. This design treats a registered scoped session as a boundary. Every operation made on behalf of that scope must enforce it. Trusted, explicitly unbound host calls may retain current broad access if that is a documented embedding mode. Control records (`control.cancel`, `control.pause`, `control.redirect`) require a distinct explicit capability; ordinary append capability cannot issue them. Resource listing must not disclose streams outside scope. Do not require a caller-supplied `session_id` on every wire method until the host's trusted identity propagation is defined.

**Test-first scenarios after the contract is approved:**

- `test_scoped_session_cannot_read_other_stream_slice`: authorized stream succeeds; other stream raises `MCPSessionInvalidError`; no content is returned.
- `test_scoped_session_cannot_read_other_stream_resource`: same for `resources/read`.
- `test_scoped_session_resource_list_only_contains_allowed_streams`: no IDs/titles for disallowed streams.
- `test_scoped_session_cannot_read_work_or_search_skills_without_capability`: `get_work_tree` and `search_skills` are currently sessionless too; define them as separate `work.read`/`skills.search` capabilities instead of pretending `allowed_streams` scopes non-stream resources.
- `test_missing_or_revoked_session_cannot_read_or_append`: cover each read/write port and revoked status.
- `test_default_append_capability_rejects_control_kinds`: ordinary capability cannot append control records; explicitly authorized control capability can only issue permitted effects/targets.
- `test_stream_scope_and_author_scope_are_both_enforced`: preserve existing append author binding and add read checks.
- `test_unscoped_host_session_compatibility`: pin explicitly documented broad host behavior if retained.

**Compatibility note:** current `resources/read` has only a URI parameter. Decide how trusted session identity is conveyed (transport-bound context is preferred; a caller-supplied capability ID is weaker). Do not silently add a session field without updating MCP schemas, client docs, and compatibility tests.

Prefer one small MCP-specific capability policy called by each protected tool/resource port. Keep its authorization facts independent of the CLI and Core model; the Core package should not learn about MCP clients. This is commonization within one ownership boundary, not a universal access layer. Add tests through public JSON-RPC dispatch once trusted caller identity reaches the handler.

### 4. Snapshot-consistent integrity audit

**Contract:** all database reads contributing to one `diag integrity` result use one read-only SQLite snapshot. A concurrent commit may appear wholly before or wholly after the audit snapshot, never split across expected and actual projection reads. Audit remains strictly non-mutating.

**Tests:**

- `test_integrity_projection_comparison_uses_single_snapshot`: controlled hook between expected/actual queries commits an observation+rollup pair; result remains OK for the pinned snapshot.
- `test_integrity_still_reports_preexisting_projection_drift`: retain existing drift detection and exit code.
- `test_integrity_audit_remains_read_only`: assert query-only mode/unchanged logical state and no sidecar/table creation on missing or read-only store paths.
- `test_integrity_snapshot_releases_after_report`: writer can commit after audit exits; no leaked read transaction.

### 5. Bridge-aware stream health

**Architecture constraint:** do not teach Core-only `ReadonlyDiag` to import or query Bridge internals; ARCH-004 intentionally constrains it. Add a Bridge-owned read-only health projection and let the CLI composition root combine the projections. Preserve Core stream status (`OPEN`/`CLOSED`) separately from Bridge delivery state. Report whether a peer/stream is blocked by unresolved certainty, with blocking delivery identity, peer, certainty, and concise resolution hint. If unresolved delivery blocks the next ask, combined operational status must not say `OK`; settle the exit code with the CLI contract owner. The reader must not reconcile, prune sessions, or mutate records.

**Tests:**

- `test_stream_health_reports_started_unacked_delivery_as_blocking`: fake runtime leaves STARTED; health reports `BLOCKED_UNCERTAIN` (or approved equivalent) and correct delivery ID.
- `test_stream_health_reports_open_stream_without_delivery_block_as_healthy`.
- `test_stream_health_reports_closed_core_state_independently_of_bridge_state`.
- `test_stream_health_multiple_peers_identifies_only_blocked_peers`.
- `test_stream_health_never_mutates_bridge_or_core_state`.
- CLI tests assert human/JSON shape and exit code for healthy, degraded, missing stream, and unavailable Bridge schema.
- Architecture tests assert Core and `ReadonlyDiag` still import no Bridge code, and composition stays at the CLI boundary. If one atomic snapshot across Core and Bridge tables is required, pass an already verified read-only connection through a narrow reader protocol; do not add a broad database service layer solely for diagnostics.

### 6. Human CLI rendering

**Contract:** all values from persisted/user-controlled text are rendered as one line with terminal controls escaped or replaced by safe visible characters/spaces. No raw controls or embedded line breaks reach the terminal. Cursor metadata and row boundaries are generated only by the renderer. JSON output preserves original values via JSON escaping. Width/truncation behavior stays deterministic.

`cli.view._safe_text` already sanitizes dashboard values but importing the whole view module into the main CLI dispatcher would pull extension diagnostics into Core-only command startup. If sharing the same behavior, move only the dependency-free sanitizer into `peerhub.cli.text` and use it from both modules; do not introduce a generic formatting framework. Alternatively keep a tiny local sanitizer if the shared module would be used only once.

**Tests:**

- `test_page_renderer_neutralizes_ansi_and_c0_controls` with ESC, CR, TAB, BEL, and backspace in each displayed field.
- `test_page_renderer_cannot_forge_cursor_or_rows` with newline plus `next_cursor:` and table delimiters.
- `test_page_renderer_preserves_zero_and_false_values`: `0` and `False` must not collapse to an empty cell.
- `test_page_renderer_measures_sanitized_visible_cells`: width calculation and printed cells use the same sanitized text, so escaping/replacement cannot break table alignment.
- `test_core_only_cli_keeps_working_without_extension_imports`: if a shared CLI text helper is added, importing it for Core list output must not import `peerhub.extensions`.
- `test_json_output_preserves_control_characters_as_escaped_data`.
- `test_terminal_safe_rendering_does_not_change_unicode_or_valid_spacing`.
- Cover peer, stream, and session list/show paths; share a sanitizer only if it has no hidden terminal-width side effects.

### Monitor cadence: retain the existing behavior pending a product requirement

CX and AG correctly challenged the proposed cadence change. `test_monitor_skips_elapsed_monotonic_slots_after_slow_probe` explicitly asserts that a two-cycle run does not add another sleep after its slow first cycle. The implementation waits for a future monotonic deadline when cycle work ends early; after a slow cycle it advances expired deadlines and begins one next cycle immediately. Depending on how quickly that next cycle finishes, the next start may be less than one interval after the delayed start. That behavior is already reflected by code/test intent, and no provider-quota harm was demonstrated.

Do not replace the existing test with a 0/120/240 schedule without an owner decision. The CLI currently promises a cycle interval but the precise overrun rule is ambiguous. Keep this out of the improvement batch; only clarify help/docs if the product owner confirms which cadence is intended. If a minimum start-to-start interval is required, add a three-or-more-cycle test before changing scheduling.

### 8. ClaimStore DDL on ask path

**Contract:** constructing a ClaimStore against the current schema with the current trigger set should not replace identical triggers or change `PRAGMA schema_version`. New database creation and upgrade/legacy repair still install exactly the required triggers. Future-schema refusal remains before mutation. Initialization does not weaken append-only protections.

**Tests:**

- `test_reopening_claim_store_does_not_change_schema_version`: initialize once, capture version and trigger SQL/names, construct again; version and trigger definitions remain byte/semantically stable.
- `test_fresh_claim_store_installs_expected_trigger_set`.
- `test_legacy_claim_store_repairs_only_known_obsolete_triggers` with explicit legacy fixtures.
- `test_future_claim_schema_refuses_before_ddl`: preserve existing refusal tests and assert schema version/triggers/tables unchanged.
- `test_finalization_triggers_still_reject_update_delete_replace`.
- No broad timing suite is justified for this cleanup. If real concurrent asks show lock waits, add a focused concurrent-open regression and state its workload before claiming a performance improvement.

**Design preference:** use an explicit extension migration/version or an idempotent compare-before-DDL path. Do not blindly skip trigger verification, because existing code intentionally replaces unknown/legacy trigger sets. Preserve exact schema compatibility and `refuse_future_schema` ordering.

### 9. Query and projection efficiency

**Single-stream health contract/tests:** target only the requested stream and its member/offset/evidence rows; result must match the full projection for that stream and avoid loading unrelated streams/records. Instrument query parameters or use a populated multi-stream fixture and a query trace; avoid asserting implementation-private helper names.

**N+1 work/skills contract/tests:** list/search results are produced from one consistent database snapshot with a bounded query count independent of item count. Compare full returned values against the current getters; test zero, one, and many items, ordering, missing associated data, and malformed JSON error policy. Measure connection/query counts rather than unstable absolute milliseconds.

**Measurement gate:** first capture query and connection counts at the current store size and one representative larger fixture. Only add a larger benchmark matrix if the measured difference changes a real command's latency or boundedness contract. Absolute timing thresholds do not belong in correctness tests; don't add connection pooling absent evidence.

### 10–12. Remaining test-first contracts

- **Windows process tree:** current code already returns unconfirmed when `taskkill` fails and explicitly states that `proc.kill()` is best-effort. A natural-exit race may produce an imprecise error, but no failed tree-kill or harmful effect was demonstrated. Do not prioritize a change. If a regression is reproduced, test parent-not-found with a surviving descendant and preserve `termination_confirmed=False`; never infer whole-tree termination from parent exit alone.
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

`Bridge` spans roughly 1,452 method lines in one class and combines delivery orchestration, evidence/certainty transitions, recovery, control, session selection/retention, and projection helpers. These are distinct concepts, but the durable state machine intentionally connects them. Candidate later boundaries are pure selectors/serializers and catch-up construction. Keep transition ownership and state-changing workflows in one explicit coordinator until characterization tests prove a narrower owner. Do not move cancellation/control code before the cancel contract is fixed. AG suggested a `bridge_projections.py` split for lines 1770–1838, but those lines include cancellation gating and uncertain reconciliation, so that proposed boundary is not a read-only projection and is rejected. A control module may become reasonable after cancel tests define the owner/lease boundary; it is not a current priority.

`IncrementalEventParser` in `adapters/base.py` is also a substantial, separable concern (roughly 443 lines) from subprocess lifecycle (`CliRuntimeTarget`). Its parsing behavior can be characterized with existing adapter tests and moved to an internal parser module if navigation or test ownership justifies it. Keep vendor-specific parse rules explicit; a large generic parser can be harder to understand than three small provider adapters. YAML/frontmatter parsing and directory hashing in `skills.py` are pure candidates, but extract them only when a separate owner or reuse exists; a file split alone does not improve the event-sourced catalog boundary.

First add/confirm behavioral characterization at public API boundaries, then extract one cohesive area at a time while keeping the public `Bridge` API stable. The repository's architecture tests explicitly inspect allowed modules/imports; update `tests/continuity/test_m2_m3_exit_gates.py` and `tests/communication/architecture/test_arch_extension_graph.py` together for any new extension module. Avoid circular imports and ensure the Core dependency direction remains intact.

### Commonization and external components

PeerHub already uses standard-library `sqlite3` for deliberate SQLite transactions/triggers, Pydantic for typed validation, `jsonschema` for contracts, and `psutil` for process/platform support. No evidence shows an absent general-purpose dependency is the source of the identified issues.

- **SQLAlchemy/SQLModel:** would add an abstraction and dependency but would not eliminate SQLite write locks, schema-version churn, or N+1 queries automatically. It risks obscuring existing explicit transaction/trigger behavior. Do not introduce for these findings.
- **Alembic:** potentially useful only if extension-schema migration ownership and ordering are being redesigned. The repo already has guarded, ordered migrations and future-schema refusal; one trigger-set hot-path issue does not justify wholesale migration replacement.
- **Pydantic/jsonschema additions:** no need; already present and not a performance bottleneck indicated by evidence.
- **SQLite utility commonization:** do not add a shared helper in this work. `diag.py` integrity needs a local read transaction; its regular renderer already has `_open()`/snapshot handling; `ask.py` reads selected metadata; Core `read_uow` also enforces restore lineage. These contracts differ, and the integrity fix is local. If Bridge health later requires one atomic Core+extension snapshot, define the narrow connection handoff at the CLI/reader boundary first; don't introduce a general database service layer.
- **External process library:** no evidence supports replacement of current argv/no-shell bounded runner; process-tree behavior is platform-sensitive and must retain explicit certainty semantics.

The best near-term efficiency gains are algorithmic: stop repeated schema DDL, filter diagnostics to the requested stream, and remove N+1 connection/query patterns. These need measurements at representative cardinalities before claiming user-visible speed gains.

**Architecture decision:** the current Core → extension ports → CLI composition-root structure is already explicit and guarded by import-graph tests. Do not add another global service/repository layer. The only clear commonization candidate in this review is an MCP-owned capability policy because several MCP ports need the same scope rules. Shared SQLite primitives remain a research candidate, not an approved extraction: Core restore lineage, diagnostic snapshots, and extension transactions have different owners and guarantees. Keep any later split behind existing public facades and update both architecture gates.

## Review and release gates for implementation

Before each implementation batch, turn the matching scenarios above into failing tests and confirm each fails for the intended reason. Then change the smallest owned module set, run the focused tests, deterministic full suite and pyright as required by repository guidance, and update architecture gates when module boundaries move. No provider quota/live tests are needed for these changes unless an adapter/runtime contract is modified; the cancel and process-tree changes should use deterministic fake runtimes and controlled OS subprocess tests. After correctness passes, run the specified non-gating benchmarks and report workload, platform, sample count, and distributions.

## Peer review status

The original delivered reviews from `cc`, `cx`, and `ag` are the cross-peer baseline. A bounded second-round critique from `cc.standard`, `cx.standard`, and `ag.standard` delivered after retries. CC/AG regard the cancel race as a serious P1 code path; CX says to keep it a hypothesis until deterministic characterization proves the process escapes cancellation. This design requires that test first and treats severity as conditional. All three identify the MCP mismatch, while CX/AG caution that severity depends on the embedded host contract and identity transport; it is P2 here. CX/AG challenged the monitor cadence as an unnecessary P2 fix. The existing test covers only two cycles and does not establish the cadence after that; there is no demonstrated quota harm, so this design removes the behavior change and leaves cadence wording as an owner decision. CC found falsy-value loss in `_print_page`; all priorities are adjusted accordingly. CX advised against premature Core/persistence helpers and broad scale matrices. AG's proposed Bridge read-only projection split at lines 1770–1838 is rejected because those methods include cancellation and reconciliation state changes. The remaining disagreements are recorded as gates, not consensus.

The GitHub line inventory was produced by script from tracked UTF-8 files and its category sums were recomputed locally; no follow-up reviewer independently reran it. Likewise, the `schema_version` 6→14→22 experiment is recorded from the prior isolated disposable-database reproduction, not independently rerun in the follow-up reviews. The failed first-round CC/CX/AG calls recorded `runtime_timeout` after 60 seconds with no output and confirmed termination. CX and AG delivered on retry with 240-second silence limits. The cause was the short silence timeout, not demonstrated quota exhaustion. The original uncertain records remain in the default peer chat streams; retry responses used dedicated streams.
