"""Adapter-boundary contracts for Slice 5 (SLICE5-KICKOFF-R1.md item 5).

Owns the ``PeerAdapter`` interface, peer/profile descriptors, invocation
planning types, artifact specs, output-decoder evidence types, and the
existing ``ProtocolAssessment`` (moved here, not redefined --
``dispatch.contract`` re-exports the same class object unchanged so every
existing ``from peerhub.dispatch.contract import ProtocolAssessment``
import across the Slice 1-4 codebase keeps working).

Dependency direction is ``core -> adapters.contract -> dispatch.contract
/service`` (ARCHITECTURE.md section 6.2 / Round 6-7 Finding 3). This
module must never import from ``peerhub.dispatch`` -- the reverse
direction (dispatch importing adapters.contract) is intentional and
expected, not a cycle.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Protocol

from peerhub.core.execution import (
    ProcessTerminalEvidence,
    TransportKind,
    TransportLimits,
)
from peerhub.core.protocol import (
    ErrorCode,
    JsonValue,
    freeze_json_mapping,
    require_text,
)


def _require_bool(value: bool, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")


def _require_nonnegative_int(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


# --- Protocol assessment (moved from dispatch.contract, item 5) -----------


@dataclass(frozen=True)
class ProtocolAssessment:
    """Adapter-produced protocol/framing observations only.

    Moved here verbatim from ``peerhub.dispatch.contract`` -- same 5
    fields, same validation. ``peerhub.dispatch.contract`` re-exports this
    exact class object rather than redefining it.
    """

    parsed: bool
    response_present: bool
    vendor_completion_marker: bool | None
    suspected_truncation: bool
    protocol_failure: ErrorCode | None

    def __post_init__(self) -> None:
        _require_bool(self.parsed, "parsed")
        _require_bool(self.response_present, "response_present")
        if (
            self.vendor_completion_marker is not None
            and type(self.vendor_completion_marker) is not bool
        ):
            raise ValueError(
                "vendor_completion_marker must be boolean or null"
            )
        _require_bool(self.suspected_truncation, "suspected_truncation")
        if self.protocol_failure is not None and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.protocol_failure, ErrorCode
        ):
            raise ValueError(
                "protocol_failure must be ErrorCode or null"
            )


# --- Peer/profile descriptors (ARCHITECTURE.md section 6.1) ---------------


class Capability(str, Enum):
    """Adapter-declared optional capabilities (ARCHITECTURE.md section 6.1).

    Only the three capabilities the architecture doc names explicitly are
    defined here (its own listing trails off with "..." rather than giving
    a closed enumeration) -- this is a direct citation, not an invented
    closed vocabulary. Extending this enum is a future ratification, not a
    guess made here.
    """

    SESSION = "SESSION"
    STREAM = "STREAM"
    GRACEFUL_CANCEL = "GRACEFUL_CANCEL"


@dataclass(frozen=True)
class ProfileDescriptor:
    """Adapter-level, generic profile-KIND metadata (ARCHITECTURE.md 6.1).

    Shared by every instance of an adapter. Does NOT own the configured
    model pin -- that is instance-level and lives in ``PeerProfileBinding``
    (ARCHITECTURE.md 6.1a), never duplicated here.
    """

    profile_id: str
    profile_class: str
    supports_reasoning_effort: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "profile_id",
            require_text(self.profile_id, "profile_id"),
        )
        object.__setattr__(
            self,
            "profile_class",
            require_text(self.profile_class, "profile_class"),
        )
        _require_bool(
            self.supports_reasoning_effort,
            "supports_reasoning_effort",
        )


@dataclass(frozen=True)
class PeerDescriptor:
    """Adapter identity and static capabilities (ARCHITECTURE.md 6.1)."""

    adapter_id: str
    adapter_version: str
    peer_kind: str
    profiles: tuple[ProfileDescriptor, ...]
    transports: frozenset[TransportKind]
    capabilities: frozenset[Capability]
    usage_provider_id: str | None
    readiness_probe_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "adapter_id",
            require_text(self.adapter_id, "adapter_id"),
        )
        object.__setattr__(
            self,
            "adapter_version",
            require_text(self.adapter_version, "adapter_version"),
        )
        object.__setattr__(
            self,
            "peer_kind",
            require_text(self.peer_kind, "peer_kind"),
        )
        object.__setattr__(
            self,
            "readiness_probe_id",
            require_text(self.readiness_probe_id, "readiness_probe_id"),
        )
        if self.usage_provider_id is not None:
            object.__setattr__(
                self,
                "usage_provider_id",
                require_text(
                    self.usage_provider_id, "usage_provider_id"
                ),
            )

        profiles = tuple(self.profiles)
        if not profiles:
            raise ValueError("profiles must be non-empty")
        for profile in profiles:
            if not isinstance(profile, ProfileDescriptor):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError(
                    "every profile must be a ProfileDescriptor"
                )
        if len({p.profile_id for p in profiles}) != len(profiles):
            raise ValueError("profile_id values must be unique")
        object.__setattr__(self, "profiles", profiles)

        transports = frozenset(self.transports)
        if not transports:
            raise ValueError("transports must be non-empty")
        for transport in transports:
            if not isinstance(transport, TransportKind):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError(
                    "every transport must be a TransportKind"
                )
        object.__setattr__(self, "transports", transports)

        capabilities = frozenset(self.capabilities)
        for capability in capabilities:
            if not isinstance(capability, Capability):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError(
                    "every capability must be a Capability"
                )
        object.__setattr__(self, "capabilities", capabilities)


# --- Request/invocation planning types (ARCHITECTURE.md section 6.2) ------


@dataclass(frozen=True)
class PromptPolicy:
    """Per-profile prompt-transport policy."""

    policy_id: str
    max_inline_utf8_bytes: int
    artifact_reference_supported: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "policy_id", require_text(self.policy_id, "policy_id")
        )
        # Nonnegative, not positive: neither ratifying doc establishes a
        # positivity floor here (cross-review finding, cx, 2026-08-02).
        _require_nonnegative_int(
            self.max_inline_utf8_bytes, "max_inline_utf8_bytes"
        )
        _require_bool(
            self.artifact_reference_supported,
            "artifact_reference_supported",
        )


class SessionAction(str, Enum):
    """Requested session policy for one dispatch."""

    NONE = "NONE"
    CREATE = "CREATE"
    RESUME = "RESUME"


class CompletionContractView(Protocol):
    """Structural view of ``dispatch.contract.CompletionContract``.

    Carries only ``contract_id`` -- lets ``AdapterRequest`` reference a
    completion contract without ``adapters.contract`` importing
    ``peerhub.dispatch`` (a forbidden direction; ARCHITECTURE.md section
    6.2 / SLICE5-KICKOFF-R1.md item 5).
    ``peerhub.dispatch.contract.CompletionContract`` structurally satisfies
    this Protocol as shipped; no adapter class is needed.
    """

    @property
    def contract_id(self) -> str: ...


@dataclass(frozen=True)
class SessionHint:
    """Caller-supplied session-resume hint.

    A fake/reference adapter that does not implement session support
    rejects any non-null hint (ARCHITECTURE.md line ~382: session support
    is optional per adapter).
    """

    external_session_id: str | None
    adapter_fingerprint: str | None
    session_generation: int | None

    def __post_init__(self) -> None:
        if self.external_session_id is not None:
            object.__setattr__(
                self,
                "external_session_id",
                require_text(
                    self.external_session_id, "external_session_id"
                ),
            )
        if self.adapter_fingerprint is not None:
            object.__setattr__(
                self,
                "adapter_fingerprint",
                require_text(
                    self.adapter_fingerprint, "adapter_fingerprint"
                ),
            )
        if self.session_generation is not None and (
            type(self.session_generation) is not int
            or self.session_generation < 0
        ):
            raise ValueError(
                "session_generation must be a nonnegative integer or null"
            )


@dataclass(frozen=True)
class EvidencePayload:
    """An intercepted evidence payload intended for prompt inclusion."""

    source_tool_name: str
    content_bytes: bytes

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_tool_name",
            require_text(self.source_tool_name, "source_tool_name"),
        )
        if type(self.content_bytes) is not bytes:
            raise ValueError("content_bytes must be bytes")


@dataclass(frozen=True)
class AdapterRequest:
    """The already-authorized request an adapter plans an invocation for.

    The adapter never evaluates ``completion_contract``, only carries it
    (ARCHITECTURE.md section 6.2).
    """

    request_id: str
    prompt_content: str | None
    prompt_reference: str | None
    workspace_scope: str
    profile_id: str
    requested_session_action: SessionAction
    completion_contract: CompletionContractView
    evidence_payloads: tuple[EvidencePayload, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "request_id", require_text(self.request_id, "request_id")
        )
        object.__setattr__(
            self,
            "workspace_scope",
            require_text(self.workspace_scope, "workspace_scope"),
        )
        object.__setattr__(
            self, "profile_id", require_text(self.profile_id, "profile_id")
        )
        if not isinstance(self.requested_session_action, SessionAction):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(
                "requested_session_action must be SessionAction"
            )
        if self.completion_contract is None or not hasattr(  # pyright: ignore[reportUnnecessaryComparison]
            self.completion_contract, "contract_id"
        ):
            raise ValueError(
                "completion_contract is required and must expose "
                "contract_id"
            )

        has_content = self.prompt_content is not None
        has_reference = self.prompt_reference is not None
        if has_content == has_reference:
            raise ValueError(
                "exactly one of prompt_content/prompt_reference is "
                "required"
            )
        if has_content:
            object.__setattr__(
                self,
                "prompt_content",
                require_text(self.prompt_content, "prompt_content"),  # pyright: ignore[reportArgumentType]
            )
        if has_reference:
            object.__setattr__(
                self,
                "prompt_reference",
                require_text(self.prompt_reference, "prompt_reference"),  # pyright: ignore[reportArgumentType]
            )

        evidence_payloads = tuple(self.evidence_payloads)
        for payload in evidence_payloads:
            if not isinstance(payload, EvidencePayload):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError("every evidence payload must be an EvidencePayload")
        object.__setattr__(self, "evidence_payloads", evidence_payloads)


@dataclass(frozen=True)
class ArtifactSpec:
    """Declarative artifact-materialization request.

    ``access_mode``/``lifecycle`` are kept as validated free text:
    ARCHITECTURE.md and SLICE5-KICKOFF-R1.md both name these fields
    without enumerating a closed set of values, so a specific enum here
    would be an invented vocabulary, not a ratified one -- a future
    ratification round can tighten this without changing the field shape.
    """

    artifact_id: str
    placeholder: str
    content_bytes: bytes | None
    content_reference: str | None
    sha256_hex: str
    expected_length: int
    access_mode: str
    lifecycle: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_id",
            require_text(self.artifact_id, "artifact_id"),
        )
        object.__setattr__(
            self,
            "placeholder",
            require_text(self.placeholder, "placeholder"),
        )
        object.__setattr__(
            self,
            "access_mode",
            require_text(self.access_mode, "access_mode"),
        )
        object.__setattr__(
            self, "lifecycle", require_text(self.lifecycle, "lifecycle")
        )

        normalized_sha = require_text(
            self.sha256_hex, "sha256_hex"
        ).lower()
        if len(normalized_sha) != 64 or any(
            character not in "0123456789abcdef"
            for character in normalized_sha
        ):
            raise ValueError(
                "sha256_hex must be a lowercase SHA-256 hex digest"
            )
        object.__setattr__(self, "sha256_hex", normalized_sha)

        # Nonnegative, not positive: a zero-length artifact (empty file,
        # valid SHA-256 of b"") is not forbidden by either ratifying doc
        # (cross-review finding, cx, 2026-08-02).
        _require_nonnegative_int(self.expected_length, "expected_length")

        has_bytes = self.content_bytes is not None
        has_reference = self.content_reference is not None
        if has_bytes == has_reference:
            raise ValueError(
                "exactly one of content_bytes/content_reference is "
                "required"
            )
        if has_bytes and type(self.content_bytes) is not bytes:
            raise ValueError("content_bytes must be bytes")
        if has_reference:
            object.__setattr__(
                self,
                "content_reference",
                require_text(
                    self.content_reference, "content_reference"  # pyright: ignore[reportArgumentType]
                ),
            )


@dataclass(frozen=True)
class InvocationPlan:
    """Immutable, fully-resolved plan for one process invocation.

    Mechanically generated from a resolved ``PeerProfileBinding`` +
    ``AdapterRequest`` (ARCHITECTURE.md 6.1a) -- never independently
    specified by a caller. No shell command string, no ambient environment
    capture. A graceful-cancel recipe field is deliberately omitted here:
    vendor-specific graceful-cancel recipes remain deferred (item 4).
    """

    argv: tuple[str, ...]
    cwd_reference: str
    environment_delta: Mapping[str, str]
    transport: TransportKind
    stdin_payload: bytes | None
    limits: TransportLimits
    redacted_display: str
    artifacts: tuple[ArtifactSpec, ...]
    session_action: SessionAction

    def __post_init__(self) -> None:
        argv = tuple(self.argv)
        if not argv or any(
            not isinstance(token, str) or not token for token in argv  # pyright: ignore[reportUnnecessaryIsInstance]
        ):
            raise ValueError(
                "argv must be a non-empty tuple of non-empty strings"
            )
        object.__setattr__(self, "argv", argv)

        object.__setattr__(
            self,
            "cwd_reference",
            require_text(self.cwd_reference, "cwd_reference"),
        )

        env = dict(self.environment_delta)
        for key, value in env.items():
            if not isinstance(key, str) or not key:  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError(
                    "environment_delta keys must be non-empty strings"
                )
            if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError(
                    "environment_delta values must be strings"
                )
        object.__setattr__(
            self, "environment_delta", MappingProxyType(env)
        )

        if not isinstance(self.transport, TransportKind):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("transport must be TransportKind")

        if (
            self.stdin_payload is not None
            and type(self.stdin_payload) is not bytes
        ):
            raise ValueError("stdin_payload must be bytes or null")

        if not isinstance(self.limits, TransportLimits):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("limits must be TransportLimits")

        object.__setattr__(
            self,
            "redacted_display",
            require_text(self.redacted_display, "redacted_display"),
        )

        artifacts = tuple(self.artifacts)
        for artifact in artifacts:
            if not isinstance(artifact, ArtifactSpec):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError(
                    "every artifact must be an ArtifactSpec"
                )
        if len({a.artifact_id for a in artifacts}) != len(artifacts):
            raise ValueError("artifact_id values must be unique")
        object.__setattr__(self, "artifacts", artifacts)

        if not isinstance(self.session_action, SessionAction):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("session_action must be SessionAction")


# --- Output decoding (ARCHITECTURE.md section 6.2, item 3) ----------------


class OutputChannel(str, Enum):
    """Origin channel of one decoded output chunk."""

    STDOUT = "STDOUT"
    STDERR = "STDERR"
    PTY = "PTY"


class DecoderEventKind(str, Enum):
    """Typed evidence categories an ``OutputDecoder`` may emit."""

    PROGRESS = "PROGRESS"
    ASSISTANT_TEXT = "ASSISTANT_TEXT"
    SESSION_IDENTITY = "SESSION_IDENTITY"
    USAGE_HINT = "USAGE_HINT"
    VENDOR_ERROR = "VENDOR_ERROR"
    COMPLETION_MARKER = "COMPLETION_MARKER"
    TOOL_CALL = "TOOL_CALL"


@dataclass(frozen=True)
class DecoderEvent:
    """One typed decoder evidence event."""

    kind: DecoderEventKind
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DecoderEventKind):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("kind must be DecoderEventKind")
        object.__setattr__(
            self, "payload", freeze_json_mapping(self.payload)
        )


@dataclass(frozen=True)
class DecodedOutput:
    """Terminal decoder output: canonical transcript plus typed events.

    Owns ``canonical_lines`` -- deliberately NOT part of
    ``ProtocolAssessment`` (item 3): ``ProtocolAssessment`` stays frozen to
    its existing 5 protocol facts; transcript/decoder output lives here.
    """

    canonical_text: str
    canonical_lines: tuple[str, ...]
    events: tuple[DecoderEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_text, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("canonical_text must be a string")

        lines = tuple(self.canonical_lines)
        if any(not isinstance(line, str) for line in lines):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError(
                "canonical_lines must be a tuple of strings"
            )
        object.__setattr__(self, "canonical_lines", lines)

        events = tuple(self.events)
        for event in events:
            if not isinstance(event, DecoderEvent):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ValueError("every event must be a DecoderEvent")
        object.__setattr__(self, "events", events)


class OutputDecoder(Protocol):
    """Per-invocation mutable decoder state (ARCHITECTURE.md section 6.2).

    Never a singleton -- exactly one instance per invocation, created via
    ``PeerAdapter.new_decoder``. Never writes state or calls another
    service. Bounded-memory with transcript spill to core artifact
    infrastructure (spill mechanism is a later-step concern, not part of
    this Protocol's shape).
    """

    def feed(
        self, chunk: bytes, *, channel: OutputChannel
    ) -> tuple[DecoderEvent, ...]: ...

    def finalize(self) -> DecodedOutput: ...


# --- The adapter interface itself (ARCHITECTURE.md section 6.2) -----------


class PeerAdapter(Protocol):
    """Vendor-adapter interface.

    ``interpret_output`` MUST NOT decide task fulfillment -- vendor-protocol
    evidence only (malformed/truncated framing, empty response, vendor
    error, progress-without-terminal marker, suspicious delegation
    marker). Task-fulfillment judgment is core's job via a central
    ``CompletionAssessor``, never the adapter's.
    """

    descriptor: PeerDescriptor

    def prompt_policy(self, profile: ProfileDescriptor) -> PromptPolicy: ...

    def plan_invocation(
        self,
        request: AdapterRequest,
        profile: ProfileDescriptor,
        session: SessionHint | None,
        limits: TransportLimits,
    ) -> InvocationPlan: ...

    def new_decoder(self, plan: InvocationPlan) -> OutputDecoder: ...

    def interpret_output(
        self,
        plan: InvocationPlan,
        process: ProcessTerminalEvidence,
        raw_chunks: Sequence[bytes],
    ) -> ProtocolAssessment: ...
