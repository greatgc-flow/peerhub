"""Real Claude adapter implementation for Stage 3."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from peerhub.adapters.contract import (
    AdapterRequest,
    ArtifactSpec,
    Capability,
    DecodedOutput,
    DecoderEvent,
    DecoderEventKind,
    InvocationPlan,
    ModelSelectionMode,
    OutputChannel,
    OutputDecoder,
    PeerDescriptor,
    ProfileDescriptor,
    ProtocolAssessment,
    PromptPolicy,
    SessionAction,
    SessionHint,
)
from peerhub.adapters.prompt_transport import resolve_prompt_payload
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


_CLAUDE_STANDARD_PROFILE = ProfileDescriptor(
    profile_id="cc.standard",
    profile_class="tier",
    supports_reasoning_effort=False,
)

_CLAUDE_EFFORT_PROFILE = ProfileDescriptor(
    profile_id="cc.effort",
    profile_class="tier",
    supports_reasoning_effort=True,
)

_CLAUDE_DEEPTHINK_PROFILE = ProfileDescriptor(
    profile_id="cc.deepthink",
    profile_class="tier",
    supports_reasoning_effort=True,
)

_CLAUDE_PROFILES = (
    _CLAUDE_STANDARD_PROFILE,
    _CLAUDE_EFFORT_PROFILE,
    _CLAUDE_DEEPTHINK_PROFILE,
)

# Kept for any external references that still expect a single default profile.
_CLAUDE_PROFILE = _CLAUDE_STANDARD_PROFILE

_CLAUDE_DESCRIPTOR = PeerDescriptor(
    adapter_id="claude-peer",
    adapter_version="1.0.1",
    peer_kind="cc",
    profiles=_CLAUDE_PROFILES,
    transports=frozenset({TransportKind.PIPE}),
    capabilities=frozenset({Capability.SESSION}),
    usage_provider_id=None,
    readiness_probe_id="claude-readiness",
    default_profile_id="cc.standard",
)


class ClaudeOutputDecoder:
    """Decoder for claude.cmd --output-format stream-json."""

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

        parsed_objects = _decode_json_lines(raw_bytes)
        for parsed in parsed_objects:
            session_id = parsed.get("session_id")
            if isinstance(session_id, str) and session_id:
                events.append(DecoderEvent(
                    kind=DecoderEventKind.SESSION_IDENTITY,
                    payload={"session_id": session_id},
                ))
                break
        for parsed in parsed_objects:
            if parsed.get("is_error", False):
                raw_error_type = parsed.get("error_type", "")
                err_type = raw_error_type if isinstance(raw_error_type, str) else ""
                normalized = {
                    "invalid_session": "session_invalid",
                    "over_quota": "quota_exhausted",
                    "invalid_request_error": "invocation_plan_rejected",
                    "provider_down": "provider_unavailable",
                }.get(err_type)
                if normalized:
                    events.append(DecoderEvent(
                        kind=DecoderEventKind.VENDOR_ERROR,
                        payload={
                            "normalized_kind": normalized,
                            "evidence_source": "structured_vendor_output",
                        },
                    ))

        for parsed in reversed(parsed_objects):
            if parsed.get("type") == "result" or "result" in parsed:
                response_text = parsed.get("result", "")
                if isinstance(response_text, str) and response_text:
                    canonical_text = response_text
                    if not parsed.get("is_error", False):
                        events.append(DecoderEvent(
                            kind=DecoderEventKind.ASSISTANT_TEXT,
                            payload={"text": response_text},
                        ))
                break

        if not canonical_text:
            canonical_text = raw_bytes.decode("utf-8", errors="replace")

        if "model_operand_invalid" in canonical_text and not any(e.kind == DecoderEventKind.VENDOR_ERROR for e in events):
            events.append(DecoderEvent(kind=DecoderEventKind.VENDOR_ERROR, payload={"normalized_kind": "invocation_plan_rejected", "evidence_source": "known_terminal_pattern"}))

        return DecodedOutput(
            canonical_text=canonical_text,
            canonical_lines=_split_canonical_lines(canonical_text),
            events=tuple(events),
        )


def _decode_json_lines(raw_bytes: bytes) -> list[dict[str, object]]:
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return []
    objects: list[dict[str, object]] = []
    for line in decoded.splitlines() or [decoded]:
        candidate = line.strip()
        object_start = candidate.find("{")
        if object_start < 0:
            continue
        try:
            parsed = json.loads(candidate[object_start:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            objects.append(cast(dict[str, object], parsed))
    return objects


class RealClaudeAdapter:
    """Real adapter that shells out to claude.cmd."""

    descriptor = _CLAUDE_DESCRIPTOR

    def __init__(self, executable_path: str | Sequence[str] | Path | None = None) -> None:
        self.executable_path = executable_path

    def prompt_policy(self, profile: ProfileDescriptor) -> PromptPolicy:
        if profile.profile_id not in {p.profile_id for p in _CLAUDE_PROFILES}:
            raise ValueError(f"Unsupported profile {profile.profile_id}")
        return PromptPolicy(
            policy_id=f"{profile.profile_id}-policy",
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

        if profile.profile_id not in {p.profile_id for p in _CLAUDE_PROFILES}:
            raise ValueError(f"Unsupported profile {profile.profile_id}")

        prompt = resolve_prompt_payload(request)

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

        if self.executable_path is not None:
            if isinstance(self.executable_path, (list, tuple)):
                exec_argv = tuple(str(x) for x in self.executable_path)
            else:
                exec_argv = (str(self.executable_path),)
        else:
            exec_argv = ("claude.cmd",)

        # Model resolution is centralized: the caller resolves a
        # ResolvedModelBinding (workspace binding > global config > packaged
        # default, which defaults cc.standard to cli_default) and carries it
        # on the request; this adapter only translates PINNED into
        # claude.cmd's `--model` flag, never reads config itself.
        binding = request.model_binding
        model_flags: tuple[str, ...] = ()
        model_display = ""
        if binding.selection_mode is ModelSelectionMode.PINNED:
            assert binding.model_id is not None
            model_flags = ("--model", binding.model_id)
            model_display = f" --model {binding.model_id}"

        if request.requested_session_action == SessionAction.RESUME:
            if session is None or session.external_session_id is None:
                raise ValueError("external_session_id is required for RESUME")
            argv = (*exec_argv, "-p", "-", "--output-format", "stream-json", "--verbose", *model_flags, "--resume", session.external_session_id, "--autocompact", "auto")
            redacted_display = f"claude.cmd -p - --output-format stream-json --verbose{model_display} --resume <redacted> --autocompact auto"
        else:
            # stream-json init/result records expose the actual vendor
            # session ID; ClaudeOutputDecoder publishes it for future resumes.
            argv = (*exec_argv, "-p", "-", "--output-format", "stream-json", "--verbose", *model_flags)
            redacted_display = f"claude.cmd -p - --output-format stream-json --verbose{model_display}"

        return InvocationPlan(
            argv=argv,
            cwd_reference=request.workspace_scope,
            environment_delta={},
            transport=TransportKind.PIPE,
            stdin_payload=prompt.encode("utf-8"),
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

        parsed_objects = _decode_json_lines(b"".join(raw_chunks))
        response_present = any(
            not parsed.get("is_error", False) and "result" in parsed
            for parsed in parsed_objects
        )
        return ProtocolAssessment(
            parsed=bool(parsed_objects),
            response_present=response_present,
            vendor_completion_marker=None,
            suspected_truncation=False,
            protocol_failure=(
                None
                if response_present or has_vendor_error
                else ErrorCode.INTERNAL_ERROR
            ),
        )
