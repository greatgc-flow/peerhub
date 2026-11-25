"""Real runtime adapters behind the Session Bridge RuntimeTarget (T1). Extension side only: Core never imports this package.

Semantics (TD-11): PrespawnError ONLY when the OS never created the process (or an argument/prompt was rejected before spawn);
once a process exists, any failure is reported as RuntimeTargetError / a timeout event (uncertain), never as pre-spawn.
Sessions: native resume is explicit and supported for cc/cx/ag with a caller-supplied vendor id.
Fresh deliveries expose candidate ids for the Bridge to persist. terminate kills the active local process tree;
interrupt/steer are unsupported.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from peerhub.extensions.adapters import ag, ag_transport, cc, common, registry
from peerhub.extensions.adapters.event_parser import IncrementalEventParser
from peerhub.extensions.adapters.live_output import LiveOutput
from peerhub.extensions.adapters.process import (DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_S, BoundedProcess, ProcessResult,
                                                 SpawnFailure, check_argv)
from peerhub.extensions.adapters.sanitize import Sanitizer, env_secrets, path_secrets
from peerhub.extensions.process_tree import process_created
from peerhub.extensions.runtime_contract import PrespawnError, RuntimeEvent, RuntimeTargetError

if TYPE_CHECKING:
    from peerhub.extensions.accounts import AccountRef, AccountSnapshot


_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CliRuntimeTarget:
    """RuntimeTarget over one vendor CLI. `command` overrides binary resolution (tests use python fake CLIs)."""

    supports_interrupt = False
    supports_terminate = True
    supports_steer = False

    def __init__(self, kind: str, workspace: str | Path, *, model: str | None = None, effort: str | None = None,
                 profile: str | None = None, silence_timeout_s: float | None = None, writable: bool = False,
                 command: Sequence[str] | None = None, timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = DEFAULT_MAX_BYTES,
                 env_extra: Mapping[str, str] | None = None, extra_secrets: Iterable[str] = (),
                 on_output: Callable[[str], None] | None = None,
                 account: AccountRef | None = None, env_unset: Iterable[str] = (), notice_alias: bool = False) -> None:
        if kind not in registry.SPECS:
            raise ValueError(f"unknown provider kind {kind!r}")
        if account is not None and kind not in ("cc", "cx"):
            raise PrespawnError("account isolation supports cc/cx only; ag authentication lives in the OS keyring")
        if silence_timeout_s is not None and (not math.isfinite(silence_timeout_s) or silence_timeout_s <= 0):
            raise ValueError("silence timeout must be finite and positive")
        if profile is not None:
            from peerhub.extensions.adapters.profiles import qualified_profile, resolve_profile
            profile = qualified_profile(kind, profile)
            profile_model, profile_effort = resolve_profile(kind, profile, workspace, notice_alias=notice_alias)
            model = model if model is not None else profile_model
            effort = effort if effort is not None else profile_effort
        for v in (model, effort):
            if v is not None and not _MODEL_RE.match(v):
                raise ValueError("model/effort contains unsupported characters")
        if kind == "ag" and effort is not None:
            if effort not in ("low", "medium", "high", "xhigh", "max"):
                raise ValueError("ag effort must be low, medium, high, xhigh or max")
            # AG advertises effort-qualified model IDs. Do not send contradictory
            # settings and silently let vendor precedence choose a different tier.
            suffix = model.rsplit("-", 1)[-1] if model else None
            if suffix in ("low", "medium", "high") and suffix != effort:
                raise ValueError("ag effort conflicts with the effort-qualified model")
        self._resume_sessions: dict[str, str] = {}  # Bridge-provided bindings only, never inferred from output
        self.runtime_kind = kind
        self._spec = registry.SPECS[kind]
        self._workspace = str(workspace)
        self._model, self._effort = model, effort
        self._profile, self._silence_timeout_s = profile, silence_timeout_s
        self._writable = writable  # peers are read-only unless the caller explicitly asks for write access
        self._command = list(command) if command is not None else None
        self._timeout_s, self._max_bytes = timeout_s, max_bytes
        self._on_output = on_output
        self._active_lock = threading.Lock()
        self._active: dict[str, BoundedProcess] = {}
        self._account = account
        self._parent_env = dict(os.environ)
        self._env_extra = dict(env_extra or {})
        self._extra_secrets = tuple(extra_secrets)
        # #374: batch ids describe the ask process only; a nested ask made by the provider is not a task of that batch.
        unset = {name.upper() for name in env_unset} | {"PEERHUB_BATCH_ID", "PEERHUB_BATCH_TASK"}
        self._env_unset = unset
        merged = {**self._parent_env, **self._env_extra}
        self._child_env: dict[str, str] | None = (
            None if account is not None else {name: value for name, value in merged.items() if name.upper() not in unset})
        self.env_scrubbed: list[str] = []
        self._san = Sanitizer(secrets=[*env_secrets(merged), *self._extra_secrets])
        self.evidence: deque[dict[str, Any]] = deque(maxlen=100)  # sanitized and bounded
        self._ag_transport: ag_transport.AgTransport | None = None  # decided before submission, consumed by the next delivery

    @property
    def _env(self) -> dict[str, str]:
        if self._child_env is None:
            raise PrespawnError("account environment has not been bound")
        return self._child_env

    def bind_account_env(self, snapshot: AccountSnapshot) -> None:
        from peerhub.extensions.accounts import ROOT_VARS, scrub_names

        if self._account is None or snapshot.slot_id != self._account.slot_id or snapshot.provider != self.runtime_kind:
            raise PrespawnError("account snapshot does not match target identity")
        root_var = ROOT_VARS[snapshot.provider]
        merged = {**self._parent_env, **self._env_extra}
        self.env_scrubbed = scrub_names(self._parent_env, snapshot.provider)
        removed = {name.upper() for name in scrub_names(merged, snapshot.provider)} | self._env_unset
        # Normalize the root variable's spelling so mixed-case keys cannot win.
        self._child_env = {name: value for name, value in merged.items()
                           if name.upper() not in removed and name.upper() != root_var}
        self._child_env[root_var] = str(snapshot.root_realpath)
        self._san = Sanitizer(secrets=[*env_secrets(self._parent_env), *env_secrets(merged),
                                      *self._extra_secrets, *path_secrets(str(snapshot.root_realpath))])

    def execution_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {"env_scrubbed": list(self.env_scrubbed)} if self._account is not None else {}
        if self.runtime_kind == "ag":
            try:
                decision = self._ag_transport_decision()
            except Exception as e:  # evidence must never fail the delivery; the decision fails it closed instead
                decision = ag_transport.AgTransport("undetermined", "help", None, f"cannot determine agy stdin support: {type(e).__name__}")
            self._ag_transport = decision
            metadata.update(decision.metadata())
        return metadata

    def _ag_transport_decision(self) -> ag_transport.AgTransport:
        return ag_transport.select_transport(self._base, self._env, self._workspace, self._spec.binary)

    @property
    def can_resume(self) -> bool:
        return self._spec.can_resume

    @property
    def resumable(self) -> bool:
        """Existing RuntimeTarget capability boundary, independent of caller intent."""
        return self.can_resume

    # ---- helpers
    def _note(self, event: str, **kw: Any) -> None:
        self.evidence.append({"ts": _now(), "provider": self.runtime_kind, "event": event, **kw})

    def _base(self) -> list[str] | None:
        return list(self._command) if self._command is not None else registry.resolve_binary(self._spec)

    def _run(self, args: list[str], timeout_s: float) -> ProcessResult:
        base = self._base()
        if base is None:
            raise SpawnFailure(common.executable_not_found(self._spec.binary))
        return common.probe_cli(base, args, env=self._env, cwd=self._workspace, timeout_s=timeout_s, max_bytes=self._max_bytes)

    def validate_execution_policy(self) -> None:
        """Fail closed before execution when the provider CLI cannot enforce the requested write policy (#140)."""
        if self.runtime_kind == "ag" and not self._writable:
            raise ag.AgRequiresWritableError()

    # ---- discovery (LIVE-004/005): never spends model tokens
    def discover(self, timeout_s: float = 20.0) -> dict[str, Any]:
        """Observed version + capabilities as timestamped evidence. Unavailable/unsupported is reported, never synthesized."""
        caps: dict[str, dict[str, Any]] = {c: {"status": "unsupported", "source": "adapter", "reason": "not implemented by this adapter"}
                                 for c in ("interrupt", "steer")}
        caps["terminate"] = {"status": "supported", "source": "adapter",
                             "reason": "kills active local process tree and observes exit"}
        info: dict[str, Any] = {"provider": self.runtime_kind, "observed_at": _now(), "available": False, "version": None,
                                "version_line": None, "reason": None}
        try:
            r = self._run(["--version"], timeout_s)
        except SpawnFailure as e:
            info["reason"] = self._san.clean(f"cannot spawn: {e}", common.EXCERPT_CHARS)
            info["capabilities"] = {c: {"status": "unavailable", "source": "discovery", "reason": "provider CLI not available"}
                                    for c in ("resume", *caps)}
            self._note("discover", available=False)
            return info
        first = common.provider_excerpt(r.stdout or r.stderr, self._san, 120).strip().splitlines()[:1]
        line = first[0] if first else ""
        m = common.VERSION_RE.search(line)
        ok = r.returncode == 0 and not r.timed_out
        info.update(available=ok, version=m.group(0) if m else None, version_line=line or None)
        if not ok:
            info["reason"] = "timeout" if r.timed_out else f"--version exit {r.returncode}"
        cli_resume = "unavailable"
        if ok:
            try:
                h = self._run(["--help"], timeout_s)
                cli_resume = "supported" if h.returncode == 0 and self._spec.resume_flag in (h.stdout + h.stderr).decode("utf-8", "replace") else "unsupported"
            except SpawnFailure:
                cli_resume = "unavailable"
        caps["resume"] = {"status": "supported" if self.resumable else "unsupported", "source": "adapter", "cli_status": cli_resume,
                          "reason": "explicit vendor session binding" if self.resumable else "native resume is disabled",
                          "fallback": "fresh session generation with Stream catch-up"}
        info["capabilities"] = caps
        self._note("discover", available=ok, version=info["version"])
        return info

    # ---- RuntimeTarget
    def fingerprint(self) -> str:
        return f"adapter:{self.runtime_kind}:v2"

    def binding(self) -> str:
        binding = f"{self._model or 'default'}/{self._effort or 'default'}"
        binding = f"{self._profile}/{binding}" if self._profile is not None else binding
        # The provider cwd and write mode decide whether a native session may be reused; keep the path itself out of Records.
        scope = f"{os.path.normcase(str(Path(self._workspace).resolve()))}|{'w' if self._writable else 'r'}"
        if self._account is not None:
            scope += f"|acct:{self._account.slot_id}"
        return f"{binding}#{hashlib.sha256(scope.encode('utf-8')).hexdigest()[:12]}"

    def session_binding(self) -> dict[str, Any]:
        """Effective creation metadata; no cwd, credentials or vendor identity."""
        return dict(profile=self._profile, model=self._model, effort=self._effort, writable=self._writable)

    def quota_pools(self) -> list[str] | None:
        """Store quota is observed for the default account and known model pools."""
        if self._account is not None:
            return None
        if self.runtime_kind == "cx":
            scopes = ["X-7D", "X-5H"]
        elif self.runtime_kind == "cc":
            model = self._model or ""
            if model.startswith(("fable", "claude-fable")):
                scopes = ["C-7D", "C-5H", "F-7D"]
            else:
                scopes = ["C-7D", "C-5H"]  # the all-models pools also apply to CLI-default models
        else:
            from peerhub.extensions.adapters.profiles import inspect_model_pools
            try:
                model_pools = inspect_model_pools(self._model or "", self._workspace)
            except (OSError, ValueError):
                return None
            if not model_pools or not model_pools["verified"]:
                return None
            scopes = sorted(model_pools["pools"], key=lambda scope: (not scope.endswith("7D"), scope))
        return [f"quota:{self.runtime_kind}:{scope}" for scope in scopes]

    def create_session(self) -> str:
        return f"{self.runtime_kind}-local-{uuid.uuid4().hex}"  # a local handle: one process per delivery, no vendor call

    def resume_session(self, external_session_id: str, *, vendor_session_id: str | None = None) -> str:
        """Bind an explicit vendor id; the one-argument Bridge call remains valid."""
        if not self.resumable:
            self._note("resume_unsupported")
            return "unsupported"
        if vendor_session_id is None:
            return "missing"
        if common.session_id(vendor_session_id) is None:
            return "rejected"
        with self._active_lock:
            if external_session_id in self._active:
                return "rejected"
            self._resume_sessions[external_session_id] = vendor_session_id
        return "ok"

    def terminate(self, external_session_id: str) -> None:
        with self._active_lock:
            proc = self._active.get(external_session_id)
            if proc is None:
                raise RuntimeTargetError("nothing running")
        # Keep the process reference, not the registry lock, during the bounded OS wait.
        # A racing session replacement cannot redirect this cancellation to a new process.
        try:
            proc.terminate()
        except OSError as e:
            self._note("terminate_failed", pid=proc.pid, error=str(e), termination_confirmed=False)
            raise RuntimeTargetError(str(e), termination_confirmed=False) from e
        self._note("terminated", pid=proc.pid, termination_confirmed=True)

    def deliver(self, external_session_id: str, record: Any, catch_up: list[Any]) -> Iterable[RuntimeEvent]:
        prompt = common.render_prompt(record, catch_up)
        # ---- genuine pre-spawn failures: nothing was created
        self.validate_execution_policy()
        stream = False
        if self.runtime_kind == "ag":
            surrogate = next((i for i, ch in enumerate(prompt) if "\ud800" <= ch <= "\udfff"), None)
            if surrogate is not None:
                raise PrespawnError(f"prompt contains a lone surrogate at index {surrogate}")
            decision, self._ag_transport = self._ag_transport or self._ag_transport_decision(), None
            if decision.transport not in ("stream", "argv"):
                raise PrespawnError(self._san.with_prompt(prompt).clean(decision.error or "cannot determine agy stdin support", common.EXCERPT_CHARS))
            stream = decision.transport == "stream"
        n_bytes = len(prompt.encode("utf-8"))
        limit = common.MAX_PROMPT_BYTES if stream else min(common.MAX_PROMPT_BYTES, self._spec.max_prompt_bytes)
        if n_bytes > limit:
            prompt = common.render_prompt(record, catch_up, limit=limit)
            n_bytes = len(prompt.encode("utf-8"))
        if n_bytes > limit:
            raise PrespawnError(f"prompt is {n_bytes} bytes, exceeding the {self.runtime_kind} inline limit of {limit} bytes "
                                f"(catch-up history is never truncated silently; lower the catch-up budget)")
        san = self._san.with_prompt(prompt)
        if not Path(self._workspace).is_dir():
            raise PrespawnError("workspace is not a directory")
        base = self._base()
        if base is None:
            raise PrespawnError(common.executable_not_found(self._spec.binary))
        with self._active_lock:
            vendor_session_id = self._resume_sessions.get(external_session_id)
        if stream:
            argv = [*base, *ag.AgSpec.stream_argv(self._model, self._effort, self._writable, vendor_session_id)]
            stdin_payload: bytes | None = ag.encode_user_line(prompt)
        else:
            argv = [*base, *self._spec.argv(self._model, self._effort, prompt, self._writable, vendor_session_id)]
            stdin_payload = prompt.encode("utf-8") if self._spec.stdin_prompt else None
        try:
            check_argv(argv)
        except ValueError as e:
            raise PrespawnError(san.clean(str(e), common.EXCERPT_CHARS)) from e

        parser = IncrementalEventParser(self.runtime_kind, max_bytes=self._max_bytes)
        proc: BoundedProcess | None = None

        def mark_display_truncated() -> None:
            if proc is not None:
                proc.mark_display_truncated()

        def emit(text: str) -> bool:
            if self._on_output is None:
                return False
            try:
                self._on_output(self._san.clean(text))
                return True
            except Exception:
                self._on_output = None
                display.mark_truncated()
                return False

        display = LiveOutput(self.runtime_kind, stream, self._max_bytes, emit, mark_display_truncated)

        def display_callback(chunk: bytes) -> None:
            if self._on_output is not None:
                try:
                    display.feed(chunk)
                except Exception:
                    self._on_output = None
                    display.mark_truncated()
            else:
                display.mark_truncated()

        proc = BoundedProcess(argv, stdin=stdin_payload, cwd=self._workspace,
                              env=self._env, timeout_s=self._timeout_s, max_bytes=self._max_bytes,
                              silence_timeout_s=self._silence_timeout_s,
                              on_stdout=None if stream else parser.feed,
                              on_display=display_callback if self._on_output is not None else None)
        try:
            # Publish only after spawn; serialize registration with external cancellation.
            with self._active_lock:
                if external_session_id in self._active:
                    raise PrespawnError("delivery already running for session")
                proc.start()
                self._active[external_session_id] = proc
        except SpawnFailure as e:
            self._note("prespawn_failure")
            raise PrespawnError(san.clean(f"cannot spawn {self._spec.binary}: {e}", common.EXCERPT_CHARS)) from e
        # ---- a process exists from here on: every failure is uncertain, never pre-spawn
        finished = False
        try:
            try:
                created = process_created(proc.pid)
            except Exception:  # process identity is best-effort evidence after spawn
                created = None
            yield ("started", f"{self.runtime_kind}:{proc.pid}:{uuid.uuid4().hex[:8]}",
                   {"provider_pid": proc.pid, "provider_created": created})
            res = proc.wait()
            parser.close()
            finished = True
        finally:
            if not finished:  # consumer stopped iterating (fenced/closed): never leave an orphan process
                proc.abort()
                self._note("aborted", pid=proc.pid, termination_confirmed=proc.termination_confirmed)
            with self._active_lock:
                if self._active.get(external_session_id) is proc:
                    del self._active[external_session_id]
        if parser.oversized_count:
            self._note("event_counts", oversized_event_count=parser.oversized_count)
        display_truncated = res.display_truncated or display.truncated
        self._note("exited", pid=res.pid, returncode=res.returncode, timed_out=res.timed_out,
                   silence_timed_out=res.silence_timed_out,
                   output_exceeded=res.output_exceeded, output_truncated=res.output_truncated,
                   display_truncated=display_truncated,
                   parser_failed=res.parser_failed or parser.parser_failed,
                   aborted=res.aborted,
                   termination_confirmed=res.termination_confirmed,
                   duration_s=round(res.duration_s, 3))

        if res.aborted:
            raise RuntimeTargetError(f"{self.runtime_kind} delivery aborted by termination request",
                                     termination_confirmed=res.termination_confirmed)
        if res.silence_timed_out:
            raise RuntimeTargetError(f"{self.runtime_kind} produced no stdout/stderr for {self._silence_timeout_s} seconds; process killed",
                                     termination_confirmed=res.termination_confirmed)
        if res.timed_out:
            yield ("timeout", self._timeout_s, res.termination_confirmed)
            return
        if res.drain_timed_out:
            raise RuntimeTargetError(f"{self.runtime_kind} output drain timed out; outcome indeterminate",
                                     termination_confirmed=res.termination_confirmed)
        if res.parser_failed or parser.parser_failed:
            raise RuntimeTargetError(f"{self.runtime_kind} parser failed during output drain; outcome indeterminate",
                                     termination_confirmed=res.termination_confirmed)
        raw_text = res.stdout.decode("utf-8", "replace")
        stdout_text, stream_validation = (
            ag.stream_stdout(res, san, len(stdin_payload or b"")) if stream else (raw_text, None))
        refusal: dict[str, Any] | None = None
        if (not parser.has_tool_calls and not parser.has_success
                and not parser.refusal_evidence_incomplete and parser.oversized_count == 0):
            refusal = parser.refusal_event
            if self.runtime_kind == "ag":
                refusal = ag.read_only_refusal(res, parser.saw_stdout, self._writable)

        if refusal is not None:
            refusal_payload: dict[str, Any] = {
                "outcome": "NOT_EXECUTED",
                "reason": refusal["reason"],
                "message": refusal.get("message", ""),
                "reset_time": refusal.get("reset_time"),
                "termination_confirmed": res.termination_confirmed,
            }
            self._note("refusal_detected", pid=res.pid, returncode=res.returncode,
                       reason=refusal["reason"], reset_time=refusal.get("reset_time"),
                       termination_confirmed=res.termination_confirmed)
            yield ("refusal", refusal_payload)
            return

        if res.returncode != 0:
            excerpt = common.provider_excerpt(res.stderr or res.stdout, san)
            if parser.terminal_error_summary:
                term_err = san.clean(parser.terminal_error_summary, 200)
                if term_err and term_err not in excerpt:
                    excerpt = san.clean(f"{term_err} | {excerpt}" if excerpt else term_err, common.EXCERPT_CHARS)
            raise RuntimeTargetError(f"{self.runtime_kind} exited {res.returncode}: " + excerpt,
                                     termination_confirmed=res.termination_confirmed)
        try:
            if res.stdout_truncated or parser.truncated or parser.oversized_count > 0:
                if self.runtime_kind == "ag":
                    raw_head = res.stdout.lstrip()
                    if (raw_head and not raw_head.startswith(b"{")) or parser.ag_invalid_top_level:
                        raise ValueError("ag output is not a well-formed top-level object")
                text = parser.parse()
            else:
                text = self._spec.parse(stdout_text)
        except ValueError as e:
            err_msg = str(e)
            if parser.terminal_error_summary:
                term_err = san.clean(parser.terminal_error_summary, 200)
                if term_err and term_err not in err_msg:
                    err_msg = f"{err_msg} ({term_err})"
            raise RuntimeTargetError(san.clean(f"unparseable {self.runtime_kind} output: {err_msg}", common.EXCERPT_CHARS)) from e
        cleaned_response = self._san.clean(text)
        payload: dict[str, Any] = {"response": cleaned_response}  # secrets redacted; the prompt may legitimately be quoted
        if self._account is not None:
            payload["env_scrubbed"] = list(self.env_scrubbed)
        if not cleaned_response.strip():
            payload["empty_response"] = True
            payload["task_effects"] = "unverified"
            denied = ag.denied_action_names(stdout_text) if self.runtime_kind == "ag" else []
            if denied:
                payload["denied_actions"] = denied
        if self.runtime_kind == "cc":
            # Redact first, then cut to 120 chars; surfaced even with reply text so allow-rule mismatches are never silent (#303).
            # The incrementally parsed final result survives stdout truncation (the retained window may have evicted it).
            cc_denied = [san.clean(d, cc.CC_DENIAL_CHARS) for d in cc.cc_result_denials(parser.cc_results[-1] if parser.cc_results else None)]
            if cc_denied:
                payload["denied_actions"] = cc_denied
        if res.output_truncated or parser.truncated:
            payload["output_truncated"] = True
            if res.stdout_truncated or parser.truncated:
                usage = parser.parse_usage()
                vendor_session_id = parser.parse_session_id()
            else:
                usage = self._spec.parse_usage(stdout_text)
                vendor_session_id = self._spec.parse_session_id(stdout_text)
        else:
            usage = self._spec.parse_usage(stdout_text)
            vendor_session_id = self._spec.parse_session_id(stdout_text)
        if parser.oversized_count:
            payload["oversized_event_count"] = parser.oversized_count
        if display_truncated:
            payload["display_truncated"] = True
        if usage is not None:
            payload["usage"] = usage
        from peerhub.extensions.adapters.telemetry import parse_provider_usage
        from peerhub.extensions.usage_normalization import unknown_usage
        payload["provider_telemetry"] = (
            unknown_usage("output_incomplete") if res.stdout_truncated or parser.truncated
            else parse_provider_usage(self.runtime_kind, stdout_text))
        if stream_validation is not None:  # decision D3 (#369): delivered, but usage is not derived from such a stream
            payload.pop("usage", None)
            payload["provider_telemetry"] = unknown_usage(stream_validation["reason"])
            payload["ag_stream_validation"] = stream_validation
        if vendor_session_id is not None:
            source = {"cc": "cc.result.session_id", "cx": "cx.thread.started",
                      "ag": "ag.conversation_id"}[self.runtime_kind]
            payload["vendor_session"] = {"id": vendor_session_id, "source": source}
        yield ("terminal", payload)
