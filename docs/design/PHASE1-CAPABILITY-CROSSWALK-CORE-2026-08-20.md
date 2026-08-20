# Phase 1: Core Capability & Consumer Migration Crosswalk (`_sys/core`)

> **DOCUMENT: Phase 1 Dialectical Revision (Round 3, Task 1 of 6 — Second Half)**  
> **AUTHOR:** `ag` (DeepMind Advanced Agentic Coding)  
> **SCOPE:** Exhaustive capability decomposition of all 30 legacy files in `_sys/core`  
> **TARGET PATH:** `docs/design/PHASE1-CAPABILITY-CROSSWALK-CORE-2026-08-20.md`  
> **COMPLIANCE:** Addresses finding **R2-01** from cx's Round 2 counter-critique (`docs/design/PHASE1-CX-COUNTERCRITIQUE-ROUND2-2026-08-20.md`) & **DIR-004** (Measured-Only Claims).

---

## 1. Executive Summary & Namespace Disambiguation

In Round 2 critique finding **R2-01**, cx established that the term *capability* had become severely overloaded across three conflicting domains:
1. **`migration_capability_id` (Migration / Architecture Domain):** Functional responsibility and ownership decomposition for legacy files and exported symbols during Phase 1–3 refactoring.
2. **`adapter_feature` (Runtime Contract Domain):** The strict runtime capability enum defined in `peerhub/adapters/contract.py`, strictly restricted to `SESSION`, `STREAM`, and `GRACEFUL_CANCEL`.
3. **`coverage_case_id` (Release Matrix Domain):** Exact release-proof and test matrix ledger rows defined in the test taxonomy.

This document provides the normative **`migration_capability_id`** crosswalk for all **30 files** in `_sys/core`, completing the second half of the 69-file legacy inventory alongside the CLI crosswalk (`docs/design/PHASE1-CAPABILITY-CROSSWALK-CLI-2026-08-20.md`).

For mixed-concern files—especially `hub.py` (which houses the 90-action dispatch surface, ask runner, lease manager, mailbox broker, and session engine) and `provisioner.py` (which mixes generic portable toolchain installation with peer CLI configuration and lease validation)—sub-capabilities and individual exported symbols are granularly partitioned into distinct rows with explicit ownership boundaries.

### Reserved Fields Notation
- **`adapter_feature`**: *[Reserved — Unpopulated in migration crosswalk]* — Stays strictly `SESSION`, `STREAM`, `GRACEFUL_CANCEL` in `peerhub/adapters/contract.py`.
- **`coverage_case_id`**: *[Reserved — TBD by subsequent Phase 1 test matrix]*.

---

## 2. Exhaustive 30-File Verification & Summary Statistics

- **Total Legacy Files Covered:** 30 / 30 (100% MECE verified across `_sys/core`)
- **Total Crosswalk Capability Rows:** 58
- **Dispositions Breakdown:**
  - **`replace`**: 36 rows (Replaced by native PeerHub core engines, adapters, and telemetry)
  - **`stay`**: 16 rows (Preserved in Engram host toolchain, launcher, and virtualization)
  - **`split`**: 4 rows (Split between host toolchain installer/layout and PeerHub core/binding)
  - **`deprecate`**: 2 rows (Deprecated legacy forwarding shims to be decommissioned)

### 30 Files Checklist
| # | File Name | Kind | Disposition Summary | Row Count |
|---|---|---|---|---|
| 1 | `config.py` | Python Module | `split/replace` | 2 |
| 2 | `dispatch.bat` | Windows Batch Wrapper | `stay` | 1 |
| 3 | `dispatcher.py` | Python Module | `stay` | 2 |
| 4 | `doctor.py` | Python Module | `stay` | 1 |
| 5 | `env_loader.py` | Python Module | `stay` | 1 |
| 6 | `hub.py` | Python Module | `replace` | 5 |
| 7 | `hub_config.json` | JSON Configuration | `replace` | 1 |
| 8 | `hub_context.py` | Python Module | `replace` | 3 |
| 9 | `hub_error.py` | Python Module | `replace` | 1 |
| 10 | `hub_health.py` | Python Module | `replace` | 2 |
| 11 | `hub_interceptor.py` | Python Module | `replace` | 1 |
| 12 | `hub_logging.py` | Python Module | `replace` | 1 |
| 13 | `hub_peer.py` | Python Module | `replace` | 7 |
| 14 | `hub_profile_router.py` | Python Module | `replace` | 2 |
| 15 | `launcher.py` | Python Module | `stay` | 2 |
| 16 | `operational_guard_matrix.py` | Python Module | `replace` | 1 |
| 17 | `pathlayout.py` | Python Module | `split` | 1 |
| 18 | `provisioner.py` | Python Module | `split/stay/replace` | 4 |
| 19 | `quota.py` | Python Module | `replace` | 1 |
| 20 | `quota_capabilities.py` | Python Module | `replace` | 1 |
| 21 | `registrar.py` | Python Module | `stay` | 1 |
| 22 | `relocator.py` | Python Module | `deprecate` | 1 |
| 23 | `scrubber.py` | Python Module | `split/stay` | 2 |
| 24 | `setup.py` | Python Module | `deprecate` | 1 |
| 25 | `snapshot.py` | Python Module | `replace` | 7 |
| 26 | `tidy_temp.py` | Python Module | `stay` | 1 |
| 27 | `timestamps.py` | Python Module | `replace` | 1 |
| 28 | `updater.py` | Python Module | `stay` | 1 |
| 29 | `version_resolver.py` | Python Module | `stay` | 1 |
| 30 | `virtualizer.py` | Python Module | `stay` | 2 |

---

## 3. Migration Capability Crosswalk Ledger

### Row 1: `mig.core.config.config_manager`
- **Legacy File / Symbol:** `_sys/core/config.py:ConfigManager`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `peerhub.config.manager` (PeerHub hierarchical config) / `core.config` (Engram host config)
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/core/hub_peer.py`, `_sys/core/dispatcher.py`, `_sys/ai/backlog.json`
- **State Read / Written:** Reads global config (`_sys/config.json`), shared config (`_sys/config/shared.json`), workspace-local config (`.peerhub/config.json` or `.ai/config.json`); caches config in memory; writes modified global/workspace keys atomically.
- **External Effects:** File reads/writes to JSON config files on disk.
- **Compatibility Actions / Fixtures:** `fixture_config_manager_layered`; adapter layer to import legacy config files during Phase 1 transition.
- **Retirement Condition:** PeerHub switches to native `peerhub.config` schema; Engram host uses its own isolated config loader.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 2: `mig.core.config.strict_loader`
- **Legacy File / Symbol:** `_sys/core/config.py:load_strict`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.config.loader`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_context.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_config_strict.py`
- **State Read / Written:** Reads raw JSON files; enforces strict JSON schema validation, UTF-8 decoding, and required keys without silent fallbacks.
- **External Effects:** Raises `ValueError` / `KeyError` on malformed or schema-divergent JSON configurations.
- **Compatibility Actions / Fixtures:** `fixture_strict_json_validation`.
- **Retirement Condition:** All configuration parsing standardized on `peerhub.config.loader`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 3: `mig.core.dispatch.bootstrap_bat`
- **Legacy File / Symbol:** `_sys/core/dispatch.bat`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host bootstrap lifecycle (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `INSTALL.bat`, `CLEANUP.bat`, `STATUS.bat`, `UPDATE.bat`, `TIDY.bat`, `register.bat`, `unregister.bat`
- **State Read / Written:** Reads `%~dp0..`, checks existence of `%SYS_DIR%\env\python\python.exe`; writes no persistent state.
- **External Effects:** Invokes python interpreter with `_sys/core/dispatcher.py` passing CLI arguments, or triggers bootstrap fallback if Python is missing.
- **Compatibility Actions / Fixtures:** Preserved in Engram host root repository; excluded from standalone PeerHub package.
- **Retirement Condition:** Engram host bootstrap modernized with self-contained installer.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 4: `mig.core.dispatcher.pipeline_executor`
- **Legacy File / Symbol:** `_sys/core/dispatcher.py:run_pipeline` / `_run_operation`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host lifecycle dispatcher (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/core/dispatch.bat`, `_sys/core/setup.py`, `_sys/checks/check_pipeline.py`, `_sys/tests/unit/test_dispatcher.py`
- **State Read / Written:** Reads pipeline definition JSON (`_sys/dispatch.json`); executes ordered stages (`virtualize`, `provision`, `register`, `doctor`, `scrub`); reads/writes operation exit codes and status.
- **External Effects:** Executes Python subprocesses or dynamic module imports for host lifecycle phases.
- **Compatibility Actions / Fixtures:** Preserved in Engram host repository; `fixture_host_dispatcher_pipeline`.
- **Retirement Condition:** Engram host refactors internal lifecycle stages.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 5: `mig.core.dispatcher.state_tracker`
- **Legacy File / Symbol:** `_sys/core/dispatcher.py:_write_state` / `_prune_state` / `_build_ctx`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host state manager (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/core/dispatcher.py`, `_sys/core/doctor.py`, `_sys/core/virtualizer.py`, `_sys/tests/unit/test_dispatcher_state.py`
- **State Read / Written:** Reads/writes `_sys/data/state.json` (or `.ai/state.json`); records active host mounts, registration timestamps, and last successful pipeline runs.
- **External Effects:** Atomic file writes to host runtime state store.
- **Compatibility Actions / Fixtures:** Preserved in Engram host repository.
- **Retirement Condition:** Host runtime state schema finalized in Engram.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 6: `mig.core.doctor.health_checker`
- **Legacy File / Symbol:** `_sys/core/doctor.py:run` / `check_python` / `check_subst` / `check_registration` / `check_components` / `check_sessions`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host diagnostic tool (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/checks/check_policy.py`, `_sys/core/dispatcher.py`, `_sys/core/provisioner.py`, `STATUS.bat`, `_sys/tests/unit/test_doctor.py`
- **State Read / Written:** Inspects local Python version, SUBST drive status (`subst` query), HKCU registry keys, component directory layouts, and active terminal sessions.
- **External Effects:** Prints diagnostic report to console; returns exit code (0 for healthy, non-zero for failed checks).
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Engram host diagnostic tooling maintained independently of PeerHub.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 7: `mig.core.env.environment_loader`
- **Legacy File / Symbol:** `_sys/core/env_loader.py:EnvironmentLoader` / `load_json_env`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host environment virtualizer (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/core/dispatcher.py`, `_sys/core/launcher.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_env_loader.py`
- **State Read / Written:** Reads `_sys/env.json` and `_sys/paths.json`; interpolates `${SYS_DIR}`, `${WORKSPACE}`, and system environment variables; resolves absolute paths.
- **External Effects:** Injects resolved paths and variables into `os.environ` or returns customized environment dicts for subprocess spawning.
- **Compatibility Actions / Fixtures:** Preserved in Engram host root; `fixture_env_loader_interpolation`.
- **Retirement Condition:** Engram host packaging stabilization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 8: `mig.core.hub_config.declarations`
- **Legacy File / Symbol:** `_sys/core/hub_config.json`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.config.defaults`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/ai/infra.json`, `_sys/docs/history/protocol-session.md`, `_sys/docs/history/ops/full-repo-mece-inventory-2026-07-10.md`
- **State Read / Written:** Static JSON declaring buffer bounds: `mailbox_max` (500), `handoff_max_chars` (12000), `handoff_max_completed` (5), `handoff_max_issues` (3), `handoff_max_decisions` (3), `handoff_max_consensus` (10), `handoff_max_threads` (5), `large_payload_threshold` (4000).
- **External Effects:** Read during Hub initialization to constrain IPC and session buffer allocations.
- **Compatibility Actions / Fixtures:** Embedded in `peerhub.config.defaults` with frozen dataclass schema `HubLimitsConfig`.
- **Retirement Condition:** JSON file replaced by strongly-typed configuration in `peerhub.config`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 9: `mig.core.context.token_estimator`
- **Legacy File / Symbol:** `_sys/core/hub_context.py:estimate_tokens` / `_cjk_ratio`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.context.token_estimator`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/core/hub_peer.py`, `_sys/tests/unit/test_hub_context_c3.py`, `_sys/tests/unit/test_context_gate.py`
- **State Read / Written:** Analyzes Unicode code points of input text, calculates CJK character density, applies character-to-token heuristic ratios (1.5 for CJK vs 4.0 for Latin); writes no persistent state.
- **External Effects:** Pure function returning estimated integer token count.
- **Compatibility Actions / Fixtures:** `fixture_token_estimator_cjk_ratio` ensuring deterministic parity on Latin, CJK, and mixed inputs.
- **Retirement Condition:** Consolidated into `peerhub.context.token_estimator`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 10: `mig.core.context.context_gate`
- **Legacy File / Symbol:** `_sys/core/hub_context.py:ContextGate` / `resolve_context_target` / `resolve_dispatch_target`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.routing.context_gate`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/core/snapshot.py`, `_sys/tests/unit/test_hub_context_c3.py`, `_sys/tests/unit/test_context_gate.py`, `_sys/ai/protocol.json`
- **State Read / Written:** Reads model context limits from `_sys/ai/model-registry.json` and active session token counts; evaluates capacity thresholds (`warn_pct` 70%, `failover_pct` 85%); plans failover routes or triggers context pruning.
- **External Effects:** Returns `ResolvedContextTarget` or `ResolvedDispatchTarget`; raises `ContextGateError` or `UnknownModelCapacityError` when limits are breached.
- **Compatibility Actions / Fixtures:** `fixture_context_gate_evaluation` with simulated context pressure scenarios.
- **Retirement Condition:** Native implementation in `peerhub.routing.context_gate`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 11: `mig.core.context.types`
- **Legacy File / Symbol:** `_sys/core/hub_context.py:ResolvedContextTarget` / `ResolvedDispatchTarget` / `ContextFailoverPlan` / `ContextGateError`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.types.context`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_context.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_hub_context_c3.py`
- **State Read / Written:** Dataclass structures and custom exceptions representing token allocation decisions.
- **External Effects:** None (in-memory data models).
- **Compatibility Actions / Fixtures:** Typed aliases in `peerhub.types.context`.
- **Retirement Condition:** Legacy dataclass declarations replaced by PeerHub schema types.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 12: `mig.core.error.hub_error_reporter`
- **Legacy File / Symbol:** `_sys/core/hub_error.py:HubError` / `report_error`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.errors.reporter`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/core/hub_peer.py`, `_sys/core/hub_logging.py`, `_sys/ai/error-taxonomy.json`, `_sys/tests/unit/test_hub_error.py`
- **State Read / Written:** Reads `_sys/ai/error-taxonomy.json` and `_sys/ai/governance_params.json`; formats error severity, remediation hints, and 5-Whys diagnostic templates; writes structured error entries via `HubLogger`.
- **External Effects:** Emits formatted ANSI diagnostic banners to stderr; appends error events to `_sys/ai/logs/error-*.jsonl`.
- **Compatibility Actions / Fixtures:** `fixture_error_taxonomy_dispatch`; schema alignment with `peerhub.errors`.
- **Retirement Condition:** Error classification and telemetry consolidated into `peerhub.errors.reporter`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 13: `mig.core.health.health_reader`
- **Legacy File / Symbol:** `_sys/core/hub_health.py:HealthReader` / `_load_json`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.health.reader`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/cli/console_runner.py`, `_sys/tests/unit/test_hub_health.py`, `_sys/ai/traceability_map.json`
- **State Read / Written:** Reads `.peer_health.json` files from peer state directories (`P:\.claude`, `P:\.codex`, `P:\.agy`); calculates consecutive failure counts, JSONL log sizes, and gate availability.
- **External Effects:** Computes aggregate health summary across all known peers; filters eligible peers for routing.
- **Compatibility Actions / Fixtures:** `fixture_health_reader_snapshots`.
- **Retirement Condition:** Health queries served by `peerhub.health.reader`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 14: `mig.core.health.peer_health_state`
- **Legacy File / Symbol:** `_sys/core/hub_health.py:PeerHealthState`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.types.health`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_health.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_hub_health.py`
- **State Read / Written:** Represents immutable snapshot of peer health: `context_status`, `gate_open`, `consecutive_failures`, `jsonl_mb`, `checked_at`, `availability`, `entrypoint_ok`, `authenticated`.
- **External Effects:** Serialization via `to_dict()`.
- **Compatibility Actions / Fixtures:** `peerhub.types.health.PeerHealthState` dataclass.
- **Retirement Condition:** Dataclass migrated to PeerHub types.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 15: `mig.core.interceptor.hub_interceptor`
- **Legacy File / Symbol:** `_sys/core/hub_interceptor.py:HubInterceptor` / `InterceptResult`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.security.interceptor`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/tests/unit/l3_mocked/test_hub_enforced_crosscheck.py`, `docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md`
- **State Read / Written:** Inspects requested Hub action name, payload, and origin peer tier; reads guard rules and broadcast policies; writes intercept notices to room review threads.
- **External Effects:** Blocks unverified mutating actions; broadcasts pending high-risk mutations to peer channels for multi-agent review.
- **Compatibility Actions / Fixtures:** `fixture_hub_interceptor_evaluation`.
- **Retirement Condition:** Security interception natively handled by `peerhub.security.interceptor`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 16: `mig.core.logging.hub_logger`
- **Legacy File / Symbol:** `_sys/core/hub_logging.py:HubLogger`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.telemetry.logger`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/core/hub_error.py`, `_sys/core/snapshot.py`, `_sys/ai/logging-config.json`, `_sys/tests/unit/test_hub_logging.py`
- **State Read / Written:** Reads `_sys/ai/logging-config.json`; maintains daily log files under `_sys/ai/logs/`; logs 7 distinct event types (`ipc`, `console`, `cost`, `error`, `reasoning`, `token_calibration`, `model_drift`, `self_care`); manages date-based log rolling.
- **External Effects:** Appends JSON Lines entries to structured log files on disk.
- **Compatibility Actions / Fixtures:** `fixture_hub_structured_logging` with log format validation.
- **Retirement Condition:** Telemetry and audit logging unified in `peerhub.telemetry.logger`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 17: `mig.core.peer.adapter_contract`
- **Legacy File / Symbol:** `_sys/core/hub_peer.py:PeerAdapter` / `BaseAdapter`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.base`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_peer.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_hub_peer.py`, `_sys/ai/backlog.json`
- **State Read / Written:** Abstract base class defining `build_cmd`, `build_session_cmd`, `prepare_input`, `session_fingerprint`, `extract_session_id`, `extract_usage`, `parse_output`, `context_policy`, `get_session_state`, `store_session_state`.
- **External Effects:** Subprocess command construction and output parsing abstractions.
- **Compatibility Actions / Fixtures:** `peerhub.adapters.base.BaseAdapter` contract implementation.
- **Retirement Condition:** Replaced by native PeerHub adapter architecture.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 18: `mig.core.peer.claude_adapter`
- **Legacy File / Symbol:** `_sys/core/hub_peer.py:ClaudeAdapter`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.claude`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_peer.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_hub_peer_claude.py`, `_sys/tests/unit/test_hub_peer.py`
- **State Read / Written:** Builds Claude CLI arguments (`--print`, `--session-id`, `--dangerously-skip-permissions`); parses Claude stdout/stderr; extracts token counts and project session state.
- **External Effects:** None directly (constructs invocation plans and parses responses).
- **Compatibility Actions / Fixtures:** `fixture_claude_adapter_golden_output`.
- **Retirement Condition:** Replaced by `peerhub.adapters.claude`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 19: `mig.core.peer.codex_adapter`
- **Legacy File / Symbol:** `_sys/core/hub_peer.py:CodexAdapter`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.codex`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_peer.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_hub_peer_codex.py`, `_sys/tests/unit/test_hub_peer.py`
- **State Read / Written:** Builds Codex CLI arguments (`exec`, `-c sandbox="workspace-write"`, `--jsonl`); decodes JSONL stream frames; extracts session ID and completion tokens.
- **External Effects:** In-memory stream parsing and command templating.
- **Compatibility Actions / Fixtures:** `fixture_codex_adapter_jsonl_stream`.
- **Retirement Condition:** Replaced by `peerhub.adapters.codex`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 20: `mig.core.peer.agy_adapter`
- **Legacy File / Symbol:** `_sys/core/hub_peer.py:AgyAdapter`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.agy`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_peer.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_hub_peer_agy.py`, `_sys/tests/unit/test_hub_peer.py`
- **State Read / Written:** Builds Antigravity CLI arguments; locates conversation transcripts in brain storage; extracts token telemetry and session resume tokens.
- **External Effects:** File reads from brain storage transcripts.
- **Compatibility Actions / Fixtures:** `fixture_agy_transcript_decoder`.
- **Retirement Condition:** Replaced by `peerhub.adapters.agy`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 21: `mig.core.peer.virtual_adapter`
- **Legacy File / Symbol:** `_sys/core/hub_peer.py:VirtualAdapter`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.virtual`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_peer.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_hub_peer_virtual.py`
- **State Read / Written:** Mock adapter for testing and deterministic synthetic peer simulation; synthesizes fixed responses and usage statistics.
- **External Effects:** Pure in-memory simulation.
- **Compatibility Actions / Fixtures:** `fixture_virtual_adapter_echo`.
- **Retirement Condition:** Integrated as test fixture in `peerhub.adapters.virtual`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 22: `mig.core.peer.token_normalizer`
- **Legacy File / Symbol:** `_sys/core/hub_peer.py:_normalize_usage` / `_token_int` / `_usage_from_obj`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.telemetry.token_extractor`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_peer.py`, `_sys/core/hub.py`, `_sys/core/snapshot.py`, `_sys/tests/unit/test_hub_peer.py`
- **State Read / Written:** Normalizes heterogeneous token telemetry structures (input tokens, output tokens, total tokens, cache creation, cache read) into canonical integer mappings.
- **External Effects:** None (pure functional data conversion).
- **Compatibility Actions / Fixtures:** `fixture_token_telemetry_normalization`.
- **Retirement Condition:** Standardized in `peerhub.telemetry.token_extractor`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 23: `mig.core.peer.orchestration_resolver`
- **Legacy File / Symbol:** `_sys/core/hub_peer.py:normalize_orchestration` / `profile_catalog` / `resolve_node_id` / `canonical_reality_model_key`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.governance.orchestration_resolver`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_peer.py`, `_sys/core/hub.py`, `_sys/core/hub_profile_router.py`, `_sys/core/snapshot.py`, `_sys/checks/check_cli_reality.py`
- **State Read / Written:** Reads `_sys/ai/orchestration.json`, `_sys/ai/model-registry.json`; normalizes peer hierarchy, root aliases, profile IDs, and reality model keys.
- **External Effects:** In-memory validation and profile catalog lookup.
- **Compatibility Actions / Fixtures:** `fixture_orchestration_profile_catalog`.
- **Retirement Condition:** Consolidated into `peerhub.governance.orchestration_resolver`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 24: `mig.core.router.profile_selector`
- **Legacy File / Symbol:** `_sys/core/hub_profile_router.py:select_profile_node` / `_score_query` / `_profile_for_score`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.routing.profile_router`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/core/snapshot.py`, `_sys/tests/unit/test_hub_profile_router.py`, `_sys/docs-v2/ops/mega-mece-audit-2026-07-16.md`
- **State Read / Written:** Reads `_sys/ai/routing-config.json` and query text; computes heuristic score from risk keywords, task markers, and explicit routing tags; resolves optimal runtime profile (`standard`, `effort`, `deepthink`).
- **External Effects:** Pure function returning deterministic `ProfileDecision`.
- **Compatibility Actions / Fixtures:** `fixture_profile_routing_decision_matrix`.
- **Retirement Condition:** Replaced by `peerhub.routing.profile_router`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 25: `mig.core.router.types`
- **Legacy File / Symbol:** `_sys/core/hub_profile_router.py:ProfileDecision` / `ProfileRoutingError`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.types.routing`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub_profile_router.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_hub_profile_router.py`
- **State Read / Written:** Dataclass capturing routing decision outcome: `profile_id`, `root_peer`, `score`, `reason`, `markers_hit`, `fallback_used`.
- **External Effects:** None (in-memory data structure).
- **Compatibility Actions / Fixtures:** `peerhub.types.routing.ProfileDecision`.
- **Retirement Condition:** Migrated to PeerHub core types.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 26: `mig.core.launcher.environment_builder`
- **Legacy File / Symbol:** `_sys/core/launcher.py:build_env` / `_resolve_path_entry` / `_map_subst_drive`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host launcher (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/core/launcher.py`, `_sys/checks/check_cli_canary.py`, `_sys/checks/check_sandbox_behavior.py`, `_sys/tests/unit/test_launcher_paths.py`
- **State Read / Written:** Reads `_sys/env.json`, `_sys/paths.json`, and host drive state; maps SUBST virtual drives; constructs child process environment dictionary.
- **External Effects:** Sets up system PATH and process environment.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Engram host environment packaging stabilization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 27: `mig.core.launcher.process_launcher`
- **Legacy File / Symbol:** `_sys/core/launcher.py:main` / `_relocate`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host launcher (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/start.bat`, `_sys/cli/launch.bat`, `_sys/checks/check_cli_reality.py`, `Engram.exe`
- **State Read / Written:** Reads command line arguments; launches target portable shells or editors in customized environment.
- **External Effects:** Spawns interactive subprocess (e.g. bash, cmd, or editor) with inherited or redirected stdio.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Engram host modernization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 28: `mig.core.guard.guard_oracle`
- **Legacy File / Symbol:** `_sys/core/operational_guard_matrix.py:expected_decision` / `action_group` / `is_mutating` / `enumerate_cases`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.security.guard_matrix`
- **Current Real Consumers (Empirically Measured):** `_sys/checks/check_operational_guard_matrix.py`, `_sys/checks/check_operational_guard_shadow.py`, `_sys/tests/unit/test_operational_guard_matrix.py`, `README.md`
- **State Read / Written:** Authoritative decision oracle defining exact expected security outcomes for every combination of Hub action (90 actions) and origin caller tier (`tier_terminal`, `tier_peer`, `tier_subagent`, `tier_untrusted`).
- **External Effects:** Evaluates whether action is mutating, permitted, blocked, or requires explicit review.
- **Compatibility Actions / Fixtures:** `fixture_operational_guard_oracle` running exhaustive matrix tests.
- **Retirement Condition:** Integrated as canonical guard evaluator in `peerhub.security.guard_matrix`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 29: `mig.core.pathlayout.path_resolver`
- **Legacy File / Symbol:** `_sys/core/pathlayout.py:PathLayout` / `resolve_path_layout`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `peerhub.storage.layout` / Engram host pathlayout
- **Current Real Consumers (Empirically Measured):** `_sys/core/pathlayout.py`, `_sys/docs-v2/00-MANIFEST.md`, `_sys/docs-v2/ops/engram-refactor-blueprint-2026-07-20.md`, `_sys/ai/unreferenced_functions_baseline.json`
- **State Read / Written:** Resolves canonical roots for system, portable dev home, AI directories, and workspace paths; returns frozen `PathLayout` dataclass.
- **External Effects:** Path normalization and directory existence checks.
- **Compatibility Actions / Fixtures:** `fixture_path_layout_resolution`.
- **Retirement Condition:** PeerHub paths managed by `peerhub.storage.layout`; Engram host paths managed by Engram.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 30: `mig.core.provisioner.toolchain_installer`
- **Legacy File / Symbol:** `_sys/core/provisioner.py:ensure_tool` / `ensure_runtime` / `_download` / `_extract` / `_secure_download` / `_install_atomic`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host provisioner (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/core/provisioner.py`, `_sys/core/setup.py`, `_sys/core/updater.py`, `INSTALL.bat`, `_sys/checks/check_deps.py`
- **State Read / Written:** Reads `_sys/runtimes.json`; downloads zip/tar archives from GitHub/npm/python.org; verifies hashes; atomically extracts into `_sys/tools/` and `_sys/env/`.
- **External Effects:** Network downloads, filesystem extractions, binary installations.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain root; excluded from PeerHub core package.
- **Retirement Condition:** Maintained as Engram host toolchain provisioning engine.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 31: `mig.core.provisioner.peer_cli_config`
- **Legacy File / Symbol:** `_sys/core/provisioner.py:ensure_peer_cli` / `_resolve_peer_key` / `_peer_postcondition`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `Engram host CLI installer` (physical binary setup) / `peerhub.adapters.executable_binding` (immutable validation & binding receipt)
- **Current Real Consumers (Empirically Measured):** `_sys/core/provisioner.py`, `_sys/ai/capability-declarations.json`, `_sys/ai/orchestration.json`, `_sys/tests/unit/test_provisioner_peer.py`
- **State Read / Written:** Reads peer declarations in `_sys/ai/orchestration.json` and `_sys/runtimes.json`; installs AI CLI tools (`claude`, `codex`, `agy`); verifies binary execution postconditions.
- **External Effects:** Executes canary runs of peer CLIs to verify functionality; generates executable admission receipts.
- **Compatibility Actions / Fixtures:** `fixture_peer_executable_binding_receipt`.
- **Retirement Condition:** Physical CLI installation handled by Engram; runtime binding and validation owned by PeerHub.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 32: `mig.core.provisioner.lease_gate`
- **Legacy File / Symbol:** `_sys/core/provisioner.py:_is_peer_leased`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.coordination.lease_client`
- **Current Real Consumers (Empirically Measured):** `_sys/core/provisioner.py:deploy`, `_sys/tests/unit/test_provisioner_lease.py`
- **State Read / Written:** Reads `.leases.json` in peer storage to check whether a peer is currently holding an active session lease prior to performing upgrades or mutations.
- **External Effects:** Aborts deployment/upgrade with warning if peer is actively leased to prevent mid-session corruption.
- **Compatibility Actions / Fixtures:** `fixture_lease_query_client`.
- **Retirement Condition:** Replaced by PeerHub lease coordination client.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 33: `mig.core.provisioner.deferred_installer`
- **Legacy File / Symbol:** `_sys/core/provisioner.py:_drain_deferred_lazy` / `_add_deferred` / `_save_deferred`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host provisioner (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/core/provisioner.py`, `_sys/core/dispatcher.py`, `_sys/tests/unit/test_provisioner_deferred.py`
- **State Read / Written:** Reads/writes `_sys/data/deferred_installs.json`; tracks non-critical tools scheduled for background or lazy installation.
- **External Effects:** Drains pending installs sequentially upon startup.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Engram installer optimization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 34: `mig.core.quota.pacing_calculator`
- **Legacy File / Symbol:** `_sys/core/quota.py:calculate_pacing` / `time_to_exhaustion` / `get_remaining_seconds`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.telemetry.pacing`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/diag.py`, `_sys/core/snapshot.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_quota_pacing.py`
- **State Read / Written:** Calculates remaining duration in quota reset windows (5-hour rolling, monthly credits); computes pacing ratios, consumption burn rates, and time-to-exhaustion projections.
- **External Effects:** Pure calculation returning structured pacing metrics.
- **Compatibility Actions / Fixtures:** `fixture_quota_pacing_calculations`.
- **Retirement Condition:** Consolidated into `peerhub.telemetry.pacing`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 35: `mig.core.quota.capabilities_lookup`
- **Legacy File / Symbol:** `_sys/core/quota_capabilities.py:root_quota_capability` / `supports_reset_credits`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.governance.quota_capabilities`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/diag.py`, `_sys/core/hub.py`, `_sys/core/snapshot.py`, `_sys/ai/orchestration.json`, `_sys/tests/unit/test_quota_capabilities.py`
- **State Read / Written:** Reads `_sys/ai/orchestration.json`; queries root peer quota tracking capabilities (reset credits, sliding window, fixed tier limits).
- **External Effects:** Pure lookup function.
- **Compatibility Actions / Fixtures:** `fixture_quota_capability_matrix`.
- **Retirement Condition:** Replaced by `peerhub.governance.quota_capabilities`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 36: `mig.core.registrar.context_menu_manager`
- **Legacy File / Symbol:** `_sys/core/registrar.py:apply` / `remove` / `_register_entry` / `_unregister_entry` / `_write_relay`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host registrar (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/manage.py`, `_sys/core/dispatcher.py`, `register.bat`, `unregister.bat`, `_sys/tests/unit/test_registrar.py`
- **State Read / Written:** Reads `_sys/context_menu.json` and `_sys/paths.json`; writes Windows registry keys under `HKCU\Software\Classes\Directory\Background\shell` and `Directory\shell`.
- **External Effects:** Modifies Windows Explorer context menu entries and writes `.relay.bat` launchers.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Maintained as Engram host Windows Explorer integration utility.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 37: `mig.core.relocator.path_relocator_shim`
- **Legacy File / Symbol:** `_sys/core/relocator.py:relocate`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `core.launcher (Engram host)`
- **Current Real Consumers (Empirically Measured):** `_sys/core/relocator.py`, `_sys/docs/history/ops/remaining-items.md`, `docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md`
- **State Read / Written:** Thin 22-line forwarding shim importing and calling `core.launcher._relocate`.
- **External Effects:** Delegates to `core.launcher`.
- **Compatibility Actions / Fixtures:** Deprecated; callers migrated to direct `core.launcher` invocation.
- **Retirement Condition:** Removed once all references are updated.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 38: `mig.core.scrubber.cleanup_engine`
- **Legacy File / Symbol:** `_sys/core/scrubber.py:run` / `_tier1`..`_tier5`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host scrubber (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/cleanup.py`, `_sys/core/dispatcher.py`, `CLEANUP.bat`, `_sys/tests/unit/test_scrubber.py`
- **State Read / Written:** Scans directories across `_sys/data/`, `_sys/tools/`, portable cache folders; measures disk usage; removes obsolete build artifacts, stale temp directories, and unreferenced runtime files.
- **External Effects:** Deletes files and directories matching retention tiers; unlinks orphaned junctions.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Maintained as Engram host maintenance utility.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 39: `mig.core.scrubber.ai_ephemeral_cleaner`
- **Legacy File / Symbol:** `_sys/core/scrubber.py:_clean_ai_ephemeral`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `peerhub.storage.cleanup` / Engram host scrubber
- **Current Real Consumers (Empirically Measured):** `_sys/core/scrubber.py`, `_sys/cli/cleanup.py`, `_sys/tests/unit/test_scrubber.py`
- **State Read / Written:** Scans `.ai/sessions/`, `.ai/mailbox/`, `.ai/logs/`; identifies stale peer execution sessions and orphaned lock files.
- **External Effects:** Prunes expired AI sessions and IPC queues exceeding age limits.
- **Compatibility Actions / Fixtures:** `fixture_storage_cleanup_retention`.
- **Retirement Condition:** AI ephemeral storage cleanup owned by `peerhub.storage.cleanup`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 40: `mig.core.setup.setup_shim`
- **Legacy File / Symbol:** `_sys/core/setup.py`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `core.provisioner (Engram host)`
- **Current Real Consumers (Empirically Measured):** `_sys/core/dispatcher.py`, `_sys/docs/history/ops/full-system-purpose-audit-2026-07-12.md`, `docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md`
- **State Read / Written:** 37-line legacy wrapper that imports and executes `core.provisioner.deploy`.
- **External Effects:** Delegates to `core.provisioner`.
- **Compatibility Actions / Fixtures:** Deprecated; dispatcher and scripts updated to invoke provisioner directly.
- **Retirement Condition:** Removed once legacy pipeline references are updated.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 41: `mig.core.snapshot.telemetry_collector`
- **Legacy File / Symbol:** `_sys/core/snapshot.py:collect_snapshot` / `gather_peer` / `normalize_peer`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.telemetry.snapshot_collector`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/diag.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_snapshot_collector.py`, `_sys/ai/orchestration.json`
- **State Read / Written:** Aggregates live and cached telemetry across all configured peers into a canonical immutable telemetry snapshot dictionary; computes snapshot hash.
- **External Effects:** Reads peer config, cache files, and active session states; outputs snapshot JSON.
- **Compatibility Actions / Fixtures:** `fixture_snapshot_collection_schema`.
- **Retirement Condition:** Replaced by `peerhub.telemetry.snapshot_collector`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 42: `mig.core.snapshot.codex_scraper`
- **Legacy File / Symbol:** `_sys/core/snapshot.py:_codex_rate_limits` / `_codex_quota_buckets` / `_codex_context`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.codex.telemetry`
- **Current Real Consumers (Empirically Measured):** `_sys/core/snapshot.py`, `_sys/tests/unit/test_snapshot_codex.py`
- **State Read / Written:** Reads Codex cache and rate limit responses; extracts 5-hour rolling limits, credit balance, and reset timestamps.
- **External Effects:** In-memory parsing and telemetry extraction.
- **Compatibility Actions / Fixtures:** `fixture_codex_telemetry_scraper`.
- **Retirement Condition:** Moved to `peerhub.adapters.codex.telemetry`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 43: `mig.core.snapshot.claude_scraper`
- **Legacy File / Symbol:** `_sys/core/snapshot.py:_parse_claude_usage` / `_claude_usage_quotas` / `_parse_claude_usage_reset`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.claude.telemetry`
- **Current Real Consumers (Empirically Measured):** `_sys/core/snapshot.py`, `_sys/tests/unit/test_snapshot_claude.py`
- **State Read / Written:** Parses Claude usage metrics, 5-hour quota reset windows, credit availability, and pacing limits.
- **External Effects:** In-memory telemetry parsing.
- **Compatibility Actions / Fixtures:** `fixture_claude_telemetry_scraper`.
- **Retirement Condition:** Moved to `peerhub.adapters.claude.telemetry`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 44: `mig.core.snapshot.agy_scraper`
- **Legacy File / Symbol:** `_sys/core/snapshot.py:_load_ag_last_good_quota` / `_load_ag_session_context`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.agy.telemetry`
- **Current Real Consumers (Empirically Measured):** `_sys/core/snapshot.py`, `_sys/tests/unit/test_snapshot_agy.py`
- **State Read / Written:** Reads Antigravity cached quota and active session token telemetry from brain storage.
- **External Effects:** Reads local JSON state files.
- **Compatibility Actions / Fixtures:** `fixture_agy_telemetry_scraper`.
- **Retirement Condition:** Moved to `peerhub.adapters.agy.telemetry`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 45: `mig.core.snapshot.headroom_pacing`
- **Legacy File / Symbol:** `_sys/core/snapshot.py:_derive_headroom_rows` / `pacing_admission_for_profile` / `_quota_remaining` / `_context_remaining`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.telemetry.headroom_evaluator`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/diag.py`, `_sys/core/snapshot.py`, `_sys/tests/unit/test_snapshot_pacing.py`
- **State Read / Written:** Evaluates per-profile quota headroom, burn rate against pacing admission limits, and generates diagnostic alert rows.
- **External Effects:** Pure evaluation returning headroom data structures.
- **Compatibility Actions / Fixtures:** `fixture_headroom_pacing_evaluation`.
- **Retirement Condition:** Replaced by `peerhub.telemetry.headroom_evaluator`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 46: `mig.core.snapshot.arbiter_engine`
- **Legacy File / Symbol:** `_sys/core/snapshot.py:select_arbiter` / `evaluate_arbiter_trigger` / `build_final_opinion_record`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.governance.arbiter_evaluator`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/core/snapshot.py`, `_sys/tests/unit/test_arbiter_selection.py`, `_sys/docs/history/ops/token-load-balancing-design.md`
- **State Read / Written:** Reads DIR-005 configuration; evaluates whether peer dissent or high-risk thresholds trigger smartest-model final arbiter invocation; constructs `FinalOpinionRecord`.
- **External Effects:** Governs invocation of premium arbiter model.
- **Compatibility Actions / Fixtures:** `fixture_arbiter_trigger_matrix`.
- **Retirement Condition:** Replaced by `peerhub.governance.arbiter_evaluator`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 47: `mig.core.snapshot.failover_selector`
- **Legacy File / Symbol:** `_sys/core/snapshot.py:snapshot_failover_target` / `should_switch_session_peer` / `select_load_balanced_peer`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.routing.failover_selector`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/core/snapshot.py`, `_sys/tests/unit/test_snapshot_failover.py`
- **State Read / Written:** Analyzes snapshot headroom and health scores to determine optimal failover peer target or load-balanced peer selection.
- **External Effects:** Selects next active peer candidate for execution routing.
- **Compatibility Actions / Fixtures:** `fixture_failover_selection_rules`.
- **Retirement Condition:** Replaced by `peerhub.routing.failover_selector`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 48: `mig.core.tidy.debris_sweeper`
- **Legacy File / Symbol:** `_sys/core/tidy_temp.py:main` / `plan_ipc` / `plan_root_tmp` / `plan_data_temp` / `plan_brain_logs` / `plan_vscode_logs` / `plan_pytest_of_great`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host maintenance (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `TIDY.bat`, `_sys/core/tidy_temp.py`, `_sys/tests/unit/test_tidy_temp.py`, `docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md`
- **State Read / Written:** Scans age and pattern of temporary debris files in `tmp/`, VSCode logs, pytest cache, brain logs; calculates safe removal candidates.
- **External Effects:** Deletes orphaned temporary files older than configurable retention age.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Maintained as host background cleanup utility.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 49: `mig.core.timestamps.iso_parser`
- **Legacy File / Symbol:** `_sys/core/timestamps.py:parse_iso_timestamp`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.types.timestamps`
- **Current Real Consumers (Empirically Measured):** `_sys/core/hub.py`, `_sys/core/quota.py`, `_sys/core/snapshot.py`, `_sys/tests/unit/test_timestamps.py`
- **State Read / Written:** Parses ISO-8601 strings into UTC datetime objects; handles 'Z' suffix, fractional seconds, timezone offsets, and naive time normalization policies.
- **External Effects:** None (pure functional parsing).
- **Compatibility Actions / Fixtures:** `fixture_timestamp_iso_parser`.
- **Retirement Condition:** Unified in `peerhub.types.timestamps`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 50: `mig.core.updater.update_dispatcher`
- **Legacy File / Symbol:** `_sys/core/updater.py:run` / `_parse_args`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host updater (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `UPDATE.bat`, `_sys/core/updater.py`, `_sys/tests/unit/test_updater.py`, `docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md`
- **State Read / Written:** Parses CLI update target arguments; checks component versions via `version_resolver`; invokes `provisioner` update routines.
- **External Effects:** Orchestrates toolchain and runtime component updates.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Maintained as Engram host toolchain updater.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 51: `mig.core.version.version_resolver`
- **Legacy File / Symbol:** `_sys/core/version_resolver.py:resolve_latest` / `_resolve_github` / `_resolve_npm` / `_resolve_sqlite` / `_classify_windows_asset_arch`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host version resolver (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/checks/check_tool_updates.py`, `_sys/core/updater.py`, `_sys/core/provisioner.py`, `_sys/tests/unit/test_version_resolver.py`
- **State Read / Written:** Queries GitHub API, npm registry, and SQLite release pages; caches HTTP responses in `_sys/data/version_cache.json`; parses asset URLs and version tags.
- **External Effects:** Outbound HTTP requests to release registries; reads/writes local cache.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Maintained as host version discovery utility.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 52: `mig.core.virtualizer.subst_manager`
- **Legacy File / Symbol:** `_sys/core/virtualizer.py:mount` / `unmount` / `_assign_subst` / `_release_subst` / `_get_subst_mappings`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host virtualizer (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/manage.py`, `_sys/core/dispatcher.py`, `_sys/core/doctor.py`, `_sys/checks/self_care.py`, `_sys/tests/unit/test_virtualizer.py`
- **State Read / Written:** Reads configured virtual drive mappings from `_sys/paths.json`; executes Windows `subst.exe` commands to mount and unmount virtual drive letters (e.g. `P:`).
- **External Effects:** Mounts and releases Windows SUBST virtual drives.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Maintained as Engram host filesystem virtualization engine.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 53: `mig.core.virtualizer.junction_manager`
- **Legacy File / Symbol:** `_sys/core/virtualizer.py:_ensure_junction` / `_remove_junction` / `_set_peer_junctions` / `_remove_peer_junctions`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host virtualizer (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** `_sys/core/virtualizer.py`, `_sys/core/scrubber.py`, `_sys/tests/unit/test_virtualizer.py`
- **State Read / Written:** Creates and validates NTFS directory junctions linking peer directories (e.g. `.claude`, `.codex`, `.agy`) between user profile and portable root.
- **External Effects:** Creates and removes Windows NTFS directory junctions via `cmd.exe /c mklink /J`.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Maintained as Engram host directory junction utility.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 54: `mig.core.hub.action_dispatcher`
- **Legacy File / Symbol:** `_sys/core/hub.py:main` / `_dispatch_action` / 90-action surface (`action_*`)
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.engine.action_dispatcher` / `peerhub.cli.hub`
- **Current Real Consumers (Empirically Measured):** 271 consumer files across `_sys/cli/`, `_sys/ai/common/skills/*` (consensus-vote, context-fill, health-check, lesson-add, peer-propose), `_sys/tests/`
- **State Read / Written:** Parses CLI arguments; maps action verbs to action handlers; validates caller permissions; returns structured JSON output.
- **External Effects:** CLI stdout/stderr output; exit codes.
- **Compatibility Actions / Fixtures:** `fixture_hub_action_dispatch_matrix` covering all 90 ratified actions.
- **Retirement Condition:** All CLI and IPC callers invoke native `peerhub` actions.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 55: `mig.core.hub.peer_ask_engine`
- **Legacy File / Symbol:** `_sys/core/hub.py:action_ask` / `_ask_core` / `_invoke_pipe` / `_invoke_pty` / `_spawn_process`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.engine.invocation_runner`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/msg.py`, `_sys/ai/common/skills/*`, `_sys/tests/unit/test_hub_ask.py`, `_sys/tests/unit/test_hub_ask_contract.py`
- **State Read / Written:** Resolves profile, injects directives, builds command, acquires lease, spawns peer process via pipe or PTY, parses response, extracts tokens, updates health/lease state.
- **External Effects:** Spawns child process for peer CLI; writes to stdin; reads stdout/stderr; manages process lifecycle.
- **Compatibility Actions / Fixtures:** `fixture_hub_ask_invocation_runner`.
- **Retirement Condition:** Replaced by `peerhub.engine.invocation_runner`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 56: `mig.core.hub.lease_manager`
- **Legacy File / Symbol:** `_sys/core/hub.py:_lease_cfg` / `_claim_lease` / `_release_lease` / `_validated_live_lease_pid` / `action_claim_lease` / `action_release_lease`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.coordination.lease_manager`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/console_runner.py`, `_sys/core/hub.py`, `_sys/tests/unit/test_contracts.py`, `_sys/tests/unit/test_lease_concurrency.py`, `DIR-003`
- **State Read / Written:** Reads/writes `.leases.json` under `.peerhub/leases/` or `.ai/leases/`; checks PID liveness on Windows; manages lease timeout, renewal heartbeats, and atomic release.
- **External Effects:** Enforces exclusive single-writer concurrency across terminal and subagents.
- **Compatibility Actions / Fixtures:** `fixture_lease_coordination_lifecycle`.
- **Retirement Condition:** Replaced by `peerhub.coordination.lease_manager`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 57: `mig.core.hub.mailbox_broker`
- **Legacy File / Symbol:** `_sys/core/hub.py:_broker_submit` / `_broker_drain` / `action_send` / `action_broadcast` / `action_broker_submit` / `action_broker_drain` / `action_mark_read`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.messaging.broker`
- **Current Real Consumers (Empirically Measured):** `_sys/cli/msg.py`, `_sys/ai/common/skills/peer-propose.md`, `_sys/tests/unit/test_hub_broker.py`, `_sys/tests/unit/test_hub_mailbox.py`
- **State Read / Written:** Manages message queues under `.ai/mailbox/<peer>/inbox/` and `outbox/`; validates payload size against `hub_config.json`; tracks read receipts and message sequence numbers.
- **External Effects:** Atomic file creation and deletion in mailbox directories.
- **Compatibility Actions / Fixtures:** `fixture_mailbox_broker_delivery`.
- **Retirement Condition:** Replaced by `peerhub.messaging.broker`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 58: `mig.core.hub.session_handoff`
- **Legacy File / Symbol:** `_sys/core/hub.py:action_init_session` / `action_end_session` / `_render_handoff` / `_parse_handoff` / `action_handoff_export` / `action_handoff_import` / `_role_guard` / `_validate_consensus_payload`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.session.manager` / `peerhub.governance`
- **Current Real Consumers (Empirically Measured):** `_sys/ai/common/skills/*`, `_sys/tests/unit/test_hub_session.py`, `_sys/tests/unit/test_hub_handoff.py`, `_sys/ai/protocol.json`
- **State Read / Written:** Manages room lifecycle under `.ai/sessions/room-*/`; generates structured markdown handoff files preserving active issues, decisions, and completed tasks; validates role permissions and consensus proposals.
- **External Effects:** Creates and archives session directories and handoff documents.
- **Compatibility Actions / Fixtures:** `fixture_session_handoff_serialization`.
- **Retirement Condition:** Replaced by `peerhub.session.manager` and `peerhub.governance`.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

---

## 4. Master Crosswalk Matrix Table

| # | Migration Capability ID | Legacy File / Symbol | Disposition | Target Owner / API | Measured Consumers |
|---|---|---|---|---|---|
| 1 | `mig.core.config.config_manager` | `_sys/core/config.py:ConfigManager` | `split` | `peerhub.config.manager / core.config (Engram host)` | _sys/core/hub.py, _sys/core/hub_peer.py, _sys/core/dispatcher.py, _sys/ai/backlog.json |
| 2 | `mig.core.config.strict_loader` | `_sys/core/config.py:load_strict` | `replace` | `peerhub.config.loader` | _sys/core/hub_context.py, _sys/core/hub.py, _sys/tests/unit/test_config_strict.py |
| 3 | `mig.core.dispatch.bootstrap_bat` | `_sys/core/dispatch.bat` | `stay` | `Engram host bootstrap lifecycle (out of PeerHub core)` | INSTALL.bat, CLEANUP.bat, STATUS.bat, UPDATE.bat, TIDY.bat, register.bat, unregister.bat |
| 4 | `mig.core.dispatcher.pipeline_executor` | `_sys/core/dispatcher.py:run_pipeline` | `stay` | `Engram host lifecycle dispatcher (out of PeerHub core)` | _sys/core/dispatch.bat, _sys/core/setup.py, _sys/checks/check_pipeline.py, _sys/tests/unit/test_dispatcher.py |
| 5 | `mig.core.dispatcher.state_tracker` | `_sys/core/dispatcher.py:_write_state` | `stay` | `Engram host state manager (out of PeerHub core)` | _sys/core/dispatcher.py, _sys/core/doctor.py, _sys/core/virtualizer.py, _sys/tests/unit/test_dispatcher_state.py |
| 6 | `mig.core.doctor.health_checker` | `_sys/core/doctor.py:run` | `stay` | `Engram host diagnostic tool (out of PeerHub core)` | _sys/checks/check_policy.py, _sys/core/dispatcher.py, _sys/core/provisioner.py, STATUS.bat, _sys/tests/unit/test_doctor.py |
| 7 | `mig.core.env.environment_loader` | `_sys/core/env_loader.py:EnvironmentLoader` | `stay` | `Engram host environment virtualizer (out of PeerHub core)` | _sys/core/dispatcher.py, _sys/core/launcher.py, _sys/core/hub.py, _sys/tests/unit/test_env_loader.py |
| 8 | `mig.core.hub_config.declarations` | `_sys/core/hub_config.json` | `replace` | `peerhub.config.defaults` | _sys/core/hub.py, _sys/ai/infra.json, _sys/docs/history/protocol-session.md, _sys/docs/history/ops/full-repo-mece-inventory-2026-07-10.md |
| 9 | `mig.core.context.token_estimator` | `_sys/core/hub_context.py:estimate_tokens` | `replace` | `peerhub.context.token_estimator` | _sys/core/hub.py, _sys/core/hub_peer.py, _sys/tests/unit/test_hub_context_c3.py, _sys/tests/unit/test_context_gate.py |
| 10 | `mig.core.context.context_gate` | `_sys/core/hub_context.py:ContextGate` | `replace` | `peerhub.routing.context_gate` | _sys/core/hub.py, _sys/core/snapshot.py, _sys/tests/unit/test_hub_context_c3.py, _sys/tests/unit/test_context_gate.py, _sys/ai/protocol.json |
| 11 | `mig.core.context.types` | `_sys/core/hub_context.py:ResolvedContextTarget` | `replace` | `peerhub.types.context` | _sys/core/hub_context.py, _sys/core/hub.py, _sys/tests/unit/test_hub_context_c3.py |
| 12 | `mig.core.error.hub_error_reporter` | `_sys/core/hub_error.py:HubError` | `replace` | `peerhub.errors.reporter` | _sys/core/hub.py, _sys/core/hub_peer.py, _sys/core/hub_logging.py, _sys/ai/error-taxonomy.json, _sys/tests/unit/test_hub_error.py |
| 13 | `mig.core.health.health_reader` | `_sys/core/hub_health.py:HealthReader` | `replace` | `peerhub.health.reader` | _sys/core/hub.py, _sys/cli/console_runner.py, _sys/tests/unit/test_hub_health.py, _sys/ai/traceability_map.json |
| 14 | `mig.core.health.peer_health_state` | `_sys/core/hub_health.py:PeerHealthState` | `replace` | `peerhub.types.health` | _sys/core/hub_health.py, _sys/core/hub.py, _sys/tests/unit/test_hub_health.py |
| 15 | `mig.core.interceptor.hub_interceptor` | `_sys/core/hub_interceptor.py:HubInterceptor` | `replace` | `peerhub.security.interceptor` | _sys/core/hub.py, _sys/tests/unit/l3_mocked/test_hub_enforced_crosscheck.py, docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md |
| 16 | `mig.core.logging.hub_logger` | `_sys/core/hub_logging.py:HubLogger` | `replace` | `peerhub.telemetry.logger` | _sys/core/hub.py, _sys/core/hub_error.py, _sys/core/snapshot.py, _sys/ai/logging-config.json, _sys/tests/unit/test_hub_logging.py |
| 17 | `mig.core.peer.adapter_contract` | `_sys/core/hub_peer.py:PeerAdapter` | `replace` | `peerhub.adapters.base` | _sys/core/hub_peer.py, _sys/core/hub.py, _sys/tests/unit/test_hub_peer.py, _sys/ai/backlog.json |
| 18 | `mig.core.peer.claude_adapter` | `_sys/core/hub_peer.py:ClaudeAdapter` | `replace` | `peerhub.adapters.claude` | _sys/core/hub_peer.py, _sys/core/hub.py, _sys/tests/unit/test_hub_peer_claude.py, _sys/tests/unit/test_hub_peer.py |
| 19 | `mig.core.peer.codex_adapter` | `_sys/core/hub_peer.py:CodexAdapter` | `replace` | `peerhub.adapters.codex` | _sys/core/hub_peer.py, _sys/core/hub.py, _sys/tests/unit/test_hub_peer_codex.py, _sys/tests/unit/test_hub_peer.py |
| 20 | `mig.core.peer.agy_adapter` | `_sys/core/hub_peer.py:AgyAdapter` | `replace` | `peerhub.adapters.agy` | _sys/core/hub_peer.py, _sys/core/hub.py, _sys/tests/unit/test_hub_peer_agy.py, _sys/tests/unit/test_hub_peer.py |
| 21 | `mig.core.peer.virtual_adapter` | `_sys/core/hub_peer.py:VirtualAdapter` | `replace` | `peerhub.adapters.virtual` | _sys/core/hub_peer.py, _sys/core/hub.py, _sys/tests/unit/test_hub_peer_virtual.py |
| 22 | `mig.core.peer.token_normalizer` | `_sys/core/hub_peer.py:_normalize_usage` | `replace` | `peerhub.telemetry.token_extractor` | _sys/core/hub_peer.py, _sys/core/hub.py, _sys/core/snapshot.py, _sys/tests/unit/test_hub_peer.py |
| 23 | `mig.core.peer.orchestration_resolver` | `_sys/core/hub_peer.py:normalize_orchestration` | `replace` | `peerhub.governance.orchestration_resolver` | _sys/core/hub_peer.py, _sys/core/hub.py, _sys/core/hub_profile_router.py, _sys/core/snapshot.py, _sys/checks/check_cli_reality.py |
| 24 | `mig.core.router.profile_selector` | `_sys/core/hub_profile_router.py:select_profile_node` | `replace` | `peerhub.routing.profile_router` | _sys/core/hub.py, _sys/core/snapshot.py, _sys/tests/unit/test_hub_profile_router.py, _sys/docs-v2/ops/mega-mece-audit-2026-07-16.md |
| 25 | `mig.core.router.types` | `_sys/core/hub_profile_router.py:ProfileDecision` | `replace` | `peerhub.types.routing` | _sys/core/hub_profile_router.py, _sys/core/hub.py, _sys/tests/unit/test_hub_profile_router.py |
| 26 | `mig.core.launcher.environment_builder` | `_sys/core/launcher.py:build_env` | `stay` | `Engram host launcher (out of PeerHub core)` | _sys/core/launcher.py, _sys/checks/check_cli_canary.py, _sys/checks/check_sandbox_behavior.py, _sys/tests/unit/test_launcher_paths.py |
| 27 | `mig.core.launcher.process_launcher` | `_sys/core/launcher.py:main` | `stay` | `Engram host launcher (out of PeerHub core)` | _sys/start.bat, _sys/cli/launch.bat, _sys/checks/check_cli_reality.py, Engram.exe |
| 28 | `mig.core.guard.guard_oracle` | `_sys/core/operational_guard_matrix.py:expected_decision` | `replace` | `peerhub.security.guard_matrix` | _sys/checks/check_operational_guard_matrix.py, _sys/checks/check_operational_guard_shadow.py, _sys/tests/unit/test_operational_guard_matrix.py, README.md |
| 29 | `mig.core.pathlayout.path_resolver` | `_sys/core/pathlayout.py:PathLayout` | `split` | `peerhub.storage.layout / Engram host pathlayout` | _sys/core/pathlayout.py, _sys/docs-v2/00-MANIFEST.md, _sys/docs-v2/ops/engram-refactor-blueprint-2026-07-20.md, _sys/ai/unreferenced_functions_baseline.json |
| 30 | `mig.core.provisioner.toolchain_installer` | `_sys/core/provisioner.py:ensure_tool` | `stay` | `Engram host provisioner (out of PeerHub core)` | _sys/core/provisioner.py, _sys/core/setup.py, _sys/core/updater.py, INSTALL.bat, _sys/checks/check_deps.py |
| 31 | `mig.core.provisioner.peer_cli_config` | `_sys/core/provisioner.py:ensure_peer_cli` | `split` | `Engram host CLI installer / peerhub.adapters.executable_binding` | _sys/core/provisioner.py, _sys/ai/capability-declarations.json, _sys/ai/orchestration.json, _sys/tests/unit/test_provisioner_peer.py |
| 32 | `mig.core.provisioner.lease_gate` | `_sys/core/provisioner.py:_is_peer_leased` | `replace` | `peerhub.coordination.lease_client` | _sys/core/provisioner.py:deploy, _sys/tests/unit/test_provisioner_lease.py |
| 33 | `mig.core.provisioner.deferred_installer` | `_sys/core/provisioner.py:_drain_deferred_lazy` | `stay` | `Engram host provisioner (out of PeerHub core)` | _sys/core/provisioner.py, _sys/core/dispatcher.py, _sys/tests/unit/test_provisioner_deferred.py |
| 34 | `mig.core.quota.pacing_calculator` | `_sys/core/quota.py:calculate_pacing` | `replace` | `peerhub.telemetry.pacing` | _sys/cli/diag.py, _sys/core/snapshot.py, _sys/core/hub.py, _sys/tests/unit/test_quota_pacing.py |
| 35 | `mig.core.quota.capabilities_lookup` | `_sys/core/quota_capabilities.py:root_quota_capability` | `replace` | `peerhub.governance.quota_capabilities` | _sys/cli/diag.py, _sys/core/hub.py, _sys/core/snapshot.py, _sys/ai/orchestration.json, _sys/tests/unit/test_quota_capabilities.py |
| 36 | `mig.core.registrar.context_menu_manager` | `_sys/core/registrar.py:apply` | `stay` | `Engram host registrar (out of PeerHub core)` | _sys/cli/manage.py, _sys/core/dispatcher.py, register.bat, unregister.bat, _sys/tests/unit/test_registrar.py |
| 37 | `mig.core.relocator.path_relocator_shim` | `_sys/core/relocator.py:relocate` | `deprecate` | `core.launcher (Engram host)` | _sys/core/relocator.py, _sys/docs/history/ops/remaining-items.md, docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md |
| 38 | `mig.core.scrubber.cleanup_engine` | `_sys/core/scrubber.py:run` | `stay` | `Engram host scrubber (out of PeerHub core)` | _sys/cli/cleanup.py, _sys/core/dispatcher.py, CLEANUP.bat, _sys/tests/unit/test_scrubber.py |
| 39 | `mig.core.scrubber.ai_ephemeral_cleaner` | `_sys/core/scrubber.py:_clean_ai_ephemeral` | `split` | `peerhub.storage.cleanup / Engram host scrubber` | _sys/core/scrubber.py, _sys/cli/cleanup.py, _sys/tests/unit/test_scrubber.py |
| 40 | `mig.core.setup.setup_shim` | `_sys/core/setup.py` | `deprecate` | `core.provisioner (Engram host)` | _sys/core/dispatcher.py, _sys/docs/history/ops/full-system-purpose-audit-2026-07-12.md, docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md |
| 41 | `mig.core.snapshot.telemetry_collector` | `_sys/core/snapshot.py:collect_snapshot` | `replace` | `peerhub.telemetry.snapshot_collector` | _sys/cli/diag.py, _sys/core/hub.py, _sys/tests/unit/test_snapshot_collector.py, _sys/ai/orchestration.json |
| 42 | `mig.core.snapshot.codex_scraper` | `_sys/core/snapshot.py:_codex_rate_limits` | `replace` | `peerhub.adapters.codex.telemetry` | _sys/core/snapshot.py, _sys/tests/unit/test_snapshot_codex.py |
| 43 | `mig.core.snapshot.claude_scraper` | `_sys/core/snapshot.py:_parse_claude_usage` | `replace` | `peerhub.adapters.claude.telemetry` | _sys/core/snapshot.py, _sys/tests/unit/test_snapshot_claude.py |
| 44 | `mig.core.snapshot.agy_scraper` | `_sys/core/snapshot.py:_load_ag_last_good_quota` | `replace` | `peerhub.adapters.agy.telemetry` | _sys/core/snapshot.py, _sys/tests/unit/test_snapshot_agy.py |
| 45 | `mig.core.snapshot.headroom_pacing` | `_sys/core/snapshot.py:_derive_headroom_rows` | `replace` | `peerhub.telemetry.headroom_evaluator` | _sys/cli/diag.py, _sys/core/snapshot.py, _sys/tests/unit/test_snapshot_pacing.py |
| 46 | `mig.core.snapshot.arbiter_engine` | `_sys/core/snapshot.py:select_arbiter` | `replace` | `peerhub.governance.arbiter_evaluator` | _sys/core/hub.py, _sys/core/snapshot.py, _sys/tests/unit/test_arbiter_selection.py, _sys/docs/history/ops/token-load-balancing-design.md |
| 47 | `mig.core.snapshot.failover_selector` | `_sys/core/snapshot.py:snapshot_failover_target` | `replace` | `peerhub.routing.failover_selector` | _sys/core/hub.py, _sys/core/snapshot.py, _sys/tests/unit/test_snapshot_failover.py |
| 48 | `mig.core.tidy.debris_sweeper` | `_sys/core/tidy_temp.py:main` | `stay` | `Engram host maintenance (out of PeerHub core)` | TIDY.bat, _sys/core/tidy_temp.py, _sys/tests/unit/test_tidy_temp.py, docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md |
| 49 | `mig.core.timestamps.iso_parser` | `_sys/core/timestamps.py:parse_iso_timestamp` | `replace` | `peerhub.types.timestamps` | _sys/core/hub.py, _sys/core/quota.py, _sys/core/snapshot.py, _sys/tests/unit/test_timestamps.py |
| 50 | `mig.core.updater.update_dispatcher` | `_sys/core/updater.py:run` | `stay` | `Engram host updater (out of PeerHub core)` | UPDATE.bat, _sys/core/updater.py, _sys/tests/unit/test_updater.py, docs/design/PHASE1-AUTODETECT-SIDECAR-2026-08-19.md |
| 51 | `mig.core.version.version_resolver` | `_sys/core/version_resolver.py:resolve_latest` | `stay` | `Engram host version resolver (out of PeerHub core)` | _sys/checks/check_tool_updates.py, _sys/core/updater.py, _sys/core/provisioner.py, _sys/tests/unit/test_version_resolver.py |
| 52 | `mig.core.virtualizer.subst_manager` | `_sys/core/virtualizer.py:mount` | `stay` | `Engram host virtualizer (out of PeerHub core)` | _sys/cli/manage.py, _sys/core/dispatcher.py, _sys/core/doctor.py, _sys/checks/self_care.py, _sys/tests/unit/test_virtualizer.py |
| 53 | `mig.core.virtualizer.junction_manager` | `_sys/core/virtualizer.py:_ensure_junction` | `stay` | `Engram host virtualizer (out of PeerHub core)` | _sys/core/virtualizer.py, _sys/core/scrubber.py, _sys/tests/unit/test_virtualizer.py |
| 54 | `mig.core.hub.action_dispatcher` | `_sys/core/hub.py:main` | `replace` | `peerhub.engine.action_dispatcher / peerhub.cli.hub` | 271 consumer files across _sys/cli/, _sys/ai/common/skills/*, _sys/tests/ |
| 55 | `mig.core.hub.peer_ask_engine` | `_sys/core/hub.py:action_ask` | `replace` | `peerhub.engine.invocation_runner` | _sys/cli/msg.py, _sys/ai/common/skills/*, _sys/tests/unit/test_hub_ask.py, _sys/tests/unit/test_hub_ask_contract.py |
| 56 | `mig.core.hub.lease_manager` | `_sys/core/hub.py:_lease_cfg` | `replace` | `peerhub.coordination.lease_manager` | _sys/cli/console_runner.py, _sys/core/hub.py, _sys/tests/unit/test_contracts.py, _sys/tests/unit/test_lease_concurrency.py, DIR-003 |
| 57 | `mig.core.hub.mailbox_broker` | `_sys/core/hub.py:_broker_submit` | `replace` | `peerhub.messaging.broker` | _sys/cli/msg.py, _sys/ai/common/skills/peer-propose.md, _sys/tests/unit/test_hub_broker.py, _sys/tests/unit/test_hub_mailbox.py |
| 58 | `mig.core.hub.session_handoff` | `_sys/core/hub.py:action_init_session` | `replace` | `peerhub.session.manager / peerhub.governance` | _sys/ai/common/skills/*, _sys/tests/unit/test_hub_session.py, _sys/tests/unit/test_hub_handoff.py, _sys/ai/protocol.json |

---

## 5. Comprehensive 69-File Combined Crosswalk Synthesis

Together with the CLI half (`docs/design/PHASE1-CAPABILITY-CROSSWALK-CLI-2026-08-20.md`), this document completes the exhaustive MECE migration crosswalk for the entire 69-file legacy codebase.

### Combined Statistics & Disposition Breakdown
| Partition | Legacy Files Covered | Total Capability Rows | `replace` | `stay` | `split` | `deprecate` |
|---|---|---|---|---|---|---|
| **CLI Half (`_sys/cli`)** | 39 / 39 | 71 | 40 | 15 | 6 | 10 |
| **Core Half (`_sys/core`)** | 30 / 30 | 58 | 36 | 16 | 4 | 2 |
| **COMBINED TOTAL** | **69 / 69 (100%)** | **129** | **76 (58.9%)** | **31 (24.0%)** | **10 (7.8%)** | **12 (9.3%)** |

### MECE Guarantee
1. **Zero Gaps:** Every file in `_sys/cli` (39 files) and `_sys/core` (30 files) is accounted for in the checklists and ledger rows.
2. **Zero Double-Counting:** Each capability row possesses a globally unique `migration_capability_id` prefixed under either `mig.cli.*` or `mig.core.*`.
3. **Strict Namespace Isolation:** `adapter_feature` remains strictly bounded to `SESSION`, `STREAM`, and `GRACEFUL_CANCEL` in `peerhub/adapters/contract.py` and unpopulated in migration rows; `coverage_case_id` is reserved for the downstream test taxonomy.
4. **Decoupled Architecture:** `provisioner.py` and other mixed files have been cleanly split between Engram host toolchain installation (`stay`) and PeerHub executable binding/coordination (`split`/`replace`), resolving finding **R2-01** and fulfilling **DIR-004** measured-only standards.
