"""Real runtime adapters behind the Session Bridge RuntimeTarget (T1). Extension side only: Core never imports this package.

Semantics (TD-11): PrespawnError ONLY when the OS never created the process (or an argument/prompt was rejected before spawn);
once a process exists, any failure is reported as RuntimeTargetError / a timeout event (uncertain), never as pre-spawn.
Sessions: the adapter is NOT resumable (resume_session -> "unsupported", never synthesized); the Bridge falls back to a fresh
session generation with a Stream catch-up. interrupt/terminate/steer are not implemented and reported unsupported.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, cast

from peerhub.extensions.adapters.process import (DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_S, BoundedProcess, ProcessResult,
                                                 SpawnFailure, check_argv, run_bounded)
from peerhub.extensions.adapters.sanitize import Sanitizer, env_secrets
from peerhub.extensions.bridge import PrespawnError, RuntimeEvent, RuntimeTargetError

MAX_PROMPT_BYTES = 1_000_000
EXCERPT_CHARS = 300
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+[0-9A-Za-z.+-]*")
_WIN_EXT = {"claude": ["claude.cmd", "claude.exe"], "codex": ["codex.cmd", "codex.exe"], "agy": ["agy.exe", "agy.cmd"]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProviderSpec:
    kind: str
    binary: str
    resume_flag: str  # token looked up in `--help` output to report CLI-level resume support
    stdin_prompt: bool
    max_prompt_bytes: int = MAX_PROMPT_BYTES  # provider-specific inline limit (argv-borne prompts are far smaller)

    def argv(self, model: str | None, effort: str | None, prompt: str) -> list[str]:
        raise NotImplementedError

    def parse(self, stdout: str) -> str:
        """Return canonical response text or raise ValueError (garbage / vendor error)."""
        raise NotImplementedError


def _json_lines(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                v: object = json.loads(line)
            except ValueError:
                continue
            if isinstance(v, dict):
                out.append(cast("dict[str, Any]", v))  # json.loads objects always have str keys
    return out


class CcSpec(ProviderSpec):
    kind, binary, resume_flag, stdin_prompt = "cc", "claude", "--resume", True

    def argv(self, model: str | None, effort: str | None, prompt: str) -> list[str]:
        a = ["-p", "-", "--output-format", "stream-json", "--verbose"]
        if model:
            a += ["--model", model]
        if effort:
            a += ["--effort", effort]
        return a

    def parse(self, stdout: str) -> str:
        results = [e for e in _json_lines(stdout) if e.get("type") == "result"]
        if not results:
            raise ValueError("no result record in cc output")
        r = results[-1]
        if r.get("is_error") is not False:  # explicit completion needs an explicit is_error=false (absent/true/other = not success)
            raise ValueError("cc result is not explicitly is_error=false")
        if r.get("subtype", "success") != "success":
            raise ValueError(f"cc result subtype {r.get('subtype')!r} is not success")
        if not isinstance(r.get("result"), str):
            raise ValueError("cc result is not text")
        return cast(str, r["result"])


class CxSpec(ProviderSpec):
    kind, binary, resume_flag, stdin_prompt = "cx", "codex", "resume", True

    def argv(self, model: str | None, effort: str | None, prompt: str) -> list[str]:
        a = ["exec", "--skip-git-repo-check", "-s", "read-only"]
        if model:
            a += ["-m", model]
        if effort:
            a += ["-c", f"model_reasoning_effort={effort}"]
        return a + ["--json", "-"]

    def parse(self, stdout: str) -> str:
        events = _json_lines(stdout)
        for e in events:
            if e.get("type") in ("turn.failed", "error"):
                raise ValueError(f"cx reported {e.get('type')}")
        msgs = [e["item"]["text"] for e in events if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
                and e["item"].get("type") == "agent_message" and isinstance(e["item"].get("text"), str)]
        if not msgs:
            raise ValueError("no agent_message in cx output")
        last_msg = max(i for i, e in enumerate(events) if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
                       and e["item"].get("type") == "agent_message")
        if not any(e.get("type") == "turn.completed" for e in events[last_msg + 1:]):
            raise ValueError("cx output has no turn.completed after the last agent_message (incomplete)")
        return "\n".join(msgs)


class AgSpec(ProviderSpec):
    kind, binary, resume_flag, stdin_prompt = "ag", "agy", "--conversation", False
    max_prompt_bytes = 30_000 if sys.platform == "win32" else 120_000  # prompt travels in argv: CreateProcess ~32k chars / MAX_ARG_STRLEN

    def argv(self, model: str | None, effort: str | None, prompt: str) -> list[str]:
        a = ["-p", prompt, "--output-format", "json"]
        if model:
            a += ["--model", model]
        if effort:
            a += ["--effort", effort]
        return a

    def parse(self, stdout: str) -> str:
        try:
            v: object = json.loads(stdout.strip())  # strict: the WHOLE stdout is exactly one flat JSON object (no scanning for fragments)
        except ValueError:
            raise ValueError("ag output is not a single JSON object") from None
        if not isinstance(v, dict):
            raise ValueError("no flat JSON response in ag output")
        v = cast("dict[str, Any]", v)
        if not isinstance(v.get("response"), str):
            raise ValueError("no flat JSON response in ag output")
        if v.get("is_error") not in (None, False) or v.get("error") not in (None, False, "", {}):
            raise ValueError("ag reported an error")
        return cast(str, v["response"])


SPECS: dict[str, ProviderSpec] = {s.kind: s for s in (CcSpec(), CxSpec(), AgSpec())}


def resolve_binary(spec: ProviderSpec) -> list[str] | None:
    names = _WIN_EXT[spec.binary] if sys.platform == "win32" else [spec.binary]
    for n in names:
        p = shutil.which(n)
        if p:
            if Path(p).suffix.lower() in (".cmd", ".bat"):
                from peerhub.extensions.adapters.binary_resolution import resolve_direct_binary
                direct = resolve_direct_binary(Path(p))
                if direct:
                    return direct
            return [p]
    return None


class CliRuntimeTarget:
    """RuntimeTarget over one vendor CLI. `command` overrides binary resolution (tests use python fake CLIs)."""

    resumable = False
    supports_interrupt = False
    supports_terminate = False
    supports_steer = False

    def __init__(self, kind: str, workspace: str | Path, *, model: str | None = None, effort: str | None = None,
                 profile: str | None = None, silence_timeout_s: float | None = None,
                 command: Sequence[str] | None = None, timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = DEFAULT_MAX_BYTES,
                 env_extra: Mapping[str, str] | None = None, extra_secrets: Iterable[str] = (),
                 on_output: Callable[[str], None] | None = None) -> None:
        if kind not in SPECS:
            raise ValueError(f"unknown provider kind {kind!r}")
        if silence_timeout_s is not None and (not math.isfinite(silence_timeout_s) or silence_timeout_s <= 0):
            raise ValueError("silence timeout must be finite and positive")
        if profile is not None:
            from peerhub.extensions.adapters.profiles import qualified_profile, resolve_profile
            profile = qualified_profile(kind, profile)
            profile_model, profile_effort = resolve_profile(kind, profile, workspace)
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
        self.runtime_kind = kind
        self._spec = SPECS[kind]
        self._workspace = str(workspace)
        self._model, self._effort = model, effort
        self._profile, self._silence_timeout_s = profile, silence_timeout_s
        self._command = list(command) if command is not None else None
        self._timeout_s, self._max_bytes = timeout_s, max_bytes
        self._on_output = on_output
        self._env = {**os.environ, **(env_extra or {})}
        self._san = Sanitizer(secrets=[*env_secrets(self._env), *extra_secrets])
        self.evidence: deque[dict[str, Any]] = deque(maxlen=100)  # sanitized and bounded

    # ---- helpers
    def _note(self, event: str, **kw: Any) -> None:
        self.evidence.append({"ts": _now(), "provider": self.runtime_kind, "event": event, **kw})

    def _base(self) -> list[str] | None:
        return list(self._command) if self._command is not None else resolve_binary(self._spec)

    def _run(self, args: list[str], timeout_s: float) -> ProcessResult:
        base = self._base()
        if base is None:
            raise SpawnFailure(f"{self._spec.binary} executable not found")
        return run_bounded([*base, *args], stdin=None, cwd=self._workspace, env=self._env,
                           timeout_s=timeout_s, max_bytes=self._max_bytes)

    @staticmethod
    def _text(b: bytes, san: Sanitizer, limit: int = EXCERPT_CHARS) -> str:
        return san.clean(b.decode("utf-8", "replace"), limit)

    # ---- discovery (LIVE-004/005): never spends model tokens
    def discover(self, timeout_s: float = 20.0) -> dict[str, Any]:
        """Observed version + capabilities as timestamped evidence. Unavailable/unsupported is reported, never synthesized."""
        caps: dict[str, dict[str, Any]] = {c: {"status": "unsupported", "source": "adapter", "reason": "not implemented by this adapter"}
                                 for c in ("interrupt", "terminate", "steer")}
        info: dict[str, Any] = {"provider": self.runtime_kind, "observed_at": _now(), "available": False, "version": None,
                                "version_line": None, "reason": None}
        try:
            r = self._run(["--version"], timeout_s)
        except SpawnFailure as e:
            info["reason"] = self._san.clean(f"cannot spawn: {e}", EXCERPT_CHARS)
            info["capabilities"] = {c: {"status": "unavailable", "source": "discovery", "reason": "provider CLI not available"}
                                    for c in ("resume", *caps)}
            self._note("discover", available=False)
            return info
        first = self._text(r.stdout or r.stderr, self._san, 120).strip().splitlines()[:1]
        line = first[0] if first else ""
        m = _VERSION_RE.search(line)
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
        caps["resume"] = {"status": "unsupported", "source": "adapter", "cli_status": cli_resume,
                          "reason": "adapter does not map vendor sessions across restarts",
                          "fallback": "fresh session generation with Stream catch-up"}
        info["capabilities"] = caps
        self._note("discover", available=ok, version=info["version"])
        return info

    # ---- RuntimeTarget
    def fingerprint(self) -> str:
        return f"adapter:{self.runtime_kind}:v1"

    def binding(self) -> str:
        binding = f"{self._model or 'default'}/{self._effort or 'default'}"
        return f"{self._profile}/{binding}" if self._profile is not None else binding

    def create_session(self) -> str:
        return f"{self.runtime_kind}-local-{uuid.uuid4().hex}"  # a local handle: one process per delivery, no vendor call

    def resume_session(self, external_session_id: str) -> str:
        self._note("resume_unsupported")
        return "unsupported"

    @staticmethod
    def _prompt(record: Any, catch_up: list[Any]) -> str:
        def txt(r: Any) -> str:
            b = getattr(r, "body", None)
            return b if isinstance(b, str) else json.dumps(b, ensure_ascii=False, sort_keys=True)
        parts: list[str] = []
        if catch_up:
            parts.append("Earlier context:\n" + "\n".join(f"[{getattr(r, 'author_peer_id', '?')}] {txt(r)}" for r in catch_up))  # never truncated here: the Bridge's injected budget decides
        parts.append(txt(record))
        return "\n\n".join(parts)

    def deliver(self, external_session_id: str, record: Any, catch_up: list[Any]) -> Iterable[RuntimeEvent]:
        prompt = self._prompt(record, catch_up)
        san = self._san.with_prompt(prompt)
        # ---- genuine pre-spawn failures: nothing was created
        n_bytes, limit = len(prompt.encode("utf-8")), min(MAX_PROMPT_BYTES, self._spec.max_prompt_bytes)
        if n_bytes > limit:
            raise PrespawnError(f"prompt is {n_bytes} bytes, exceeding the {self.runtime_kind} inline limit of {limit} bytes "
                                f"(catch-up history is never truncated silently; lower the catch-up budget)")
        if not Path(self._workspace).is_dir():
            raise PrespawnError("workspace is not a directory")
        base = self._base()
        if base is None:
            raise PrespawnError(f"{self._spec.binary} executable not found")
        argv = [*base, *self._spec.argv(self._model, self._effort, prompt)]
        try:
            check_argv(argv)
        except ValueError as e:
            raise PrespawnError(san.clean(str(e), EXCERPT_CHARS)) from e
        pending = bytearray()
        emitted = False

        def stream_stdout(chunk: bytes) -> None:
            nonlocal emitted
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, tail = pending.partition(b"\n")
                pending[:] = tail
                events = _json_lines(line.decode("utf-8", "replace"))
                if not events:
                    continue
                event = events[0]
                text = None
                if self.runtime_kind == "cx" and event.get("type") == "item.completed":
                    item = event.get("item", {})
                    if isinstance(item, dict) and cast("dict[str, Any]", item).get("type") == "agent_message":
                        text = cast("dict[str, Any]", item).get("text")
                elif self.runtime_kind == "cc":
                    if event.get("type") == "assistant":
                        message = event.get("message", {})
                        content: Any = cast("dict[str, Any]", message).get("content", []) if isinstance(message, dict) else []
                        if isinstance(content, list):
                            parts: list[str] = []
                            for p in cast("list[Any]", content):
                                if isinstance(p, dict):
                                    block = cast("dict[str, Any]", p)
                                    piece = block.get("text")
                                    if block.get("type") == "text" and isinstance(piece, str):
                                        parts.append(piece)
                            text = "".join(parts)
                    elif not emitted and event.get("type") == "result" and event.get("is_error") is False:
                        text = event.get("result")
                elif self.runtime_kind == "ag" and event.get("is_error") not in (True,):
                    text = event.get("response")
                if isinstance(text, str) and text and self._on_output is not None:
                    self._on_output(self._san.clean(text))
                    emitted = True

        proc = BoundedProcess(argv, stdin=prompt.encode("utf-8") if self._spec.stdin_prompt else None, cwd=self._workspace,
                              env=self._env, timeout_s=self._timeout_s, max_bytes=self._max_bytes,
                              silence_timeout_s=self._silence_timeout_s,
                              on_stdout=stream_stdout if self._on_output else None)
        try:
            proc.start()
        except SpawnFailure as e:
            self._note("prespawn_failure")
            raise PrespawnError(san.clean(f"cannot spawn {self._spec.binary}: {e}", EXCERPT_CHARS)) from e
        # ---- a process exists from here on: every failure is uncertain, never pre-spawn
        finished = False
        try:
            yield ("started", f"{self.runtime_kind}:{proc.pid}:{uuid.uuid4().hex[:8]}")
            res = proc.wait()
            finished = True
        finally:
            if not finished:  # consumer stopped iterating (fenced/closed): never leave an orphan process
                proc.abort()
                self._note("aborted", pid=proc.pid)
        self._note("exited", pid=res.pid, returncode=res.returncode, timed_out=res.timed_out,
                   silence_timed_out=res.silence_timed_out,
                   output_exceeded=res.output_exceeded, duration_s=round(res.duration_s, 3))
        if res.silence_timed_out:
            raise RuntimeTargetError(f"{self.runtime_kind} produced no stdout/stderr for {self._silence_timeout_s} seconds; process killed")
        if res.timed_out:
            yield ("timeout", self._timeout_s)
            return
        if res.output_exceeded:
            raise RuntimeTargetError(f"{self.runtime_kind} output exceeded {self._max_bytes} bytes; process killed")
        if res.returncode != 0:
            raise RuntimeTargetError(f"{self.runtime_kind} exited {res.returncode}: " + self._text(res.stderr or res.stdout, san))
        try:
            text = self._spec.parse(res.stdout.decode("utf-8", "replace"))
        except ValueError as e:
            raise RuntimeTargetError(san.clean(f"unparseable {self.runtime_kind} output: {e}", EXCERPT_CHARS)) from e
        yield ("terminal", {"response": self._san.clean(text)})  # secrets redacted; the prompt may legitimately be quoted
