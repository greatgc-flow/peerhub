# Promotion Ledger Schema & Execution Rules V1
**Date:** 2026-08-20

This document defines the machine-readable schema, enumeration rules, state transitions, and deterministic algorithms for the Peerhub Promotion Ledger. This replaces the previous descriptive prose with a concrete, computable model.


## 0. Scope and Threat Model

This document specifies the machine-readable schema, enumeration rules, state transitions, and deterministic algorithms for the Peerhub Promotion Ledger. The mechanisms described herein (such as the construction-guard token, the admission registry's closure-scoped storage, and the evidence registry) exist solely to prevent accidental, careless, or naive misuse by legitimate code operating in good faith. 

They do **not** provide a real security boundary against an adversary who already has arbitrary code execution capabilities inside the same Python interpreter. No purely in-process Python mechanism can ever provide a genuine absolute security boundary against a fully compromised or deliberately hostile process already running inside the same interpreter. Achieving real protection against that specific threat would require moving trust enforcement outside the Python process entirely (e.g., using OS-level process separation), which this document does not attempt and should not claim to achieve.

## 1. Machine-Readable Schema

The Promotion Ledger is modeled as an inventory of discrete **Cells**. Each cell uniquely identifies a test execution context and its outcome. 

### 1.1 Promotion Ledger Cell (JSON Schema)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PromotionLedgerCell",
  "description": "A genuinely independent, immutable snapshot of an evidence capture for a specific capability context.",
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
MAX_ALLOWED_CLOCK_SKEW_SECONDS = 3600 # 1 hour maximum allowed future clock skew

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
        
    # Check formal age validation and future skew bounds
    # Both timestamps are guaranteed to be timezone-aware datetime objects in UTC
    env_time = current_env.current_time_utc
    cell_time = cell.provenance.timestamp_utc

    # Defensive timezone alignment if necessary
    if env_time.tzinfo is None and cell_time.tzinfo is not None:
        env_time = env_time.replace(tzinfo=timezone.utc)
    elif env_time.tzinfo is not None and cell_time.tzinfo is None:
        cell_time = cell_time.replace(tzinfo=timezone.utc)

    skew = (cell_time - env_time).total_seconds()
    if skew > MAX_ALLOWED_CLOCK_SKEW_SECONDS:
        return "ERROR"

    age = env_time - cell_time
    if age.total_seconds() > MAX_AGE_SECONDS:
        return "STALE"
         
    return cell.evidence_state
```

### 5. Adapter Manifest Evaluation Context & Type Definitions

To eliminate schema-drift vulnerabilities and establish a strict single source of truth (SSOT), `docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md` (Section 3) is explicitly designated as the **single normative source of truth** for the Phase 1 Manifest JSON Schema (Draft 2020-12). This document does not maintain a second, decoupled inline transcription of the schema; the admission and promotion validator is loaded directly from that canonical source definition.

To ground the requirement rules in concrete, typed contracts matching `PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`, the evaluation context defines `load_manifest_schema_v2`, `AdmissionRegistry`, `CellKey`, `ProfileDescriptor`, and `AdapterManifest` (which carries genuine immutable `ProfileDescriptor` snapshots of the admitted manifest's real declared profiles).

### 6. Architectural Mapping: AdmissionCoordinator & StateStore

The `AdmissionRegistry` model defined below is a design-stage abstraction. It explicitly represents the executable-integrity and manifest-evaluation portion of the **real, already-existing `peerhub.dispatch.admission.AdmissionCoordinator`'s broader admission pipeline**. During Phase 2 implementation, this capability will not be a permanently separate mechanism; instead, it will be wired directly into `AdmissionCoordinator` as an additional critical verification step before a request is fully admitted.

Furthermore, the closure-scoped in-memory store (`_store`) used throughout this document's execution traces is strictly a design-stage prototype. Phase 2 implementation will back this concept with the **real, durable `StateStore`-backed persistence** that `peerhub.dispatch.admission.py` already uses for its own real admission state, entirely replacing the in-memory closure pattern.

```python
from __future__ import annotations
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import ClassVar
import os
import sys
import hashlib
import json
import re
import secrets
import threading
import jsonschema
from dataclasses import dataclass
from typing import Literal
from enum import Enum

class ExecutableRole(str, Enum):
    ENTRYPOINT_WRAPPER = "ENTRYPOINT_WRAPPER"
    INTERPRETER = "INTERPRETER"
    SCRIPT = "SCRIPT"
    NATIVE_BINARY = "NATIVE_BINARY"
    HELPER_BINARY = "HELPER_BINARY"

@dataclass(frozen=True, slots=True)
class TransitiveExecutableNode:
    role: ExecutableRole
    canonical_path: str
    file_size_bytes: int
    sha256: str
    is_reparse_point: Literal[None] = None

@dataclass(frozen=True, slots=True)
class AclEvaluationEvidence:
    evaluated_paths: tuple[str, ...]
    volume_type: str
    everyone_writable: bool
    anonymous_writable: bool
    authenticated_users_modify_allowed: bool
    effective_dacl_summary: str
    verdict: Literal["PASS_SECURE_LOCAL", "FAIL_WORLD_WRITABLE", "FAIL_NON_NTFS"]

from types import MappingProxyType

@dataclass(frozen=True, slots=True)
class ProvisioningEvidenceReceipt:
    receipt_id: str
    schema_version: Literal["2.0.0"]
    adapter_id: str
    peer_kind: str
    inventory_generation: int
    trust_root: MappingProxyType[str, str]
    observed_vendor: MappingProxyType[str, str | None]
    acl_evaluation: AclEvaluationEvidence | None
    transitive_executable_chain: tuple[TransitiveExecutableNode, ...]
    companion_binaries: tuple[TransitiveExecutableNode, ...]
    aggregate_chain_digest: str
    timestamp_utc: str
    # In this Phase 1 in-memory prototype, chain_complete is ALWAYS False for every admission.
    # What chain_complete=False means and does not mean:
    # A two-byte "MZ" magic-number check alone cannot prove a file is a genuine, safe, or complete native binary
    # (as forged bytes can easily bypass it), and a caller-supplied role label is untrusted.
    # Therefore, this Phase 1 prototype only ever performs shallow entrypoint-only hash verification and makes
    # NO claim whatsoever about full closure verification or execution surface completeness.
    # chain_complete=False proves the exact bytes of the single entrypoint file that will be invoked,
    # but makes no claim about downstream execution or completeness. This field/status must NOT be relied upon
    # as a completeness or safety guarantee under any circumstances in Phase 1 (full recursive derivation and
    # structural PE validation are deferred to Phase 2).
    chain_complete: bool

@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    admission_receipt_id: str
    manifest_canonical_sha256: str
    provisioning_evidence: ProvisioningEvidenceReceipt
    admitted_at_utc: str
    # Mirrors provisioning_evidence.chain_complete: ALWAYS False in Phase 1.
    # Indicates shallow entrypoint verification only; must not be relied upon as a completeness or safety guarantee.
    chain_complete: bool

def load_manifest_schema_v2(schema_path: str | Path | None = None) -> dict:
    """Loads the normative Phase 1 Manifest JSON Schema (Draft 2020-12) directly from its canonical source of truth."""
    if schema_path is None:
        candidates = [
            Path("docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md"),
            Path(__file__).parent / "PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md" if "__file__" in globals() else None,
            Path("PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md"),
        ]
        for c in candidates:
            if c and c.exists():
                schema_path = c
                break
        if schema_path is None:
            schema_path = Path("docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md")
    
    content = Path(schema_path).read_text(encoding="utf-8")
    match = re.search(r"```json\s*(\{[\s\S]*?\"\$id\":\s*\"https://peerhub\.local/schema/adapter-manifest/v2\"[\s\S]*?\})\s*```", content)
    if not match:
        raise ValueError(f"Could not extract normative manifest schema v2 from {schema_path}")
    return json.loads(match.group(1))

_MANIFEST_SCHEMA_V2 = load_manifest_schema_v2()
_MANIFEST_VALIDATOR = jsonschema.Draft202012Validator(_MANIFEST_SCHEMA_V2)

_active_manifest_token: ContextVar[str | None] = ContextVar("_active_manifest_token", default=None)

def _build_admission_registry():
    _store: dict[str, AdmissionReceipt] = {}  # receipt_id -> AdmissionReceipt
    _lock: threading.Lock = threading.Lock()

    class AdmissionRegistry:
        """Minimal trusted registry for admitted manifests and executables.
        
        Populated exclusively during a real admission event after rigorous validation
        against the PHASE1-MANIFEST-SCHEMA-V2 specification AND real executable-integrity
        evidence (single entrypoint hash verification and manifest-target binding, with truthful role
        and explicit chain_complete=False receipt status). ACL evaluation, trust-root verification,
        vendor observation, full PE structural validation, and full recursive wrapper-chain derivation
        from the filesystem are honestly scoped OUT of this Phase 1 in-memory prototype (see acl_evaluation=None
        and chain_complete=False below) and deferred to a real Phase 2 HostCapabilityInventory implementation;
        this registry does not claim to perform them.
        It computes and stores the AdmissionReceipt under a newly issued, collision-safe 
        real receipt ID (e.g., receipt-cc-claude-peer-...), following the proven single-source-of-truth
        discipline established by ARCHITECTURE.md's AdmissionSnapshot.
        
        The registry is the only trusted source for linking a receipt ID to a digest,
        discouraging callers from accidentally supplying their own digests as proof.

        The backing store and lock are encapsulated inside a closure rather than exposed
        as public or mutable class attributes. Direct external writes (such as
        mutating AdmissionRegistry._store or monkeypatching class attributes) fail or
        have zero effect on trusted admission and get_trusted_digest lookups.
        """

        @classmethod
        def validate_manifest(cls, raw_manifest: dict) -> None:
            """Validates raw manifest against PHASE1-MANIFEST-SCHEMA-V2 specification and semantic rules."""
            if not isinstance(raw_manifest, dict):
                raise TypeError(f"Manifest must be a dict, got {type(raw_manifest).__name__}")

            # 1. Authoritative V2 JSON Schema structural validation (Draft 2020-12)
            errors = list(_MANIFEST_VALIDATOR.iter_errors(raw_manifest))
            if errors:
                err_details = [f"{e.message}" for e in errors]
                raise ValueError(f"Manifest schema validation failed: {'; '.join(err_details)}")

            # 2. Semantic admission rules (Section 4.1 in V2 Specification)
            # Rule 4.1.1: Prompt placeholder in start template
            start_tmpl = raw_manifest["execution"]["templates"]["start"]
            start_has_prompt = any(
                "{prompt_content}" in arg or "{prompt_reference}" in arg
                for arg in start_tmpl.get("argv", [])
            ) or (
                isinstance(start_tmpl.get("stdin"), str)
                and ("{prompt_content}" in start_tmpl["stdin"] or "{prompt_reference}" in start_tmpl["stdin"])
            )
            if not start_has_prompt:
                raise ValueError("Semantic validation failed: 'execution.templates.start' MUST contain '{prompt_content}' or '{prompt_reference}' in argv or stdin.")

            # Rule 4.1.2: Resume template guard (mandatory if SESSION capability declared)
            capabilities = raw_manifest["adapter"].get("capabilities", [])
            templates = raw_manifest["execution"]["templates"]
            if "SESSION" in capabilities and "resume" not in templates:
                raise ValueError("Manifest declares SESSION capability but is missing 'execution.templates.resume'")

            if "resume" in templates:
                resume_tmpl = templates["resume"]
                resume_has_session = any(
                    "{session.external_session_id}" in arg or "{conversation}" in arg or "{session." in arg
                    for arg in resume_tmpl.get("argv", [])
                ) or (
                    isinstance(resume_tmpl.get("stdin"), str)
                    and ("{session.external_session_id}" in resume_tmpl["stdin"] or "{session." in resume_tmpl["stdin"])
                )
                if not resume_has_session:
                    raise ValueError("Semantic validation failed: 'execution.templates.resume' MUST contain session placeholder in argv or stdin.")

        @classmethod
        def validate_executable_chain(cls, raw_manifest: dict, transitive_executable_chain: list[dict]) -> tuple[str, tuple[TransitiveExecutableNode, ...]]:
            """Validates the transitive executable chain and returns its aggregate digest."""
            if not isinstance(transitive_executable_chain, list) or len(transitive_executable_chain) != 1:
                # Scope decision explicit note:
                # Multi-node wrapper-chain admission, requiring real recursive derivation of actual invocation 
                # edges from wrapper file contents, is deferred entirely to Phase 2. Phase 1 admission restricts
                # the chain to exactly the single resolved entrypoint node under its truthful declared role.
                raise ValueError("Executable chain must contain exactly one node (Phase 1 limitation).")
            
            target = raw_manifest.get("execution", {}).get("executable", {}).get("target")
            if not target:
                raise ValueError("Manifest missing execution.executable.target")
                
            nodes = []
            for idx, item in enumerate(transitive_executable_chain):
                if not isinstance(item, dict):
                    raise TypeError("Each executable chain item must be a dictionary.")
                if "role" not in item or "canonical_path" not in item or "sha256" not in item:
                    raise ValueError("Executable chain item missing required fields: role, canonical_path, sha256.")
                    
                role_str = item["role"]
                if role_str not in [e.value for e in ExecutableRole]:
                    raise ValueError(f"Invalid role {role_str}")
                role = ExecutableRole(role_str)
                
                c_path = item["canonical_path"]
                if not os.path.isabs(c_path):
                    raise ValueError(f"canonical_path must be an absolute path, got '{c_path}'")
                
                # Consistently use canonicalized absolute form everywhere
                c_path_canon = os.path.normpath(os.path.abspath(c_path))
                claimed_hash = item["sha256"]
                
                if not os.path.exists(c_path_canon):
                    raise ValueError(f"Executable path does not exist: {c_path_canon}")
                
                if idx == 0:
                    resolution_rule = raw_manifest.get("execution", {}).get("executable", {}).get("resolution_rule")
                    if not resolution_rule:
                        raise ValueError("Manifest missing execution.executable.resolution_rule")
                    
                    resolved_target = None
                    if resolution_rule == "absolute":
                        if not os.path.isabs(target):
                            raise ValueError(f"resolution_rule 'absolute' requires target to be an absolute path, got '{target}'")
                        resolved_target = target
                    elif resolution_rule == "sibling":
                        # Honest scope deferral: PHASE1-MANIFEST-SCHEMA-V1's normative definition
                        # requires "sibling" to resolve relative to the *manifest file's own
                        # directory*. This in-memory prototype has no concept of a manifest's
                        # source file path (raw_manifest is a caller-supplied dict, not a loaded
                        # file), so os.path.abspath(target) would silently resolve relative to the
                        # process's current working directory instead -- a different, incorrect
                        # binding that could accidentally match or accidentally reject depending on
                        # CWD. Rather than accept that unreliable behavior, "sibling" is explicitly
                        # unsupported until Phase 2 threads a real manifest source path through.
                        raise ValueError(
                            "resolution_rule 'sibling' is not supported by this in-memory "
                            "prototype: no manifest source directory is tracked. Use 'absolute' "
                            "or 'path' instead, or defer to Phase 2 HostCapabilityInventory."
                        )
                    elif resolution_rule == "path":
                        if (
                            "/" in target
                            or "\\" in target
                            or os.sep in target
                            or (os.altsep and os.altsep in target)
                            or os.path.isabs(target)
                            or bool(os.path.dirname(target))
                            or bool(os.path.splitdrive(target)[0])
                        ):
                            raise ValueError(
                                f"resolution_rule 'path' requires a bare command name with no path components, got '{target}'"
                            )
                        
                        # Honest scope disclosure (Modeled Launcher):
                        # This Phase 1 prototype's "path" resolution_rule models cmd.exe-style PATHEXT-based
                        # command resolution specifically, not the Win32 CreateProcess API's implicit-.exe-only
                        # search. This choice is deliberate because this project's real wrapper-fronted peers
                        # (claude.cmd, codex.cmd) are batch/cmd scripts that require the command interpreter
                        # to run, making cmd.exe semantics the actually relevant real launcher behavior here.
                        #
                        # Scope Boundary & Quirk Deferral:
                        # This validator does not attempt to replicate every obscure cmd.exe parsing quirk.
                        # For example, quoted PATH directory entries (e.g. PATH='C:\foo;"C:\Program Files\bar"')
                        # and arbitrary cmd.exe error recovery behaviors are explicitly out of scope for this
                        # Phase 1 prototype. Instead, this validator strictly enforces well-formed inputs:
                        # malformed PATHEXT values (entries missing leading dots, empty entries adjacent to
                        # semicolons) fail closed immediately rather than attempting heuristic repair.
                        
                        # Strict PATH-only resolution: Never consult or fall back to CWD implicitly.
                        # Enumerate only directories explicitly present in the PATH environment variable.
                        path_dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
                        resolved_target = None
                        is_windows = sys.platform == "win32" or os.name == "nt"
                        target_has_ext = bool(os.path.splitext(target)[1])
                        
                        pathext_list = []
                        if is_windows:
                            if "PATHEXT" not in os.environ:
                                raw_pathext = ".COM;.EXE;.BAT;.CMD"
                            else:
                                raw_pathext = os.environ["PATHEXT"]
                            
                            if raw_pathext == "":
                                pathext_list = []
                            else:
                                raw_tokens = raw_pathext.split(os.pathsep)
                                for token in raw_tokens:
                                    t = token.strip()
                                    if not t:
                                        raise ValueError(
                                            f"resolution_rule 'path' requires a well-formed PATHEXT environment variable "
                                            f"(found empty token in PATHEXT '{raw_pathext}'). Cannot safely resolve "
                                            f"commands under an ambiguous or malformed PATHEXT."
                                        )
                                    if not t.startswith("."):
                                        raise ValueError(
                                            f"resolution_rule 'path' requires a well-formed PATHEXT environment variable "
                                            f"(every entry must start with a dot, got '{t}' in PATHEXT '{raw_pathext}'). "
                                            f"Cannot safely resolve commands under an ambiguous or malformed PATHEXT."
                                        )
                                    pathext_list.append(t)
                        
                        for directory in path_dirs:
                            candidate = os.path.join(directory, target)
                            if is_windows and not target_has_ext:
                                # When target has no extension on Windows, cmd.exe searches PATHEXT-extended candidates
                                # in PATHEXT order; bare extensionless files are not executable via PATH.
                                matched = False
                                for ext in pathext_list:
                                    ext_candidate = candidate + ext
                                    if os.path.isfile(ext_candidate):
                                        resolved_target = ext_candidate
                                        matched = True
                                        break
                                if matched:
                                    break
                            else:
                                # Non-Windows or target already has an explicit extension
                                if os.path.isfile(candidate):
                                    resolved_target = candidate
                                    break
                                    
                        if resolved_target is None:
                            raise ValueError(f"Target '{target}' with resolution_rule 'path' could not be resolved via OS PATH")
                    else:
                        raise ValueError(f"Unknown resolution_rule {resolution_rule}")
                        
                    resolved_canon = os.path.normpath(os.path.abspath(resolved_target))
                    if not os.path.exists(resolved_canon):
                        raise ValueError(f"Resolved target does not exist: {resolved_canon}")
                    
                    try:
                        same_file = os.path.samefile(resolved_canon, c_path_canon)
                    except OSError:
                        same_file = False
                    if not same_file:
                        raise ValueError(f"Executable chain entrypoint {c_path_canon} does not match resolved manifest target {resolved_target}")
                
                with open(c_path_canon, "rb") as f:
                    file_content = f.read()
                    actual_hash = hashlib.sha256(file_content).hexdigest().upper()
                if actual_hash != claimed_hash.upper():
                    raise ValueError(f"Executable hash mismatch for {c_path_canon}! Claimed: {claimed_hash}, Actual: {actual_hash}")
                
                if role == ExecutableRole.NATIVE_BINARY and not file_content.startswith(b"MZ"):
                    # Honest scope deferral: This is a minimal two-byte magic-number check, not full PE structural
                    # validation. A magic-byte check alone cannot prove a file is a genuine, safe, or complete native
                    # binary (e.g. a script with forged "MZ" bytes). Phase 1 performs only shallow entrypoint hash
                    # verification and never asserts full execution surface completeness.
                    raise ValueError(f"File content at {c_path_canon} does not match NATIVE_BINARY format claim (missing MZ magic bytes).")
                
                nodes.append(TransitiveExecutableNode(
                    role=role,
                    canonical_path=c_path_canon,
                    file_size_bytes=os.path.getsize(c_path_canon),
                    sha256=actual_hash,
                    is_reparse_point=None
                ))

            # Compute aggregate chain digest (deterministic sort by role and path)
            sorted_nodes = sorted(nodes, key=lambda x: (x.role.value, x.canonical_path))
            payload = ""
            for node in sorted_nodes:
                payload += f"{node.role.value}:{node.canonical_path}:{node.sha256}\n"
            
            return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper(), tuple(nodes)

        @classmethod
        def admit(cls, raw_manifest: dict, transitive_executable_chain: list[dict], max_retries: int = 10) -> str:
            """Admission lifecycle: Validates manifest and executable integrity, issues rich receipt."""
            # 1. Genuine schema validation before issuance
            cls.validate_manifest(raw_manifest)

            # 2. Canonical AST digest computation over full manifest (manifest_ast_digest)
            manifest_digest = AdapterManifest.canonical_digest(raw_manifest)

            # 3. Validate executable integrity and compute aggregate digest
            aggregate_chain_digest, nodes = cls.validate_executable_chain(raw_manifest, transitive_executable_chain)

            # In this Phase 1 in-memory prototype, chain_complete is ALWAYS False for every admission:
            # caller-supplied role labels and a 2-byte MZ check cannot prove full closure or binary safety/completeness.
            # This prototype performs shallow entrypoint-only verification; full verification is deferred to Phase 2.
            chain_complete = False

            # Extract fields for receipt generation
            adapter_id = raw_manifest["adapter"]["adapter_id"]
            peer_kind = raw_manifest["adapter"]["peer_kind"]
            timestamp_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

            # Scope decision explicit note:
            # We explicitly skip full real ACL evaluation, trust root verification, vendor observations,
            # and full recursive wrapper-chain derivation from the filesystem for this in-memory prototype,
            # opting for honest placeholders instead of mocking OS mechanics.
            # A genuine Phase 2 HostCapabilityInventory implementation will populate these fields with
            # real NTFS/OS evidence before inserting into the real AdmissionCoordinator.
            
            # 4. Collision-safe receipt ID issuance with atomic check-and-insert under lock
            for attempt in range(max_retries):
                # e.g. receipt-cc-claude-peer-20260820T215000Z-a1b2c3d4...
                random_suffix = secrets.token_hex(16)
                candidate_id = f"receipt-{peer_kind}-{adapter_id}-{timestamp_utc}-{random_suffix}"
                
                with _lock:
                    if candidate_id not in _store:
                        prov_evidence = ProvisioningEvidenceReceipt(
                            receipt_id=candidate_id,
                            schema_version="2.0.0",
                            adapter_id=adapter_id,
                            peer_kind=peer_kind,
                            inventory_generation=1,
                            trust_root=MappingProxyType({"host_machine": "UNVERIFIED_PROTOTYPE"}),
                            observed_vendor=MappingProxyType({}),
                            acl_evaluation=None, # Explicitly skipped in Phase 1 schema model
                            transitive_executable_chain=nodes,
                            companion_binaries=(),
                            aggregate_chain_digest=aggregate_chain_digest,
                            timestamp_utc=timestamp_utc,
                            chain_complete=chain_complete,
                        )
                        receipt = AdmissionReceipt(
                            admission_receipt_id=candidate_id,
                            manifest_canonical_sha256=manifest_digest,
                            provisioning_evidence=prov_evidence,
                            admitted_at_utc=timestamp_utc,
                            chain_complete=chain_complete,
                        )
                        _store[candidate_id] = receipt
                        return candidate_id

            raise RuntimeError("Collision resolution exhausted: unable to generate a unique admission receipt ID.")

        @classmethod
        def get_trusted_receipt(cls, receipt_id: str) -> AdmissionReceipt:
            """Looks up the full trusted receipt by registry-issued receipt ID."""
            if not isinstance(receipt_id, str):
                raise TypeError("receipt_id must be a string")
            with _lock:
                receipt = _store.get(receipt_id)
            if receipt is None:
                raise ValueError(f"Unknown admission receipt ID: {receipt_id}")
            return receipt
            
        @classmethod
        def get_trusted_digest(cls, receipt_id: str) -> str:
            """Promotion lifecycle: Looks up the trusted digest by registry-issued receipt ID."""
            receipt = cls.get_trusted_receipt(receipt_id)
            return receipt.manifest_canonical_sha256

        @classmethod
        def store_size(cls) -> int:
            """Read-only introspection returning the current number of admitted entries in the trusted registry."""
            with _lock:
                return len(_store)

    return AdmissionRegistry

AdmissionRegistry = _build_admission_registry()

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
class ProfileDescriptor:
    """Immutable snapshot descriptor for a declared adapter profile captured at admission time.
    Prevents post-admission mutations of caller-owned raw manifest dictionaries from retroactively
    altering the admitted manifest's declared profiles or transport bindings.
    """
    profile_id: str
    transport: str  # "PIPE" | "PTY"
    profile_class: str = "tier"
    supports_reasoning_effort: bool = False

@dataclass(frozen=True, slots=True)
class AdapterManifest:
    """Documented contract of an admitted adapter manifest used during promotion evaluation.
    Enforces deterministic construction exclusively from an admitted manifest schema instance
    whose canonical content digest matches an authentic admission receipt.
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
    profiles: tuple[ProfileDescriptor, ...]
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
                "Instances must be traceably constructed via AdapterManifest.from_manifest(raw_manifest, admission_receipt, VALID_CHAIN)."
            )

    @staticmethod
    def canonical_digest(raw_manifest: dict) -> str:
        """Computes the unambiguous, deterministic SHA-256 hex digest over canonical JSON.

        Canonicalization rule (Full Manifest Scope):
        1. Serialize the full manifest document to UTF-8 encoded JSON with sorted keys
           (sort_keys=True) and compact delimiters separators=(',', ':') with ensure_ascii=False.
        2. Compute SHA-256 over the UTF-8 bytes and return uppercase hexadecimal string.
        """
        if not isinstance(raw_manifest, dict):
            raise TypeError(f"Payload must be a dict, got {type(raw_manifest).__name__}.")
        
        canonical_bytes = json.dumps(
            raw_manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest().upper()

    @classmethod
    def from_manifest(
        cls,
        raw_manifest: dict,
        admission_receipt_id: str,
        transitive_executable_chain: list[dict],
    ) -> AdapterManifest:
        """Constructs this contract strictly by validating schema shape, verifying canonical
        digest authenticity via the trusted AdmissionRegistry, and reading fields directly
        out of the admitted manifest into independent, immutable ProfileDescriptor snapshots.
        
        The caller MUST provide a valid, registry-issued admission_receipt_id.
        The registry itself provides the expected digest, closing the accidental forgery gap.
        """
        if not isinstance(raw_manifest, dict) or "adapter" not in raw_manifest or "profiles" not in raw_manifest:
            raise ValueError("raw_manifest must be an admitted manifest dict containing 'adapter' and 'profiles' blocks.")

        # 1. Look up trusted receipt from the registry using the opaque ID
        trusted_receipt = AdmissionRegistry.get_trusted_receipt(admission_receipt_id)
        expected_manifest_digest = trusted_receipt.manifest_canonical_sha256
        expected_chain_digest = trusted_receipt.provisioning_evidence.aggregate_chain_digest

        # 2. Recompute canonical digest over FULL manifest content and verify authenticity
        recomputed_manifest_digest = cls.canonical_digest(raw_manifest)
        if recomputed_manifest_digest != expected_manifest_digest:
            raise ValueError(
                f"Manifest admission digest mismatch! Registry expects digest '{expected_manifest_digest}', "
                f"but recomputed canonical digest over provided manifest is '{recomputed_manifest_digest}'. "
                "Manifest is either unadmitted, fabricated, or unintentionally modified."
            )

        # 3. Recompute aggregate chain digest over provided chain and verify authenticity
        recomputed_chain_digest, _ = AdmissionRegistry.validate_executable_chain(raw_manifest, transitive_executable_chain)
        if recomputed_chain_digest != expected_chain_digest:
            raise ValueError(
                f"Executable chain admission digest mismatch! Registry expects digest '{expected_chain_digest}', "
                f"but recomputed aggregate digest over provided chain is '{recomputed_chain_digest}'. "
                "Chain is either unadmitted, fabricated, or unintentionally modified."
            )

        adapter = raw_manifest["adapter"]
        required_adapter_keys = (
            "adapter_id",
            "peer_kind",
            "capabilities",
            "supported_platforms",
            "supported_transports",
            "core_parity_requirements",
            "required_proof_kinds",
            "requires_snapshots",
        )
        missing_adapter = [k for k in required_adapter_keys if k not in adapter]
        if missing_adapter:
            raise ValueError(f"Admitted manifest missing required adapter policy fields: {missing_adapter}")

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

        # 3. Validate and extract declared profile descriptors into genuine immutable snapshots
        raw_profiles = raw_manifest["profiles"]
        if not isinstance(raw_profiles, (list, tuple)) or not raw_profiles:
            raise TypeError("Field 'profiles' must be a non-empty list or tuple.")
        
        parsed_profiles: list[ProfileDescriptor] = []
        for p in raw_profiles:
            if not isinstance(p, dict) or "profile_id" not in p or not isinstance(p["profile_id"], str) or not p["profile_id"]:
                raise TypeError("Each profile in 'profiles' must be a dict containing a non-empty string 'profile_id'.")
            transport = p.get("transport", "PIPE")
            if not isinstance(transport, str) or transport not in ("PIPE", "PTY"):
                raise TypeError(f"Profile '{p['profile_id']}' transport must be 'PIPE' or 'PTY', got {transport!r}.")
            profile_class = p.get("profile_class", "tier")
            if not isinstance(profile_class, str):
                raise TypeError(f"Profile '{p['profile_id']}' profile_class must be a string, got {type(profile_class).__name__}.")
            supports_effort = p.get("supports_reasoning_effort", False)
            if not isinstance(supports_effort, bool):
                raise TypeError(f"Profile '{p['profile_id']}' supports_reasoning_effort must be a bool, got {type(supports_effort).__name__}.")
            
            # Deep snapshot: capture values into a fresh frozen ProfileDescriptor instance.
            # No reference to caller-owned dicts or mutable nested objects is retained.
            parsed_profiles.append(
                ProfileDescriptor(
                    profile_id=str(p["profile_id"]),
                    transport=str(transport),
                    profile_class=str(profile_class),
                    supports_reasoning_effort=supports_effort,
                )
            )

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
                profiles=tuple(parsed_profiles),
                _token=token,
            )
        finally:
            _active_manifest_token.reset(reset_token)

    @property
    def declared_profile_ids(self) -> frozenset[str]:
        """Returns the set of genuine profile_id strings declared in this admitted manifest."""
        return frozenset(p.profile_id for p in self.profiles)

    def is_valid_peer_binding(self, peer_binding: str) -> bool:
        """Checks if a peer_binding string corresponds to a real profile_id declared in this manifest."""
        if not isinstance(peer_binding, str):
            return False
        norm = peer_binding[8:] if peer_binding.startswith("profile:") else peer_binding
        return norm in self.declared_profile_ids or peer_binding in self.declared_profile_ids

    def get_profile(self, peer_binding_or_profile_id: str) -> ProfileDescriptor | None:
        """Looks up the declared profile descriptor by peer_binding or profile_id."""
        if not isinstance(peer_binding_or_profile_id, str):
            return None
        norm = (
            peer_binding_or_profile_id[8:]
            if peer_binding_or_profile_id.startswith("profile:")
            else peer_binding_or_profile_id
        )
        for p in self.profiles:
            if p.profile_id == norm:
                return p
        return None

    def get_profile_transport(self, peer_binding_or_profile_id: str) -> str | None:
        """Returns the declared transport ('PIPE' | 'PTY') for the given profile."""
        p = self.get_profile(peer_binding_or_profile_id)
        return p.transport if p else None

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
        transport: str | None = None,
    ) -> set[CellKey]:
        """Enumerates the full composite CellKey set required for promotion."""
        if not self.is_valid_peer_binding(peer_binding):
            return set()
        resolved_transport = transport if transport is not None else self.get_profile_transport(peer_binding)
        if resolved_transport is None:
            return set()
        keys: set[CellKey] = set()
        for case_id in self.core_parity_requirements:
            for proof in self.required_proof_kinds:
                key = CellKey(
                    coverage_case_id=case_id,
                    peer_binding=peer_binding,
                    platform=platform,
                    transport=resolved_transport,
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

### 7.1 EvidenceSnapshot Schema Scope & Field Projection

`EvidenceSnapshot` is intentionally a **smaller, named promotion-specific projection** of the full `PromotionLedgerCell` schema defined in Section 1.1:

- **Captured Fields:** It captures exactly the minimal subset of attributes required for deterministic evaluation of `can_promote()`: `cell_key` (`coverage_case_id`, `peer_binding`, `platform`, `transport`, `proof_kind`), `attempt_outcome`, `evidence_state`, and `provenance.timestamp_utc` (used for freshness, skew, and lifecycle validation in `determine_evidence_state()`).
- **Timestamp UTC Normalization:** Every genuinely timezone-aware timestamp is normalized directly to canonical UTC (`astimezone(timezone.utc)`) at admission time upon snapshot construction before validation and storage, ensuring that timestamps carrying non-UTC offsets (such as +09:00) are converted to a zero-offset representation (`timezone.utc`) while preserving the exact absolute point in time. Offset-naive timestamps without timezone information remain strictly rejected.
- **Enum Validation on CellKey:** `validate_snapshot()` strictly validates `transport` and `proof_kind` against the manifest schema's authoritative enum sets (`{"PIPE", "PTY"}` for transport; `{"deterministic contract or integration", "controlled real-OS executable", "live provider exact-profile", "legacy-parity evidence"}` for proof_kind), rejecting invalid or fabricated values before a receipt is issued so bogus evidence cannot land in contradiction rollup groups.
- **Deliberately Omitted Fields:** It omits `requirement_state` (which is computed dynamically by the promotion gate against the authoritative `AdapterManifest`), `raw_capture_protection`, `serialization_policy`, and execution-level provenance metadata (`isolation_root`, `provider_home`, `session_id`, `lease_id`, `source_tags`, `redacted_receipt_hash`).
- **Enforcement of Omitted Fields:** These omitted evidence-integrity and execution-context fields are not evaluated during the in-memory boolean rollup; they are enforced at the ingestion and ledger-storage layers during raw receipt generation, cryptographic hashing, and persistence serialization prior to admission into the promotion evaluation pipeline.

```python

@dataclass(frozen=True, slots=True)
class ProvenanceSnapshot:
    timestamp_utc: datetime

    def __post_init__(self):
        if not isinstance(self.timestamp_utc, datetime):
            raise TypeError("provenance must carry a real timestamp_utc of the real datetime type")
        if self.timestamp_utc.tzinfo is None or self.timestamp_utc.tzinfo.utcoffset(self.timestamp_utc) is None:
            raise ValueError("timestamp_utc must be a timezone-aware datetime (e.g. timezone.utc)")
        if self.timestamp_utc.tzinfo != timezone.utc or self.timestamp_utc.utcoffset() != timezone.utc.utcoffset(None):
            object.__setattr__(self, "timestamp_utc", self.timestamp_utc.astimezone(timezone.utc))

@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    """Immutable promotion-specific projection descriptor for an admitted evidence cell.
    
    Captures only the fields required for promotion rollup (cell_key, attempt_outcome,
    evidence_state, provenance.timestamp_utc), deliberately omitting ledger-integrity
    fields (raw_capture_protection, serialization_policy, extended provenance) which are
    enforced at receipt ingestion and persistence layers.
    """
    cell_key: CellKey
    attempt_outcome: str
    evidence_state: str
    provenance: ProvenanceSnapshot

def _build_evidence_registry():
    _store: dict[str, dict] = {}  # receipt_id -> cell dict
    _lock: threading.Lock = threading.Lock()

    class EvidenceRegistry:
        """Registry for admitted evidence cells.
        
        This registry performs real shape and type validation against accidental or 
        malformed evidence, consistent with the threat model section's honest framing.
        It does NOT provide unforgeable provenance binding or absolute security guarantees
        against deliberate same-process tampering. It constructs a genuinely independent,
        immutable promotion-specific snapshot of the cell's evaluation data at admission time,
        validating its required projection fields so admitted evidence cannot be retroactively altered.
        """
        
        @classmethod
        def validate_snapshot(cls, snapshot: EvidenceSnapshot) -> None:
            if not isinstance(snapshot, EvidenceSnapshot):
                raise TypeError("Candidate snapshot must be an EvidenceSnapshot instance")
                
            if not isinstance(snapshot.cell_key, CellKey):
                raise TypeError("cell_obj must expose a real cell_key of the real CellKey type")
                
            ck = snapshot.cell_key
            for field_name in ["coverage_case_id", "peer_binding", "platform", "transport", "proof_kind"]:
                val = getattr(ck, field_name, None)
                if not isinstance(val, str):
                    raise TypeError(f"cell_key.{field_name} must be a string")
                    
            valid_transports = {"PIPE", "PTY"}
            if ck.transport not in valid_transports:
                raise ValueError(f"cell_key.transport must be one of {valid_transports}, got {ck.transport!r}")

            valid_proof_kinds = {
                "deterministic contract or integration",
                "controlled real-OS executable",
                "live provider exact-profile",
                "legacy-parity evidence",
            }
            if ck.proof_kind not in valid_proof_kinds:
                raise ValueError(f"cell_key.proof_kind must be one of {valid_proof_kinds}, got {ck.proof_kind!r}")

            valid_outcomes = {"EXECUTED_PASS", "PRODUCT_FAILURE", "QUOTA_BLOCKED", "ENVIRONMENT_UNAVAILABLE", "NOT_REQUESTED"}
            if snapshot.attempt_outcome not in valid_outcomes:
                raise ValueError(f"cell_obj.attempt_outcome must be one of {valid_outcomes}")
                
            valid_evidence_states = {"MEASURED", "ABSENT", "UNAVAILABLE", "ERROR", "STALE"}
            if snapshot.evidence_state not in valid_evidence_states:
                raise ValueError(f"cell_obj.evidence_state must be one of {valid_evidence_states}")
                
            if snapshot.provenance is None:
                raise ValueError("cell_obj must have a real provenance object")
            if not isinstance(snapshot.provenance.timestamp_utc, datetime):
                raise ValueError("provenance must carry a real timestamp_utc of the real datetime type")
            if snapshot.provenance.timestamp_utc.tzinfo is None or snapshot.provenance.timestamp_utc.tzinfo.utcoffset(snapshot.provenance.timestamp_utc) is None:
                raise ValueError("provenance.timestamp_utc must be a timezone-aware datetime (offset-naive datetimes are rejected)")
            if snapshot.provenance.timestamp_utc.tzinfo != timezone.utc or snapshot.provenance.timestamp_utc.utcoffset() != timezone.utc.utcoffset(None):
                raise ValueError("provenance.timestamp_utc must have a UTC offset of exactly zero")

        @classmethod
        def admit(cls, cell_data: dict, max_retries: int = 10) -> str:
            if not isinstance(cell_data, dict):
                raise TypeError("cell_data must be a dictionary")
            
            if "cell_obj" not in cell_data:
                raise ValueError("cell_data must contain a 'cell_obj' key")
                
            cell = cell_data["cell_obj"]
            
            # Read each required value off the caller's cell object exactly once
            raw_ck = getattr(cell, "cell_key", None)
            if raw_ck is None or not isinstance(raw_ck, CellKey):
                raise TypeError("cell_obj must expose a real cell_key of the real CellKey type")

            # Capture individual key attributes exactly once into fresh string values
            coverage_case_id = getattr(raw_ck, "coverage_case_id", None)
            peer_binding = getattr(raw_ck, "peer_binding", None)
            platform = getattr(raw_ck, "platform", None)
            transport = getattr(raw_ck, "transport", None)
            proof_kind = getattr(raw_ck, "proof_kind", None)

            for field_name, field_val in [
                ("coverage_case_id", coverage_case_id),
                ("peer_binding", peer_binding),
                ("platform", platform),
                ("transport", transport),
                ("proof_kind", proof_kind),
            ]:
                if not isinstance(field_val, str):
                    raise TypeError(f"cell_key.{field_name} must be a string")

            # Reconstruct fresh CellKey from captured strings for full independence
            fresh_cell_key = CellKey(
                coverage_case_id=coverage_case_id,
                peer_binding=peer_binding,
                platform=platform,
                transport=transport,
                proof_kind=proof_kind,
            )

            raw_outcome = getattr(cell, "attempt_outcome", None)
            raw_state = getattr(cell, "evidence_state", None)
            raw_prov = getattr(cell, "provenance", None)

            if raw_prov is None:
                raise ValueError("cell_obj must have a real provenance object")
            raw_ts = getattr(raw_prov, "timestamp_utc", None)

            # Build candidate EvidenceSnapshot from single reads immediately
            candidate_snapshot = EvidenceSnapshot(
                cell_key=fresh_cell_key,
                attempt_outcome=raw_outcome,
                evidence_state=raw_state,
                provenance=ProvenanceSnapshot(
                    timestamp_utc=raw_ts
                ),
            )

            # Run validation strictly against the already-constructed candidate snapshot
            cls.validate_snapshot(candidate_snapshot)

            # Atomic issue
            for attempt in range(max_retries):
                candidate_id = f"ev_{secrets.token_hex(16)}"
                with _lock:
                    if candidate_id not in _store:
                        _store[candidate_id] = {"cell_obj": candidate_snapshot}
                        return candidate_id
            raise RuntimeError("Unable to generate a unique evidence receipt ID.")
            
        @classmethod
        def get_validated_cell(cls, receipt_id: str) -> dict:
            if not isinstance(receipt_id, str):
                raise TypeError("receipt_id must be a string")
            with _lock:
                cell_data = _store.get(receipt_id)
            if cell_data is None:
                raise ValueError(f"Unknown evidence receipt ID: {receipt_id}")
            return dict(cell_data)

    return EvidenceRegistry

EvidenceRegistry = _build_evidence_registry()

def can_promote(
    evidence_receipts: list[str],
    current_env,
    adapter_manifest: AdapterManifest,
    required_cell_keys: set[CellKey] | None = None,
) -> bool:
    """
    Returns True if and only if:
    1. rollup_cells is non-empty and every cell corresponds to a real admitted profile declared in adapter_manifest.
    2. Every required composite CellKey for the admitted manifest's declared profiles (using each profile's
       own declared transport) is covered and passing.
    3. If caller supplies required_cell_keys, it is strictly validated as a subset of the manifest-derived
       requirements and cannot substitute, weaken, or reduce the manifest-authoritative required set.
    4. Every REQUIRED cell is in evidence_state MEASURED and attempt_outcome EXECUTED_PASS.
    5. Contradiction Guard: No sibling cell within the same rollup context (same coverage_case_id,
       peer_binding, platform, transport) has a divergent contradictory outcome (PRODUCT_FAILURE,
       QUOTA_BLOCKED, ENVIRONMENT_UNAVAILABLE) against a passing sibling cell in the same rollup group.
    6. Returns False if any cell carries an unadmitted/fabricated peer_binding, or if any required cell
       is missing, stale, unavailable, failed, omitted, or contradicted by a divergent sibling cell.
    """
    if not evidence_receipts:
        return False

    # 1. Look up validated cell objects from EvidenceRegistry
    rollup_cells = []
    for receipt_id in evidence_receipts:
        try:
            cell_data = EvidenceRegistry.get_validated_cell(receipt_id)
            rollup_cells.append(cell_data["cell_obj"])
        except ValueError:
            return False

    # 2. Reject any rollup cell whose peer_binding does not correspond to a real admitted profile
    for cell in rollup_cells:
        if not adapter_manifest.is_valid_peer_binding(cell.cell_key.peer_binding):
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

    # 2. Contradiction Detection
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

    # 3. Always compute the real, manifest-derived required set first from the admitted manifest's
    # own declared profiles, reading each profile's individually declared transport.
    manifest_required: set[CellKey] = set()
    for profile in adapter_manifest.profiles:
        profile_id = profile.profile_id
        profile_transport = profile.transport
        manifest_required.update(
            adapter_manifest.get_expected_required_cell_keys(
                peer_binding=f"profile:{profile_id}",
                transport=profile_transport,
            )
        )

    if not manifest_required:
        return False

    # Caller-supplied required_cell_keys cannot substitute or reduce the manifest-derived set.
    # If supplied, validate that it is an allowable subset of the manifest-derived requirements.
    if required_cell_keys is not None:
        caller_set = set(required_cell_keys)
        if not caller_set.issubset(manifest_required):
            return False

    expected_required: set[CellKey] = manifest_required

    # 4. Verify completeness: 100% of required composite CellKeys must be covered and passing
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


## 10. Execution Trace (Verification of SSOT, Admission, Promotion, Staleness & Invariants)

This execution trace exercises the normative single source of truth (SSOT) schema loader, the admission lifecycle, promotion evaluation, freshness/staleness checks, and security invariants. Rather than maintaining a separate, decoupled re-implementation of the validator, registry, or promotion algorithms within this trace section, these tests directly invoke the primary definitions established above in Sections 4, 5, 6, and 7.

```python
# --- EXECUTION TRACES ---
# Directly invoking primary definitions from Sections 4, 5, 6, 7 above without duplication.

import sys
import os
import shutil
import hashlib

_real_native_path = sys.executable
with open(_real_native_path, "rb") as f:
    _real_native_hash = hashlib.sha256(f.read()).hexdigest().upper()
VALID_NATIVE_CHAIN = [{'role': 'NATIVE_BINARY', 'canonical_path': _real_native_path, 'sha256': _real_native_hash, 'is_reparse_point': False}]
VALID_CHAIN = VALID_NATIVE_CHAIN

# Real tracked wrapper fixtures from PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md
_real_codex_path = shutil.which("codex.cmd") or os.path.abspath(r"P:\_sys\cli\codex.bat")
with open(_real_codex_path, "rb") as f:
    _real_codex_hash = hashlib.sha256(f.read()).hexdigest().upper()
CODEX_WRAPPER_CHAIN = [{'role': 'ENTRYPOINT_WRAPPER', 'canonical_path': _real_codex_path, 'sha256': _real_codex_hash, 'is_reparse_point': False}]

_real_claude_path = shutil.which("claude.cmd") or os.path.abspath(r"P:\_sys\cli\claude.bat")
with open(_real_claude_path, "rb") as f:
    _real_claude_hash = hashlib.sha256(f.read()).hexdigest().upper()
CLAUDE_WRAPPER_CHAIN = [{'role': 'ENTRYPOINT_WRAPPER', 'canonical_path': _real_claude_path, 'sha256': _real_claude_hash, 'is_reparse_point': False}]

print("--- 1. Single Normative Schema SSOT & Mechanical Equality Check ---")
canonical_raw_schema = load_manifest_schema_v2()
print(f"Normative V2 Schema ID loaded from canonical file: {canonical_raw_schema['$id']}")
print(f"Validator schema equals canonical source: {_MANIFEST_SCHEMA_V2 == canonical_raw_schema}")

print("\n--- 2. cx's readiness_probe_id Discrepancy Resolved ---")
probe_id_subschema = _MANIFEST_SCHEMA_V2["properties"]["adapter"]["properties"]["readiness_probe_id"]
print(f"Normative schema definition for 'readiness_probe_id': {probe_id_subschema}")
print(f"Has minLength constraint: {'minLength' in probe_id_subschema} (Correct: False, unconstrained string)")

print("\n--- 3. cx's missing-required-fields manifest rejected at admit() ---")
cx_missing_fields_manifest = {
    "adapter": {
        "adapter_id": "test-adapter",
        "peer_kind": "test",
        "capabilities": ["STREAM"],
        "supported_platforms": ["win32-x64"],
        "supported_transports": ["PIPE"],
        "core_parity_requirements": ["action.hub.ask"],
        "required_proof_kinds": ["deterministic contract or integration"],
        "requires_snapshots": False
    },
    "version": "1.0"
}
try:
    AdmissionRegistry.admit(cx_missing_fields_manifest, CLAUDE_WRAPPER_CHAIN)
    print("FAILED: cx missing-fields manifest was unexpectedly admitted!")
except Exception as e:
    print(f"REJECTED at admit() as expected: {type(e).__name__}: {e}")

print("\n--- 4. cx's exact extra-key Codex manifest rejected at admit() before receipt issuance ---")
valid_claude_manifest = {
    "manifest_version": "2.0.0",
    "status": "active",
    "adapter": {
        "adapter_id": "claude-peer",
        "adapter_version": "1.0.0",
        "peer_kind": "cc",
        "capabilities": ["SESSION"],
        "supported_platforms": ["win32-x64"],
        "supported_transports": ["PIPE"],
        "core_parity_requirements": ["action.hub.ask", "action.hub.thread-new"],
        "required_proof_kinds": ["deterministic contract or integration", "controlled real-OS executable"],
        "requires_snapshots": False,
        "readiness_probe_id": "process-exit-zero"
    },
    "execution": {
        "executable": {
            "resolution_rule": "path",
            "target": "claude.cmd"
        },
        "templates": {
            "start": {
                "argv": ["{executable}", "-p", "{prompt_content}", "--output-format", "json"],
                "cwd": "{workspace_scope}"
            },
            "resume": {
                "argv": ["{executable}", "-p", "{prompt_content}", "--output-format", "json", "--resume", "{session.external_session_id}"],
                "cwd": "{workspace_scope}"
            }
        },
        "env_policy": {
            "inherit": ["PATH", "SYSTEMROOT", "USERPROFILE"],
            "set": {}
        }
    },
    "engine": {
        "engine_id": "builtin:json-claude-v1",
        "options": {"enforce_strict_json": True}
    },
    "profiles": [
        {
            "profile_id": "cc.standard",
            "profile_class": "tier",
            "supports_reasoning_effort": False,
            "transport": "PIPE",
            "prompt_policy": {
                "policy_id": "cc-standard-policy",
                "max_inline_utf8_bytes": 1000000,
                "artifact_reference_supported": False
            }
        }
    ]
}

cx_extra_key_codex_manifest = dict(valid_claude_manifest)
cx_extra_key_codex_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
cx_extra_key_codex_manifest["adapter"]["adapter_id"] = "codex-peer"
cx_extra_key_codex_manifest["adapter"]["peer_kind"] = "cx"
cx_extra_key_codex_manifest["engine"] = {
    "engine_id": "builtin:jsonl-codex-v1",
    "options": {"enforce_strict_json": True}
}

store_size_before = AdmissionRegistry.store_size()
try:
    AdmissionRegistry.admit(cx_extra_key_codex_manifest, CLAUDE_WRAPPER_CHAIN)
    print("FAILED: cx extra-key Codex manifest was unexpectedly admitted!")
except Exception as e:
    store_size_after = AdmissionRegistry.store_size()
    print(f"REJECTED at admit() as expected: {type(e).__name__}: {e}")
    print(f"Store unpolluted (no receipt issued): {store_size_before == store_size_after}")

print("\n--- 5. Fully schema-valid Codex manifest with empty options admitted successfully ---")
valid_codex_manifest = dict(valid_claude_manifest)
valid_codex_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
valid_codex_manifest["adapter"]["adapter_id"] = "codex-peer"
valid_codex_manifest["adapter"]["peer_kind"] = "cx"
valid_codex_manifest["adapter"]["capabilities"] = ["SESSION", "STREAM"]
valid_codex_manifest["adapter"]["core_parity_requirements"] = ["action.hub.credit-status", "action.hub.credit-consume", "action.hub.thread-new"]
valid_codex_manifest["adapter"]["requires_snapshots"] = True
valid_codex_manifest["execution"] = dict(valid_claude_manifest["execution"])
valid_codex_manifest["execution"]["executable"] = {
    "resolution_rule": "path",
    "target": "codex.cmd"
}
valid_codex_manifest["execution"]["templates"] = {
    "start": {
        "argv": ["{executable}", "exec", "--json", "{prompt_content}"],
        "cwd": "{workspace_scope}"
    },
    "resume": {
        "argv": ["{executable}", "exec", "resume", "--json", "{session.external_session_id}", "{prompt_content}"],
        "cwd": "{workspace_scope}"
    }
}
valid_codex_manifest["execution"]["env_policy"] = {
    "inherit": ["PATH", "SYSTEMROOT", "USERPROFILE", "APPDATA"],
    "set": {}
}
valid_codex_manifest["engine"] = {
    "engine_id": "builtin:jsonl-codex-v1",
    "options": {}
}
valid_codex_manifest["profiles"] = [
    {
        "profile_id": "cx.standard",
        "profile_class": "tier",
        "supports_reasoning_effort": False,
        "transport": "PIPE",
        "prompt_policy": {
            "policy_id": "cx-standard-policy",
            "max_inline_utf8_bytes": 1000000,
            "artifact_reference_supported": False
        }
    }
]

codex_receipt_id = AdmissionRegistry.admit(valid_codex_manifest, CODEX_WRAPPER_CHAIN)
print(f"Admitted valid Codex manifest, got 128-bit collision-safe receipt: {codex_receipt_id}")
codex_receipt = AdmissionRegistry.get_trusted_receipt(codex_receipt_id)
print(f"Codex receipt chain_complete (shallow entrypoint verification): {codex_receipt.chain_complete}")
codex_manifest_obj = AdapterManifest.from_manifest(valid_codex_manifest, codex_receipt_id, CODEX_WRAPPER_CHAIN)
print(f"SUCCESS: Constructed {codex_manifest_obj.adapter_id} ({codex_manifest_obj.peer_kind}) carrying genuine declared profiles: {codex_manifest_obj.declared_profile_ids}")

print("\n--- 6. Fully schema-valid Claude manifest admitted with declared profiles ---")
claude_receipt_id = AdmissionRegistry.admit(valid_claude_manifest, CLAUDE_WRAPPER_CHAIN)
print(f"Admitted valid Claude manifest, got 128-bit collision-safe receipt: {claude_receipt_id}")
claude_receipt = AdmissionRegistry.get_trusted_receipt(claude_receipt_id)
print(f"Claude receipt chain_complete (shallow entrypoint verification): {claude_receipt.chain_complete}")
claude_manifest_obj = AdapterManifest.from_manifest(valid_claude_manifest, claude_receipt_id, CLAUDE_WRAPPER_CHAIN)
print(f"SUCCESS: Constructed {claude_manifest_obj.adapter_id} ({claude_manifest_obj.peer_kind}) carrying genuine declared profiles: {claude_manifest_obj.declared_profile_ids}")

print("\n--- 7. Repro of cx's Fabricated peer_binding Attack Against can_promote() ---")
from dataclasses import field

@dataclass(frozen=True)
class MockProvenance:
    timestamp_utc: datetime = datetime(2026, 8, 20, 22, 0, 0, tzinfo=timezone.utc)

@dataclass
class MockEnv:
    missing_dependencies: bool = False
    current_time_utc: datetime = datetime(2026, 8, 20, 22, 30, 0, tzinfo=timezone.utc)

@dataclass
class MockCell:
    cell_key: CellKey
    attempt_outcome: str = "EXECUTED_PASS"
    evidence_state: str = "MEASURED"
    provenance: MockProvenance = field(default_factory=MockProvenance)

# Attacker provides passing cells for a fabricated peer_binding never declared in admitted manifest
fabricated_binding = "profile:fabricated.peer"
fabricated_cells = [
    MockCell(CellKey(
        coverage_case_id="action.hub.ask",
        peer_binding=fabricated_binding,
        platform="win32-x64",
        transport="PIPE",
        proof_kind="deterministic contract or integration",
    )),
    MockCell(CellKey(
        coverage_case_id="action.hub.ask",
        peer_binding=fabricated_binding,
        platform="win32-x64",
        transport="PIPE",
        proof_kind="controlled real-OS executable",
    )),
    MockCell(CellKey(
        coverage_case_id="action.hub.thread-new",
        peer_binding=fabricated_binding,
        platform="win32-x64",
        transport="PIPE",
        proof_kind="deterministic contract or integration",
    )),
    MockCell(CellKey(
        coverage_case_id="action.hub.thread-new",
        peer_binding=fabricated_binding,
        platform="win32-x64",
        transport="PIPE",
        proof_kind="controlled real-OS executable",
    )),
]

fabricated_receipts = [EvidenceRegistry.admit({"cell_obj": c}) for c in fabricated_cells]

mock_env = MockEnv()
promotion_result_fabricated = can_promote(fabricated_receipts, mock_env, claude_manifest_obj)
print(f"can_promote(fabricated_cells) returned: {promotion_result_fabricated}")
print(f"ATTACK BLOCKED: Fabricated peer_binding rejected by can_promote: {promotion_result_fabricated is False}")

print("\n--- 8. Genuine Admitted Profile Promotion Rollup ---")
genuine_binding = "profile:cc.standard"
genuine_cells = [
    MockCell(CellKey(
        coverage_case_id="action.hub.ask",
        peer_binding=genuine_binding,
        platform="win32-x64",
        transport="PIPE",
        proof_kind="deterministic contract or integration",
    )),
    MockCell(CellKey(
        coverage_case_id="action.hub.ask",
        peer_binding=genuine_binding,
        platform="win32-x64",
        transport="PIPE",
        proof_kind="controlled real-OS executable",
    )),
    MockCell(CellKey(
        coverage_case_id="action.hub.thread-new",
        peer_binding=genuine_binding,
        platform="win32-x64",
        transport="PIPE",
        proof_kind="deterministic contract or integration",
    )),
    MockCell(CellKey(
        coverage_case_id="action.hub.thread-new",
        peer_binding=genuine_binding,
        platform="win32-x64",
        transport="PIPE",
        proof_kind="controlled real-OS executable",
    )),
]

genuine_receipts = [EvidenceRegistry.admit({"cell_obj": c}) for c in genuine_cells]

promotion_result_genuine = can_promote(genuine_receipts, mock_env, claude_manifest_obj)
print(f"can_promote(genuine_cells) returned: {promotion_result_genuine}")
print(f"GENUINE PROMOTION SUCCESS: Admitted profile correctly promoted: {promotion_result_genuine is True}")

print("\n--- 9. Mixed Genuine + Unadmitted Peer Binding Rollup ---")
mixed_cells = genuine_cells + [MockCell(CellKey(
    coverage_case_id="action.hub.ask",
    peer_binding="profile:unadmitted.injected",
    platform="win32-x64",
    transport="PIPE",
    proof_kind="deterministic contract or integration",
))]

mixed_receipts = [EvidenceRegistry.admit({"cell_obj": c}) for c in mixed_cells]
promotion_result_mixed = can_promote(mixed_receipts, mock_env, claude_manifest_obj)
print(f"can_promote(mixed_cells) returned: {promotion_result_mixed}")
print(f"MIXED INJECTION BLOCKED: Unadmitted cell rejected: {promotion_result_mixed is False}")

print("\n--- 10. Collision safety: forced sequential collision retried to fresh receipt ID ---")
existing_receipt_id = claude_receipt_id
raw_existing_token = existing_receipt_id.split("-")[-1]

second_valid_manifest = dict(valid_claude_manifest)
second_valid_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
# Force the same peer_kind and adapter_id so the receipt prefix matches exactly
second_valid_manifest["adapter"]["adapter_id"] = "claude-peer"
second_valid_manifest["adapter"]["peer_kind"] = "cc"
second_valid_manifest["engine"] = {"engine_id": "builtin:json-claude-v1", "options": {"enforce_strict_json": True}}
second_valid_manifest["profiles"] = valid_claude_manifest["profiles"]

mock_tokens = [raw_existing_token, "abcdef0123456789abcdef0123456789"]
def mock_token_hex(nbytes=16):
    return mock_tokens.pop(0) if mock_tokens else "99999999999999999999999999999999"

orig_token_hex = secrets.token_hex
secrets.token_hex = mock_token_hex
try:
    first_digest_before = AdmissionRegistry.get_trusted_digest(existing_receipt_id)
    second_receipt_id = AdmissionRegistry.admit(second_valid_manifest, CLAUDE_WRAPPER_CHAIN)
    first_digest_after = AdmissionRegistry.get_trusted_digest(existing_receipt_id)
    
    print(f"Earlier receipt ID ({existing_receipt_id}) digest preserved intact: {first_digest_before == first_digest_after}")
    print(f"Second admission detected collision and retried to fresh ID: {second_receipt_id}")
    print(f"Store size now: {AdmissionRegistry.store_size()} distinct entries (no clobbering!)")
finally:
    secrets.token_hex = orig_token_hex

print("\n--- 11. Concurrency safety: Real multi-threaded concurrent execution with forced interleaving ---")
colliding_candidate_raw = "11112222333344445555666677778888"
t1_unique_raw = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
t2_unique_raw = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

thread_token_streams = {
    "T1": [colliding_candidate_raw, t1_unique_raw],
    "T2": [colliding_candidate_raw, t2_unique_raw],
}

def concurrent_mock_token_hex(nbytes=16):
    tname = threading.current_thread().name
    stream = thread_token_streams.get(tname)
    if stream:
        return stream.pop(0)
    return secrets.token_bytes(nbytes).hex()

manifest_t1 = dict(valid_claude_manifest)
manifest_t1["adapter"] = dict(valid_claude_manifest["adapter"])
manifest_t1["adapter"]["adapter_id"] = "conc-peer"

manifest_t2 = dict(valid_claude_manifest)
manifest_t2["adapter"] = dict(valid_claude_manifest["adapter"])
manifest_t2["adapter"]["adapter_id"] = "conc-peer"

concurrent_results = {}
barrier = threading.Barrier(2)

def concurrent_worker(tname, manifest):
    threading.current_thread().name = tname
    barrier.wait()
    receipt = AdmissionRegistry.admit(manifest, CLAUDE_WRAPPER_CHAIN)
    concurrent_results[tname] = (receipt, AdapterManifest.canonical_digest(manifest))

secrets.token_hex = concurrent_mock_token_hex
try:
    store_count_before_conc = AdmissionRegistry.store_size()
    th1 = threading.Thread(target=concurrent_worker, args=("T1", manifest_t1))
    th2 = threading.Thread(target=concurrent_worker, args=("T2", manifest_t2))
    th1.start(); th2.start()
    th1.join(); th2.join()

    r1, d1 = concurrent_results["T1"]
    r2, d2 = concurrent_results["T2"]

    print(f"Thread 1 receipt ID: {r1}")
    print(f"Thread 2 receipt ID: {r2}")
    print(f"Receipt IDs are distinct (no duplicate ID issued): {r1 != r2}")
    print(f"Thread 1 digest in registry: {AdmissionRegistry.get_trusted_digest(r1) == d1}")
    print(f"Thread 2 digest in registry: {AdmissionRegistry.get_trusted_digest(r2) == d2}")
    print(f"Store size increased by exactly 2 entries: {AdmissionRegistry.store_size() == store_count_before_conc + 2}")
    print("CONCURRENCY RACE VERIFIED FIXED: Atomic lock prevents TOCTOU clobbering under real thread contention.")
finally:
    secrets.token_hex = orig_token_hex

print("\n--- 12. Forgery attempt (supplying own digest as receipt ID) ---")
forged_manifest = dict(valid_claude_manifest)
forged_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
forged_manifest["adapter"]["adapter_id"] = "forged-adapter"

forged_digest = AdapterManifest.canonical_digest(forged_manifest)
try:
    AdapterManifest.from_manifest(forged_manifest, forged_digest, CLAUDE_WRAPPER_CHAIN)
    print("SUCCESS: Forgery worked! (This should not happen)")
except Exception as e:
    print(f"BLOCKED: {type(e).__name__}: {e}")

print("\n--- 13. Syntactically well-formed but unknown receipt ID ---")
unknown_receipt_id = "rcpt_00000000000000000000000000000000"
try:
    AdapterManifest.from_manifest(valid_claude_manifest, unknown_receipt_id, CLAUDE_WRAPPER_CHAIN)
    print("SUCCESS: Unknown receipt worked! (This should not happen)")
except Exception as e:
    print(f"BLOCKED: {type(e).__name__}: {e}")

print("\n--- 14. Freshness & Staleness Invalidation (8-Day-Old Evidence Evaluates to STALE) ---")
# Evidence captured 8 days ago (exceeding MAX_AGE_SECONDS = 7 days)
fresh_timestamp = datetime(2026, 8, 20, 22, 0, 0, tzinfo=timezone.utc)
stale_8d_timestamp = datetime(2026, 8, 12, 22, 0, 0, tzinfo=timezone.utc)  # 8 days before evaluation time 2026-08-20T22:30:00+00:00

fresh_cell = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=fresh_timestamp),
)

stale_cell_8d = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=stale_8d_timestamp),
)

eval_fresh_state = determine_evidence_state(fresh_cell, mock_env)
eval_stale_state = determine_evidence_state(stale_cell_8d, mock_env)
age_days = (mock_env.current_time_utc - stale_cell_8d.provenance.timestamp_utc).total_seconds() / 86400.0

print(f"Fresh cell age: {(mock_env.current_time_utc - fresh_cell.provenance.timestamp_utc).total_seconds() / 3600.0:.1f} hours -> evidence_state: {eval_fresh_state}")
print(f"8-day-old cell age: {age_days:.1f} days -> evidence_state: {eval_stale_state}")
print(f"STALENESS CHECK VERIFIED: 8-day-old evidence evaluates to STALE: {eval_stale_state == 'STALE'}")

# Test promotion rejection when a required cell is 8 days old
stale_rollup_cells = [
    stale_cell_8d,
    MockCell(CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable")),
    MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration")),
    MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable")),
]

stale_receipts = [EvidenceRegistry.admit({"cell_obj": c}) for c in stale_rollup_cells]
promotion_result_stale = can_promote(stale_receipts, mock_env, claude_manifest_obj)
print(f"can_promote(stale_rollup_cells) with 8-day-old cell returned: {promotion_result_stale}")
print(f"STALE PROMOTION BLOCKED: can_promote rejected stale evidence: {promotion_result_stale is False}")

print("\n--- 15. Round 31 Trust Boundary: caller-supplied required_cell_keys cannot override/weaken manifest ---")
# Caller provides a single trivial required key attempting to bypass the 4 real required keys
one_cell_override = {CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration")}
only_one_passing_cell = [MockCell(CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"))]

only_one_passing_receipt = [EvidenceRegistry.admit({"cell_obj": c}) for c in only_one_passing_cell]

promotion_override_result = can_promote(only_one_passing_receipt, mock_env, claude_manifest_obj, required_cell_keys=one_cell_override)
print(f"can_promote with 1-cell override subset but only 1 cell provided: {promotion_override_result}")
print(f"REQUIRED SET OVERRIDE BLOCKED: 100% manifest requirement enforcement held: {promotion_override_result is False}")

# Caller provides an invalid non-manifest requirement key
invalid_override = {CellKey("action.hub.unadmitted", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration")}
promotion_invalid_override = can_promote(genuine_receipts, mock_env, claude_manifest_obj, required_cell_keys=invalid_override)
print(f"can_promote with non-manifest requirement override: {promotion_invalid_override}")
print(f"NON-SUBSET OVERRIDE REJECTED: Caller cannot inject arbitrary requirements: {promotion_invalid_override is False}")

print("\n--- 16. Round 31 Per-Profile Transport: PTY profile requires PTY evidence (cannot promote on PIPE) ---")
valid_pty_manifest = dict(valid_claude_manifest)
valid_pty_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
valid_pty_manifest["adapter"]["adapter_id"] = "pty-peer"
valid_pty_manifest["adapter"]["peer_kind"] = "pty"
valid_pty_manifest["adapter"]["supported_transports"] = ["PTY"]
valid_pty_manifest["profiles"] = [
    {
        "profile_id": "pty.standard",
        "profile_class": "tier",
        "supports_reasoning_effort": False,
        "transport": "PTY",
        "prompt_policy": {
            "policy_id": "pty-standard-policy",
            "max_inline_utf8_bytes": 1000000,
            "artifact_reference_supported": False
        }
    }
]

pty_receipt_id = AdmissionRegistry.admit(valid_pty_manifest, CLAUDE_WRAPPER_CHAIN)
pty_manifest_obj = AdapterManifest.from_manifest(valid_pty_manifest, pty_receipt_id, CLAUDE_WRAPPER_CHAIN)
pty_expected_keys = pty_manifest_obj.get_expected_required_cell_keys("profile:pty.standard")
pty_transports = {k.transport for k in pty_expected_keys}
print(f"PTY profile expected required cell transports: {pty_transports}")

# Supplying PIPE-only evidence for a PTY-declared profile fails promotion
pipe_cells_for_pty = [
    MockCell(CellKey("action.hub.ask", "profile:pty.standard", "win32-x64", "PIPE", "deterministic contract or integration")),
    MockCell(CellKey("action.hub.ask", "profile:pty.standard", "win32-x64", "PIPE", "controlled real-OS executable")),
    MockCell(CellKey("action.hub.thread-new", "profile:pty.standard", "win32-x64", "PIPE", "deterministic contract or integration")),
    MockCell(CellKey("action.hub.thread-new", "profile:pty.standard", "win32-x64", "PIPE", "controlled real-OS executable")),
]

pipe_receipts_for_pty = [EvidenceRegistry.admit({"cell_obj": c}) for c in pipe_cells_for_pty]
promotion_pipe_on_pty = can_promote(pipe_receipts_for_pty, mock_env, pty_manifest_obj)
print(f"can_promote(pipe_cells_for_pty) returned: {promotion_pipe_on_pty}")
print(f"TRANSPORT MISMATCH BLOCKED: PIPE evidence cannot satisfy PTY profile requirement: {promotion_pipe_on_pty is False}")

# Genuine PTY evidence for PTY-declared profile succeeds
genuine_pty_cells = [
    MockCell(CellKey("action.hub.ask", "profile:pty.standard", "win32-x64", "PTY", "deterministic contract or integration")),
    MockCell(CellKey("action.hub.ask", "profile:pty.standard", "win32-x64", "PTY", "controlled real-OS executable")),
    MockCell(CellKey("action.hub.thread-new", "profile:pty.standard", "win32-x64", "PTY", "deterministic contract or integration")),
    MockCell(CellKey("action.hub.thread-new", "profile:pty.standard", "win32-x64", "PTY", "controlled real-OS executable")),
]

genuine_pty_receipts = [EvidenceRegistry.admit({"cell_obj": c}) for c in genuine_pty_cells]
promotion_genuine_pty = can_promote(genuine_pty_receipts, mock_env, pty_manifest_obj)
print(f"can_promote(genuine_pty_cells) returned: {promotion_genuine_pty}")
print(f"GENUINE PTY PROMOTION SUCCESS: PTY evidence satisfies PTY profile requirement: {promotion_genuine_pty is True}")

print("\n--- 17. Round 33 Deep Snapshot: In-Place Mutation of raw_manifest Dict Does Not Alter Admitted Manifest ---")
# Build a genuine manifest and admit it
mutable_manifest = dict(valid_claude_manifest)
mutable_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
mutable_manifest["adapter"]["adapter_id"] = "mutable-claude-peer"
mutable_manifest["profiles"] = [
    {
        "profile_id": "cc.original",
        "profile_class": "tier",
        "supports_reasoning_effort": False,
        "transport": "PIPE",
        "prompt_policy": {
            "policy_id": "cc-original-policy",
            "max_inline_utf8_bytes": 1000000,
            "artifact_reference_supported": False
        }
    }
]

mutable_receipt_id = AdmissionRegistry.admit(mutable_manifest, CLAUDE_WRAPPER_CHAIN)
snapshot_manifest_obj = AdapterManifest.from_manifest(mutable_manifest, mutable_receipt_id, CLAUDE_WRAPPER_CHAIN)

profiles_before = snapshot_manifest_obj.declared_profile_ids
transport_before = snapshot_manifest_obj.get_profile_transport("profile:cc.original")
print(f"Declared profiles before caller mutation: {profiles_before}")
print(f"Declared transport before caller mutation: {transport_before}")

# cx's exact attack: Caller mutates the original raw_manifest dictionary in-place after construction
mutable_manifest["profiles"][0]["profile_id"] = "cc.injected_post_admission"
mutable_manifest["profiles"][0]["transport"] = "PTY"
mutable_manifest["profiles"].append({
    "profile_id": "cc.forged_appended",
    "profile_class": "tier",
    "supports_reasoning_effort": False,
    "transport": "PTY",
    "prompt_policy": {
        "policy_id": "forged",
        "max_inline_utf8_bytes": 0,
        "artifact_reference_supported": False
    }
})

profiles_after = snapshot_manifest_obj.declared_profile_ids
transport_after = snapshot_manifest_obj.get_profile_transport("profile:cc.original")
print(f"Declared profiles after caller in-place mutation: {profiles_after}")
print(f"Declared transport after caller in-place mutation: {transport_after}")
print(f"SNAPSHOT IMMUTABILITY VERIFIED: declared_profile_ids unchanged: {profiles_before == profiles_after}")
print(f"SNAPSHOT IMMUTABILITY VERIFIED: get_profile_transport unchanged: {transport_before == transport_after}")

# Promotion attempt using injected post-admission profile fails
injected_cells = [
    MockCell(CellKey("action.hub.ask", "profile:cc.injected_post_admission", "win32-x64", "PIPE", "deterministic contract or integration")),
    MockCell(CellKey("action.hub.ask", "profile:cc.injected_post_admission", "win32-x64", "PIPE", "controlled real-OS executable")),
    MockCell(CellKey("action.hub.thread-new", "profile:cc.injected_post_admission", "win32-x64", "PIPE", "deterministic contract or integration")),
    MockCell(CellKey("action.hub.thread-new", "profile:cc.injected_post_admission", "win32-x64", "PIPE", "controlled real-OS executable")),
]

injected_receipts = [EvidenceRegistry.admit({"cell_obj": c}) for c in injected_cells]
promotion_injected = can_promote(injected_receipts, mock_env, snapshot_manifest_obj)
print(f"can_promote(injected_cells) with mutated binding returned: {promotion_injected}")
print(f"MUTATION INJECTION BLOCKED: Post-admission mutation cannot achieve promotion: {promotion_injected is False}")

# Genuine evidence matching the immutable snapshot still succeeds
original_cells = [
    MockCell(CellKey("action.hub.ask", "profile:cc.original", "win32-x64", "PIPE", "deterministic contract or integration")),
    MockCell(CellKey("action.hub.ask", "profile:cc.original", "win32-x64", "PIPE", "controlled real-OS executable")),
    MockCell(CellKey("action.hub.thread-new", "profile:cc.original", "win32-x64", "PIPE", "deterministic contract or integration")),
    MockCell(CellKey("action.hub.thread-new", "profile:cc.original", "win32-x64", "PIPE", "controlled real-OS executable")),
]

original_receipts = [EvidenceRegistry.admit({"cell_obj": c}) for c in original_cells]
promotion_original = can_promote(original_receipts, mock_env, snapshot_manifest_obj)
print(f"can_promote(original_cells) with original binding returned: {promotion_original}")
print(f"GENUINE SNAPSHOT PROMOTION SUCCESS: Immutable snapshot correctly promotes genuine evidence: {promotion_original is True}")

print("\n--- 18. Round 35 Trust Boundary: Direct external write to AdmissionRegistry storage is blocked / has zero effect ---")
unadmitted_forged_manifest = dict(valid_claude_manifest)
unadmitted_forged_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
unadmitted_forged_manifest["adapter"]["adapter_id"] = "forged-bypass-peer"
unadmitted_forged_manifest["profiles"] = [
    {
        "profile_id": "cc.forged",
        "profile_class": "tier",
        "supports_reasoning_effort": False,
        "transport": "PIPE",
        "prompt_policy": {
            "policy_id": "forged-policy",
            "max_inline_utf8_bytes": 1000000,
            "artifact_reference_supported": False,
        },
    }
]

forged_digest = AdapterManifest.canonical_digest(unadmitted_forged_manifest)
forged_receipt_id = "rcpt_cx_forged_storage_bypass_token_12345"

# Attack Step A: Attacker attempts direct subscript mutation on AdmissionRegistry._store
try:
    AdmissionRegistry._store[forged_receipt_id] = forged_digest
    print("FAILED: Direct subscript write to AdmissionRegistry._store unexpectedly succeeded!")
except AttributeError as e:
    print(f"ATTACK STEP A BLOCKED: Direct subscript mutation raised {type(e).__name__}: {e}")

# Attack Step B: Attacker attempts to monkeypatch AdmissionRegistry._store by assigning a dict
AdmissionRegistry._store = {forged_receipt_id: forged_digest}

# Verify get_trusted_digest still queries the closure-scoped store and rejects the forged entry
try:
    AdmissionRegistry.get_trusted_digest(forged_receipt_id)
    print("FAILED: get_trusted_digest trusted monkeypatched AdmissionRegistry._store!")
except ValueError as e:
    print(f"ATTACK STEP B BLOCKED: get_trusted_digest ignored monkeypatch: {type(e).__name__}: {e}")

# Verify AdapterManifest.from_manifest also fails when using the forged receipt ID
try:
    AdapterManifest.from_manifest(unadmitted_forged_manifest, forged_receipt_id, CLAUDE_WRAPPER_CHAIN)
    print("FAILED: from_manifest succeeded with forged receipt ID!")
except ValueError as e:
    print(f"FORGERY REJECTED at from_manifest(): {type(e).__name__}: {e}")

# Clean up monkeypatched class attribute (if any)
if hasattr(AdmissionRegistry, "_store"):
    delattr(AdmissionRegistry, "_store")

# Genuine admission still succeeds exactly as before
genuine_forged_receipt = AdmissionRegistry.admit(unadmitted_forged_manifest, CLAUDE_WRAPPER_CHAIN)
print(f"GENUINE ADMISSION SUCCESS: Valid manifest admitted through admit(), got: {genuine_forged_receipt}")
genuine_manifest_obj = AdapterManifest.from_manifest(unadmitted_forged_manifest, genuine_forged_receipt, CLAUDE_WRAPPER_CHAIN)
print(f"GENUINE CONSTRUCT SUCCESS: Constructed {genuine_manifest_obj.adapter_id} with digest verified from trusted registry.")

print("\n--- 19. Round 38 EvidenceRegistry Validates cell_data Shape ---")
# 1. Attempt to admit a bare dictionary
bare_dict_cell = {"attempt_outcome": "EXECUTED_PASS", "evidence_state": "MEASURED"}
try:
    EvidenceRegistry.admit({"cell_obj": bare_dict_cell})
    print("FAILED: EvidenceRegistry.admit accepted a bare dictionary instead of a real cell object!")
except TypeError as e:
    print(f"SHAPE VALIDATION BLOCKED: Bare dictionary rejected: {type(e).__name__}: {e}")

# 2. Attempt to admit a cell with an invalid attempt_outcome
class MalformedCell(MockCell):
    pass

malformed_cell = MalformedCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    attempt_outcome="INVALID_OUTCOME",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)
)

try:
    EvidenceRegistry.admit({"cell_obj": malformed_cell})
    print("FAILED: EvidenceRegistry.admit accepted a cell with an invalid attempt_outcome!")
except ValueError as e:
    print(f"SHAPE VALIDATION BLOCKED: Invalid attempt_outcome rejected: {type(e).__name__}: {e}")

# 3. Genuine cell admitted successfully
genuine_test_cell = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)
)

genuine_receipt = EvidenceRegistry.admit({"cell_obj": genuine_test_cell})
print(f"GENUINE CELL SUCCESS: Properly shaped cell admitted, got receipt: {genuine_receipt}")

# Verify it still promotes correctly (mocking required cell for cc.standard)
genuine_receipts = [
    genuine_receipt,
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))}),
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))}),
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))})
]
promotion_genuine_test = can_promote(genuine_receipts, mock_env, claude_manifest_obj)
print(f"GENUINE CELL PROMOTION: Properly shaped cell still promotes exactly as before: {promotion_genuine_test}")

print("\n--- 20. cx's EvidenceRegistry Exact Attacks (Type-name Spoof, Non-datetime Timestamp, Post-admission Mutation) ---")

# A. Type-name Spoofing
class FakeCellKeyClass:
    def __init__(self):
        self.coverage_case_id = "action.hub.ask"
        self.peer_binding = "profile:cc.standard"
        self.platform = "win32-x64"
        self.transport = "PIPE"
        self.proof_kind = "deterministic contract or integration"
FakeCellKeyClass.__name__ = "CellKey"

spoofed_key = FakeCellKeyClass()
spoofed_cell = MockCell(
    cell_key=spoofed_key, # Fake class spoofing the name "CellKey"
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)
)

try:
    EvidenceRegistry.admit({"cell_obj": spoofed_cell})
    print("FAILED: EvidenceRegistry.admit accepted a cell with a spoofed cell_key type!")
except TypeError as e:
    print(f"TYPE-NAME SPOOF BLOCKED: {type(e).__name__}: {e}")

# B. Non-datetime Timestamp
non_datetime_cell = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc="not-a-datetime") # type: ignore
)

try:
    EvidenceRegistry.admit({"cell_obj": non_datetime_cell})
    print("FAILED: EvidenceRegistry.admit accepted a non-datetime timestamp!")
except (TypeError, ValueError) as e:
    print(f"NON-DATETIME TIMESTAMP BLOCKED: {type(e).__name__}: {e}")

# C. Post-admission Mutation
class MutableCell:
    def __init__(self, key, outcome, state, prov):
        self.cell_key = key
        self.attempt_outcome = outcome
        self.evidence_state = state
        self.provenance = prov

mutable_cell = MutableCell(
    key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    outcome="PRODUCT_FAILURE",
    state="MEASURED",
    prov=MockProvenance(timestamp_utc=mock_env.current_time_utc)
)

receipt_id = EvidenceRegistry.admit({"cell_obj": mutable_cell})
print(f"Admitted mutable cell as PRODUCT_FAILURE, got receipt: {receipt_id}")

# Attempt promotion - should fail because it's PRODUCT_FAILURE
fail_receipts = [receipt_id] + [EvidenceRegistry.admit({"cell_obj": c}) for c in genuine_cells[1:]]
promotion_before = can_promote(fail_receipts, mock_env, claude_manifest_obj)
print(f"Promotion with PRODUCT_FAILURE returned: {promotion_before}")

# Attacker mutates the object after admission
mutable_cell.attempt_outcome = "EXECUTED_PASS"
print("Attacker mutated original cell object to EXECUTED_PASS.")

# Attempt promotion again - should STILL fail because the registry took an immutable snapshot
promotion_after = can_promote(fail_receipts, mock_env, claude_manifest_obj)
print(f"Promotion after post-admission mutation returned: {promotion_after}")
print(f"POST-ADMISSION MUTATION BLOCKED: Promotion result remained {promotion_after} despite mutation of original object.")

print("\n--- 21. Round 42 cx's Changing-Getter TOCTOU Attack in admit() ---")

class TOCTOUMutatingCell:
    """Simulates a caller object whose getter returns an invalid value on first read
    but would flip to a valid passing outcome on second read."""
    def __init__(self, key, prov):
        self.cell_key = key
        self.evidence_state = "MEASURED"
        self.provenance = prov
        self._access_count = 0

    @property
    def attempt_outcome(self) -> str:
        self._access_count += 1
        if self._access_count == 1:
            return "INVALID_MUTATED_OUTCOME"
        return "EXECUTED_PASS"

toctou_cell = TOCTOUMutatingCell(
    key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    prov=MockProvenance(timestamp_utc=mock_env.current_time_utc)
)

try:
    EvidenceRegistry.admit({"cell_obj": toctou_cell})
    print("FAILED: TOCTOU mutating getter cell was admitted!")
except ValueError as e:
    print(f"TOCTOU CHANGING GETTER BLOCKED: {type(e).__name__}: {e}")

# Verify genuine properly-shaped cell still admits and promotes correctly
genuine_r42_receipts = [
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))}),
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))}),
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))}),
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))})
]
promotion_r42_result = can_promote(genuine_r42_receipts, mock_env, claude_manifest_obj)
print(f"GENUINE CELL PROMOTION R42: Properly shaped cell still promotes correctly: {promotion_r42_result}")

print("\n--- 22. Round 42 Item 2: Timezone-Aware UTC Enforcement & Future Skew Bounds ---")

# 1. Offset-naive datetime timestamp is rejected at validate_snapshot() / admit()
naive_ts_cell = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=datetime(2026, 8, 20, 22, 0, 0)) # naive!
)

try:
    EvidenceRegistry.admit({"cell_obj": naive_ts_cell})
    print("FAILED: EvidenceRegistry.admit accepted an offset-naive datetime timestamp!")
except ValueError as e:
    print(f"OFFSET-NAIVE DATETIME BLOCKED: {type(e).__name__}: {e}")

# 2. Ten-years-in-the-future timestamp evaluated to ERROR (not treated as fresh MEASURED)
future_10y_ts = datetime(2036, 8, 20, 22, 0, 0, tzinfo=timezone.utc)
future_cell = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=future_10y_ts)
)

future_state = determine_evidence_state(future_cell, mock_env)
future_receipt = EvidenceRegistry.admit({"cell_obj": future_cell})
future_rollup = [future_receipt] + [
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))}),
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))}),
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc))})
]
future_promotion = can_promote(future_rollup, mock_env, claude_manifest_obj)

print(f"Ten-years-in-future cell evidence_state: {future_state}")
print(f"FUTURE TIMESTAMP SKEW REJECTED: determine_evidence_state returned ERROR: {future_state == 'ERROR'}")
print(f"FUTURE TIMESTAMP PROMOTION BLOCKED: can_promote returned False: {future_promotion is False}")

# 3. Genuine timezone-aware recent timestamp does not crash subtraction and promotes correctly
recent_ts = datetime(2026, 8, 20, 22, 15, 0, tzinfo=timezone.utc)
recent_cell = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=recent_ts)
)

recent_state = determine_evidence_state(recent_cell, mock_env)
recent_receipt = EvidenceRegistry.admit({"cell_obj": recent_cell})
recent_rollup = [recent_receipt] + [
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), provenance=MockProvenance(timestamp_utc=recent_ts))}),
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"), provenance=MockProvenance(timestamp_utc=recent_ts))}),
    EvidenceRegistry.admit({"cell_obj": MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), provenance=MockProvenance(timestamp_utc=recent_ts))})
]
recent_promotion = can_promote(recent_rollup, mock_env, claude_manifest_obj)

print(f"Timezone-aware recent cell evidence_state: {recent_state}")
print(f"TIMEZONE-AWARE NO CRASH: determine_evidence_state evaluated cleanly without TypeError: {recent_state == 'MEASURED'}")
print(f"GENUINE TIMEZONE-AWARE PROMOTION SUCCESS: can_promote returned True: {recent_promotion is True}")

print("\n--- 23. Round 44 Item 1: Genuine Non-UTC Timezone-Aware Timestamp Normalized to UTC ---")
# Non-UTC timezone-aware timestamp (+09:00, e.g. Tokyo / JST)
jst_tz = timezone(timedelta(hours=9))
jst_ts = datetime(2026, 8, 21, 7, 15, 0, tzinfo=jst_tz) # equivalent to 2026-08-20 22:15:00 UTC
jst_cell = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=jst_ts)
)

jst_receipt = EvidenceRegistry.admit({"cell_obj": jst_cell})
admitted_snapshot = EvidenceRegistry.get_validated_cell(jst_receipt)["cell_obj"]
stored_ts = admitted_snapshot.provenance.timestamp_utc

print(f"Original input timestamp: {jst_ts} (tz offset: {jst_ts.utcoffset()})")
print(f"Stored snapshot timestamp: {stored_ts} (tz offset: {stored_ts.utcoffset()})")
print(f"NON-UTC NORMALIZED TO UTC: Stored tzinfo is timezone.utc and offset is zero: {stored_ts.tzinfo == timezone.utc and stored_ts.utcoffset() == timedelta(0)}")
print(f"TIMESTAMP EQUIVALENCE PRESERVED: Normalized timestamp matches original point in time: {stored_ts == jst_ts}")

print("\n--- 24. Round 44 Item 2: Enum Validation on transport & proof_kind & False-Contradiction Prevention ---")
# A. Bogus transport value (e.g. SOCKET) rejected before receipt issuance
bogus_transport_cell = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "SOCKET", "deterministic contract or integration"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)
)

try:
    EvidenceRegistry.admit({"cell_obj": bogus_transport_cell})
    print("FAILED: EvidenceRegistry.admit accepted a cell with bogus transport 'SOCKET'!")
except ValueError as e:
    print(f"BOGUS TRANSPORT REJECTED: {type(e).__name__}: {e}")

# B. Bogus proof_kind value rejected before receipt issuance
bogus_proof_cell = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "invented arbitrary proof kind"),
    attempt_outcome="EXECUTED_PASS",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)
)

try:
    EvidenceRegistry.admit({"cell_obj": bogus_proof_cell})
    print("FAILED: EvidenceRegistry.admit accepted a cell with bogus proof_kind!")
except ValueError as e:
    print(f"BOGUS PROOF_KIND REJECTED: {type(e).__name__}: {e}")

# C. False-contradiction scenario: bogus-proof_kind failing sibling rejected at admission
# Genuine passing cells for action.hub.ask and action.hub.thread-new
genuine_passing_cells = [
    MockCell(CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"), attempt_outcome="EXECUTED_PASS", evidence_state="MEASURED", provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)),
    MockCell(CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), attempt_outcome="EXECUTED_PASS", evidence_state="MEASURED", provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)),
    MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "deterministic contract or integration"), attempt_outcome="EXECUTED_PASS", evidence_state="MEASURED", provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)),
    MockCell(CellKey("action.hub.thread-new", "profile:cc.standard", "win32-x64", "PIPE", "controlled real-OS executable"), attempt_outcome="EXECUTED_PASS", evidence_state="MEASURED", provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)),
]
valid_receipts = [EvidenceRegistry.admit({"cell_obj": c}) for c in genuine_passing_cells]

# Sibling cell in the same contradiction group (action.hub.ask, profile:cc.standard, win32-x64, PIPE)
# with a bogus proof_kind and PRODUCT_FAILURE outcome
bogus_failing_sibling = MockCell(
    cell_key=CellKey("action.hub.ask", "profile:cc.standard", "win32-x64", "PIPE", "bogus unvalidated proof kind"),
    attempt_outcome="PRODUCT_FAILURE",
    evidence_state="MEASURED",
    provenance=MockProvenance(timestamp_utc=mock_env.current_time_utc)
)

try:
    EvidenceRegistry.admit({"cell_obj": bogus_failing_sibling})
    print("FAILED: Bogus failing sibling admitted!")
except ValueError as e:
    print(f"FALSE-CONTRADICTION PREVENTED: Bogus failing sibling rejected at admission: {type(e).__name__}: {e}")

# The legitimate promotion proceeds cleanly without being blocked by unvalidated garbage
legitimate_promotion = can_promote(valid_receipts, mock_env, claude_manifest_obj)
print(f"LEGITIMATE PROMOTION PRESERVED: can_promote returned True: {legitimate_promotion is True}")

import dataclasses

print("\n--- 25. Round 47: Mismatched-Target Admission Attack ---")
mismatched_manifest = dict(valid_claude_manifest)
mismatched_manifest["execution"] = dict(valid_claude_manifest["execution"])
mismatched_manifest["execution"]["executable"] = {
    "resolution_rule": "path",
    "target": "does_not_exist.cmd"
}
try:
    AdmissionRegistry.admit(mismatched_manifest, CLAUDE_WRAPPER_CHAIN)
    print("FAILED: Mismatched target was unexpectedly admitted!")
except ValueError as e:
    print(f"MISMATCHED TARGET BLOCKED: {type(e).__name__}: {e}")

print("\n--- 26. Round 47: Mutable-Receipt-Leak Attack ---")
receipt_id = AdmissionRegistry.admit(valid_claude_manifest, CLAUDE_WRAPPER_CHAIN)
leaked_receipt = AdmissionRegistry.get_trusted_receipt(receipt_id)
try:
    # Attempt to mutate the aggregate chain digest on the returned receipt
    leaked_receipt.provisioning_evidence.aggregate_chain_digest = 'FORGED_DIGEST'
    print("FAILED: Leaked receipt was successfully mutated!")
except dataclasses.FrozenInstanceError as e:
    print(f"MUTABLE RECEIPT LEAK PREVENTED: {type(e).__name__}: {e}")

print("\n--- 27. Round 50 cx's unrelated-node-1 attack rejected (multi-node Phase 1 limit) ---")
import tempfile
with tempfile.NamedTemporaryFile(delete=False) as f:
    f.write(b"dummy")
    dummy_path = f.name
try:
    with open(dummy_path, "rb") as f:
        dummy_hash = hashlib.sha256(f.read()).hexdigest().upper()
    
    multi_node_chain = [
        {'role': 'ENTRYPOINT_WRAPPER', 'canonical_path': _real_claude_path, 'sha256': _real_claude_hash, 'is_reparse_point': False},
        {'role': 'NATIVE_BINARY', 'canonical_path': dummy_path, 'sha256': dummy_hash, 'is_reparse_point': False}
    ]
    
    try:
        AdmissionRegistry.admit(valid_claude_manifest, multi_node_chain)
        print("FAILED: Multi-node chain unexpectedly admitted!")
    except ValueError as e:
        print(f"MULTI-NODE CHAIN BLOCKED: {type(e).__name__}: {e}")
finally:
    os.remove(dummy_path)

print("\n--- 28. Round 50 relative canonical_path rejected ---")
rel_path = os.path.basename(_real_claude_path)
rel_node_chain = [
    {'role': 'ENTRYPOINT_WRAPPER', 'canonical_path': rel_path, 'sha256': _real_claude_hash, 'is_reparse_point': False}
]
try:
    AdmissionRegistry.admit(valid_claude_manifest, rel_node_chain)
    print("FAILED: Relative canonical_path unexpectedly admitted!")
except ValueError as e:
    print(f"RELATIVE PATH BLOCKED: {type(e).__name__}: {e}")

print("\n--- 29. Round 53 NATIVE_BINARY magic-byte format validation ---")
non_pe_path = os.path.abspath("docs/design/PHASE1-PROMOTION-SCHEMA-V1-2026-08-20.md")
with open(non_pe_path, "rb") as f:
    non_pe_hash = hashlib.sha256(f.read()).hexdigest().upper()

non_pe_chain = [
    {'role': 'NATIVE_BINARY', 'canonical_path': non_pe_path, 'sha256': non_pe_hash, 'is_reparse_point': False}
]

non_pe_manifest = dict(valid_claude_manifest)
non_pe_manifest["execution"] = dict(valid_claude_manifest["execution"])
non_pe_manifest["execution"]["executable"] = {
    "resolution_rule": "absolute",
    "target": non_pe_path
}

try:
    AdmissionRegistry.admit(non_pe_manifest, non_pe_chain)
    print("FAILED: Non-PE file was unexpectedly admitted as NATIVE_BINARY!")
except ValueError as e:
    print(f"NON-PE FORMAT REJECTED: {type(e).__name__}: {e}")

print("\n--- 30. Round 55/57 Entrypoint Verification with chain_complete=False ---")
real_wrapper_path = os.path.abspath(r"P:\_sys\cli\claude.bat")
with open(real_wrapper_path, "rb") as f:
    real_wrapper_hash = hashlib.sha256(f.read()).hexdigest().upper()

honest_wrapper_chain = [
    {'role': 'ENTRYPOINT_WRAPPER', 'canonical_path': real_wrapper_path, 'sha256': real_wrapper_hash, 'is_reparse_point': False}
]

wrapper_manifest = dict(valid_claude_manifest)
wrapper_manifest["execution"] = dict(valid_claude_manifest["execution"])
wrapper_manifest["execution"]["executable"] = {
    "resolution_rule": "absolute",
    "target": real_wrapper_path
}

wrapper_receipt_id = AdmissionRegistry.admit(wrapper_manifest, honest_wrapper_chain)
wrapper_receipt = AdmissionRegistry.get_trusted_receipt(wrapper_receipt_id)
print(f"Admitted wrapper-fronted peer manifest: {wrapper_receipt_id}")
print(f"Declared role: {wrapper_receipt.provisioning_evidence.transitive_executable_chain[0].role.value}")
print(f"Wrapper receipt chain_complete flag: {wrapper_receipt.chain_complete}")
print(f"Provisioning evidence chain_complete flag: {wrapper_receipt.provisioning_evidence.chain_complete}")
print(f"SHALLOW ENTRYPOINT VERIFICATION VERIFIED: chain_complete is False: {wrapper_receipt.chain_complete is False}")

# Also verify that a NATIVE_BINARY admission receives chain_complete=False in Phase 1
native_manifest = dict(valid_claude_manifest)
native_manifest["execution"] = dict(valid_claude_manifest["execution"])
native_manifest["execution"]["executable"] = {
    "resolution_rule": "absolute",
    "target": _real_native_path
}
native_receipt_id = AdmissionRegistry.admit(native_manifest, VALID_NATIVE_CHAIN)
native_receipt = AdmissionRegistry.get_trusted_receipt(native_receipt_id)
print(f"Native binary receipt chain_complete flag: {native_receipt.chain_complete}")
print(f"NATIVE BINARY NEVER OVERCLAIMS: chain_complete is False: {native_receipt.chain_complete is False}")

print("\n--- 31. Round 55 resolution_rule 'absolute' rejects relative target ---")
rel_target_manifest = dict(valid_claude_manifest)
rel_target_manifest["execution"] = dict(valid_claude_manifest["execution"])
rel_target_manifest["execution"]["executable"] = {
    "resolution_rule": "absolute",
    "target": "claude.cmd"
}

try:
    AdmissionRegistry.admit(rel_target_manifest, CLAUDE_WRAPPER_CHAIN)
    print("FAILED: Relative target was unexpectedly admitted under resolution_rule 'absolute'!")
except ValueError as e:
    print(f"RELATIVE TARGET UNDER ABSOLUTE RULE REJECTED: {type(e).__name__}: {e}")

print("\n--- 32. Round 57 resolution_rule 'path' rejects path separators (enforces bare command name) ---")
# 1. Target containing relative path separator ".\" rejected
rel_sep_manifest = dict(valid_claude_manifest)
rel_sep_manifest["execution"] = dict(valid_claude_manifest["execution"])
rel_sep_manifest["execution"]["executable"] = {
    "resolution_rule": "path",
    "target": r".\claude.cmd"
}
try:
    AdmissionRegistry.admit(rel_sep_manifest, CLAUDE_WRAPPER_CHAIN)
    print("FAILED: Relative path target with '.\\' was unexpectedly admitted under resolution_rule 'path'!")
except ValueError as e:
    print(f"PATH SEPARATOR IN PATH RULE REJECTED (.\\): {type(e).__name__}: {e}")

# 2. Target containing subpath navigation separator "subdir\..\name.cmd" rejected
subpath_manifest = dict(valid_claude_manifest)
subpath_manifest["execution"] = dict(valid_claude_manifest["execution"])
subpath_manifest["execution"]["executable"] = {
    "resolution_rule": "path",
    "target": r"subdir\..\claude.cmd"
}
try:
    AdmissionRegistry.admit(subpath_manifest, CLAUDE_WRAPPER_CHAIN)
    print("FAILED: Target with 'subdir\\..\\claude.cmd' was unexpectedly admitted under resolution_rule 'path'!")
except ValueError as e:
    print(f"PATH SEPARATOR IN PATH RULE REJECTED (subdir\\..\\): {type(e).__name__}: {e}")

# 3. Genuine bare command name succeeds via real PATH lookup
bare_manifest = dict(valid_claude_manifest)
bare_manifest["execution"] = dict(valid_claude_manifest["execution"])
bare_manifest["execution"]["executable"] = {
    "resolution_rule": "path",
    "target": "claude.cmd"
}
bare_receipt_id = AdmissionRegistry.admit(bare_manifest, CLAUDE_WRAPPER_CHAIN)
print(f"GENUINE BARE NAME ADMISSION SUCCESS: Admitted via real PATH lookup, got receipt: {bare_receipt_id}")

print("\n--- 33. Round 59 Strict PATH-Only Resolution (CWD Shadowing & Registry Independence) ---")
# 1. Confirm genuine real command on PATH admits correctly under resolution_rule 'path'
py_bare_name = os.path.basename(sys.executable)
py_manifest = dict(valid_claude_manifest)
py_manifest["execution"] = dict(valid_claude_manifest["execution"])
py_manifest["execution"]["executable"] = {
    "resolution_rule": "path",
    "target": py_bare_name
}
py_receipt_id = AdmissionRegistry.admit(py_manifest, VALID_NATIVE_CHAIN)
print(f"GENUINE PATH COMMAND ADMISSION SUCCESS: Bare target '{py_bare_name}' resolved via PATH directory, got receipt: {py_receipt_id}")

# 2. cx's Adversarial Scenario: Bare file created in CWD (not in any PATH directory)
# Under shutil.which without NoDefaultCurrentDirectoryInExePath, Windows would resolve to .\\adversarial_cmd.cmd.
# Under manual PATH enumeration, CWD is never consulted, so admission must be rejected.
adversarial_cwd_name = "adversarial_cwd_shadow_command.cmd"
adversarial_cwd_path = os.path.abspath(adversarial_cwd_name)
try:
    with open(adversarial_cwd_path, "w", encoding="utf-8") as f:
        f.write("@echo off\necho shadow\n")
    
    with open(adversarial_cwd_path, "rb") as f:
        adversarial_hash = hashlib.sha256(f.read()).hexdigest().upper()
        
    adversarial_chain = [{
        "role": "ENTRYPOINT_WRAPPER",
        "canonical_path": adversarial_cwd_path,
        "sha256": adversarial_hash,
        "is_reparse_point": False
    }]
    
    adv_manifest = dict(valid_claude_manifest)
    adv_manifest["execution"] = dict(valid_claude_manifest["execution"])
    adv_manifest["execution"]["executable"] = {
        "resolution_rule": "path",
        "target": adversarial_cwd_name
    }
    
    try:
        AdmissionRegistry.admit(adv_manifest, adversarial_chain)
        print("FAILED: Bare command in CWD was unexpectedly resolved and admitted under resolution_rule 'path'!")
    except ValueError as e:
        print(f"CWD SHADOWING ATTACK BLOCKED: {type(e).__name__}: {e}")
finally:
    if os.path.exists(adversarial_cwd_path):
        os.remove(adversarial_cwd_path)

print("\n--- 34. Round 61 PATHEXT Resolution Order (Bare extensionless vs PATHEXT-extended precedence) ---")
# When a directory in PATH contains both a bare extensionless file ('probe_precedence_r61')
# and a PATHEXT-extended executable ('probe_precedence_r61.cmd'), real Windows resolution
# cmd.exe PATHEXT resolution resolves and executes the PATHEXT-extended candidate.
# The validator now matches this precedence ordering: resolving 'probe_precedence_r61' resolves to
# 'probe_precedence_r61.cmd', rejecting any chain that claims to bind against the bare extensionless file.
with tempfile.TemporaryDirectory() as tmp_pathext_dir:
    bare_probe_path = os.path.join(tmp_pathext_dir, "probe_precedence_r61")
    cmd_probe_path = os.path.join(tmp_pathext_dir, "probe_precedence_r61.cmd")
    with open(bare_probe_path, "wb") as f:
        f.write(b"BARE_EXTENSIONLESS_FILE_NOT_EXECUTABLE")
    with open(cmd_probe_path, "wb") as f:
        f.write(b"@echo off\necho PATHEXT_EXECUTABLE\n")
        
    with open(bare_probe_path, "rb") as f:
        bare_hash = hashlib.sha256(f.read()).hexdigest().upper()
    with open(cmd_probe_path, "rb") as f:
        cmd_hash = hashlib.sha256(f.read()).hexdigest().upper()
        
    orig_path_env = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{tmp_pathext_dir}{os.pathsep}{orig_path_env}"
    try:
        # 1. Manifest specifies bare target 'probe_precedence_r61'.
        # Attacker tries to bind chain to the bare file 'probe_precedence_r61'.
        # Validator resolves 'probe_precedence_r61' -> 'probe_precedence_r61.cmd' (PATHEXT precedence)
        # and detects that chain entrypoint 'probe_precedence_r61' does not match resolved target 'probe_precedence_r61.cmd'.
        mismatched_bare_chain = [{
            "role": "ENTRYPOINT_WRAPPER",
            "canonical_path": os.path.abspath(bare_probe_path),
            "sha256": bare_hash,
            "is_reparse_point": False
        }]
        probe_manifest = dict(valid_claude_manifest)
        probe_manifest["execution"] = dict(valid_claude_manifest["execution"])
        probe_manifest["execution"]["executable"] = {
            "resolution_rule": "path",
            "target": "probe_precedence_r61"
        }
        try:
            AdmissionRegistry.admit(probe_manifest, mismatched_bare_chain)
            print("FAILED: Bare extensionless candidate was incorrectly admitted over PATHEXT candidate!")
        except ValueError as e:
            print(f"PATHEXT PRECEDENCE ENFORCED (bare candidate rejected): {type(e).__name__}: {e}")

        # 2. Genuine admission binding to the PATHEXT-resolved executable succeeds
        correct_cmd_chain = [{
            "role": "ENTRYPOINT_WRAPPER",
            "canonical_path": os.path.abspath(cmd_probe_path),
            "sha256": cmd_hash,
            "is_reparse_point": False
        }]
        probe_receipt_id = AdmissionRegistry.admit(probe_manifest, correct_cmd_chain)
        print(f"GENUINE PATHEXT ADMISSION SUCCESS: Admitted via PATHEXT precedence, got receipt: {probe_receipt_id}")

        # 3. Directory with ONLY a bare extensionless file (no PATHEXT match) cannot resolve on Windows
        bare_only_path = os.path.join(tmp_pathext_dir, "probe_bare_only_r61")
        with open(bare_only_path, "wb") as f:
            f.write(b"BARE_ONLY")
        bare_only_manifest = dict(valid_claude_manifest)
        bare_only_manifest["execution"] = dict(valid_claude_manifest["execution"])
        bare_only_manifest["execution"]["executable"] = {
            "resolution_rule": "path",
            "target": "probe_bare_only_r61"
        }
        try:
            AdmissionRegistry.admit(bare_only_manifest, mismatched_bare_chain)
            print("FAILED: Bare extensionless file with no PATHEXT match was unexpectedly resolved!")
        except ValueError as e:
            print(f"BARE EXTENSIONLESS WITHOUT PATHEXT REJECTED: {type(e).__name__}: {e}")
    finally:
        os.environ["PATH"] = orig_path_env

print("\n--- 35. Round 61 Unicode Case-Folding Path Identity (Real Filesystem Identity via os.path.samefile) ---")
# cx showed that .lower() string comparison incorrectly conflates two distinct real NTFS files
# with Unicode-confusable names (e.g. Kelvin sign '\u212A' vs ASCII 'K') because str.lower()
# maps both code points to 'k', even though they are distinct files with different content.
# Replacing .lower() with os.path.samefile() ensures genuine filesystem inode/handle identity.
with tempfile.TemporaryDirectory() as tmp_unicode_dir:
    kelvin_name = "target_\u212A_r61.cmd"
    ascii_name = "target_K_r61.cmd"
    kelvin_path = os.path.join(tmp_unicode_dir, kelvin_name)
    ascii_path = os.path.join(tmp_unicode_dir, ascii_name)
    
    with open(kelvin_path, "wb") as f:
        f.write(b"@echo off\necho KELVIN_SIGN_SCRIPT\n")
    with open(ascii_path, "wb") as f:
        f.write(b"@echo off\necho ASCII_K_SCRIPT\n")
        
    with open(kelvin_path, "rb") as f:
        kelvin_hash = hashlib.sha256(f.read()).hexdigest().upper()
    with open(ascii_path, "rb") as f:
        ascii_hash = hashlib.sha256(f.read()).hexdigest().upper()
        
    # Verify that these are two distinct real files on NTFS with different content
    print(f"Two distinct files created on NTFS: '{kelvin_name}' and '{ascii_name}'")
    print(f"Python str.lower() conflates paths: {kelvin_path.lower() == ascii_path.lower()}")
    print(f"os.path.samefile() correctly distinguishes files: {not os.path.samefile(kelvin_path, ascii_path)}")
    
    # 1. Manifest targets the Kelvin sign file, but chain claims the ASCII K file
    unicode_confusable_manifest = dict(valid_claude_manifest)
    unicode_confusable_manifest["execution"] = dict(valid_claude_manifest["execution"])
    unicode_confusable_manifest["execution"]["executable"] = {
        "resolution_rule": "absolute",
        "target": os.path.abspath(kelvin_path)
    }
    
    spoofed_ascii_chain = [{
        "role": "ENTRYPOINT_WRAPPER",
        "canonical_path": os.path.abspath(ascii_path),
        "sha256": ascii_hash,
        "is_reparse_point": False
    }]
    
    try:
        AdmissionRegistry.admit(unicode_confusable_manifest, spoofed_ascii_chain)
        print("FAILED: Unicode-confusable path was incorrectly bound to different file via string lower()!")
    except ValueError as e:
        print(f"UNICODE CONFUSABLE BINDING BLOCKED: {type(e).__name__}: {e}")
        
    # 2. Manifest targets Kelvin sign file and chain provides genuine Kelvin sign file
    genuine_kelvin_chain = [{
        "role": "ENTRYPOINT_WRAPPER",
        "canonical_path": os.path.abspath(kelvin_path),
        "sha256": kelvin_hash,
        "is_reparse_point": False
    }]
    unicode_receipt_id = AdmissionRegistry.admit(unicode_confusable_manifest, genuine_kelvin_chain)
    print(f"GENUINE UNICODE TARGET ADMISSION SUCCESS: Admitted with genuine filesystem identity, got receipt: {unicode_receipt_id}")

print("\n--- 36. Round 63 PATHEXT Strictness & Malformed-Syntax Rejection (Fail-Closed on Missing Dot or Empty Token) ---")
# cx's Round 62 review showed that auto-normalizing PATHEXT tokens (auto-adding missing leading dots)
# diverges from cmd.exe behavior when multiple candidates exist (e.g., PATHEXT="BAT;.CMD" with probe.bat
# and probe.cmd present). Rather than attempting heuristic repair or guessing cmd.exe parsing quirks,
# resolution_rule 'path' now strictly validates PATHEXT and fails closed on any malformed token
# (missing leading dot or empty tokens between/adjacent to semicolons), while handling explicitly empty
# and unset PATHEXT deterministically.
with tempfile.TemporaryDirectory() as tmp_r63_dir:
    bat_file_path = os.path.join(tmp_r63_dir, "cx_probe_r63.bat")
    cmd_file_path = os.path.join(tmp_r63_dir, "cx_probe_r63.cmd")
    with open(bat_file_path, "wb") as f:
        f.write(b"@echo off\necho PROBE_BAT\n")
    with open(cmd_file_path, "wb") as f:
        f.write(b"@echo off\necho PROBE_CMD\n")
        
    with open(bat_file_path, "rb") as f:
        bat_hash = hashlib.sha256(f.read()).hexdigest().upper()
    with open(cmd_file_path, "rb") as f:
        cmd_hash = hashlib.sha256(f.read()).hexdigest().upper()
        
    orig_path_env = os.environ.get("PATH", "")
    orig_pathext_env = os.environ.get("PATHEXT", None)
    
    os.environ["PATH"] = f"{tmp_r63_dir}{os.pathsep}{orig_path_env}"
    
    try:
        manifest_r63 = dict(valid_claude_manifest)
        manifest_r63["execution"] = dict(valid_claude_manifest["execution"])
        manifest_r63["execution"]["executable"] = {
            "resolution_rule": "path",
            "target": "cx_probe_r63"
        }
        bat_chain = [{
            "role": "ENTRYPOINT_WRAPPER",
            "canonical_path": os.path.abspath(bat_file_path),
            "sha256": bat_hash,
            "is_reparse_point": False
        }]
        cmd_chain = [{
            "role": "ENTRYPOINT_WRAPPER",
            "canonical_path": os.path.abspath(cmd_file_path),
            "sha256": cmd_hash,
            "is_reparse_point": False
        }]

        # 1. cx's exact adversarial scenario: PATHEXT="BAT;.CMD" (missing dot on first entry)
        # Previously normalized to ".BAT" and admitted cx_probe.bat; now rejected outright.
        os.environ["PATHEXT"] = "BAT;.CMD"
        try:
            AdmissionRegistry.admit(manifest_r63, bat_chain)
            print("FAILED: Malformed PATHEXT 'BAT;.CMD' (missing dot) was accepted!")
        except ValueError as e:
            print(f"MALFORMED PATHEXT MISSING DOT REJECTED: {type(e).__name__}: {e}")

        # 2. Empty token in PATHEXT (leading semicolon: ";.BAT;.CMD")
        os.environ["PATHEXT"] = ";.BAT;.CMD"
        try:
            AdmissionRegistry.admit(manifest_r63, bat_chain)
            print("FAILED: Malformed PATHEXT with leading empty token was accepted!")
        except ValueError as e:
            print(f"MALFORMED PATHEXT LEADING EMPTY TOKEN REJECTED: {type(e).__name__}: {e}")

        # 3. Empty token in PATHEXT (consecutive semicolons: ".BAT;;.CMD")
        os.environ["PATHEXT"] = ".BAT;;.CMD"
        try:
            AdmissionRegistry.admit(manifest_r63, bat_chain)
            print("FAILED: Malformed PATHEXT with consecutive semicolons was accepted!")
        except ValueError as e:
            print(f"MALFORMED PATHEXT CONSECUTIVE SEMICOLONS REJECTED: {type(e).__name__}: {e}")

        # 4. Explicitly empty PATHEXT ("") - does not fall back to default, fails resolution
        os.environ["PATHEXT"] = ""
        try:
            AdmissionRegistry.admit(manifest_r63, bat_chain)
            print("FAILED: Extensionless target resolved with explicitly empty PATHEXT!")
        except ValueError as e:
            print(f"EXPLICITLY EMPTY PATHEXT RESOLUTION REJECTED: {type(e).__name__}: {e}")

        # 5. Well-formed PATHEXT (".BAT;.CMD") - resolves cx_probe_r63 -> cx_probe_r63.bat
        os.environ["PATHEXT"] = ".BAT;.CMD"
        receipt_bat = AdmissionRegistry.admit(manifest_r63, bat_chain)
        print(f"GENUINE WELL-FORMED PATHEXT ADMISSION SUCCESS: Admitted via .BAT precedence, got receipt: {receipt_bat}")

        # 6. Well-formed PATHEXT (".CMD;.BAT") - resolves cx_probe_r63 -> cx_probe_r63.cmd
        os.environ["PATHEXT"] = ".CMD;.BAT"
        receipt_cmd = AdmissionRegistry.admit(manifest_r63, cmd_chain)
        print(f"GENUINE WELL-FORMED PATHEXT ADMISSION SUCCESS (.CMD precedence): Got receipt: {receipt_cmd}")

        # 7. Unset PATHEXT (falls back to default .COM;.EXE;.BAT;.CMD)
        if "PATHEXT" in os.environ:
            del os.environ["PATHEXT"]
        receipt_default = AdmissionRegistry.admit(manifest_r63, bat_chain)
        print(f"GENUINE UNSET PATHEXT ADMISSION SUCCESS (default list precedence): Got receipt: {receipt_default}")

    finally:
        os.environ["PATH"] = orig_path_env
        if orig_pathext_env is not None:
            os.environ["PATHEXT"] = orig_pathext_env
        elif "PATHEXT" in os.environ:
            del os.environ["PATHEXT"]

```

**Output:**

```text
--- 1. Single Normative Schema SSOT & Mechanical Equality Check ---
Normative V2 Schema ID loaded from canonical file: https://peerhub.local/schema/adapter-manifest/v2
Validator schema equals canonical source: True

--- 2. cx's readiness_probe_id Discrepancy Resolved ---
Normative schema definition for 'readiness_probe_id': {'type': 'string'}
Has minLength constraint: False (Correct: False, unconstrained string)

--- 3. cx's missing-required-fields manifest rejected at admit() ---
REJECTED at admit() as expected: ValueError: Manifest schema validation failed: Additional properties are not allowed ('version' was unexpected); 'manifest_version' is a required property; 'status' is a required property; 'execution' is a required property; 'engine' is a required property; 'profiles' is a required property; 'adapter_version' is a required property; 'readiness_probe_id' is a required property

--- 4. cx's exact extra-key Codex manifest rejected at admit() before receipt issuance ---
REJECTED at admit() as expected: ValueError: Manifest schema validation failed: Additional properties are not allowed ('enforce_strict_json' was unexpected); {'enforce_strict_json': True} is expected to be empty
Store unpolluted (no receipt issued): True

--- 5. Fully schema-valid Codex manifest with empty options admitted successfully ---
Admitted valid Codex manifest, got 128-bit collision-safe receipt: receipt-cx-codex-peer-20260821T113445Z-2708cff97dba39d40a0c990a04bd37d1
Codex receipt chain_complete (shallow entrypoint verification): False
SUCCESS: Constructed codex-peer (cx) carrying genuine declared profiles: frozenset({'cx.standard'})

--- 6. Fully schema-valid Claude manifest admitted with declared profiles ---
Admitted valid Claude manifest, got 128-bit collision-safe receipt: receipt-cc-claude-peer-20260821T113445Z-9c3e17edffd6e5876517592cd0b47418
Claude receipt chain_complete (shallow entrypoint verification): False
SUCCESS: Constructed claude-peer (cc) carrying genuine declared profiles: frozenset({'cc.standard'})

--- 7. Repro of cx's Fabricated peer_binding Attack Against can_promote() ---
can_promote(fabricated_cells) returned: False
ATTACK BLOCKED: Fabricated peer_binding rejected by can_promote: True

--- 8. Genuine Admitted Profile Promotion Rollup ---
can_promote(genuine_cells) returned: True
GENUINE PROMOTION SUCCESS: Admitted profile correctly promoted: True

--- 9. Mixed Genuine + Unadmitted Peer Binding Rollup ---
can_promote(mixed_cells) returned: False
MIXED INJECTION BLOCKED: Unadmitted cell rejected: True

--- 10. Collision safety: forced sequential collision retried to fresh receipt ID ---
Earlier receipt ID (receipt-cc-claude-peer-20260821T113445Z-9c3e17edffd6e5876517592cd0b47418) digest preserved intact: True
Second admission detected collision and retried to fresh ID: receipt-cc-claude-peer-20260821T113445Z-abcdef0123456789abcdef0123456789
Store size now: 3 distinct entries (no clobbering!)

--- 11. Concurrency safety: Real multi-threaded concurrent execution with forced interleaving ---
Thread 1 receipt ID: receipt-cc-conc-peer-20260821T113445Z-11112222333344445555666677778888
Thread 2 receipt ID: receipt-cc-conc-peer-20260821T113445Z-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
Receipt IDs are distinct (no duplicate ID issued): True
Thread 1 digest in registry: True
Thread 2 digest in registry: True
Store size increased by exactly 2 entries: True
CONCURRENCY RACE VERIFIED FIXED: Atomic lock prevents TOCTOU clobbering under real thread contention.

--- 12. Forgery attempt (supplying own digest as receipt ID) ---
BLOCKED: ValueError: Unknown admission receipt ID: 6BD899AB9DC099CE261BBF959712E62BBF156B0E8DC141FBC784D533C6774DE1

--- 13. Syntactically well-formed but unknown receipt ID ---
BLOCKED: ValueError: Unknown admission receipt ID: rcpt_00000000000000000000000000000000

--- 14. Freshness & Staleness Invalidation (8-Day-Old Evidence Evaluates to STALE) ---
Fresh cell age: 0.5 hours -> evidence_state: MEASURED
8-day-old cell age: 8.0 days -> evidence_state: STALE
STALENESS CHECK VERIFIED: 8-day-old evidence evaluates to STALE: True
can_promote(stale_rollup_cells) with 8-day-old cell returned: False
STALE PROMOTION BLOCKED: can_promote rejected stale evidence: True

--- 15. Round 31 Trust Boundary: caller-supplied required_cell_keys cannot override/weaken manifest ---
can_promote with 1-cell override subset but only 1 cell provided: False
REQUIRED SET OVERRIDE BLOCKED: 100% manifest requirement enforcement held: True
can_promote with non-manifest requirement override: False
NON-SUBSET OVERRIDE REJECTED: Caller cannot inject arbitrary requirements: True

--- 16. Round 31 Per-Profile Transport: PTY profile requires PTY evidence (cannot promote on PIPE) ---
PTY profile expected required cell transports: {'PTY'}
can_promote(pipe_cells_for_pty) returned: False
TRANSPORT MISMATCH BLOCKED: PIPE evidence cannot satisfy PTY profile requirement: True
can_promote(genuine_pty_cells) returned: True
GENUINE PTY PROMOTION SUCCESS: PTY evidence satisfies PTY profile requirement: True

--- 17. Round 33 Deep Snapshot: In-Place Mutation of raw_manifest Dict Does Not Alter Admitted Manifest ---
Declared profiles before caller mutation: frozenset({'cc.original'})
Declared transport before caller mutation: PIPE
Declared profiles after caller in-place mutation: frozenset({'cc.original'})
Declared transport after caller in-place mutation: PIPE
SNAPSHOT IMMUTABILITY VERIFIED: declared_profile_ids unchanged: True
SNAPSHOT IMMUTABILITY VERIFIED: get_profile_transport unchanged: True
can_promote(injected_cells) with mutated binding returned: False
MUTATION INJECTION BLOCKED: Post-admission mutation cannot achieve promotion: True
can_promote(original_cells) with original binding returned: True
GENUINE SNAPSHOT PROMOTION SUCCESS: Immutable snapshot correctly promotes genuine evidence: True

--- 18. Round 35 Trust Boundary: Direct external write to AdmissionRegistry storage is blocked / has zero effect ---
ATTACK STEP A BLOCKED: Direct subscript mutation raised AttributeError: type object 'AdmissionRegistry' has no attribute '_store'
ATTACK STEP B BLOCKED: get_trusted_digest ignored monkeypatch: ValueError: Unknown admission receipt ID: rcpt_cx_forged_storage_bypass_token_12345
FORGERY REJECTED at from_manifest(): ValueError: Unknown admission receipt ID: rcpt_cx_forged_storage_bypass_token_12345
GENUINE ADMISSION SUCCESS: Valid manifest admitted through admit(), got: receipt-cc-forged-bypass-peer-20260821T113445Z-f853f54e3487e5f4d33ebdd256065265
GENUINE CONSTRUCT SUCCESS: Constructed forged-bypass-peer with digest verified from trusted registry.

--- 19. Round 38 EvidenceRegistry Validates cell_data Shape ---
SHAPE VALIDATION BLOCKED: Bare dictionary rejected: TypeError: cell_obj must expose a real cell_key of the real CellKey type
SHAPE VALIDATION BLOCKED: Invalid attempt_outcome rejected: ValueError: cell_obj.attempt_outcome must be one of {'ENVIRONMENT_UNAVAILABLE', 'PRODUCT_FAILURE', 'EXECUTED_PASS', 'NOT_REQUESTED', 'QUOTA_BLOCKED'}
GENUINE CELL SUCCESS: Properly shaped cell admitted, got receipt: ev_2be940cf28a80b16501dc02b634340a2
GENUINE CELL PROMOTION: Properly shaped cell still promotes exactly as before: True

--- 20. cx's EvidenceRegistry Exact Attacks (Type-name Spoof, Non-datetime Timestamp, Post-admission Mutation) ---
TYPE-NAME SPOOF BLOCKED: TypeError: cell_obj must expose a real cell_key of the real CellKey type
NON-DATETIME TIMESTAMP BLOCKED: TypeError: provenance must carry a real timestamp_utc of the real datetime type
Admitted mutable cell as PRODUCT_FAILURE, got receipt: ev_4a9d28199a4c56fe83fdeb52a7ffbf31
Promotion with PRODUCT_FAILURE returned: False
Attacker mutated original cell object to EXECUTED_PASS.
Promotion after post-admission mutation returned: False
POST-ADMISSION MUTATION BLOCKED: Promotion result remained False despite mutation of original object.

--- 21. Round 42 cx's Changing-Getter TOCTOU Attack in admit() ---
TOCTOU CHANGING GETTER BLOCKED: ValueError: cell_obj.attempt_outcome must be one of {'ENVIRONMENT_UNAVAILABLE', 'PRODUCT_FAILURE', 'EXECUTED_PASS', 'NOT_REQUESTED', 'QUOTA_BLOCKED'}
GENUINE CELL PROMOTION R42: Properly shaped cell still promotes correctly: True

--- 22. Round 42 Item 2: Timezone-Aware UTC Enforcement & Future Skew Bounds ---
OFFSET-NAIVE DATETIME BLOCKED: ValueError: timestamp_utc must be a timezone-aware datetime (e.g. timezone.utc)
Ten-years-in-future cell evidence_state: ERROR
FUTURE TIMESTAMP SKEW REJECTED: determine_evidence_state returned ERROR: True
FUTURE TIMESTAMP PROMOTION BLOCKED: can_promote returned False: True
Timezone-aware recent cell evidence_state: MEASURED
TIMEZONE-AWARE NO CRASH: determine_evidence_state evaluated cleanly without TypeError: True
GENUINE TIMEZONE-AWARE PROMOTION SUCCESS: can_promote returned True: True

--- 23. Round 44 Item 1: Genuine Non-UTC Timezone-Aware Timestamp Normalized to UTC ---
Original input timestamp: 2026-08-21 07:15:00+09:00 (tz offset: 9:00:00)
Stored snapshot timestamp: 2026-08-20 22:15:00+00:00 (tz offset: 0:00:00)
NON-UTC NORMALIZED TO UTC: Stored tzinfo is timezone.utc and offset is zero: True
TIMESTAMP EQUIVALENCE PRESERVED: Normalized timestamp matches original point in time: True

--- 24. Round 44 Item 2: Enum Validation on transport & proof_kind & False-Contradiction Prevention ---
BOGUS TRANSPORT REJECTED: ValueError: cell_key.transport must be one of {'PTY', 'PIPE'}, got 'SOCKET'
BOGUS PROOF_KIND REJECTED: ValueError: cell_key.proof_kind must be one of {'controlled real-OS executable', 'deterministic contract or integration', 'live provider exact-profile', 'legacy-parity evidence'}, got 'invented arbitrary proof kind'
FALSE-CONTRADICTION PREVENTED: Bogus failing sibling rejected at admission: ValueError: cell_key.proof_kind must be one of {'controlled real-OS executable', 'deterministic contract or integration', 'live provider exact-profile', 'legacy-parity evidence'}, got 'bogus unvalidated proof kind'
LEGITIMATE PROMOTION PRESERVED: can_promote returned True: True

--- 25. Round 47: Mismatched-Target Admission Attack ---
MISMATCHED TARGET BLOCKED: ValueError: Target 'does_not_exist.cmd' with resolution_rule 'path' could not be resolved via OS PATH

--- 26. Round 47: Mutable-Receipt-Leak Attack ---
MUTABLE RECEIPT LEAK PREVENTED: FrozenInstanceError: cannot assign to field 'aggregate_chain_digest'

--- 27. Round 50 cx's unrelated-node-1 attack rejected (multi-node Phase 1 limit) ---
MULTI-NODE CHAIN BLOCKED: ValueError: Executable chain must contain exactly one node (Phase 1 limitation).

--- 28. Round 50 relative canonical_path rejected ---
RELATIVE PATH BLOCKED: ValueError: canonical_path must be an absolute path, got 'claude.cmd'

--- 29. Round 53 NATIVE_BINARY magic-byte format validation ---
NON-PE FORMAT REJECTED: ValueError: File content at P:\workspace\peerhub\docs\design\PHASE1-PROMOTION-SCHEMA-V1-2026-08-20.md does not match NATIVE_BINARY format claim (missing MZ magic bytes).

--- 30. Round 55/57 Entrypoint Verification with chain_complete=False ---
Admitted wrapper-fronted peer manifest: receipt-cc-claude-peer-20260821T113445Z-ef02b731978ff56a083f27402a5d1d8b
Declared role: ENTRYPOINT_WRAPPER
Wrapper receipt chain_complete flag: False
Provisioning evidence chain_complete flag: False
SHALLOW ENTRYPOINT VERIFICATION VERIFIED: chain_complete is False: True
Native binary receipt chain_complete flag: False
NATIVE BINARY NEVER OVERCLAIMS: chain_complete is False: True

--- 31. Round 55 resolution_rule 'absolute' rejects relative target ---
RELATIVE TARGET UNDER ABSOLUTE RULE REJECTED: ValueError: resolution_rule 'absolute' requires target to be an absolute path, got 'claude.cmd'

--- 32. Round 57 resolution_rule 'path' rejects path separators (enforces bare command name) ---
PATH SEPARATOR IN PATH RULE REJECTED (.\): ValueError: resolution_rule 'path' requires a bare command name with no path components, got '.\claude.cmd'
PATH SEPARATOR IN PATH RULE REJECTED (subdir\..\): ValueError: resolution_rule 'path' requires a bare command name with no path components, got 'subdir\..\claude.cmd'
GENUINE BARE NAME ADMISSION SUCCESS: Admitted via real PATH lookup, got receipt: receipt-cc-claude-peer-20260821T113445Z-3b21221cc19b0c7dbbfc10d8e4c1ca6b

--- 33. Round 59 Strict PATH-Only Resolution (CWD Shadowing & Registry Independence) ---
GENUINE PATH COMMAND ADMISSION SUCCESS: Bare target 'python.exe' resolved via PATH directory, got receipt: receipt-cc-claude-peer-20260821T113445Z-b8a275961078bbd7a7b41abebc305ffa
CWD SHADOWING ATTACK BLOCKED: ValueError: Target 'adversarial_cwd_shadow_command.cmd' with resolution_rule 'path' could not be resolved via OS PATH

--- 34. Round 61 PATHEXT Resolution Order (Bare extensionless vs PATHEXT-extended precedence) ---
PATHEXT PRECEDENCE ENFORCED (bare candidate rejected): ValueError: Executable chain entrypoint D:\Engram&Peerhub\PortableDev (v2.1)\_sys\data\temp\ask_ask-5a55\tmp0aiwtl0x\probe_precedence_r61 does not match resolved manifest target D:\Engram&Peerhub\PortableDev (v2.1)\_sys\data\temp\ask_ask-5a55\tmp0aiwtl0x\probe_precedence_r61.CMD
GENUINE PATHEXT ADMISSION SUCCESS: Admitted via PATHEXT precedence, got receipt: receipt-cc-claude-peer-20260821T113445Z-3e7c5f7f7bfeeb5680e68385ec1b4b46
BARE EXTENSIONLESS WITHOUT PATHEXT REJECTED: ValueError: Target 'probe_bare_only_r61' with resolution_rule 'path' could not be resolved via OS PATH

--- 35. Round 61 Unicode Case-Folding Path Identity (Real Filesystem Identity via os.path.samefile) ---
Two distinct files created on NTFS: 'target_K_r61.cmd' and 'target_K_r61.cmd'
Python str.lower() conflates paths: True
os.path.samefile() correctly distinguishes files: True
UNICODE CONFUSABLE BINDING BLOCKED: ValueError: Executable chain entrypoint D:\Engram&Peerhub\PortableDev (v2.1)\_sys\data\temp\ask_ask-5a55\tmpojlvj8ug\target_K_r61.cmd does not match resolved manifest target D:\Engram&Peerhub\PortableDev (v2.1)\_sys\data\temp\ask_ask-5a55\tmpojlvj8ug\target_K_r61.cmd
GENUINE UNICODE TARGET ADMISSION SUCCESS: Admitted with genuine filesystem identity, got receipt: receipt-cc-claude-peer-20260821T113445Z-fcc78c3c7d9625c8195f7fa71a4c91cd

--- 36. Round 63 PATHEXT Strictness & Malformed-Syntax Rejection (Fail-Closed on Missing Dot or Empty Token) ---
MALFORMED PATHEXT MISSING DOT REJECTED: ValueError: resolution_rule 'path' requires a well-formed PATHEXT environment variable (every entry must start with a dot, got 'BAT' in PATHEXT 'BAT;.CMD'). Cannot safely resolve commands under an ambiguous or malformed PATHEXT.
MALFORMED PATHEXT LEADING EMPTY TOKEN REJECTED: ValueError: resolution_rule 'path' requires a well-formed PATHEXT environment variable (found empty token in PATHEXT ';.BAT;.CMD'). Cannot safely resolve commands under an ambiguous or malformed PATHEXT.
MALFORMED PATHEXT CONSECUTIVE SEMICOLONS REJECTED: ValueError: resolution_rule 'path' requires a well-formed PATHEXT environment variable (found empty token in PATHEXT '.BAT;;.CMD'). Cannot safely resolve commands under an ambiguous or malformed PATHEXT.
EXPLICITLY EMPTY PATHEXT RESOLUTION REJECTED: ValueError: Target 'cx_probe_r63' with resolution_rule 'path' could not be resolved via OS PATH
GENUINE WELL-FORMED PATHEXT ADMISSION SUCCESS: Admitted via .BAT precedence, got receipt: receipt-cc-claude-peer-20260821T113445Z-0bb9e11d954b61912dce34569512d63b
GENUINE WELL-FORMED PATHEXT ADMISSION SUCCESS (.CMD precedence): Got receipt: receipt-cc-claude-peer-20260821T113445Z-be6a12cbf8af65e2ba293ccfed63221f
GENUINE UNSET PATHEXT ADMISSION SUCCESS (default list precedence): Got receipt: receipt-cc-claude-peer-20260821T113445Z-26ea77fb147fb6091382e5cebc30285a
```
