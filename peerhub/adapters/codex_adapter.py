"""Real Codex adapter implementation for Stage 3."""

from __future__ import annotations

import json
from collections.abc import Sequence

from peerhub.adapters.contract import (
    AdapterRequest,
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


_CODEX_PROFILE = ProfileDescriptor(
    profile_id="cx.standard",
    profile_class="tier",
    supports_reasoning_effort=False,
)

_CODEX_DESCRIPTOR = PeerDescriptor(
    adapter_id="codex-peer",
    adapter_version="1.0.0",
    peer_kind="cx",
    profiles=(_CODEX_PROFILE,),
    transports=frozenset({TransportKind.PIPE}),
    capabilities=frozenset({Capability.SESSION}),
    usage_provider_id=None,
    readiness_probe_id="codex-readiness",
)


class CodexOutputDecoder:
    """Decoder for codex.cmd exec --json."""

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
                
                # Parse JSONL event stream
                for line in decoded.splitlines():
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                        
                    try:
                        parsed = json.loads(line)
                        if parsed.get("type") == "thread.started":
                            thread_id = parsed.get("thread_id")
                            if isinstance(thread_id, str) and thread_id:
                                # First SESSION_IDENTITY emitter: future adapters
                                # should use this single-key payload shape too.
                                events.append(
                                    DecoderEvent(
                                        kind=DecoderEventKind.SESSION_IDENTITY,
                                        payload={"session_id": thread_id},
                                    )
                                )
                        elif parsed.get("type") == "item.completed":
                            item = parsed.get("item", {})
                            if item.get("type") == "agent_message":
                                response_text = item.get("text", "")
                                if response_text:
                                    # Overwrite canonical_text (assume last agent_message is the final one, or append)
                                    if canonical_text:
                                        canonical_text += "\n" + response_text
                                    else:
                                        canonical_text = response_text
                                        
                                    event = DecoderEvent(
                                        kind=DecoderEventKind.ASSISTANT_TEXT,
                                        payload={"text": response_text},
                                    )
                                    events.append(event)
                        elif parsed.get("type") == "error":
                            err_code = parsed.get("error", {}).get("code", "")
                            if err_code == "session_expired":
                                events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "session_invalid", "evidence_source": "structured_vendor_output"}))
                            elif err_code == "invalid_model":
                                events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "invocation_plan_rejected", "evidence_source": "structured_vendor_output"}))
                            elif err_code == "auth_unavailable":
                                events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "auth_unavailable", "evidence_source": "structured_vendor_output"}))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            # Not valid decoding or other error
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


class RealCodexAdapter:
    """Real adapter that shells out to codex.cmd."""

    descriptor = _CODEX_DESCRIPTOR

    def prompt_policy(self, profile: ProfileDescriptor) -> PromptPolicy:
        if profile.profile_id != _CODEX_PROFILE.profile_id:
            raise ValueError(f"Unsupported profile {profile.profile_id}")
        return PromptPolicy(
            policy_id="cx-standard-policy",
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

        if profile.profile_id != _CODEX_PROFILE.profile_id:
            raise ValueError(f"Unsupported profile {profile.profile_id}")

        prompt = request.prompt_content
        if prompt is None:
            raise ValueError("prompt_content is required")

        if request.requested_session_action == SessionAction.RESUME:
            if session is None or session.external_session_id is None:
                raise ValueError("external_session_id is required for RESUME")
            argv = (
                "codex.cmd",
                "exec",
                "resume",
                "--json",
                session.external_session_id,
                prompt,
            )
            redacted_display = (
                "codex.cmd exec resume --json <session-id> <redacted>"
            )
        else:
            argv = ("codex.cmd", "exec", "--json", prompt)
            redacted_display = "codex.cmd exec --json <redacted>"

        return InvocationPlan(
            argv=argv,
            cwd_reference=request.workspace_scope,
            environment_delta={},
            transport=TransportKind.PIPE,
            stdin_payload=None,
            limits=limits,
            redacted_display=redacted_display,
            artifacts=(),
            session_action=request.requested_session_action,
        )

    def new_decoder(self, plan: InvocationPlan) -> OutputDecoder:
        return CodexOutputDecoder()

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
            response_present = False
            for line in decoded.splitlines():
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    parsed = json.loads(line)
                    if parsed.get("type") == "item.completed":
                        item = parsed.get("item", {})
                        if item.get("type") == "agent_message" and item.get("text"):
                            response_present = True
                            break
                except json.JSONDecodeError:
                    pass
                    
            if response_present:
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
