"""Real Claude adapter implementation for Stage 3."""

from __future__ import annotations

import json
from collections.abc import Sequence

from peerhub.adapters.contract import (
    AdapterRequest,
    ArtifactSpec,
    Capability,
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
    SessionAction,
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


_CLAUDE_PROFILE = ProfileDescriptor(
    profile_id="cc.standard",
    profile_class="tier",
    supports_reasoning_effort=False,
)

_CLAUDE_DESCRIPTOR = PeerDescriptor(
    adapter_id="claude-peer",
    adapter_version="1.0.0",
    peer_kind="cc",
    profiles=(_CLAUDE_PROFILE,),
    transports=frozenset({TransportKind.PIPE}),
    capabilities=frozenset({Capability.SESSION}),
    usage_provider_id=None,
    readiness_probe_id="claude-readiness",
)


class ClaudeOutputDecoder:
    """Decoder for claude.cmd --output-format json."""

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
                # claude.cmd might print "Warning: no stdin data received..." before the JSON.
                # Extract the JSON object.
                start_idx = decoded.find("{")
                end_idx = decoded.rfind("}")
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    json_str = decoded[start_idx:end_idx + 1]
                    parsed = json.loads(json_str)
                    
                    if not parsed.get("is_error", False):
                        response_text = parsed.get("result", "")
                        canonical_text = response_text or json_str
                        if response_text:
                            event = DecoderEvent(
                                kind=DecoderEventKind.ASSISTANT_TEXT,
                                payload={"text": response_text},
                            )
                            events.append(event)
                    else:
                        canonical_text = json_str
                        err_type = parsed.get("error_type", "")
                        if err_type == "invalid_session":
                            events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "session_invalid", "evidence_source": "structured_vendor_output"}))
                        elif err_type == "over_quota":
                            events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "quota_exhausted", "evidence_source": "structured_vendor_output"}))
                        elif err_type == "invalid_request_error":
                            events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "invocation_plan_rejected", "evidence_source": "structured_vendor_output"}))
                        elif err_type == "provider_down":
                            events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "provider_unavailable", "evidence_source": "structured_vendor_output"}))
        except Exception:
            # Not valid JSON or decoding error
            pass

        if not canonical_text:
            canonical_text = raw_bytes.decode("utf-8", errors="replace")

        if "model_operand_invalid" in canonical_text and not any(e.kind == DecoderEventKind.VENDOR_ERROR for e in events):
            events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "invocation_plan_rejected", "evidence_source": "known_terminal_pattern"}))

        return DecodedOutput(
            canonical_text=canonical_text,
            canonical_lines=_split_canonical_lines(canonical_text),
            events=tuple(events),
        )


class RealClaudeAdapter:
    """Real adapter that shells out to claude.cmd."""

    descriptor = _CLAUDE_DESCRIPTOR

    def prompt_policy(self, profile: ProfileDescriptor) -> PromptPolicy:
        if profile.profile_id != _CLAUDE_PROFILE.profile_id:
            raise ValueError(f"Unsupported profile {profile.profile_id}")
        return PromptPolicy(
            policy_id="cc-standard-policy",
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

        if profile.profile_id != _CLAUDE_PROFILE.profile_id:
            raise ValueError(f"Unsupported profile {profile.profile_id}")

        prompt = request.prompt_content
        if prompt is None:
            raise ValueError("prompt_content is required")

        policy = self.prompt_policy(profile)
        artifacts: list[ArtifactSpec] = []

        if request.evidence_payloads:
            import uuid
            import hashlib
            for payload in request.evidence_payloads:
                if len(payload.content_bytes) > policy.max_inline_utf8_bytes:
                    ev_id = f"evidence://ev_{uuid.uuid4().hex}"
                    sha = hashlib.sha256(payload.content_bytes).hexdigest()
                    spec = ArtifactSpec(
                        artifact_id=ev_id,
                        placeholder=ev_id,
                        content_bytes=payload.content_bytes,
                        content_reference=None,
                        sha256_hex=sha,
                        expected_length=len(payload.content_bytes),
                        access_mode="evidence",
                        lifecycle="ephemeral",
                    )
                    artifacts.append(spec)
                    content_str = payload.content_bytes.decode("utf-8", errors="replace")
                    summary_clean = content_str[:200].replace("\n", " ").strip()
                    summary = f"{summary_clean}..." if len(content_str) > 200 else summary_clean
                    prompt += f"\n<large output was {len(payload.content_bytes)} bytes, offloaded to {ev_id}, summary: {summary}>"
                else:
                    content_str = payload.content_bytes.decode("utf-8", errors="replace")
                    prompt += f"\n{content_str}"

        if request.requested_session_action == SessionAction.RESUME:
            if session is None or session.external_session_id is None:
                raise ValueError("external_session_id is required for RESUME")
            argv = ("claude.cmd", "-p", prompt, "--output-format", "json", "--resume", session.external_session_id)
            redacted_display = "claude.cmd -p <redacted> --output-format json --resume <redacted>"
        else:
            # SESSION_IDENTITY is now emitted by 2 of 3 real adapters (Codex, Agy); Claude
            # intentionally never emits it because Claude's session ID is caller-pregenerated
            # via `--session-id <uuid>` before invocation (see this adapter's own RESUME handling),
            # so there is nothing for Claude's decoder to capture post-hoc -- unlike Codex/Agy,
            # which generate a new ID server-side that must be captured from output after the fact.
            # This is a permanent architectural asymmetry, not a deferred gap.
            argv = ("claude.cmd", "-p", prompt, "--output-format", "json")
            redacted_display = "claude.cmd -p <redacted> --output-format json"

        return InvocationPlan(
            argv=argv,
            cwd_reference=request.workspace_scope,
            environment_delta={},
            transport=TransportKind.PIPE,
            stdin_payload=None,
            limits=limits,
            redacted_display=redacted_display,
            artifacts=tuple(artifacts),
            session_action=request.requested_session_action,
        )

    def new_decoder(self, plan: InvocationPlan) -> OutputDecoder:
        return ClaudeOutputDecoder()

    def interpret_output(
        self,
        plan: InvocationPlan,
        process: ProcessTerminalEvidence,
        raw_chunks: Sequence[bytes],
    ) -> ProtocolAssessment:
        decoder = self.new_decoder(plan)
        for chunk in raw_chunks:
            decoder.feed(chunk, channel=OutputChannel.STDOUT)
        decoded_output = decoder.finalize()
        has_vendor_error = any(e.kind == DecoderEventKind.VENDOR_ERROR for e in decoded_output.events)

        raw_bytes = b"".join(raw_chunks)
        try:
            decoded = raw_bytes.decode("utf-8")
            start_idx = decoded.find("{")
            end_idx = decoded.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = decoded[start_idx:end_idx + 1]
                parsed = json.loads(json_str)
                if not parsed.get("is_error", False) and "result" in parsed:
                    return ProtocolAssessment(
                        parsed=True,
                        response_present=True,
                        vendor_completion_marker=None,
                        suspected_truncation=False,
                        protocol_failure=None,
                    )
            
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
