"""Real runtime adapters behind the Session Bridge RuntimeTarget (T1). Extension side only: Core never imports this package.

Semantics (TD-11): PrespawnError ONLY when the OS never created the process (or an argument/prompt was rejected before spawn);
once a process exists, any failure is reported as RuntimeTargetError / a timeout event (uncertain), never as pre-spawn.
Sessions: native resume is explicit and supported for cc/cx/ag with a caller-supplied vendor id.
Fresh deliveries expose candidate ids for the Bridge to persist. terminate kills the active local process tree;
interrupt/steer are unsupported.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence, cast

from peerhub.extensions.adapters import ag, cc, common, cx
from peerhub.extensions.adapters.process import (DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_S, BoundedProcess, ProcessResult,
                                                 SpawnFailure, check_argv)
from peerhub.extensions.adapters.sanitize import Sanitizer, env_secrets, path_secrets
from peerhub.extensions.adapters.telemetry import CC_EVENT_TYPES
from peerhub.extensions.runtime_contract import PrespawnError, RuntimeEvent, RuntimeTargetError

if TYPE_CHECKING:
    from peerhub.extensions.accounts import AccountRef, AccountSnapshot


_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_WIN_EXT = {"claude": ["claude.cmd", "claude.exe"], "codex": ["codex.cmd", "codex.exe"], "agy": ["agy.exe", "agy.cmd"]}
MAX_EVENT_BYTES = 262_144  # 256 KB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IncrementalEventParser:
    """Bounded incremental JSONL event parser for adapter stdout draining.

    Lines exceeding MAX_EVENT_BYTES are bounded, marked oversized, and discarded
    so that truncated JSON prefixes are never accepted as completion evidence.
    Tracks terminal completion, refusal, usage, and session candidates while draining.
    Cumulative parser state is bounded by max_bytes; if exceeded, truncated is set.
    """

    def __init__(self, kind: str, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.kind = kind
        self.max_bytes = max_bytes
        self.pending = bytearray()
        self.line_oversized = False
        self.oversized_count = 0
        self.truncated = False
        self.parser_failed = False

        # cc state (bounded)
        self.cc_results: deque[dict[str, Any]] = deque(maxlen=10)
        self.cc_candidate_session_id: str | None = None
        self._cc_observed_session_id: str | None = None
        self._cc_session_id_conflict = False
        self._cc_oversized_unresolved = False

        # cx state (bounded)
        self.cx_candidate_thread_id: str | None = None
        self._cx_observed_thread_id: str | None = None
        self._cx_thread_conflict = False
        self._cx_oversized_unresolved = False
        self._cx_agent_messages_head: list[str] = []
        self._cx_agent_messages_tail: deque[str] = deque()
        self._cx_head_bytes: int = 0
        self._cx_tail_bytes: int = 0
        self.cx_turn_completed: dict[str, Any] | None = None
        self._cx_has_completed_item = False
        self._cx_latest_activity_has_turn_completed = False
        self._cx_final_messages_head: list[str] = []
        self._cx_final_messages_tail: deque[str] = deque()
        self._cx_final_head_bytes: int = 0
        self._cx_final_tail_bytes: int = 0
        self._cx_final_tail_started = False
        self.cx_errors: deque[str] = deque(maxlen=10)
        self._cx_tail_started = False

        # ag state (bounded)
        self.ag_results: deque[dict[str, Any]] = deque(maxlen=10)
        self.ag_errors: deque[str] = deque(maxlen=10)
        self._ag_observed_conv_id: str | None = None
        self._ag_conv_conflict = False
        self._ag_saw_non_whitespace = False
        self._ag_invalid_top_level = False
        self._ag_raw_buf = bytearray()
        self._ag_parsed = False
        self.has_tool_calls = False
        self.refusal_event: dict[str, Any] | None = None
        self.refusal_evidence_incomplete = False
        self.terminal_error_summary: str | None = None
        self.has_success = False
        self.saw_stdout = False

    @property
    def cx_agent_messages(self) -> list[str]:
        return self._cx_agent_messages_head + list(self._cx_agent_messages_tail)

    @property
    def cc_session_ids(self) -> list[str]:
        return [self.cc_candidate_session_id] if (self.cc_candidate_session_id and not self._cc_session_id_conflict) else []

    @property
    def cx_thread_ids(self) -> list[str]:
        return [self.cx_candidate_thread_id] if (self.cx_candidate_thread_id and not self._cx_thread_conflict) else []

    @property
    def ag_invalid_top_level(self) -> bool:
        return self._ag_invalid_top_level

    def _add_cx_agent_message(self, text: str) -> None:
        text_bytes = max(1, len(text.encode("utf-8", "replace")))
        head_limit = max(0, self.max_bytes // 2)
        tail_limit = max(0, self.max_bytes - head_limit)
        if not self._cx_tail_started and self._cx_head_bytes + text_bytes <= head_limit:
            self._cx_agent_messages_head.append(text)
            self._cx_head_bytes += text_bytes
        elif not self._cx_tail_started and not self._cx_agent_messages_head and head_limit > 0:
            self._cx_agent_messages_head.append(text)
            self._cx_head_bytes += text_bytes
            if self._cx_head_bytes > head_limit:
                self.truncated = True
                self._cx_tail_started = True
        else:
            self.truncated = True
            self._cx_tail_started = True
            if tail_limit > 0:
                self._cx_agent_messages_tail.append(text)
                self._cx_tail_bytes += text_bytes
                while self._cx_tail_bytes > tail_limit and len(self._cx_agent_messages_tail) > 1:
                    popped = self._cx_agent_messages_tail.popleft()
                    self._cx_tail_bytes -= max(1, len(popped.encode("utf-8", "replace")))

    @property
    def cx_final_messages(self) -> list[str]:
        return self._cx_final_messages_head + list(self._cx_final_messages_tail)

    def _add_cx_final_message(self, text: str) -> None:
        text_bytes = max(1, len(text.encode("utf-8", "replace")))
        head_limit = max(0, self.max_bytes // 2)
        tail_limit = max(0, self.max_bytes - head_limit)
        if not self._cx_final_tail_started and self._cx_final_head_bytes + text_bytes <= head_limit:
            self._cx_final_messages_head.append(text)
            self._cx_final_head_bytes += text_bytes
        elif not self._cx_final_tail_started and not self._cx_final_messages_head and head_limit > 0:
            self._cx_final_messages_head.append(text)
            self._cx_final_head_bytes += text_bytes
            if self._cx_final_head_bytes > head_limit:
                self.truncated = True
                self._cx_final_tail_started = True
        else:
            self.truncated = True
            self._cx_final_tail_started = True
            if tail_limit > 0:
                self._cx_final_messages_tail.append(text)
                self._cx_final_tail_bytes += text_bytes
                while self._cx_final_tail_bytes > tail_limit and len(self._cx_final_messages_tail) > 1:
                    popped = self._cx_final_messages_tail.popleft()
                    self._cx_final_tail_bytes -= max(1, len(popped.encode("utf-8", "replace")))

    def _note_oversized(self) -> None:
        self.oversized_count += 1
        if self.kind == "cx":
            # A discarded record could be an answer, failure, or newer activity.
            # Only a subsequent intact answer AND turn completion can supersede it.
            self._cx_oversized_unresolved = True
            self._cx_latest_activity_has_turn_completed = False
            self.cx_turn_completed = None
        elif self.kind == "cc":
            # A gap after an earlier result makes it stale; only a later intact result resolves it (#325).
            self._cc_oversized_unresolved = True
        self.pending.clear()

    def _try_parse_ag(self) -> None:
        try:
            text = self._ag_raw_buf.decode("utf-8")
        except UnicodeDecodeError:
            return
        start_idx = 0
        while start_idx < len(text) and text[start_idx].isspace():
            start_idx += 1
        if start_idx >= len(text):
            return
        if text[start_idx] != "{":
            self._ag_invalid_top_level = True
            return
        try:
            obj, end_idx = json.JSONDecoder().raw_decode(text, idx=start_idx)
        except json.JSONDecodeError:
            return
        if not isinstance(obj, dict):
            self._ag_invalid_top_level = True
            return
        event = cast("dict[str, Any]", obj)
        is_error = event.get("is_error") in (True,) or event.get("error") not in (None, False, "", {})
        has_response = isinstance(event.get("response"), str)
        if not has_response and not is_error:
            self._ag_invalid_top_level = True
            return
        trailing = text[end_idx:]
        if trailing.strip():
            self._ag_invalid_top_level = True
            return
        self._ag_parsed = True
        self._handle_event(event)

    def feed(self, chunk: bytes) -> None:
        try:
            self._feed_chunk(chunk)
        except Exception:
            self.parser_failed = True
            raise

    def _feed_chunk(self, chunk: bytes) -> None:
        if chunk.strip():
            self.saw_stdout = True
        if self.kind == "ag":
            if self._ag_invalid_top_level:
                return
            if not self._ag_saw_non_whitespace:
                stripped = chunk.lstrip()
                if stripped:
                    self._ag_saw_non_whitespace = True
                    if not stripped.startswith(b"{"):
                        self._ag_invalid_top_level = True
                        return
                    chunk = stripped
                else:
                    return
            if not self._ag_parsed:
                if len(self._ag_raw_buf) + len(chunk) > MAX_EVENT_BYTES:
                    self.oversized_count += 1
                    self._ag_invalid_top_level = True
                    return
                self._ag_raw_buf.extend(chunk)
                self._try_parse_ag()
            else:
                if chunk.strip():
                    self._ag_invalid_top_level = True
                    self.ag_results.clear()
            return
        start = 0
        n = len(chunk)
        while start < n:
            nl_pos = chunk.find(b"\n", start)
            if nl_pos != -1:
                part = chunk[start:nl_pos]
                start = nl_pos + 1
                if self.line_oversized:
                    self.line_oversized = False
                    self.pending.clear()
                    continue
                if len(self.pending) + len(part) > MAX_EVENT_BYTES:
                    self._note_oversized()
                    continue
                self.pending.extend(part)
                line = bytes(self.pending)
                self.pending.clear()
                self._handle_line(line)
            else:
                part = chunk[start:]
                start = n
                if self.line_oversized:
                    continue
                if len(self.pending) + len(part) > MAX_EVENT_BYTES:
                    self.line_oversized = True
                    self._note_oversized()
                else:
                    self.pending.extend(part)

    def close(self) -> None:
        if self.parser_failed:
            return
        if self.kind == "ag":
            if not self._ag_parsed:
                self._try_parse_ag()
                if not self._ag_parsed:
                    self._ag_invalid_top_level = True
            return
        if self.line_oversized:
            self.pending.clear()
            return
        if self.pending:
            line = bytes(self.pending)
            self.pending.clear()
            self._handle_line(line)

    def _handle_line(self, line_bytes: bytes) -> None:
        line = line_bytes.decode("utf-8", "replace").strip()
        if not line:
            return
        if not line.startswith("{"):
            self.refusal_evidence_incomplete = True
            return
        try:
            v: object = json.loads(line)
        except ValueError:
            self.refusal_evidence_incomplete = True
            return
        if not isinstance(v, dict):
            self.refusal_evidence_incomplete = True
            return
        event = cast("dict[str, Any]", v)
        self._handle_event(event)

    def _handle_event(self, event: dict[str, Any]) -> None:
        if self.kind == "cc":
            if event.get("type") not in CC_EVENT_TYPES:
                self.refusal_evidence_incomplete = True
            if event.get("type") in ("tool_use", "tool_result"):
                self.has_tool_calls = True
            if event.get("parent_tool_use_id") or event.get("tool_use_result"):
                self.has_tool_calls = True
            message = event.get("message")
            if event.get("type") in ("assistant", "user") and not isinstance(message, dict):
                self.refusal_evidence_incomplete = True
            if isinstance(message, dict):
                content = cast("dict[str, Any]", message).get("content")
                if isinstance(content, list):
                    for blk in cast("list[Any]", content):
                        if not isinstance(blk, dict) or cast("dict[str, Any]", blk).get("type") not in ("text", "thinking", "redacted_thinking"):
                            self.has_tool_calls = True
                elif not (event.get("type") == "user" and isinstance(content, str)):
                    self.refusal_evidence_incomplete = True
            if event.get("type") == "assistant" and isinstance(event.get("error"), str) and event.get("error"):
                err_code = str(event["error"])
                msg_texts: list[str] = []
                if isinstance(message, dict) and isinstance(cast("dict[str, Any]", message).get("content"), list):
                    for blk in cast("list[Any]", cast("dict[str, Any]", message)["content"]):
                        if isinstance(blk, dict) and cast("dict[str, Any]", blk).get("type") == "text":
                            txt = cast("dict[str, Any]", blk).get("text")
                            if isinstance(txt, str) and txt.strip():
                                msg_texts.append(txt.strip())
                self.terminal_error_summary = f"{err_code}: {' '.join(msg_texts)}" if msg_texts else err_code
            if event.get("type") == "stream_event":
                partial = event.get("event")
                if not isinstance(partial, dict):
                    self.refusal_evidence_incomplete = True
                else:
                    partial = cast("dict[str, Any]", partial)
                    if partial.get("type") == "content_block_start":
                        block = partial.get("content_block")
                        if not isinstance(block, dict) or cast("dict[str, Any]", block).get("type") not in ("text", "thinking", "redacted_thinking"):
                            self.has_tool_calls = True
                    elif partial.get("type") == "content_block_delta":
                        delta = partial.get("delta")
                        if not isinstance(delta, dict) or cast("dict[str, Any]", delta).get("type") not in ("text_delta", "thinking_delta", "signature_delta"):
                            self.has_tool_calls = True
                    elif partial.get("type") not in ("message_start", "message_delta", "message_stop", "content_block_stop"):
                        self.refusal_evidence_incomplete = True
            if event.get("type") == "result":
                self._cc_oversized_unresolved = False
                self.cc_results.append(event)
                if event.get("is_error") is False and event.get("subtype", "success") == "success":
                    self.has_success = True
                    self.cc_candidate_session_id = common.session_id(event.get("session_id"))
            if event.get("type") in ("error", "result") and (event.get("is_error") is True or event.get("type") == "error"):
                err_obj = cast("dict[str, Any]", event.get("error")) if isinstance(event.get("error"), dict) else {}
                candidates: list[Any] = [f"{err_obj.get('type', '')} {err_obj.get('message', '')}",
                                         err_obj.get("message"), event.get("result")]
                if event.get("type") == "error":
                    candidates.append(event.get("message"))
                errors = event.get("errors")
                if isinstance(errors, list):
                    candidates.extend(cast("list[Any]", errors))
                for candidate in candidates:
                    if isinstance(candidate, str):
                        if candidate.strip():
                            self.terminal_error_summary = candidate.strip()
                        r = common.classify_refusal_text(candidate)
                        if r is not None and self.refusal_event is None:
                            self.refusal_event = r
            if "session_id" in event:
                sid = common.session_id(event.get("session_id"))
                if sid is not None:
                    if self._cc_observed_session_id is None:
                        self._cc_observed_session_id = sid
                    elif self._cc_observed_session_id != sid:
                        self._cc_session_id_conflict = True
                else:
                    self._cc_session_id_conflict = True
        elif self.kind == "cx":
            ev_type = event.get("type")
            if ev_type in ("turn.started", "item.started", "item.updated", "item.completed"):
                self._cx_latest_activity_has_turn_completed = False
                self.cx_turn_completed = None
            if ev_type not in ("thread.started", "turn.started", "turn.completed", "turn.failed", "error",
                               "item.started", "item.updated", "item.completed"):
                self.refusal_evidence_incomplete = True
            if ev_type in ("item.started", "item.updated", "item.completed"):
                item = event.get("item")
                if not isinstance(item, dict):
                    self.refusal_evidence_incomplete = True
                elif cast("dict[str, Any]", item).get("type") not in ("agent_message", "reasoning", "error"):
                    # Unknown future items cannot establish absence of execution.
                    self.has_tool_calls = True
            if "thread_id" in event:
                tid = common.session_id(event.get("thread_id"))
                if tid is not None:
                    if self._cx_observed_thread_id is None:
                        self._cx_observed_thread_id = tid
                    elif self._cx_observed_thread_id != tid:
                        self._cx_thread_conflict = True
                else:
                    self._cx_thread_conflict = True
            if ev_type == "thread.started":
                self.cx_candidate_thread_id = common.session_id(event.get("thread_id"))
            elif ev_type in ("turn.failed", "error"):
                self.cx_errors.append(str(ev_type))
                msg = event.get("message")
                if not msg and isinstance(event.get("error"), dict):
                    msg = cast("dict[str, Any]", event["error"]).get("message")
                if isinstance(msg, str):
                    if msg.strip():
                        self.terminal_error_summary = msg.strip()
                    r = common.classify_refusal_text(msg)
                    if r is not None and not self.has_tool_calls and not self.refusal_event:
                        self.refusal_event = r
            elif ev_type == "item.completed":
                item = event.get("item")
                if isinstance(item, dict):
                    self._cx_has_completed_item = True
                    item_dict = cast("dict[str, Any]", item)
                    if item_dict.get("type") == "agent_message" and isinstance(item_dict.get("text"), str):
                        text_val = cast(str, item_dict["text"])
                        if item_dict.get("phase") != "commentary":
                            self._cx_oversized_unresolved = False
                        self._add_cx_agent_message(text_val)
                        if item_dict.get("phase") == "final":
                            self._add_cx_final_message(text_val)
            elif ev_type == "turn.completed":
                self.has_success = True
                self.cx_turn_completed = event
                self._cx_latest_activity_has_turn_completed = True
        elif self.kind == "ag":
            if event.get("tool_calls") or event.get("tool_use"):
                self.has_tool_calls = True
            if self._ag_invalid_top_level:
                return
            if len(self.ag_results) >= 1:
                self._ag_invalid_top_level = True
                self.ag_results.clear()
                self._ag_observed_conv_id = None
                return
            if "conversation_id" in event:
                cid = common.session_id(event.get("conversation_id"))
                if cid is not None:
                    if self._ag_observed_conv_id is None:
                        self._ag_observed_conv_id = cid
                    elif self._ag_observed_conv_id != cid:
                        self._ag_conv_conflict = True
                else:
                    self._ag_conv_conflict = True
            if isinstance(event.get("response"), str):
                self.ag_results.append(event)
            if event.get("is_error") in (True,) or event.get("error") not in (None, False, "", {}):
                self.ag_errors.append("error")
                err_val = event.get("error") or event.get("message")
                if isinstance(err_val, str) and err_val.strip():
                    self.terminal_error_summary = err_val.strip()

    def parse(self) -> str:
        if self.parser_failed:
            raise ValueError("parser failure encountered; outcome indeterminate")
        if self.oversized_count > 0 and (
            self.kind not in ("cx", "cc")
            or (self.kind == "cx" and self._cx_oversized_unresolved)
            or (self.kind == "cc" and self._cc_oversized_unresolved)
        ):
            raise ValueError(f"oversized event encountered ({self.oversized_count} record(s)); outcome indeterminate")
        if self.kind == "cc":
            if not self.cc_results:
                raise ValueError("no result record in cc output")
            r = self.cc_results[-1]
            if r.get("is_error") is not False:
                raise ValueError("cc result is not explicitly is_error=false")
            if r.get("subtype", "success") != "success":
                raise ValueError(f"cc result subtype {r.get('subtype')!r} is not success")
            if not isinstance(r.get("result"), str):
                raise ValueError("cc result is not text")
            return cast(str, r["result"])
        elif self.kind == "cx":
            if self.cx_errors:
                raise ValueError(f"cx reported {self.cx_errors[-1]}")
            if not self._cx_latest_activity_has_turn_completed:
                raise ValueError("cx output has no turn.completed after the latest activity (incomplete)")
            if not self.cx_agent_messages:
                if self._cx_has_completed_item:
                    return ""
                raise ValueError("no agent_message in cx output")
            if self.cx_final_messages:
                return "\n".join(self.cx_final_messages)
            return "\n".join(self.cx_agent_messages)
        elif self.kind == "ag":
            if self._ag_invalid_top_level:
                raise ValueError("ag output is not a well-formed top-level object")
            if self.ag_errors:
                raise ValueError("ag reported an error")
            if not self.ag_results or len(self.ag_results) != 1:
                raise ValueError("no flat JSON response in ag output")
            r = self.ag_results[0]
            if r.get("is_error") not in (None, False) or r.get("error") not in (None, False, "", {}):
                raise ValueError("ag reported an error")
            return cast(str, r["response"])
        raise ValueError(f"unknown provider kind {self.kind}")

    def _oversized_blocks_metadata(self) -> bool:
        if self.kind == "cc":
            return self._cc_oversized_unresolved
        return self.oversized_count > 0

    def parse_usage(self) -> dict[str, int] | None:
        if self.parser_failed or self._oversized_blocks_metadata():
            return None
        if self.kind == "cc":
            usage = self.cc_results[-1].get("usage") if self.cc_results else None
            counters = common.usage_fields(
                usage, {"input_tokens": "input_tokens", "cached_input_tokens": "cache_read_input_tokens",
                        "cache_write_tokens": "cache_creation_input_tokens", "output_tokens": "output_tokens"})
            if counters is not None:
                if all(key in counters for key in ("input_tokens", "cached_input_tokens", "cache_write_tokens")):
                    counters["input_tokens"] += counters["cached_input_tokens"] + counters["cache_write_tokens"]
                else:
                    counters.pop("input_tokens", None)
            return counters or None
        elif self.kind == "cx":
            usage = self.cx_turn_completed.get("usage") if self.cx_turn_completed else None
            return common.usage_fields(usage,
                                       {"input_tokens": "input_tokens", "cached_input_tokens": "cached_input_tokens",
                                        "cache_write_tokens": "cache_write_input_tokens", "output_tokens": "output_tokens"})
        elif self.kind == "ag":
            if self._ag_invalid_top_level or not self.ag_results or len(self.ag_results) != 1:
                return None
            return common.usage_fields(self.ag_results[0].get("usage"),
                                       {"input_tokens": "input_tokens", "cached_input_tokens": "cache_read_tokens",
                                        "output_tokens": "output_tokens"})
        return None

    def parse_session_id(self) -> str | None:
        if self.parser_failed or self._oversized_blocks_metadata():
            return None
        if self.kind == "cc":
            if self._cc_session_id_conflict or not self.cc_candidate_session_id:
                return None
            if self._cc_observed_session_id is not None and self.cc_candidate_session_id != self._cc_observed_session_id:
                return None
            return self.cc_candidate_session_id
        elif self.kind == "cx":
            if self._cx_thread_conflict or not self.cx_candidate_thread_id:
                return None
            if self._cx_observed_thread_id is not None and self.cx_candidate_thread_id != self._cx_observed_thread_id:
                return None
            return self.cx_candidate_thread_id
        elif self.kind == "ag":
            if self._ag_invalid_top_level or self._ag_conv_conflict or not self.ag_results or len(self.ag_results) != 1:
                return None
            cand = common.session_id(self.ag_results[0].get("conversation_id"))
            if cand is None or (self._ag_observed_conv_id is not None and cand != self._ag_observed_conv_id):
                return None
            return cand
        return None


SPECS: dict[str, common.ProviderSpec] = {s.kind: s for s in (cc.CcSpec(), cx.CxSpec(), ag.AgSpec())}


def resolve_binary(spec: common.ProviderSpec) -> list[str] | None:
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

    supports_interrupt = False
    supports_terminate = True
    supports_steer = False

    def __init__(self, kind: str, workspace: str | Path, *, model: str | None = None, effort: str | None = None,
                 profile: str | None = None, silence_timeout_s: float | None = None, writable: bool = False,
                 command: Sequence[str] | None = None, timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = DEFAULT_MAX_BYTES,
                 env_extra: Mapping[str, str] | None = None, extra_secrets: Iterable[str] = (),
                 on_output: Callable[[str], None] | None = None,
                 account: AccountRef | None = None, env_unset: Iterable[str] = (), notice_alias: bool = False) -> None:
        if kind not in SPECS:
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
        self._spec = SPECS[kind]
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
        self._ag_transport: ag.AgTransport | None = None  # decided before submission, consumed by the next delivery

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
                decision = ag.AgTransport("undetermined", "help", None, f"cannot determine agy stdin support: {type(e).__name__}")
            self._ag_transport = decision
            metadata.update(decision.metadata())
        return metadata

    def _ag_transport_decision(self) -> ag.AgTransport:
        return ag.select_transport(self._base, self._env, self._workspace, self._spec.binary)

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
        return list(self._command) if self._command is not None else resolve_binary(self._spec)

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

    @staticmethod
    def _prompt(record: Any, catch_up: list[Any], *, limit: int | None = None) -> str:
        def txt(r: Any) -> str:
            b = getattr(r, "body", None)
            return b if isinstance(b, str) else json.dumps(b, ensure_ascii=False, sort_keys=True)
        parts: list[str] = []
        boundary: object = getattr(catch_up, "boundary", None)
        if isinstance(boundary, dict):
            info = cast("dict[str, Any]", boundary)
            if info.get("truncated") or info.get("omitted_count"):
                parts.append(f"[context truncated: {info.get('omitted_count', '?')} earlier records omitted "
                             f"through position {info.get('omitted_through_position', '?')}]")
        # Bridge projections arrive oldest first; never truncate here: the injected budget decides.
        for r in catch_up:
            parts.append(f"[{getattr(r, 'position', '?')} {getattr(r, 'record_id', '?')} "
                         f"{getattr(r, 'author_peer_id', '?')} {getattr(r, 'kind', '?')}] {txt(r)}")
        has_context = bool(parts)
        parts.append(txt(record))
        prompt = "\n\n".join(parts)
        if has_context:
            preamble = "PeerHub stream context follows. Use the earlier records as context for the final request.\n\n"
            if limit is not None and limit - len(preamble) < len(prompt.encode("utf-8")) <= limit:
                return prompt
            return preamble + prompt
        return prompt

    def deliver(self, external_session_id: str, record: Any, catch_up: list[Any]) -> Iterable[RuntimeEvent]:
        prompt = self._prompt(record, catch_up)
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
            prompt = self._prompt(record, catch_up, limit=limit)
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
        pending = bytearray()
        emitted = False
        stream_oversized = False
        stream_display_truncated = False
        parser = IncrementalEventParser(self.runtime_kind, max_bytes=self._max_bytes)
        proc: BoundedProcess | None = None

        def stream_stdout(chunk: bytes) -> None:
            nonlocal emitted, stream_oversized, stream_display_truncated
            start = 0
            n = len(chunk)
            while start < n:
                nl_pos = chunk.find(b"\n", start)
                if nl_pos != -1:
                    part = chunk[start:nl_pos]
                    start = nl_pos + 1
                    if stream_oversized:
                        stream_oversized = False
                        stream_display_truncated = True
                        if proc is not None:
                            proc.mark_display_truncated()
                        pending.clear()
                        continue
                    if len(pending) + len(part) > self._max_bytes:
                        stream_display_truncated = True
                        if proc is not None:
                            proc.mark_display_truncated()
                        pending.clear()
                        continue
                    pending.extend(part)
                    line = bytes(pending)
                    pending.clear()
                    events = common.json_lines(line.decode("utf-8", "replace"))
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
                    elif self.runtime_kind == "ag" and stream:
                        result_obj = event.get("result") if event.get("event") == "result" else None
                        if isinstance(result_obj, dict) and cast("dict[str, Any]", result_obj).get("status") == "SUCCESS":
                            text = cast("dict[str, Any]", result_obj).get("response")
                    elif self.runtime_kind == "ag" and event.get("is_error") not in (True,):
                        text = event.get("response")
                    if isinstance(text, str) and text and self._on_output is not None:
                        try:
                            self._on_output(self._san.clean(text))
                            emitted = True
                        except Exception:
                            self._on_output = None
                            stream_display_truncated = True
                            if proc is not None:
                                proc.mark_display_truncated()
                else:
                    part = chunk[start:]
                    start = n
                    if stream_oversized:
                        stream_display_truncated = True
                        if proc is not None:
                            proc.mark_display_truncated()
                        continue
                    if len(pending) + len(part) > self._max_bytes:
                        stream_oversized = True
                        stream_display_truncated = True
                        if proc is not None:
                            proc.mark_display_truncated()
                        pending.clear()
                    else:
                        pending.extend(part)

        def display_callback(chunk: bytes) -> None:
            nonlocal stream_display_truncated
            if self._on_output is not None:
                try:
                    stream_stdout(chunk)
                except Exception:
                    self._on_output = None
                    stream_display_truncated = True
                    if proc is not None:
                        proc.mark_display_truncated()
            else:
                stream_display_truncated = True
                if proc is not None:
                    proc.mark_display_truncated()

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
            yield ("started", f"{self.runtime_kind}:{proc.pid}:{uuid.uuid4().hex[:8]}")
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
        if pending:
            stream_display_truncated = True
        if parser.oversized_count:
            self._note("event_counts", oversized_event_count=parser.oversized_count)
        display_truncated = res.display_truncated or stream_display_truncated
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
