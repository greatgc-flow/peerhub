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
        "transport": { "type": "string", "enum": ["PIPE", "PTY"], "description": "e.g., 'PIPE' or 'PTY'" },
        "proof_kind": { "type": "string", "enum": ["deterministic contract or integration", "controlled real-OS executable", "live provider exact-profile", "legacy-parity evidence"] }
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
      "enum": ["EXECUTED_PASS", "PRODUCT_FAILURE", "QUOTA_BLOCKED", "ENVIRONMENT_UNAVAILABLE", "NOT_REQUESTED"],
      "description": "The deterministic result of the execution matching the Phase 1 Test Taxonomy V3 five-state classifier."
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

**Contradiction Rule:** A rollup is `CONTRADICTORY` if and only if two sibling cells within the same rollup group have divergent deterministic `attempt_outcome`s (e.g., one sibling is `EXECUTED_PASS`, but another sibling has a failing or unavailable outcome: `PRODUCT_FAILURE`, `QUOTA_BLOCKED`, or `ENVIRONMENT_UNAVAILABLE`) with active evaluated evidence. 
Contradictions halt promotion and require manual resolution.

## 3. Classifier Algorithm (5-State Attempt Outcome)

The classifier maps raw execution evidence to exactly one unambiguous attempt outcome matching `PHASE1-TEST-TAXONOMY-V3-2026-08-20.md` Section 3, resolving precedence when multiple conditions apply simultaneously.

### 3.1 Taxonomy State & Reason Code Mapping Table

| Taxonomy State | Meaning & Precedence | Trigger Conditions / Reason Codes | Promotion Gate Effect |
|---|---|---|---|
| `QUOTA_BLOCKED` | Remote provider rate limits or quota exhaustion (Precedence 1) | `QUOTA_BLOCKED`, `quota_exhausted=True`, HTTP 429 | Blocks promotion (Transient/External) |
| `ENVIRONMENT_UNAVAILABLE` | Infrastructure, missing binary, auth/network/provider outage, harness crash (Precedence 2) | `MISSING_EXECUTABLE`, `AUTHENTICATION_FAILURE`, `NETWORK_FAILURE`, `PROVIDER_OUTAGE`, `HARNESS_FAILURE`, `HARNESS_CRASH`, `MALFORMED_OUTPUT`, `exit_code == -1`, `timeout_exceeded` | Blocks promotion (`ERROR` state) |
| `PRODUCT_FAILURE` | Explicit test assertion or product logic failure (Precedence 3) | `ASSERTION_FAILED`, `PRODUCT_FAILURE`, `exit_code != 0` (clean run, failed logic) | Hard-halts promotion (Product Defect) |
| `EXECUTED_PASS` | Clean execution and successful assertions (Precedence 4) | `exit_code == 0`, valid receipt, assertions passed | Permits promotion when all required cells pass |
| `NOT_REQUESTED` | Test was omitted or not requested (Precedence 5) | `raw_evidence is None`, omitted probe | Neutral / Absent |

### 3.2 Classifier Implementation

```python
def classify_evidence(raw_evidence) -> str:
    """
    Classifies raw execution evidence into one of the canonical 5 taxonomy states:
    - EXECUTED_PASS: Clean execution with successful assertions.
    - PRODUCT_FAILURE: Explicit test assertion or product logic failure.
    - QUOTA_BLOCKED: Remote provider rate limits or quota exhaustion.
    - ENVIRONMENT_UNAVAILABLE: Missing local dependencies/binaries, auth failure, network down, provider outage, or harness failure.
    - NOT_REQUESTED: Test was omitted or not requested.
    """
    if raw_evidence is None:
        return "NOT_REQUESTED"
        
    reason_codes = set(getattr(raw_evidence, "reason_codes", []))
    
    # PRECEDENCE 1: Quota / Rate-limit blockage
    if "QUOTA_BLOCKED" in reason_codes or getattr(raw_evidence, "quota_exhausted", False):
        return "QUOTA_BLOCKED"
        
    # PRECEDENCE 2: Environment unavailability / Harness failure / Missing dependencies
    env_unavail_codes = {
        "MISSING_EXECUTABLE",
        "AUTHENTICATION_FAILURE",
        "NETWORK_FAILURE",
        "PROVIDER_OUTAGE",
        "HARNESS_FAILURE",
        "HARNESS_CRASH",
        "MALFORMED_OUTPUT"
    }
    if (
        raw_evidence.exit_code == -1
        or raw_evidence.timeout_exceeded
        or not getattr(raw_evidence, "has_valid_redacted_receipt", True)
        or bool(reason_codes & env_unavail_codes)
    ):
        return "ENVIRONMENT_UNAVAILABLE"
        
    # PRECEDENCE 3: Explicit test assertion failure / Product defect
    if raw_evidence.exit_code != 0 or "ASSERTION_FAILED" in reason_codes or "PRODUCT_FAILURE" in reason_codes:
        return "PRODUCT_FAILURE"
        
    # PRECEDENCE 4: Clean execution and successful assertions
    if raw_evidence.exit_code == 0:
        return "EXECUTED_PASS"
        
    return "NOT_REQUESTED"
```

## 4. Freshness and Invalidation Rules

Freshness is not just a timestamp; it is a deterministic function comparing the cell's provenance against environment constraints and maximum age thresholds.

```python
MAX_AGE_SECONDS = 86400 * 7 # 7 days maximum age for any evidence

def determine_evidence_state(cell, current_env) -> str:
    """
    Returns one of: ["MEASURED", "ABSENT", "UNAVAILABLE", "ERROR", "STALE"]
    """
    if cell is None:
        if current_env.missing_dependencies:
            return "UNAVAILABLE" # Cannot run test due to environment constraints
        return "ABSENT"          # We just haven't run it yet
        
    if cell.attempt_outcome == "ENVIRONMENT_UNAVAILABLE":
        return "ERROR"
        
    # Check formal age validation
    age = current_env.current_time_utc - cell.provenance.timestamp_utc
    if age.total_seconds() > MAX_AGE_SECONDS:
        return "STALE"
         
    return cell.evidence_state
```

## 5. Adapter Manifest Evaluation Context & Type Definitions

To ground the requirement rules in concrete, typed contracts matching `PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`, the evaluation context defines the `CellKey` and `AdapterManifest` structures:

```python
from __future__ import annotations
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime
import secrets

_active_manifest_token: ContextVar[str | None] = ContextVar("_active_manifest_token", default=None)

@dataclass(frozen=True, slots=True)
class CellKey:
    """The composite primary key uniquely identifying an evidence context."""
    coverage_case_id: str
    peer_binding: str
    platform: str
    transport: str  # "PIPE" | "PTY"
    proof_kind: str  # "deterministic contract or integration" | "controlled real-OS executable" | "live provider exact-profile" | "legacy-parity evidence"

    def as_tuple(self) -> tuple[str, str, str, str, str]:
        return (
            self.coverage_case_id,
            self.peer_binding,
            self.platform,
            self.transport,
            self.proof_kind,
        )

@dataclass(frozen=True, slots=True)
class AdapterManifest:
    """Documented contract of an admitted adapter manifest used during promotion evaluation.
    Enforces deterministic construction exclusively from an admitted manifest schema instance.
    Direct manual construction with arbitrary or conflicting values is guarded via a context-local
    ephemeral token scoped to from_manifest execution (using contextvars.ContextVar). This prevents
    accidental manual instantiation and cross-thread concurrency races, establishing a clearly-marked
    internal boundary rather than claiming absolute unforgeability against intentional private-state tampering.
    """
    adapter_id: str
    peer_kind: str
    capabilities: tuple[str, ...]
    supported_platforms: tuple[str, ...]
    supported_transports: tuple[str, ...]
    core_parity_requirements: tuple[str, ...]
    required_proof_kinds: tuple[str, ...]
    requires_snapshots: bool
    _token: str | None = None

    def __post_init__(self):
        active_token = _active_manifest_token.get()
        if (
            self._token is None
            or active_token is None
            or self._token != active_token
        ):
            raise TypeError(
                "AdapterManifest direct construction is prohibited to guarantee promotion determinism. "
                "Instances must be traceably constructed via AdapterManifest.from_manifest(admitted_manifest_dict)."
            )

    @classmethod
    def from_manifest(cls, raw_manifest: dict) -> AdapterManifest:
        """Constructs this contract strictly by validating and reading fields from an admitted manifest instance."""
        if not isinstance(raw_manifest, dict) or "adapter" not in raw_manifest:
            raise ValueError("raw_manifest must be an admitted manifest dict containing an 'adapter' block.")
        adapter = raw_manifest["adapter"]
        required_keys = (
            "adapter_id",
            "peer_kind",
            "capabilities",
            "supported_platforms",
            "supported_transports",
            "core_parity_requirements",
            "required_proof_kinds",
            "requires_snapshots",
        )
        missing = [k for k in required_keys if k not in adapter]
        if missing:
            raise ValueError(f"Admitted manifest missing required policy fields: {missing}")

        if not isinstance(adapter["adapter_id"], str) or not isinstance(adapter["peer_kind"], str):
            raise TypeError("Fields 'adapter_id' and 'peer_kind' must be strings.")

        for seq_field in (
            "capabilities",
            "supported_platforms",
            "supported_transports",
            "core_parity_requirements",
            "required_proof_kinds",
        ):
            val = adapter[seq_field]
            if not isinstance(val, (list, tuple)) or not all(isinstance(x, str) for x in val):
                raise TypeError(f"Field '{seq_field}' must be a list or tuple of strings, got {type(val).__name__}.")

        if not isinstance(adapter["requires_snapshots"], bool):
            raise TypeError(f"Field 'requires_snapshots' must be a bool, got {type(adapter['requires_snapshots']).__name__}.")

        token = secrets.token_hex(32)
        reset_token = _active_manifest_token.set(token)
        try:
            return cls(
                adapter_id=adapter["adapter_id"],
                peer_kind=adapter["peer_kind"],
                capabilities=tuple(adapter["capabilities"]),
                supported_platforms=tuple(adapter["supported_platforms"]),
                supported_transports=tuple(adapter["supported_transports"]),
                core_parity_requirements=tuple(adapter["core_parity_requirements"]),
                required_proof_kinds=tuple(adapter["required_proof_kinds"]),
                requires_snapshots=adapter["requires_snapshots"],
                _token=token,
            )
        finally:
            _active_manifest_token.reset(reset_token)

    def declares_capability(self, coverage_case_id: str) -> bool:
        """Verifies whether the adapter declares capability for the given case or general actions."""
        if "session" in coverage_case_id:
            return "SESSION" in self.capabilities
        if "stream" in coverage_case_id:
            return "STREAM" in self.capabilities
        return coverage_case_id in self.core_parity_requirements or len(self.capabilities) > 0

    def supports_platform(self, platform: str) -> bool:
        """Verifies if the target OS/architecture platform is supported."""
        return platform in self.supported_platforms

    def supports_transport(self, transport: str) -> bool:
        """Verifies if the execution transport is supported."""
        return transport in self.supported_transports

    def get_expected_required_cell_keys(
        self,
        peer_binding: str,
        platform: str = "win32-x64",
        transport: str = "PIPE",
    ) -> set[CellKey]:
        """Enumerates the full composite CellKey set required for promotion."""
        keys: set[CellKey] = set()
        for case_id in self.core_parity_requirements:
            for proof in self.required_proof_kinds:
                key = CellKey(
                    coverage_case_id=case_id,
                    peer_binding=peer_binding,
                    platform=platform,
                    transport=transport,
                    proof_kind=proof,
                )
                if determine_requirement_state(key, self) == "REQUIRED":
                    keys.add(key)
        return keys
```

## 6. Cell Requirement Rules

Determines if a cell must be tested to permit promotion.

```python
def determine_requirement_state(cell_key: CellKey, adapter_manifest: AdapterManifest) -> str:
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
    # and the proof_kind is in the manifest's required proof kinds
    if cell_key.coverage_case_id in adapter_manifest.core_parity_requirements:
        if cell_key.proof_kind in adapter_manifest.required_proof_kinds:
            return "REQUIRED"
        return "OPTIONAL"
        
    # Legacy-parity snapshot evidence check
    if cell_key.proof_kind == "legacy-parity evidence" and not adapter_manifest.requires_snapshots:
        return "OPTIONAL"
        
    return "OPTIONAL"
```

## 7. Promotion Rollup Rule (`can_promote`)

Promotion is a deterministic boolean rollup over the requirement states and evidence states of all cells. It explicitly keys requirements on the **full composite `CellKey`** (coverage case, peer binding, platform, transport, and proof kind) rather than a coarse case ID alone, ensuring that omitting one genuinely required `proof_kind` for an otherwise-covered coverage case is strictly rejected.

```python
def can_promote(
    rollup_cells: list,
    current_env,
    adapter_manifest: AdapterManifest,
    required_cell_keys: set[CellKey] | None = None,
) -> bool:
    """
    Returns True if and only if:
    1. rollup_cells is non-empty and every required composite CellKey is covered.
    2. Every REQUIRED cell is in evidence_state MEASURED and attempt_outcome EXECUTED_PASS.
    3. Contradiction Guard: No sibling cell within the same rollup context (same coverage_case_id,
       peer_binding, platform, transport) has a divergent contradictory outcome (PRODUCT_FAILURE,
       QUOTA_BLOCKED, ENVIRONMENT_UNAVAILABLE) against a passing sibling cell in the same rollup group.
    4. Returns False if any required cell is missing, stale, unavailable, failed, omitted,
       or contradicted by a divergent sibling cell.
    """
    if not rollup_cells:
        return False
        
    # Group cells by coverage rollup context: (coverage_case_id, peer_binding, platform, transport)
    rollup_groups: dict[tuple[str, str, str, str], list] = {}
    for cell in rollup_cells:
        group_key = (
            cell.cell_key.coverage_case_id,
            cell.cell_key.peer_binding,
            cell.cell_key.platform,
            cell.cell_key.transport,
        )
        rollup_groups.setdefault(group_key, []).append(cell)

    # 1. Contradiction Detection
    for group_key, cells in rollup_groups.items():
        evaluated_cells = [
            c for c in cells
            if determine_evidence_state(c, current_env) in ("MEASURED", "ERROR")
        ]
        has_pass = any(c.attempt_outcome == "EXECUTED_PASS" for c in evaluated_cells)
        if has_pass:
            has_contradiction = any(
                c.attempt_outcome in ("PRODUCT_FAILURE", "QUOTA_BLOCKED", "ENVIRONMENT_UNAVAILABLE")
                for c in evaluated_cells
            )
            if has_contradiction:
                return False

    # 2. Enumerate full required composite CellKeys
    expected_required: set[CellKey] = set(required_cell_keys) if required_cell_keys is not None else set()
    if not expected_required:
        bindings = {c.cell_key.peer_binding for c in rollup_cells}
        for b in bindings:
            expected_required.update(adapter_manifest.get_expected_required_cell_keys(peer_binding=b))
            
    if not expected_required:
        return False
        
    # 3. Verify completeness: 100% of required composite CellKeys must be covered and passing
    covered_cell_keys: set[CellKey] = set()
    for cell in rollup_cells:
        req_state = determine_requirement_state(cell.cell_key, adapter_manifest)
        if req_state == "REQUIRED":
            ev_state = determine_evidence_state(cell, current_env)
            if ev_state != "MEASURED":
                return False
            if cell.attempt_outcome != "EXECUTED_PASS":
                return False
            covered_cell_keys.add(cell.cell_key)
            
    # If any required proof_kind for any coverage case is omitted, issubset returns False.
    return expected_required.issubset(covered_cell_keys)
```

## 8. Coverage Cases and Parity Ledger Mapping

This maps representative actions from the 90-action Parity Ledger (`docs/design/PHASE1-PARITY-LEDGER-BATCH1-2026-08-20.md` through `BATCH5-2026-08-20.md`) and the three real peer adapters (`claude-peer`, `codex-peer`, `agy-peer` from `docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`) into concrete `coverage_case_id`s.

The table illustrates six genuinely distinct architectural situations across the parity ledger:
1. **Core Adapter Manifest Dispatch (`ask`)**: Main prompt dispatch executing the real `claude.cmd` PIPE template (`cc.standard`) and validating the JSON response envelope.
2. **Simple Read-Only Query (`credit-status`)**: Pure read-only inspection of rate-limit reset credit quota via `CodexAccountClient().read_rate_limits()`, strictly idempotent with no side effects.
3. **Non-Idempotent / Irreversible Mutation (`credit-consume`)**: Irreversible upstream quota consumption requiring human terminal origin (`origin="terminal"`), `--confirm` flag, and UUID idempotency correlation through a 3-stage preflight/audit/verify pipeline.
4. **Real Concurrency Race Condition (`thread-new`)**: Unlocked check-then-act defect (`path.exists()` before `path.open("a")`) verified to produce duplicate `THREAD_CREATE` headers when two peers invoke simultaneously.
5. **Smart-Model Final Arbiter Governance (`arbiter-review`)**: DIR-005 governance action invoking `cc.fable` on split consensus rounds, strictly budget-guarded (5 reviews per 5h window).
6. **PIPE Transport Session (`init-session`)**: Agent lifecycle initialization on PIPE transport (`ag.standard`), exercising the `builtin:json-agy-v1` stream parser.

| Adapter | Parity Ledger Row (Action) | Batch Citation | `coverage_case_id` | Core Parity Req? | Behavioral Scenario & Finding |
|---|---|---|---|---|---|
| `claude-peer` (`cc`) | `ask` (`action_ask`) | Batch 1, Action 12 | `action.hub.ask` | YES | Standard PIPE peer prompt invocation (`cc.standard`); validates stdout JSON envelope and exit code. |
| `codex-peer` (`cx`) | `credit-status` (`action_credit_status`) | Batch 5, Action 17 | `action.hub.credit-status` | YES | Pure read-only, idempotent app-server query via `CodexAccountClient`; never modifies state. |
| `codex-peer` (`cx`) | `credit-consume` (`action_credit_consume`) | Batch 5, Action 18 | `action.hub.credit-consume` | YES | Irreversible mutation requiring human `--confirm` + canonical UUID; multi-stage preflight/audit/verify lifecycle. |
| `claude-peer` (`cc`) / `codex-peer` (`cx`) | `thread-new` (`action_thread_new`) | Batch 5, Action 4 | `action.hub.thread-new` | YES | Check-then-act race condition (`fix-thread-new-conc-01`); concurrent creation produces duplicate `THREAD_CREATE` headers. |
| `claude-peer` (`cc` Arbiter) | `arbiter-review` (`run_arbiter_on_round`) | Batch 5, Action 16 | `action.hub.arbiter-review` | NO (Optional) | DIR-005 smart-model final arbiter for dissenting rounds; strictly budget-limited (5/5h window). |
| `agy-peer` (`ag`) | `init-session` (`action_init_session`) | Batch 1, Action 1 | `action.hub.init-session` | YES | Session lifecycle initialization, agent registration in `state.json`, and `_log_p2p` JOIN emission via PIPE transport (`ag.standard`). |

## 9. Worked Examples (Concrete JSON)

Every worked example below uses Peerhub's real adapters, real paths from empirical host discovery (`PHASE1-ADMISSION-RECEIPTS-REAL-2026-08-20.md`), real profiles, and verified behavior from the 90-action parity ledger.

### 7.1 PASSING State: `action.hub.ask` via `claude-peer`
Captures successful integration execution of `hub.py ask` delegating to `claude-peer` (`cc.standard`) via PIPE transport.

```json
{
  "cell_key": {
    "coverage_case_id": "action.hub.ask",
    "peer_binding": "profile:cc.standard",
    "platform": "win32-x64",
    "transport": "PIPE",
    "proof_kind": "deterministic contract or integration"
  },
  "requirement_state": "REQUIRED",
  "evidence_state": "MEASURED",
  "attempt_outcome": "EXECUTED_PASS",
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
    "transport": "PIPE",
    "proof_kind": "deterministic contract or integration"
  },
  "requirement_state": "REQUIRED",
  "evidence_state": "MEASURED",
  "attempt_outcome": "PRODUCT_FAILURE",
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
    "transport": "PIPE",
    "proof_kind": "deterministic contract or integration"
  },
  "requirement_state": "REQUIRED",
  "evidence_state": "STALE",
  "attempt_outcome": "EXECUTED_PASS",
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

### 7.4 UNAVAILABLE State: `action.hub.init-session` via `agy-peer` (Executable Absent)
Captures execution on a host where the target executable (`agy.exe`) is absent from the `PATH` or its configured location. Because the required runtime dependency is missing from the environment, the test harness cleanly records `UNAVAILABLE` without registering a spurious product failure.

```json
{
  "cell_key": {
    "coverage_case_id": "action.hub.init-session",
    "peer_binding": "profile:ag.standard",
    "platform": "win32-x64",
    "transport": "PIPE",
    "proof_kind": "deterministic contract or integration"
  },
  "requirement_state": "REQUIRED",
  "evidence_state": "UNAVAILABLE",
  "attempt_outcome": "ENVIRONMENT_UNAVAILABLE",
  "provenance": {
    "timestamp_utc": "2026-08-20T22:35:00Z",
    "isolation_root": "N/A",
    "provider_home": "P:/_sys/tools/agy",
    "session_id": "room-efde",
    "lease_id": "lease-ag-init-003",
    "source_tags": ["empirical_probe"],
    "redacted_receipt_hash": "N/A"
  },
  "raw_capture_protection": false,
  "serialization_policy": "APPEND_ONLY"
}
```

### 7.5 CONTRADICTORY State: Rollup Example on `action.hub.credit-status`
While an individual promotion cell cannot be contradictory (as `proof_kind` is an immutable part of the `cell_key`), a **Coverage Case Rollup** evaluates sibling cells for the same `(coverage_case_id, peer_binding, platform, transport)`.

If the following two cells exist simultaneously for `action.hub.credit-status` (`profile:cx.standard`, `win32-x64`, `PIPE`):

**Cell A (`proof_kind: "deterministic contract or integration"` - EXECUTED_PASS)**
* `proof_kind`: "deterministic contract or integration"
* `attempt_outcome`: "EXECUTED_PASS"
* `evidence_state`: "MEASURED"
*(Integration test against live app-server successfully queries rate limit quota and returns exit code 0)*

**Cell B (`proof_kind: "controlled real-OS executable"` - PRODUCT_FAILURE)**
* `proof_kind`: "controlled real-OS executable"
* `attempt_outcome`: "PRODUCT_FAILURE"
* `evidence_state`: "MEASURED"
*(Executable test assertion failed because mock capability declaration in local harness config omitted `supports_reset_credits`)*

The overall rollup for `action.hub.credit-status` on `(profile:cx.standard, win32-x64, PIPE)` resolves to **`CONTRADICTORY`**, halting promotion until the discrepancy between the executable simulation and live integration execution is investigated and resolved.
