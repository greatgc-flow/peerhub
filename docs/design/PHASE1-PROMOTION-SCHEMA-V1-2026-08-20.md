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

To ground the requirement rules in concrete, typed contracts matching `PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`, the evaluation context defines `load_manifest_schema_v2`, `AdmissionRegistry`, `CellKey`, `ProfileDescriptor`, and `AdapterManifest` (which carries genuine immutable `ProfileDescriptor` snapshots of the admitted manifest's real declared profiles):

```python
from __future__ import annotations
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar
import hashlib
import json
import re
import secrets
import threading
import jsonschema

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
    _store: dict[str, str] = {}  # receipt_id -> canonical_sha256
    _lock: threading.Lock = threading.Lock()

    class AdmissionRegistry:
        """Minimal trusted registry for admitted manifests.
        
        Populated exclusively during a real admission event after rigorous validation
        against the PHASE1-MANIFEST-SCHEMA-V2 specification. It computes and stores 
        the canonical AST digest (manifest_ast_digest = SHA256(canonical_json(M_i))) 
        of a fully validated manifest under a newly issued, collision-safe receipt ID 
        (128-bit random token with atomic concurrency lock and uniqueness retry checks).
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
        def admit(cls, raw_manifest: dict, max_retries: int = 10) -> str:
            """Admission lifecycle: Validates manifest against Phase 1 V2 schema, computes canonical digest, issues collision-safe receipt ID atomically."""
            # 1. Genuine schema validation before issuance
            cls.validate_manifest(raw_manifest)

            # 2. Canonical AST digest computation over full manifest (manifest_ast_digest)
            digest = AdapterManifest.canonical_digest(raw_manifest)

            # 3. Collision-safe 128-bit receipt ID issuance with atomic check-and-insert under lock
            for attempt in range(max_retries):
                candidate_id = f"rcpt_{secrets.token_hex(16)}"
                with _lock:
                    if candidate_id not in _store:
                        _store[candidate_id] = digest
                        return candidate_id

            raise RuntimeError("Collision resolution exhausted: unable to generate a unique admission receipt ID.")

        @classmethod
        def get_trusted_digest(cls, receipt_id: str) -> str:
            """Promotion lifecycle: Looks up the trusted digest by registry-issued receipt ID."""
            if not isinstance(receipt_id, str):
                raise TypeError("receipt_id must be a string")
            with _lock:
                digest = _store.get(receipt_id)
            if digest is None:
                raise ValueError(f"Unknown admission receipt ID: {receipt_id}")
            return digest

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
        digest authenticity via the trusted AdmissionRegistry, and reading fields directly
        out of the admitted manifest into independent, immutable ProfileDescriptor snapshots.
        
        The caller MUST provide a valid, registry-issued admission_receipt_id.
        The registry itself provides the expected digest, closing the accidental forgery gap.
        """
        if not isinstance(raw_manifest, dict) or "adapter" not in raw_manifest or "profiles" not in raw_manifest:
            raise ValueError("raw_manifest must be an admitted manifest dict containing 'adapter' and 'profiles' blocks.")

        # 1. Look up trusted digest from the registry using the opaque ID
        expected_digest = AdmissionRegistry.get_trusted_digest(admission_receipt_id)

        # 2. Recompute canonical digest over FULL manifest content and verify authenticity
        recomputed_digest = cls.canonical_digest(raw_manifest)
        if recomputed_digest != expected_digest:
            raise ValueError(
                f"Manifest admission digest mismatch! Registry expects digest '{expected_digest}', "
                f"but recomputed canonical digest over provided manifest is '{recomputed_digest}'. "
                "Manifest is either unadmitted, fabricated, or unintentionally modified."
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
    AdmissionRegistry.admit(cx_missing_fields_manifest)
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
    AdmissionRegistry.admit(cx_extra_key_codex_manifest)
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

codex_receipt_id = AdmissionRegistry.admit(valid_codex_manifest)
print(f"Admitted valid Codex manifest, got 128-bit collision-safe receipt: {codex_receipt_id}")
codex_manifest_obj = AdapterManifest.from_manifest(valid_codex_manifest, codex_receipt_id)
print(f"SUCCESS: Constructed {codex_manifest_obj.adapter_id} ({codex_manifest_obj.peer_kind}) carrying genuine declared profiles: {codex_manifest_obj.declared_profile_ids}")

print("\n--- 6. Fully schema-valid Claude manifest admitted with declared profiles ---")
claude_receipt_id = AdmissionRegistry.admit(valid_claude_manifest)
print(f"Admitted valid Claude manifest, got 128-bit collision-safe receipt: {claude_receipt_id}")
claude_manifest_obj = AdapterManifest.from_manifest(valid_claude_manifest, claude_receipt_id)
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
raw_existing_token = existing_receipt_id.replace("rcpt_", "")

second_valid_manifest = dict(valid_claude_manifest)
second_valid_manifest["adapter"] = dict(valid_claude_manifest["adapter"])
second_valid_manifest["adapter"]["adapter_id"] = "agy-peer"
second_valid_manifest["adapter"]["peer_kind"] = "ag"
second_valid_manifest["engine"] = {"engine_id": "builtin:json-agy-v1", "options": {}}
second_valid_manifest["profiles"] = [
    {
        "profile_id": "ag.standard",
        "profile_class": "tier",
        "supports_reasoning_effort": False,
        "transport": "PIPE",
        "prompt_policy": {
            "policy_id": "ag-standard-policy",
            "max_inline_utf8_bytes": 1000000,
            "artifact_reference_supported": False
        }
    }
]

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
manifest_t1["adapter"]["adapter_id"] = "conc-peer-1"

manifest_t2 = dict(valid_claude_manifest)
manifest_t2["adapter"] = dict(valid_claude_manifest["adapter"])
manifest_t2["adapter"]["adapter_id"] = "conc-peer-2"

concurrent_results = {}
barrier = threading.Barrier(2)

def concurrent_worker(tname, manifest):
    threading.current_thread().name = tname
    barrier.wait()
    receipt = AdmissionRegistry.admit(manifest)
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
    AdapterManifest.from_manifest(forged_manifest, forged_digest)
    print("SUCCESS: Forgery worked! (This should not happen)")
except Exception as e:
    print(f"BLOCKED: {type(e).__name__}: {e}")

print("\n--- 13. Syntactically well-formed but unknown receipt ID ---")
unknown_receipt_id = "rcpt_00000000000000000000000000000000"
try:
    AdapterManifest.from_manifest(valid_claude_manifest, unknown_receipt_id)
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

pty_receipt_id = AdmissionRegistry.admit(valid_pty_manifest)
pty_manifest_obj = AdapterManifest.from_manifest(valid_pty_manifest, pty_receipt_id)
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

mutable_receipt_id = AdmissionRegistry.admit(mutable_manifest)
snapshot_manifest_obj = AdapterManifest.from_manifest(mutable_manifest, mutable_receipt_id)

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
    AdapterManifest.from_manifest(unadmitted_forged_manifest, forged_receipt_id)
    print("FAILED: from_manifest succeeded with forged receipt ID!")
except ValueError as e:
    print(f"FORGERY REJECTED at from_manifest(): {type(e).__name__}: {e}")

# Clean up monkeypatched class attribute (if any)
if hasattr(AdmissionRegistry, "_store"):
    delattr(AdmissionRegistry, "_store")

# Genuine admission still succeeds exactly as before
genuine_forged_receipt = AdmissionRegistry.admit(unadmitted_forged_manifest)
print(f"GENUINE ADMISSION SUCCESS: Valid manifest admitted through admit(), got: {genuine_forged_receipt}")
genuine_manifest_obj = AdapterManifest.from_manifest(unadmitted_forged_manifest, genuine_forged_receipt)
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
Admitted valid Codex manifest, got 128-bit collision-safe receipt: rcpt_1abe1da63a1f5056b7c559ad48f43d20
SUCCESS: Constructed codex-peer (cx) carrying genuine declared profiles: frozenset({'cx.standard'})

--- 6. Fully schema-valid Claude manifest admitted with declared profiles ---
Admitted valid Claude manifest, got 128-bit collision-safe receipt: rcpt_cda4d26b5ba183297e831668a925db02
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
Earlier receipt ID (rcpt_cda4d26b5ba183297e831668a925db02) digest preserved intact: True
Second admission detected collision and retried to fresh ID: rcpt_abcdef0123456789abcdef0123456789
Store size now: 3 distinct entries (no clobbering!)

--- 11. Concurrency safety: Real multi-threaded concurrent execution with forced interleaving ---
Thread 1 receipt ID: rcpt_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Thread 2 receipt ID: rcpt_11112222333344445555666677778888
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
GENUINE ADMISSION SUCCESS: Valid manifest admitted through admit(), got: rcpt_e49cbbb3239f61eea6d1dd4a9defde1d
GENUINE CONSTRUCT SUCCESS: Constructed forged-bypass-peer with digest verified from trusted registry.

--- 19. Round 38 EvidenceRegistry Validates cell_data Shape ---
SHAPE VALIDATION BLOCKED: Bare dictionary rejected: TypeError: cell_obj must expose a real cell_key of the real CellKey type
SHAPE VALIDATION BLOCKED: Invalid attempt_outcome rejected: ValueError: cell_obj.attempt_outcome must be one of {'EXECUTED_PASS', 'QUOTA_BLOCKED', 'ENVIRONMENT_UNAVAILABLE', 'PRODUCT_FAILURE', 'NOT_REQUESTED'}
GENUINE CELL SUCCESS: Properly shaped cell admitted, got receipt: ev_11f950947fa88104afcf21f7d9f97742
GENUINE CELL PROMOTION: Properly shaped cell still promotes exactly as before: True

--- 20. cx's EvidenceRegistry Exact Attacks (Type-name Spoof, Non-datetime Timestamp, Post-admission Mutation) ---
TYPE-NAME SPOOF BLOCKED: TypeError: cell_obj must expose a real cell_key of the real CellKey type
NON-DATETIME TIMESTAMP BLOCKED: TypeError: provenance must carry a real timestamp_utc of the real datetime type
Admitted mutable cell as PRODUCT_FAILURE, got receipt: ev_2f064869ec92b108de1e9b1f0c2c181e
Promotion with PRODUCT_FAILURE returned: False
Attacker mutated original cell object to EXECUTED_PASS.
Promotion after post-admission mutation returned: False
POST-ADMISSION MUTATION BLOCKED: Promotion result remained False despite mutation of original object.

--- 21. Round 42 cx's Changing-Getter TOCTOU Attack in admit() ---
TOCTOU CHANGING GETTER BLOCKED: ValueError: cell_obj.attempt_outcome must be one of {'NOT_REQUESTED', 'PRODUCT_FAILURE', 'QUOTA_BLOCKED', 'ENVIRONMENT_UNAVAILABLE', 'EXECUTED_PASS'}
GENUINE CELL PROMOTION R42: Properly shaped cell still promotes correctly: True

--- 22. Round 42 Item 2: Timezone-Aware UTC Enforcement & Future Skew Bounds ---
OFFSET-NAIVE DATETIME BLOCKED: ValueError: timestamp_utc must be a timezone-aware datetime (e.g. timezone.utc)
Ten-years-in-future cell evidence_state: ERROR
FUTURE TIMESTAMP SKEW REJECTED: determine_evidence_state returned ERROR: True
FUTURE TIMESTAMP PROMOTION BLOCKED: can_promote returned False: True
Timezone-aware recent cell evidence_state: MEASURED
TIMEZONE-AWARE NO CRASH: determine_evidence_state evaluated cleanly without TypeError: True
GENUINE TIMEZONE-AWARE PROMOTION SUCCESS: can_promote returned True: True
```
