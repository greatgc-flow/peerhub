"""Real Codex adapter implementation for Stage 3."""

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
    OutputChannel,
    OutputDecoder,
    PeerDescriptor,
    ProfileDescriptor,
    ProtocolAssessment,
    PromptPolicy,
    SessionAction,
    SessionHint,
)
from peerhub.core.protocol import ErrorCode, JsonValue
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
    capabilities=frozenset({Capability.SESSION, Capability.STREAM}),
    usage_provider_id=None,
    readiness_probe_id="codex-readiness",
)


class CodexOutputDecoder:
    """Decoder for codex.cmd exec --json."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._stdout_remainder = b""
        self._finalized = False
        self._events: list[DecoderEvent] = []
        self._assistant_texts: list[str] = []

    def feed(self, chunk: bytes, *, channel: OutputChannel = OutputChannel.STDOUT) -> tuple[DecoderEvent, ...]:
        if self._finalized:
            raise RuntimeError("feed called after finalize")
        if type(chunk) is not bytes:
            raise ValueError("chunk must be bytes")
        if not isinstance(channel, OutputChannel):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("channel must be OutputChannel")
        self._chunks.append(chunk)

        # codex --json emits JSONL on stdout.  Split at byte-level line
        # boundaries so both UTF-8 code points and JSON objects may span
        # arbitrary process-read chunks without data loss.
        if channel not in (OutputChannel.STDOUT, OutputChannel.PTY):
            return ()
        buffered = self._stdout_remainder + chunk
        lines = buffered.split(b"\n")
        self._stdout_remainder = lines.pop()
        emitted: list[DecoderEvent] = []
        for line in lines:
            emitted.extend(self._parse_json_line(line))
        return tuple(emitted)

    def _append_event(self, event: DecoderEvent) -> DecoderEvent:
        self._events.append(event)
        return event

    def _vendor_error_event(
        self,
        normalized_kind: str,
    ) -> DecoderEvent:
        return self._append_event(
            DecoderEvent(
                kind=DecoderEventKind.VENDOR_ERROR,
                payload={
                    "normalized_kind": normalized_kind,
                    "evidence_source": "structured_vendor_output",
                },
            )
        )

    def _event_from_message(self, message: str) -> DecoderEvent | None:
        msg_lower = message.lower()
        if "auth" in msg_lower or "unauthorized" in msg_lower:
            return self._vendor_error_event("auth_unavailable")
        if "invalid_request_error" in msg_lower or "invalid_model" in msg_lower:
            return self._vendor_error_event("invocation_plan_rejected")
        if any(
            marker in msg_lower
            for marker in ("network", "econnrefused", "enotfound", "connect")
        ):
            return self._vendor_error_event("network_unavailable")
        return None

    def _parse_json_line(self, raw_line: bytes) -> tuple[DecoderEvent, ...]:
        try:
            line = raw_line.decode("utf-8").strip()
        except UnicodeDecodeError:
            return ()
        if not line or not line.startswith("{"):
            return ()
        try:
            parsed_raw: object = json.loads(line)
        except json.JSONDecodeError:
            return ()
        if not isinstance(parsed_raw, dict):
            return ()
        parsed = cast(dict[str, object], parsed_raw)

        event_type = parsed.get("type")
        if event_type == "thread.started":
            thread_id = parsed.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return (
                    self._append_event(
                        DecoderEvent(
                            kind=DecoderEventKind.SESSION_IDENTITY,
                            # First SESSION_IDENTITY emitter: future adapters
                            # should use this single-key payload shape too.
                            payload={"session_id": thread_id},
                        )
                    ),
                )
            return ()

        if event_type == "item.completed":
            item = parsed.get("item")
            if not isinstance(item, dict):
                return ()
            item_mapping = cast(dict[str, object], item)
            # We emit TOOL_CALL on item.completed to avoid duplicating the event
            # across started and completed states. (Known limitation: a call that
            # times out or is killed mid-flight emits item.started only, producing
            # no TOOL_CALL event). We strip result fields to
            # yield just the call shape.
            if item_mapping.get("type") == "command_execution":
                call_payload = {
                    str(k): cast(JsonValue, v) for k, v in item_mapping.items()
                    if k not in ("aggregated_output", "exit_code", "status")
                }
                return (
                    self._append_event(
                        DecoderEvent(
                            kind=DecoderEventKind.TOOL_CALL,
                            payload=call_payload,
                        )
                    ),
                )
            if item_mapping.get("type") != "agent_message":
                return ()
            response_text = item_mapping.get("text")
            if not isinstance(response_text, str) or not response_text:
                return ()
            self._assistant_texts.append(response_text)
            return (
                self._append_event(
                    DecoderEvent(
                        kind=DecoderEventKind.ASSISTANT_TEXT,
                        payload={"text": response_text},
                    )
                ),
            )

        if event_type == "error":
            error_obj = parsed.get("error")
            err_code = (
                str(cast(dict[str, object], error_obj).get("code", ""))
                if isinstance(error_obj, dict)
                else ""
            )
            if err_code == "session_expired":
                return (self._vendor_error_event("session_invalid"),)
            if err_code == "invalid_model":
                return (self._vendor_error_event("invocation_plan_rejected"),)
            if err_code == "auth_unavailable":
                return (self._vendor_error_event("auth_unavailable"),)
            message = parsed.get("message")
            if isinstance(message, str) and message:
                event = self._event_from_message(message)
                return (event,) if event is not None else ()
            return ()

        if event_type == "turn.failed":
            error_obj = parsed.get("error")
            message = (
                cast(dict[str, object], error_obj).get("message")
                if isinstance(error_obj, dict)
                else None
            )
            if isinstance(message, str) and message:
                event = self._event_from_message(message)
                return (event,) if event is not None else ()
        return ()

    def finalize(self) -> DecodedOutput:
        if self._finalized:
            raise RuntimeError("finalize already called")
        if self._stdout_remainder:
            self._parse_json_line(self._stdout_remainder)
            self._stdout_remainder = b""
        self._finalized = True

        raw_bytes = b"".join(self._chunks)
        canonical_text = "\n".join(self._assistant_texts)

        if not canonical_text:
            canonical_text = raw_bytes.decode("utf-8", errors="replace")

        if "model_operand_invalid" in canonical_text and not any(
            event.kind == DecoderEventKind.VENDOR_ERROR for event in self._events
        ):
            self._events.append(
                DecoderEvent(
                    kind=DecoderEventKind.VENDOR_ERROR,
                    payload={
                        "normalized_kind": "invocation_plan_rejected",
                        "evidence_source": "known_terminal_pattern",
                    },
                )
            )

        return DecodedOutput(
            canonical_text=canonical_text,
            canonical_lines=_split_canonical_lines(canonical_text),
            events=tuple(self._events),
        )


class RealCodexAdapter:
    """Real adapter that shells out to codex.cmd."""

    descriptor = _CODEX_DESCRIPTOR

    def __init__(self, executable_path: str | Sequence[str] | Path | None = None) -> None:
        self.executable_path = executable_path

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
            exec_argv = ("codex.cmd",)

        if request.requested_session_action == SessionAction.RESUME:
            if session is None or session.external_session_id is None:
                raise ValueError("external_session_id is required for RESUME")
            argv = (
                *exec_argv,
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
            argv = (*exec_argv, "exec", "--json", prompt)
            redacted_display = "codex.cmd exec --json <redacted>"

        # No explicit --sandbox flag: inherits config.toml's sandbox_mode.
        # If workspace_scope resolves through a SUBST/junction alias whose
        # real target cannot be cleanly invoked (e.g. contains a shell
        # metacharacter), Codex's own Windows unelevated sandbox will refuse
        # every subprocess with "cannot enforce split writable root sets",
        # failing silently for any real (non-trivial) task while trivial
        # prompts still succeed. See PEERHUB-CODEX-SUBST-SANDBOX-CONFLICT-
        # 2026-08-21.md for the full root cause and recommended handling
        # (detect the failure explicitly; resolve to genuine filesystem
        # identity before choosing how to invoke, per the same discipline
        # as AdmissionRegistry's canonical_path/samefile() checks) before
        # this adapter is exercised against a workspace root that aliases
        # its own real path.
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
