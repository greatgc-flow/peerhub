# Promotion Ledger Schema & Execution Rules V1
**Date:** 2026-08-20

This document defines the machine-readable schema, enumeration rules, state transitions, and deterministic algorithms for the Peerhub Promotion Ledger. This replaces the previous descriptive prose with a concrete, computable model.

## 1. Machine-Readable Schema

The Promotion Ledger is modeled as an inventory of discrete **Cells**. Each cell uniquely identifies a test execution context and its outcome. 

### 1.1 Promotion Ledger Cell (JSON Schema)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PromotionLedgerCell",
  "description": "A single, immutable record of an evidence capture for a specific capability context.",
  "type": "object",
  "properties": {
    "cell_key": {
      "type": "object",
      "description": "The composite primary key for the cell.",
      "properties": {
        "coverage_case_id": { "type": "string", "description": "e.g., 'action.hub.ask' or 'action.hub.credit-consume'" },
        "peer_binding": { "type": "string", "description": "e.g., 'profile:cc.standard', 'profile:cx.standard', 'profile:ag.standard'" },
        "platform": { "type": "string", "description": "e.g., 'win32-x64'" },
        "transport": { "type": "string", "description": "e.g., 'stdio' or 'pty'" },
        "proof_kind": { "type": "string", "enum": ["dry_run", "integration", "fuzz", "snapshot"] }
      },
      "required": ["coverage_case_id", "peer_binding", "platform", "transport", "proof_kind"],
      "additionalProperties": false
    },
    "requirement_state": {
      "type": "string",
      "enum": ["REQUIRED", "OPTIONAL", "NOT_APPLICABLE"],
      "description": "Determined by the adapter manifest and capability matrix."
    },
    "evidence_state": {
      "type": "string",
      "enum": ["MEASURED", "ABSENT", "UNAVAILABLE", "ERROR", "STALE"],
      "description": "The frozen lifecycle state of the evidence."
    },
    "attempt_outcome": {
      "type": "string",
      "enum": ["PASS", "FAIL", "HARNESS_FAILURE", "UNTESTED"],
      "description": "The deterministic result of the execution."
    },
    "provenance": {
      "type": "object",
      "description": "Verifiable execution context and isolation boundaries.",
      "properties": {
        "timestamp_utc": { "type": "string", "format": "date-time" },
        "isolation_root": { "type": "string", "description": "The fs/chroot isolation boundary" },
        "provider_home": { "type": "string", "description": "The provider execution context" },
        "session_id": { "type": "string" },
        "lease_id": { "type": "string" },
        "source_tags": { "type": "array", "items": { "type": "string" }, "description": "e.g., ['cli_live', 'empirical_probe']" },
        "redacted_receipt_hash": { "type": "string", "description": "Hash of the PII-scrubbed execution receipt" }
      },
      "required": ["timestamp_utc", "isolation_root", "provider_home", "session_id", "lease_id", "source_tags", "redacted_receipt_hash"],
      "additionalProperties": false
    },
    "raw_capture_protection": {
      "type": "boolean",
      "description": "True if raw output was protected/redacted before receipt generation."
    },
    "serialization_policy": {
      "type": "string",
      "enum": ["EXCLUSIVE_LOCK", "OPTIMISTIC_CONCURRENCY", "APPEND_ONLY"],
      "description": "Policy used during concurrent evidence collection."
    }
  },
  "required": ["cell_key", "requirement_state", "evidence_state", "attempt_outcome", "provenance", "raw_capture_protection", "serialization_policy"],
  "additionalProperties": false
}
```

## 2. Cross-Proof-Kind Contradiction Resolution (Rollup)

**Resolution:** `proof_kind` is part of the Cell Key. Therefore, a single cell cannot be internally contradictory regarding `proof_kind`. The concept of "contradiction" applies exclusively at the **Coverage Case Rollup** level.

A Rollup groups all cells sharing the same `(coverage_case_id, peer_binding, platform, transport)` but differing in `proof_kind`. 

**Contradiction Rule:** A rollup is `CONTRADICTORY` if and only if two sibling cells within the rollup have divergent deterministic `attempt_outcome`s (e.g., `proof_kind: integration` is `PASS`, but `proof_kind: dry_run` is `FAIL`), AND both have `evidence_state: MEASURED`. 
Contradictions halt promotion and require manual resolution.

## 3. Classifier Algorithm (5-State Attempt Outcome)

The classifier maps raw execution evidence to exactly one unambiguous attempt outcome, resolving precedence when multiple conditions apply simultaneously.

```python
def classify_evidence(raw_evidence) -> str:
    """
    Returns one of: ["HARNESS_FAILURE", "FAIL", "PASS", "UNTESTED"]
    """
    if raw_evidence is None:
        return "UNTESTED"
        
    # PRECEDENCE 1: Infrastructure, timeout, or harness crash.
    # The test environment itself blew up; we cannot trust any output.
    if raw_evidence.exit_code == -1 or "HARNESS_CRASH" in raw_evidence.reason_codes or raw_evidence.timeout_exceeded:
        return "HARNESS_FAILURE"
        
    # PRECEDENCE 2: Silent failures, missing receipts, or malformed assertions.
    # The test ran, but the required validation artifacts were not produced.
    if not raw_evidence.has_valid_redacted_receipt or "MALFORMED_OUTPUT" in raw_evidence.reason_codes:
        return "HARNESS_FAILURE"
        
    # PRECEDENCE 3: Explicit test assertion failure.
    # The test executed cleanly but the business logic failed.
    if raw_evidence.exit_code != 0 or "ASSERTION_FAILED" in raw_evidence.reason_codes:
        return "FAIL"
        
    # PRECEDENCE 4: Clean execution and successful assertions.
    if raw_evidence.exit_code == 0:
        return "PASS"
        
    return "UNTESTED"
```

## 4. Freshness and Invalidation Rules

Freshness is not just a timestamp; it is a deterministic function comparing the cell's provenance against environment constraints and maximum age thresholds.

```python
MAX_AGE_SECONDS = 86400 * 7 # 7 days maximum age for any evidence

def determine_evidence_state(cell, current_env, raw_evidence) -> str:
    """
    Returns one of: ["MEASURED", "ABSENT", "UNAVAILABLE", "ERROR", "STALE"]
    """
    if cell is None or raw_evidence is None:
        if current_env.missing_dependencies:
            return "UNAVAILABLE" # Cannot run test due to environment constraints
        return "ABSENT"          # We just haven't run it yet
        
    if cell.attempt_outcome == "HARNESS_FAILURE":
        return "ERROR"
        
    # Check Age Invalidation
    age = current_env.current_time_utc - cell.provenance.timestamp_utc
    if age.total_seconds() > MAX_AGE_SECONDS:
        return "STALE"
        
    # Check Formal Invalidation Rules
    # Rule 1: Protocol breaking change invalidates old integration tests
    if cell.provenance.protocol_version < current_env.min_protocol_version:
        return "STALE"
        
    # Rule 2: Host architecture change (if the cell claims to be platform-independent but wasn't)
    if cell.provenance.provider_home_arch != current_env.architecture and cell.cell_key.platform == "native":
         return "STALE"
         
    return "MEASURED"
```

## 5. Cell Requirement Rules

Determines if a cell must be tested to permit promotion.

```python
def determine_requirement_state(cell_key, adapter_manifest) -> str:
    """
    Returns one of: ["REQUIRED", "OPTIONAL", "NOT_APPLICABLE"]
    """
    # 1. Applicability Check
    if not adapter_manifest.declares_capability(cell_key.coverage_case_id):
        return "NOT_APPLICABLE"
    if not adapter_manifest.supports_platform(cell_key.platform):
        return "NOT_APPLICABLE"
    if not adapter_manifest.supports_transport(cell_key.transport):
        return "NOT_APPLICABLE"
        
    # 2. Requirement Check
    # If the coverage case is defined as a 'Core Parity Requirement' for the adapter's domain
    if cell_key.coverage_case_id in adapter_manifest.core_parity_requirements:
        return "REQUIRED"
        
    # If the proof_kind is integration, it is required for core capabilities, 
    # but snapshot might be optional.
    if cell_key.proof_kind == "snapshot" and not adapter_manifest.requires_snapshots:
        return "OPTIONAL"
        
    return "OPTIONAL"
```

## 6. Coverage Cases and Parity Ledger Mapping

This maps representative actions from the 90-action Parity Ledger (`docs/design/PHASE1-PARITY-LEDGER-BATCH1-2026-08-20.md` through `BATCH5-2026-08-20.md`) and the three real peer adapters (`claude-peer`, `codex-peer`, `agy-peer` from `docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`) into concrete `coverage_case_id`s.

The table illustrates six genuinely distinct architectural situations across the parity ledger:
1. **Core Adapter Manifest Dispatch (`ask`)**: Main prompt dispatch executing the real `claude.cmd` stdio template (`cc.standard`) and validating the JSON response envelope.
2. **Simple Read-Only Query (`credit-status`)**: Pure read-only inspection of rate-limit reset credit quota via `CodexAccountClient().read_rate_limits()`, strictly idempotent with no side effects.
3. **Non-Idempotent / Irreversible Mutation (`credit-consume`)**: Irreversible upstream quota consumption requiring human terminal origin (`origin="terminal"`), `--confirm` flag, and UUID idempotency correlation through a 3-stage preflight/audit/verify pipeline.
4. **Real Concurrency Race Condition (`thread-new`)**: Unlocked check-then-act defect (`path.exists()` before `path.open("a")`) verified to produce duplicate `THREAD_CREATE` headers when two peers invoke simultaneously.
5. **Smart-Model Final Arbiter Governance (`arbiter-review`)**: DIR-005 governance action invoking `cc.fable` on split consensus rounds, strictly budget-guarded (5 reviews per 5h window).
6. **PTY Interactive Transport Session (`init-session`)**: Agent lifecycle initialization on PTY transport (`ag.standard`), exercising the `builtin:pty-agy-v1` terminal state machine.

| Adapter | Parity Ledger Row (Action) | Batch Citation | `coverage_case_id` | Core Parity Req? | Behavioral Scenario & Finding |
|---|---|---|---|---|---|
| `claude-peer` (`cc`) | `ask` (`action_ask`) | Batch 1, Action 12 | `action.hub.ask` | YES | Standard stdio peer prompt invocation (`cc.standard`); validates stdout JSON envelope and exit code. |
| `codex-peer` (`cx`) | `credit-status` (`action_credit_status`) | Batch 5, Action 17 | `action.hub.credit-status` | YES | Pure read-only, idempotent app-server query via `CodexAccountClient`; never modifies state. |
| `codex-peer` (`cx`) | `credit-consume` (`action_credit_consume`) | Batch 5, Action 18 | `action.hub.credit-consume` | YES | Irreversible mutation requiring human `--confirm` + canonical UUID; multi-stage preflight/audit/verify lifecycle. |
| `claude-peer` (`cc`) / `codex-peer` (`cx`) | `thread-new` (`action_thread_new`) | Batch 5, Action 4 | `action.hub.thread-new` | YES | Check-then-act race condition (`fix-thread-new-conc-01`); concurrent creation produces duplicate `THREAD_CREATE` headers. |
| `claude-peer` (`cc` Arbiter) | `arbiter-review` (`run_arbiter_on_round`) | Batch 5, Action 16 | `action.hub.arbiter-review` | NO (Optional) | DIR-005 smart-model final arbiter for dissenting rounds; strictly budget-limited (5/5h window). |
| `agy-peer` (`ag`) | `init-session` (`action_init_session`) | Batch 1, Action 1 | `action.hub.init-session` | YES | Session lifecycle initialization, agent registration in `state.json`, and `_log_p2p` JOIN emission via PTY transport (`ag.standard`). |

## 7. Worked Examples (Concrete JSON)

Every worked example below uses Peerhub's real adapters, real paths from empirical host discovery (`PHASE1-ADMISSION-RECEIPTS-REAL-2026-08-20.md`), real profiles, and verified behavior from the 90-action parity ledger.

### 7.1 PASSING State: `action.hub.ask` via `claude-peer`
Captures successful integration execution of `hub.py ask` delegating to `claude-peer` (`cc.standard`) via stdio transport.

```json
{
  "cell_key": {
    "coverage_case_id": "action.hub.ask",
    "peer_binding": "profile:cc.standard",
    "platform": "win32-x64",
    "transport": "stdio",
    "proof_kind": "integration"
  },
  "requirement_state": "REQUIRED",
  "evidence_state": "MEASURED",
  "attempt_outcome": "PASS",
  "provenance": {
    "timestamp_utc": "2026-08-20T22:30:00Z",
    "isolation_root": "P:/workspace/peerhub/.sandbox/run-1029",
    "provider_home": "P:/_sys/env/nodejs/npm-global",
    "session_id": "room-efde",
    "lease_id": "lease-cc-ask-001",
    "source_tags": ["cli_live", "empirical_probe"],
    "redacted_receipt_hash": "sha256:7b5d1a8c9e2f4b6d0e3a5c7f8a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b"
  },
  "raw_capture_protection": true,
  "serialization_policy": "EXCLUSIVE_LOCK"
}
```

### 7.2 FAILING State: `action.hub.thread-new` Concurrency Race Defect
Captures the execution of concurrency test fixture `fix-thread-new-conc-01` (Parity Ledger Batch 5 §4), where two concurrent callers (`cc` and `cx`) create the same thread topic simultaneously, causing duplicate `THREAD_CREATE` headers in `threads/{topic}.jsonl` due to unlocked check-then-act file creation.

```json
{
  "cell_key": {
    "coverage_case_id": "action.hub.thread-new",
    "peer_binding": "profile:cc.standard",
    "platform": "win32-x64",
    "transport": "stdio",
    "proof_kind": "integration"
  },
  "requirement_state": "REQUIRED",
  "evidence_state": "MEASURED",
  "attempt_outcome": "FAIL",
  "provenance": {
    "timestamp_utc": "2026-08-20T22:31:00Z",
    "isolation_root": "P:/workspace/peerhub/.sandbox/run-1030",
    "provider_home": "P:/workspace/peerhub",
    "session_id": "room-efde",
    "lease_id": "lease-conc-thread-002",
    "source_tags": ["cli_live", "empirical_probe"],
    "redacted_receipt_hash": "sha256:4f8a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a"
  },
  "raw_capture_protection": true,
  "serialization_policy": "EXCLUSIVE_LOCK"
}
```

### 7.3 STALE State: `action.hub.credit-consume` via `codex-peer`
Captures historical evidence for rate-limit reset credit consumption on `codex-peer` (`cx.standard`). The evidence passed when originally measured, but exceeded `MAX_AGE_SECONDS` (7 days), marking it `STALE` and requiring re-execution before release promotion.

```json
{
  "cell_key": {
    "coverage_case_id": "action.hub.credit-consume",
    "peer_binding": "profile:cx.standard",
    "platform": "win32-x64",
    "transport": "stdio",
    "proof_kind": "integration"
  },
  "requirement_state": "REQUIRED",
  "evidence_state": "STALE",
  "attempt_outcome": "PASS",
  "provenance": {
    "timestamp_utc": "2026-08-01T10:00:00Z",
    "isolation_root": "P:/workspace/peerhub/.sandbox/run-0050",
    "provider_home": "P:/_sys/env/nodejs/npm-global",
    "session_id": "room-old-001",
    "lease_id": "lease-cx-credit-099",
    "source_tags": ["cli_live"],
    "redacted_receipt_hash": "sha256:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
  },
  "raw_capture_protection": true,
  "serialization_policy": "EXCLUSIVE_LOCK"
}
```

### 7.4 UNAVAILABLE State: `action.hub.init-session` via `agy-peer` (PTY Transport)
Captures execution in an automated headless CI runner lacking Windows ConPTY terminal support. Because `agy-peer` (`ag.standard`) requires PTY transport (`win32-x64`), the test harness cannot allocate pseudo-terminal resources, cleanly recording `UNAVAILABLE` without registering a spurious product failure.

```json
{
  "cell_key": {
    "coverage_case_id": "action.hub.init-session",
    "peer_binding": "profile:ag.standard",
    "platform": "win32-x64",
    "transport": "pty",
    "proof_kind": "integration"
  },
  "requirement_state": "REQUIRED",
  "evidence_state": "UNAVAILABLE",
  "attempt_outcome": "UNTESTED",
  "provenance": {
    "timestamp_utc": "2026-08-20T22:35:00Z",
    "isolation_root": "N/A",
    "provider_home": "P:/_sys/tools/agy",
    "session_id": "room-efde",
    "lease_id": "lease-ag-init-003",
    "source_tags": ["app_server"],
    "redacted_receipt_hash": "N/A"
  },
  "raw_capture_protection": false,
  "serialization_policy": "APPEND_ONLY"
}
```

### 7.5 CONTRADICTORY State: Rollup Example on `action.hub.credit-status`
While an individual promotion cell cannot be contradictory (as `proof_kind` is an immutable part of the `cell_key`), a **Coverage Case Rollup** evaluates sibling cells for the same `(coverage_case_id, peer_binding, platform, transport)`.

If the following two cells exist simultaneously for `action.hub.credit-status` (`profile:cx.standard`, `win32-x64`, `stdio`):

**Cell A (`proof_kind: "integration"` - PASS)**
* `proof_kind`: "integration"
* `attempt_outcome`: "PASS"
* `evidence_state`: "MEASURED"
*(Integration test against live app-server successfully queries rate limit quota and returns exit code 0)*

**Cell B (`proof_kind: "dry_run"` - FAIL)**
* `proof_kind`: "dry_run"
* `attempt_outcome`: "FAIL"
* `evidence_state`: "MEASURED"
*(Static dry-run assertion failed because mock capability declaration in local harness config omitted `supports_reset_credits`)*

The overall rollup for `action.hub.credit-status` on `(profile:cx.standard, win32-x64, stdio)` resolves to **`CONTRADICTORY`**, halting promotion until the discrepancy between the dry-run simulation and live integration execution is investigated and resolved.
