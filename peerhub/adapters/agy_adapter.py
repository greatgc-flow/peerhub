"""Real Antigravity adapter implementation for Stage 3."""

from __future__ import annotations

import json
from collections.abc import Sequence

from peerhub.adapters.contract import (
    AdapterRequest,
    DecodedOutput,
    DecoderEvent,
    DecoderEventKind,
    InvocationPlan,
    OutputChannel,
    OutputDecoder,
    PeerDescriptor,
    ProfileDescriptor,
    ProtocolAssessment,
    PromptPolicy,
    SessionHint,
)
from peerhub.core.protocol import ErrorCode
from peerhub.core.execution import (
    ProcessTerminalEvidence,
    TransportKind,
    TransportLimits,
)


def _split_canonical_lines(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if normalized.endswith("\n"):
        lines = lines[:-1]
    return tuple(lines)


_AGY_PROFILE = ProfileDescriptor(
    profile_id="ag.standard",
    profile_class="tier",
    supports_reasoning_effort=True,
)

_AGY_DESCRIPTOR = PeerDescriptor(
    adapter_id="agy-peer",
    adapter_version="1.0.0",
    peer_kind="ag",
    profiles=(_AGY_PROFILE,),
    transports=frozenset({TransportKind.PIPE}),
    capabilities=frozenset({}),
    usage_provider_id=None,
    readiness_probe_id="agy-readiness",
)


class AgyOutputDecoder:
    """Decoder for agy.exe --output-format json."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._finalized = False
        self._events: list[DecoderEvent] = []

    def feed(self, chunk: bytes, *, channel: OutputChannel = OutputChannel.STDOUT) -> tuple[DecoderEvent, ...]:
        if self._finalized:
            raise RuntimeError("feed called after finalize")
        if type(chunk) is not bytes:
            raise ValueError("chunk must be bytes")
        self._chunks.append(chunk)
        return ()

    def finalize(self) -> DecodedOutput:
        if self._finalized:
            raise RuntimeError("finalize already called")
        self._finalized = True

        raw_bytes = b"".join(self._chunks)
        canonical_text = ""
        events: list[DecoderEvent] = []

        try:
            if raw_bytes:
                decoded = raw_bytes.decode("utf-8")
                # Attempt to parse as JSON
                parsed = json.loads(decoded)
                response_text = parsed.get("response", "")
                canonical_text = response_text or decoded
                if response_text:
                    event = DecoderEvent(
                        kind=DecoderEventKind.ASSISTANT_TEXT,
                        payload={"text": response_text},
                    )
                    events.append(event)
                if "error" in parsed:
                    err_type = parsed["error"].get("type", "")
                    if err_type == "session_not_found":
                        events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "session_invalid", "evidence_source": "structured_vendor_output"}))
                    elif err_type == "invalid_model":
                        events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "invocation_plan_rejected", "evidence_source": "structured_vendor_output"}))
                    elif err_type == "rate_limit_exceeded":
                        events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "rate_limited", "evidence_source": "structured_vendor_output"}))
                    elif err_type == "network_error":
                        events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "network_unavailable", "evidence_source": "structured_vendor_output"}))
        except Exception:
            # Not valid JSON or decoding error
            canonical_text = raw_bytes.decode("utf-8", errors="replace")

        if "model_operand_invalid" in canonical_text and not any(e.kind == DecoderEventKind.VENDOR_ERROR for e in events):
            events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "invocation_plan_rejected", "evidence_source": "known_terminal_pattern"}))

        return DecodedOutput(
            canonical_text=canonical_text,
            canonical_lines=_split_canonical_lines(canonical_text),
            events=tuple(events),
        )


class RealAgyAdapter:
    """Real adapter that shells out to agy.exe."""

    descriptor = _AGY_DESCRIPTOR

    def prompt_policy(self, profile: ProfileDescriptor) -> PromptPolicy:
        if profile.profile_id != _AGY_PROFILE.profile_id:
            raise ValueError(f"Unsupported profile {profile.profile_id}")
        return PromptPolicy(
            policy_id="ag-standard-policy",
            max_inline_utf8_bytes=1000000,
            artifact_reference_supported=False,
        )

    def plan_invocation(
        self,
        request: AdapterRequest,
        profile: ProfileDescriptor,
        session: SessionHint | None,
        limits: TransportLimits,
    ) -> InvocationPlan:

        if profile.profile_id != _AGY_PROFILE.profile_id:
            raise ValueError(f"Unsupported profile {profile.profile_id}")

        prompt = request.prompt_content
        if prompt is None:
            raise ValueError("prompt_content is required")

        argv = ("agy.exe", "-p", prompt, "--output-format", "json")
        return InvocationPlan(
            argv=argv,
            cwd_reference=request.workspace_scope,
            environment_delta={},
            transport=TransportKind.PIPE,
            stdin_payload=None,
            limits=limits,
            redacted_display="agy.exe -p <redacted> --output-format json",
            artifacts=(),
            session_action=request.requested_session_action,
        )

    def new_decoder(self, plan: InvocationPlan) -> OutputDecoder:
        return AgyOutputDecoder()

    def interpret_output(
        self,
        plan: InvocationPlan,
        process: ProcessTerminalEvidence,
        raw_chunks: Sequence[bytes],
    ) -> ProtocolAssessment:
        decoder = self.new_decoder(plan)
        for chunk in raw_chunks:
            decoder.feed(chunk, channel=OutputChannel.STDOUT)
        decoded = decoder.finalize()
        has_vendor_error = any(e.kind == DecoderEventKind.VENDOR_ERROR for e in decoded.events)

        raw_bytes = b"".join(raw_chunks)
        try:
            parsed = json.loads(raw_bytes.decode("utf-8"))
            if "response" in parsed:
                return ProtocolAssessment(
                    parsed=True,
                    response_present=True,
                    vendor_completion_marker=None,
                    suspected_truncation=False,
                    protocol_failure=None,
                )
            else:
                return ProtocolAssessment(
                    parsed=True,
                    response_present=False,
                    vendor_completion_marker=None,
                    suspected_truncation=False,
                    protocol_failure=None if has_vendor_error else ErrorCode.INTERNAL_ERROR,
                )
        except Exception:
            return ProtocolAssessment(
                parsed=False,
                response_present=False,
                vendor_completion_marker=None,
                suspected_truncation=False,
                protocol_failure=None if has_vendor_error else ErrorCode.INTERNAL_ERROR,
            )
