"""Real Codex adapter implementation for Stage 3."""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)
from peerhub.adapters.prompt_transport import resolve_prompt_payload
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


_CODEX_STANDARD_PROFILE = ProfileDescriptor(
    profile_id="cx.standard",
    profile_class="tier",
    supports_reasoning_effort=True,
)

_CODEX_EFFORT_PROFILE = ProfileDescriptor(
    profile_id="cx.effort",
    profile_class="tier",
    supports_reasoning_effort=True,
)

_CODEX_DEEPTHINK_PROFILE = ProfileDescriptor(
    profile_id="cx.deepthink",
    profile_class="tier",
    supports_reasoning_effort=True,
)

_CODEX_PROFILES = (
    _CODEX_STANDARD_PROFILE,
    _CODEX_EFFORT_PROFILE,
    _CODEX_DEEPTHINK_PROFILE,
)

_CODEX_PROFILE = _CODEX_STANDARD_PROFILE

_CODEX_DESCRIPTOR = PeerDescriptor(
    adapter_id="codex-peer",
    adapter_version="1.0.0",
    peer_kind="cx",
    profiles=_CODEX_PROFILES,
    transports=frozenset({TransportKind.PIPE}),
    capabilities=frozenset({Capability.SESSION, Capability.STREAM}),
    usage_provider_id=None,
    readiness_probe_id="codex-readiness",
    default_profile_id="cx.standard",
)


def _override_hint() -> str:
    """Build the model-override hint naming the actually-resolved global
    config path (item 13, dotdir consolidation, ratified 2026-09-09) --
    ``~/.peerhub/config/models.toml`` is only the DEFAULT spelling; under
    ``PEERHUB_CONFIG_HOME`` (e.g. redirected inside an Engram install's
    ``.engram/peerhub/config/``) the real file lives somewhere else, and a
    hardcoded spelling would send the user to the wrong path."""

    from peerhub.application.config_paths import resolve_global_config_home

    models_path = resolve_global_config_home().path / "models.toml"
    return (
        "Reconfigure via `peerhub node bind-profile --node-id <node> "
        "--profile-id cx.standard --model-id <model> --actor <you>` "
        "(workspace binding, highest priority), edit "
        f"{models_path} (global), or update peerhub's packaged "
        "model-defaults.toml (release default, lowest priority)."
    )


def _extract_model_from_argv(argv: tuple[str, ...]) -> str | None:
    """Best-effort recovery of the resolved model for error enrichment.

    Reads back the `-c model="..."` token this same adapter wrote into
    argv, rather than tracking parallel state -- avoids adding any
    per-call mutable state to an adapter instance that may be reused
    across concurrent dispatches.
    """

    for index, token in enumerate(argv):
        if token == "-c" and index + 1 < len(argv):
            candidate = argv[index + 1]
            if candidate.startswith('model="') and candidate.endswith('"'):
                return candidate[len('model="') : -1]
    return None


class CodexOutputDecoder:
    """Decoder for codex.cmd exec --json."""

    def __init__(self, plan: InvocationPlan | None = None) -> None:
        self._chunks: list[bytes] = []
        self._stdout_remainder = b""
        self._finalized = False
        self._events: list[DecoderEvent] = []
        self._assistant_texts: list[str] = []
        self._plan = plan

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
        payload: dict[str, JsonValue] = {
            "normalized_kind": normalized_kind,
            "evidence_source": "structured_vendor_output",
        }
        # A rejected invocation plan (e.g. codex's account no longer
        # supporting the resolved model) is exactly the moment provenance
        # matters most -- name the resolved model and how to override it
        # in the failure itself, not only in a separate `model explain`
        # command the caller has to think to run.
        if normalized_kind == "invocation_plan_rejected" and self._plan is not None:
            payload["resolved_model"] = (
                _extract_model_from_argv(self._plan.argv) or "(cli default)"
            )
            payload["override_hint"] = _override_hint()
        return self._append_event(
            DecoderEvent(
                kind=DecoderEventKind.VENDOR_ERROR,
                payload=payload,
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
            payload: dict[str, JsonValue] = {
                "normalized_kind": "invocation_plan_rejected",
                "evidence_source": "known_terminal_pattern",
            }
            if self._plan is not None:
                payload["resolved_model"] = (
                    _extract_model_from_argv(self._plan.argv) or "(cli default)"
                )
                payload["override_hint"] = _override_hint()
            self._events.append(
                DecoderEvent(
                    kind=DecoderEventKind.VENDOR_ERROR,
                    payload=payload,
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
        if profile.profile_id not in {p.profile_id for p in _CODEX_PROFILES}:
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

        if profile.profile_id not in {p.profile_id for p in _CODEX_PROFILES}:
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
            exec_argv = ("codex.cmd",)

        # Model resolution is centralized: the caller resolves a
        # ResolvedModelBinding (workspace binding > global config > packaged
        # default) and carries it on the request; this adapter only
        # translates it into codex's `-c model="..."` and `-c model_reasoning_effort="..."`
        # argv, never reads config itself. A codex account's own default model can be bumped
        # by the provider ahead of whatever codex CLI version is actually
        # installed (observed live: a fresh install's default resolved to a
        # model requiring "a newer version of Codex" than the installed
        # CLI, failing every dispatch with no override) -- CLI_DEFAULT
        # deliberately re-exposes that risk as an explicit, opted-in choice
        # rather than an accident, so it is logged loudly here.
        binding = request.model_binding
        model_flags: list[str] = []
        model_display = ""
        if binding.selection_mode is ModelSelectionMode.PINNED:
            assert binding.model_id is not None
            model_flags.extend(["-c", f'model="{binding.model_id}"'])
            model_display += f' -c model="{binding.model_id}"'
            if profile.supports_reasoning_effort and binding.reasoning_effort is not None:
                model_flags.extend(["-c", f'model_reasoning_effort="{binding.reasoning_effort}"'])
                model_display += f' -c model_reasoning_effort="{binding.reasoning_effort}"'
        else:
            # Registry/discovery create an unresolved dummy request solely
            # to learn argv[0]; it is not a dispatch and must not look like
            # an operator deliberately opted into cli_default. Every real
            # dispatch resolves before planning and therefore has a concrete
            # source layer, so its explicit cli_default choice remains loud.
            if binding.source_layer != "unresolved":
                logger.warning(
                    f"{profile.profile_id} dispatch is using selection_mode=cli_default: "
                    "codex's own account-side default model will be used, "
                    "which can drift ahead of the installed CLI version and "
                    "fail every dispatch with no override. Configure an "
                    "explicit pin (see `peerhub node bind-profile --help`) to "
                    "avoid this, unless this was deliberately chosen."
                )

        if request.requested_session_action == SessionAction.RESUME:
            if session is None or session.external_session_id is None:
                raise ValueError("external_session_id is required for RESUME")
            argv = (
                *exec_argv,
                "exec",
                "resume",
                "--skip-git-repo-check",
                *model_flags,
                "--json",
                session.external_session_id,
                prompt,
            )
            redacted_display = (
                "codex.cmd exec resume --skip-git-repo-check"
                f"{model_display} --json <session-id> <redacted>"
            )
        else:
            argv = (*exec_argv, "exec", "--skip-git-repo-check", *model_flags, "--json", prompt)
            redacted_display = (
                "codex.cmd exec --skip-git-repo-check"
                f"{model_display} --json <redacted>"
            )

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
        return CodexOutputDecoder(plan=plan)

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
