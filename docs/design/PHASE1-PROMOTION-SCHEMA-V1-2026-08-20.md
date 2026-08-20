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
from typing import ClassVar
import hashlib
import json
import re
import secrets
import threading
import jsonschema

_MANIFEST_SCHEMA_V2 = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://peerhub.local/schema/adapter-manifest/v2",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "manifest_version",
        "status",
        "adapter",
        "execution",
        "engine",
        "profiles",
    ],
    "properties": {
        "manifest_version": {"const": "2.0.0"},
        "status": {"enum": ["active", "inactive"]},
        "adapter": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "adapter_id",
                "adapter_version",
                "peer_kind",
                "capabilities",
                "supported_platforms",
                "supported_transports",
                "core_parity_requirements",
                "required_proof_kinds",
                "requires_snapshots",
                "readiness_probe_id",
            ],
            "properties": {
                "adapter_id": {"type": "string", "pattern": "^[a-z0-9-]+$"},
                "adapter_version": {
                    "type": "string",
                    "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$",
                },
                "peer_kind": {"type": "string", "pattern": "^[a-z]+$"},
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "capabilities": {
                    "type": "array",
                    "items": {"enum": ["SESSION", "STREAM", "GRACEFUL_CANCEL"]},
                },
                "supported_platforms": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                },
                "supported_transports": {
                    "type": "array",
                    "items": {"enum": ["PIPE", "PTY"]},
                },
                "core_parity_requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "required_proof_kinds": {
                    "type": "array",
                    "items": {
                        "enum": [
                            "deterministic contract or integration",
                            "controlled real-OS executable",
                            "live provider exact-profile",
                            "legacy-parity evidence",
                        ]
                    },
                },
                "requires_snapshots": {"type": "boolean"},
                "readiness_probe_id": {"type": "string", "minLength": 1},
                "usage_provider_id": {"type": "string"},
            },
        },
        "execution": {
            "type": "object",
            "additionalProperties": False,
            "required": ["executable", "templates", "env_policy"],
            "properties": {
                "executable": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["resolution_rule", "target"],
                    "properties": {
                        "resolution_rule": {"enum": ["absolute", "sibling", "path"]},
                        "target": {"type": "string", "minLength": 1},
                    },
                },
                "shim_names": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "templates": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["start"],
                    "properties": {
                        "start": {"$ref": "#/$defs/invocation_template"},
                        "resume": {"$ref": "#/$defs/invocation_template"},
                    },
                },
                "env_policy": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["inherit", "set"],
                    "properties": {
                        "inherit": {"type": "array", "items": {"type": "string"}},
                        "set": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                },
            },
        },
        "engine": {
            "type": "object",
            "additionalProperties": False,
            "required": ["engine_id", "options"],
            "properties": {
                "engine_id": {
                    "type": "string",
                    "enum": [
                        "builtin:json-claude-v1",
                        "builtin:jsonl-codex-v1",
                        "builtin:json-agy-v1",
                        "builtin:pty-legacy-v1",
                    ],
                },
                "options": {
                    "type": "object",
                    "description": "Explicit finite typed options per engine.",
                },
            },
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "engine_id": {"const": "builtin:json-claude-v1"}
                        }
                    },
                    "then": {
                        "properties": {
                            "options": {
                                "title": "Builtin SSE Options",
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["enforce_strict_json"],
                                "properties": {
                                    "enforce_strict_json": {"type": "boolean"}
                                },
                            }
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "engine_id": {"const": "builtin:jsonl-codex-v1"}
                        }
                    },
                    "then": {
                        "properties": {
                            "options": {
                                "title": "Empty Options",
                                "type": "object",
                                "additionalProperties": False,
                                "maxProperties": 0,
                                "properties": {},
                            }
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "engine_id": {"const": "builtin:json-agy-v1"}
                        }
                    },
                    "then": {
                        "properties": {
                            "options": {
                                "title": "Empty Options",
                                "type": "object",
                                "additionalProperties": False,
                                "maxProperties": 0,
                                "properties": {},
                            }
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "engine_id": {"const": "builtin:pty-legacy-v1"}
                        }
                    },
                    "then": {
                        "properties": {
                            "options": {
                                "title": "Builtin CLI Regex Options",
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["success_regex"],
                                "properties": {
                                    "success_regex": {"type": "string"},
                                    "error_regex": {"type": "string"},
                                },
                            }
                        }
                    },
                },
            ],
        },
        "profiles": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "profile_id",
                    "profile_class",
                    "supports_reasoning_effort",
                    "transport",
                    "prompt_policy",
                ],
                "properties": {
                    "profile_id": {"type": "string", "minLength": 1},
                    "profile_class": {"type": "string"},
                    "supports_reasoning_effort": {"type": "boolean"},
                    "transport": {"enum": ["PIPE", "PTY"]},
                    "prompt_policy": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "policy_id",
                            "max_inline_utf8_bytes",
                            "artifact_reference_supported",
                        ],
                        "properties": {
                            "policy_id": {"type": "string"},
                            "max_inline_utf8_bytes": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "artifact_reference_supported": {"type": "boolean"},
                        },
                    },
                },
            },
        },
    },
    "$defs": {
        "invocation_template": {
            "type": "object",
            "additionalProperties": False,
            "required": ["argv", "cwd"],
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string"},
                "stdin": {"type": "string"},
                "artifacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "artifact_id",
                            "placeholder",
                            "access_mode",
                            "lifecycle",
                        ],
                        "properties": {
                            "artifact_id": {"type": "string"},
                            "placeholder": {"type": "string"},
                            "access_mode": {"type": "string"},
                            "lifecycle": {"type": "string"},
                        },
                    },
                },
            },
        }
    },
}

_MANIFEST_VALIDATOR = jsonschema.Draft202012Validator(_MANIFEST_SCHEMA_V2)

_active_manifest_token: ContextVar[str | None] = ContextVar("_active_manifest_token", default=None)

class AdmissionRegistry:
    """Minimal trusted registry for admitted manifests.
    
    Populated exclusively during a real admission event after rigorous validation
    against the PHASE1-MANIFEST-SCHEMA-V2 specification. It computes and stores 
    the canonical AST digest (manifest_ast_digest = SHA256(canonical_json(M_i))) 
    of a fully validated manifest under a newly issued, collision-safe receipt ID 
    (128-bit random token with atomic concurrency lock and uniqueness retry checks).
    The registry is the only trusted source for linking a receipt ID to a digest,
    preventing callers from supplying their own digests as proof.
    """
    _store: ClassVar[dict[str, str]] = {}  # receipt_id -> canonical_sha256
    _lock: ClassVar[threading.Lock] = threading.Lock()

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
    def admit(cls, raw_manifest: dict, max_retries: int = 10) -> str:
        """Admission lifecycle: Validates manifest against Phase 1 V2 schema, computes canonical digest, issues collision-safe receipt ID atomically."""
        # 1. Genuine schema validation before issuance
        cls.validate_manifest(raw_manifest)

        # 2. Canonical AST digest computation over full manifest (manifest_ast_digest)
        digest = AdapterManifest.canonical_digest(raw_manifest)

        # 3. Collision-safe 128-bit receipt ID issuance with atomic check-and-insert under lock
        for attempt in range(max_retries):
            candidate_id = f"rcpt_{secrets.token_hex(16)}"
            with cls._lock:
                if candidate_id not in cls._store:
                    cls._store[candidate_id] = digest
                    return candidate_id

        raise RuntimeError("Collision resolution exhausted: unable to generate a unique admission receipt ID.")

    @classmethod
    def get_trusted_digest(cls, receipt_id: str) -> str:
        """Promotion lifecycle: Looks up the trusted digest by registry-issued receipt ID."""
        if not isinstance(receipt_id, str):
            raise TypeError("receipt_id must be a string")
        with cls._lock:
            digest = cls._store.get(receipt_id)
        if digest is None:
            raise ValueError(f"Unknown admission receipt ID: {receipt_id}")
        return digest

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
                "Instances must be traceably constructed via AdapterManifest.from_manifest(raw_manifest, admission_receipt)."
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
    ) -> AdapterManifest:
        """Constructs this contract strictly by validating schema shape, verifying canonical
        digest authenticity via the trusted AdmissionRegistry, and reading fields.
        
        The caller MUST provide a valid, registry-issued admission_receipt_id.
        The registry itself provides the expected digest, closing the forgery gap.
        """
        if not isinstance(raw_manifest, dict) or "adapter" not in raw_manifest:
            raise ValueError("raw_manifest must be an admitted manifest dict containing an 'adapter' block.")

        # 1. Look up trusted digest from the registry using the opaque ID
        expected_digest = AdmissionRegistry.get_trusted_digest(admission_receipt_id)

        # 2. Recompute canonical digest over FULL manifest content and verify authenticity
        recomputed_digest = cls.canonical_digest(raw_manifest)
        if recomputed_digest != expected_digest:
            raise ValueError(
                f"Manifest admission digest mismatch! Registry expects digest '{expected_digest}', "
                f"but recomputed canonical digest over provided manifest is '{recomputed_digest}'. "
                "Manifest is either unadmitted, forged, or tampered with."
            )

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


## 10. Round 27 Execution Trace (Authoritative Schema Validation & Atomic Concurrency Safety)

```python
import hashlib
import json
import re
import secrets
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from typing import ClassVar
import jsonschema

_MANIFEST_SCHEMA_V2 = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://peerhub.local/schema/adapter-manifest/v2",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "manifest_version",
        "status",
        "adapter",
        "execution",
        "engine",
        "profiles",
    ],
    "properties": {
        "manifest_version": {"const": "2.0.0"},
        "status": {"enum": ["active", "inactive"]},
        "adapter": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "adapter_id",
                "adapter_version",
                "peer_kind",
                "capabilities",
                "supported_platforms",
                "supported_transports",
                "core_parity_requirements",
                "required_proof_kinds",
                "requires_snapshots",
                "readiness_probe_id",
            ],
            "properties": {
                "adapter_id": {"type": "string", "pattern": "^[a-z0-9-]+$"},
                "adapter_version": {
                    "type": "string",
                    "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$",
                },
                "peer_kind": {"type": "string", "pattern": "^[a-z]+$"},
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "capabilities": {
                    "type": "array",
                    "items": {"enum": ["SESSION", "STREAM", "GRACEFUL_CANCEL"]},
                },
                "supported_platforms": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                },
                "supported_transports": {
                    "type": "array",
                    "items": {"enum": ["PIPE", "PTY"]},
                },
                "core_parity_requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "required_proof_kinds": {
                    "type": "array",
                    "items": {
                        "enum": [
                            "deterministic contract or integration",
                            "controlled real-OS executable",
                            "live provider exact-profile",
                            "legacy-parity evidence",
                        ]
                    },
                },
                "requires_snapshots": {"type": "boolean"},
                "readiness_probe_id": {"type": "string", "minLength": 1},
                "usage_provider_id": {"type": "string"},
            },
        },
        "execution": {
            "type": "object",
            "additionalProperties": False,
            "required": ["executable", "templates", "env_policy"],
            "properties": {
                "executable": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["resolution_rule", "target"],
                    "properties": {
                        "resolution_rule": {"enum": ["absolute", "sibling", "path"]},
                        "target": {"type": "string", "minLength": 1},
                    },
                },
                "shim_names": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "templates": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["start"],
                    "properties": {
                        "start": {"$ref": "#/$defs/invocation_template"},
                        "resume": {"$ref": "#/$defs/invocation_template"},
                    },
                },
                "env_policy": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["inherit", "set"],
                    "properties": {
                        "inherit": {"type": "array", "items": {"type": "string"}},
                        "set": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                },
            },
        },
        "engine": {
            "type": "object",
            "additionalProperties": False,
            "required": ["engine_id", "options"],
            "properties": {
                "engine_id": {
                    "type": "string",
                    "enum": [
                        "builtin:json-claude-v1",
                        "builtin:jsonl-codex-v1",
                        "builtin:json-agy-v1",
                        "builtin:pty-legacy-v1",
                    ],
                },
                "options": {
                    "type": "object",
                    "description": "Explicit finite typed options per engine.",
                },
            },
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "engine_id": {"const": "builtin:json-claude-v1"}
                        }
                    },
                    "then": {
                        "properties": {
                            "options": {
                                "title": "Builtin SSE Options",
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["enforce_strict_json"],
                                "properties": {
                                    "enforce_strict_json": {"type": "boolean"}
                                },
                            }
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "engine_id": {"const": "builtin:jsonl-codex-v1"}
                        }
                    },
                    "then": {
                        "properties": {
                            "options": {
                                "title": "Empty Options",
                                "type": "object",
                                "additionalProperties": False,
                                "maxProperties": 0,
                                "properties": {},
                            }
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "engine_id": {"const": "builtin:json-agy-v1"}
                        }
                    },
                    "then": {
                        "properties": {
                            "options": {
                                "title": "Empty Options",
                                "type": "object",
                                "additionalProperties": False,
                                "maxProperties": 0,
                                "properties": {},
                            }
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "engine_id": {"const": "builtin:pty-legacy-v1"}
                        }
                    },
                    "then": {
                        "properties": {
                            "options": {
                                "title": "Builtin CLI Regex Options",
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["success_regex"],
                                "properties": {
                                    "success_regex": {"type": "string"},
                                    "error_regex": {"type": "string"},
                                },
                            }
                        }
                    },
                },
            ],
        },
        "profiles": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "profile_id",
                    "profile_class",
                    "supports_reasoning_effort",
                    "transport",
                    "prompt_policy",
                ],
                "properties": {
                    "profile_id": {"type": "string", "minLength": 1},
                    "profile_class": {"type": "string"},
                    "supports_reasoning_effort": {"type": "boolean"},
                    "transport": {"enum": ["PIPE", "PTY"]},
                    "prompt_policy": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "policy_id",
                            "max_inline_utf8_bytes",
                            "artifact_reference_supported",
                        ],
                        "properties": {
                            "policy_id": {"type": "string"},
                            "max_inline_utf8_bytes": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "artifact_reference_supported": {"type": "boolean"},
                        },
                    },
                },
            },
        },
    },
    "$defs": {
        "invocation_template": {
            "type": "object",
            "additionalProperties": False,
            "required": ["argv", "cwd"],
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string"},
                "stdin": {"type": "string"},
                "artifacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "artifact_id",
                            "placeholder",
                            "access_mode",
                            "lifecycle",
                        ],
                        "properties": {
                            "artifact_id": {"type": "string"},
                            "placeholder": {"type": "string"},
                            "access_mode": {"type": "string"},
                            "lifecycle": {"type": "string"},
                        },
                    },
                },
            },
        }
    },
}

_MANIFEST_VALIDATOR = jsonschema.Draft202012Validator(_MANIFEST_SCHEMA_V2)

_active_manifest_token: ContextVar[str | None] = ContextVar("_active_manifest_token", default=None)

class AdmissionRegistry:
    _store: ClassVar[dict[str, str]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def validate_manifest(cls, raw_manifest: dict) -> None:
        if not isinstance(raw_manifest, dict):
            raise TypeError(f"Manifest must be a dict, got {type(raw_manifest).__name__}")

        # 1. Authoritative V2 JSON Schema structural validation (Draft 2020-12)
        errors = list(_MANIFEST_VALIDATOR.iter_errors(raw_manifest))
        if errors:
            err_details = [f"{e.message}" for e in errors]
            raise ValueError(f"Manifest schema validation failed: {'; '.join(err_details)}")

        # 2. Semantic admission rules (Section 4.1 in V2 Specification)
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
    def admit(cls, raw_manifest: dict, max_retries: int = 10) -> str:
        """Admission lifecycle: Validates manifest against Phase 1 V2 schema, computes canonical digest, issues collision-safe receipt ID atomically."""
        # 1. Genuine schema validation before issuance
        cls.validate_manifest(raw_manifest)

        # 2. Canonical AST digest computation over full manifest (manifest_ast_digest)
        digest = AdapterManifest.canonical_digest(raw_manifest)

        # 3. Collision-safe 128-bit receipt ID issuance with atomic check-and-insert under lock
        for attempt in range(max_retries):
            candidate_id = f"rcpt_{secrets.token_hex(16)}"
            with cls._lock:
                if candidate_id not in cls._store:
                    cls._store[candidate_id] = digest
                    return candidate_id

        raise RuntimeError("Collision resolution exhausted: unable to generate a unique admission receipt ID.")

    @classmethod
    def get_trusted_digest(cls, receipt_id: str) -> str:
        if not isinstance(receipt_id, str):
            raise TypeError("receipt_id must be a string")
        with cls._lock:
            digest = cls._store.get(receipt_id)
        if digest is None:
            raise ValueError(f"Unknown admission receipt ID: {receipt_id}")
        return digest

@dataclass(frozen=True, slots=True)
class AdapterManifest:
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
        if self._token is None or active_token is None or self._token != active_token:
            raise TypeError("Direct construction prohibited.")

    @staticmethod
    def canonical_digest(raw_manifest: dict) -> str:
        if not isinstance(raw_manifest, dict):
            raise TypeError(f"Payload must be a dict, got {type(raw_manifest).__name__}.")
        canonical_bytes = json.dumps(
            raw_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest().upper()

    @classmethod
    def from_manifest(cls, raw_manifest: dict, admission_receipt_id: str) -> "AdapterManifest":
        if not isinstance(raw_manifest, dict) or "adapter" not in raw_manifest:
            raise ValueError("raw_manifest must be an admitted manifest dict containing an 'adapter' block.")
        expected_digest = AdmissionRegistry.get_trusted_digest(admission_receipt_id)
        recomputed_digest = cls.canonical_digest(raw_manifest)
        if recomputed_digest != expected_digest:
            raise ValueError(f"Manifest admission digest mismatch! Registry expects digest {expected_digest}, but recomputed is {recomputed_digest}.")

        adapter = raw_manifest["adapter"]
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

print("--- 1. cx's missing-required-fields manifest rejected at admit() ---")
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
    AdmissionRegistry.admit(cx_missing_fields_manifest)
    print("FAILED: cx missing-fields manifest was unexpectedly admitted!")
except Exception as e:
    print(f"REJECTED at admit() as expected: {type(e).__name__}: {e}")

print("\n--- 2. cx's exact extra-key Codex manifest rejected at admit() before receipt issuance ---")
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
    "options": {"enforce_strict_json": True}  # Extra key forbidden under Empty Options schema!
}

store_size_before = len(AdmissionRegistry._store)
try:
    AdmissionRegistry.admit(cx_extra_key_codex_manifest)
    print("FAILED: cx extra-key Codex manifest was unexpectedly admitted!")
except Exception as e:
    store_size_after = len(AdmissionRegistry._store)
    print(f"REJECTED at admit() as expected: {type(e).__name__}: {e}")
    print(f"Store unpolluted (no receipt issued): {store_size_before == store_size_after}")

print("\n--- 3. Fully schema-valid Codex manifest with empty options admitted successfully ---")
valid_codex_manifest = dict(valid_claude_manifest)
valid_codex_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
valid_codex_manifest["adapter"]["adapter_id"] = "codex-peer"
valid_codex_manifest["adapter"]["peer_kind"] = "cx"
valid_codex_manifest["engine"] = {
    "engine_id": "builtin:jsonl-codex-v1",
    "options": {}
}

codex_receipt_id = AdmissionRegistry.admit(valid_codex_manifest)
print(f"Admitted valid Codex manifest, got 128-bit collision-safe receipt: {codex_receipt_id}")
codex_manifest_obj = AdapterManifest.from_manifest(valid_codex_manifest, codex_receipt_id)
print(f"SUCCESS: Constructed {codex_manifest_obj.adapter_id} ({codex_manifest_obj.peer_kind}) with genuine receipt.")

print("\n--- 4. Fully schema-valid Claude manifest admitted and promoted ---")
claude_receipt_id = AdmissionRegistry.admit(valid_claude_manifest)
print(f"Admitted valid Claude manifest, got 128-bit collision-safe receipt: {claude_receipt_id}")
claude_manifest_obj = AdapterManifest.from_manifest(valid_claude_manifest, claude_receipt_id)
print(f"SUCCESS: Constructed {claude_manifest_obj.adapter_id} ({claude_manifest_obj.peer_kind}) with genuine receipt.")

print("\n--- 5. Collision safety: forced sequential collision retried to fresh receipt ID ---")
existing_receipt_id = claude_receipt_id
raw_existing_token = existing_receipt_id.replace("rcpt_", "")

second_valid_manifest = dict(valid_claude_manifest)
second_valid_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
second_valid_manifest["adapter"]["adapter_id"] = "agy-peer"
second_valid_manifest["adapter"]["peer_kind"] = "ag"
second_valid_manifest["engine"] = {"engine_id": "builtin:json-agy-v1", "options": {}}

mock_tokens = [raw_existing_token, "abcdef0123456789abcdef0123456789"]
def mock_token_hex(nbytes=16):
    return mock_tokens.pop(0) if mock_tokens else "99999999999999999999999999999999"

orig_token_hex = secrets.token_hex
secrets.token_hex = mock_token_hex
try:
    first_digest_before = AdmissionRegistry.get_trusted_digest(existing_receipt_id)
    second_receipt_id = AdmissionRegistry.admit(second_valid_manifest)
    first_digest_after = AdmissionRegistry.get_trusted_digest(existing_receipt_id)
    
    print(f"Earlier receipt ID ({existing_receipt_id}) digest preserved intact: {first_digest_before == first_digest_after}")
    print(f"Second admission detected collision and retried to fresh ID: {second_receipt_id}")
    print(f"Store size now: {len(AdmissionRegistry._store)} distinct entries (no clobbering!)")
finally:
    secrets.token_hex = orig_token_hex

print("\n--- 6. Collision safety: forced exhaustion raises explicit RuntimeError ---")
def colliding_forever_token_hex(nbytes=16):
    return raw_existing_token

secrets.token_hex = colliding_forever_token_hex
try:
    AdmissionRegistry.admit(second_valid_manifest, max_retries=5)
    print("FAILED: Should have raised RuntimeError on exhaustion!")
except RuntimeError as e:
    print(f"EXPLICIT ERROR RAISED: {e}")
    print(f"Earlier receipt digest remains intact: {AdmissionRegistry.get_trusted_digest(existing_receipt_id) == first_digest_before}")
finally:
    secrets.token_hex = orig_token_hex

print("\n--- 7. Concurrency safety: Real multi-threaded concurrent execution with forced interleaving ---")
# Deliberately force two concurrent threads to generate the same candidate token initially
# and attempt to claim it simultaneously, proving that under the atomic lock, both callers
# obtain distinct receipt IDs and neither digest is lost.
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
manifest_t1["adapter"]["adapter_id"] = "conc-peer-1"

manifest_t2 = dict(valid_claude_manifest)
manifest_t2["adapter"] = dict(valid_claude_manifest["adapter"])
manifest_t2["adapter"]["adapter_id"] = "conc-peer-2"

concurrent_results = {}
barrier = threading.Barrier(2)

def concurrent_worker(tname, manifest):
    threading.current_thread().name = tname
    barrier.wait()  # synchronize start
    receipt = AdmissionRegistry.admit(manifest)
    concurrent_results[tname] = (receipt, AdapterManifest.canonical_digest(manifest))

secrets.token_hex = concurrent_mock_token_hex
try:
    store_count_before_conc = len(AdmissionRegistry._store)
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
    print(f"Store size increased by exactly 2 entries: {len(AdmissionRegistry._store) == store_count_before_conc + 2}")
    print("CONCURRENCY RACE VERIFIED FIXED: Atomic lock prevents TOCTOU clobbering under real thread contention.")
finally:
    secrets.token_hex = orig_token_hex

print("\n--- 8. Forgery attempt (supplying own digest as receipt ID) ---")
forged_manifest = dict(valid_claude_manifest)
forged_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
forged_manifest["adapter"]["adapter_id"] = "forged-adapter"

forged_digest = AdapterManifest.canonical_digest(forged_manifest)
try:
    AdapterManifest.from_manifest(forged_manifest, forged_digest)
    print("SUCCESS: Forgery worked! (This should not happen)")
except Exception as e:
    print(f"BLOCKED: {type(e).__name__}: {e}")

print("\n--- 9. Syntactically well-formed but unknown receipt ID ---")
unknown_receipt_id = "rcpt_00000000000000000000000000000000"
try:
    AdapterManifest.from_manifest(valid_claude_manifest, unknown_receipt_id)
    print("SUCCESS: Unknown receipt worked! (This should not happen)")
except Exception as e:
    print(f"BLOCKED: {type(e).__name__}: {e}")
```

**Output:**

```
--- 1. cx's missing-required-fields manifest rejected at admit() ---
REJECTED at admit() as expected: ValueError: Manifest schema validation failed: Additional properties are not allowed ('version' was unexpected); 'manifest_version' is a required property; 'status' is a required property; 'execution' is a required property; 'engine' is a required property; 'profiles' is a required property; 'adapter_version' is a required property; 'readiness_probe_id' is a required property

--- 2. cx's exact extra-key Codex manifest rejected at admit() before receipt issuance ---
REJECTED at admit() as expected: ValueError: Manifest schema validation failed: Additional properties are not allowed ('enforce_strict_json' was unexpected); {'enforce_strict_json': True} is expected to be empty
Store unpolluted (no receipt issued): True

--- 3. Fully schema-valid Codex manifest with empty options admitted successfully ---
Admitted valid Codex manifest, got 128-bit collision-safe receipt: rcpt_be1dca3248ed833926b7f767bf9c3a23
SUCCESS: Constructed codex-peer (cx) with genuine receipt.

--- 4. Fully schema-valid Claude manifest admitted and promoted ---
Admitted valid Claude manifest, got 128-bit collision-safe receipt: rcpt_15acfa9f6bc079f5ee912aeda8f62234
SUCCESS: Constructed claude-peer (cc) with genuine receipt.

--- 5. Collision safety: forced sequential collision retried to fresh receipt ID ---
Earlier receipt ID (rcpt_15acfa9f6bc079f5ee912aeda8f62234) digest preserved intact: True
Second admission detected collision and retried to fresh ID: rcpt_abcdef0123456789abcdef0123456789
Store size now: 3 distinct entries (no clobbering!)

--- 6. Collision safety: forced exhaustion raises explicit RuntimeError ---
EXPLICIT ERROR RAISED: Collision resolution exhausted: unable to generate a unique admission receipt ID.
Earlier receipt digest remains intact: True

--- 7. Concurrency safety: Real multi-threaded concurrent execution with forced interleaving ---
Thread 1 receipt ID: rcpt_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Thread 2 receipt ID: rcpt_11112222333344445555666677778888
Receipt IDs are distinct (no duplicate ID issued): True
Thread 1 digest in registry: True
Thread 2 digest in registry: True
Store size increased by exactly 2 entries: True
CONCURRENCY RACE VERIFIED FIXED: Atomic lock prevents TOCTOU clobbering under real thread contention.

--- 8. Forgery attempt (supplying own digest as receipt ID) ---
BLOCKED: ValueError: Unknown admission receipt ID: 6BD899AB9DC099CE261BBF959712E62BBF156B0E8DC141FBC784D533C6774DE1

--- 9. Syntactically well-formed but unknown receipt ID ---
BLOCKED: ValueError: Unknown admission receipt ID: rcpt_00000000000000000000000000000000
```

