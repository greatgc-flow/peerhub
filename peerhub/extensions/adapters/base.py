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
from typing import Any, Callable, Iterable, Mapping, Sequence, cast

from peerhub.extensions.adapters.process import (DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_S, BoundedProcess, ProcessResult,
                                                 SpawnFailure, check_argv, decode_stderr, run_bounded)
from peerhub.extensions.adapters.sanitize import Sanitizer, env_secrets
from peerhub.extensions.bridge import PrespawnError, RuntimeEvent, RuntimeTargetError

MAX_PROMPT_BYTES = 1_000_000
EXCERPT_CHARS = 300
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,200}")
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+[0-9A-Za-z.+-]*")
_WIN_EXT = {"claude": ["claude.cmd", "claude.exe"], "codex": ["codex.cmd", "codex.exe"], "agy": ["agy.exe", "agy.cmd"]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProviderSpec:
    kind: str
    binary: str
    resume_flag: str  # token looked up in `--help` output to report CLI-level resume support
    stdin_prompt: bool
    can_resume: bool = False
    max_prompt_bytes: int = MAX_PROMPT_BYTES  # provider-specific inline limit (argv-borne prompts are far smaller)

    def argv(self, model: str | None, effort: str | None, prompt: str, writable: bool = False,
             vendor_session_id: str | None = None) -> list[str]:
        raise NotImplementedError

    def parse(self, stdout: str) -> str:
        """Return canonical response text or raise ValueError (garbage / vendor error)."""
        raise NotImplementedError

    def parse_session_id(self, stdout: str) -> str | None:
        """Return a bounded, unambiguous vendor session candidate, when available."""
        return None

    def parse_usage(self, stdout: str) -> dict[str, int] | None:
        """Return reported request usage; absent counters stay unknown, never synthesized as zero."""
        return None


def _session_id(value: object) -> str | None:
    return value if isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) else None


def _consistent_session_id(events: list[dict[str, Any]], key: str, candidate: object) -> str | None:
    selected = _session_id(candidate)
    if selected is None:
        return None
    for event in events:
        if key in event and _session_id(event[key]) != selected:
            return None
    return selected


def _usage_fields(usage: object, fields: Mapping[str, str]) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    source = cast("dict[str, Any]", usage)
    out: dict[str, int] = {}
    for key, vendor_key in fields.items():
        value = source.get(vendor_key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    return out or None


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


MAX_EVENT_BYTES = 262_144  # 256 KB

_RESET_IN_RE = re.compile(r"(?:resets?\s+in|try\s+again\s+in)\s+([0-9]+[hdwms][0-9hdwms]*)", re.IGNORECASE)
_RESET_AT_TIME_RE = re.compile(r"(?:try\s+again\s+at|resets?\s+at)\s+([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\s*(?:AM|PM|am|pm)?)", re.IGNORECASE)
_RESET_AT_ISO_RE = re.compile(r"(?:resets?\s+at|try\s+again\s+at)\s+([0-9]{4}-[0-9]{2}-[0-9]{2}T[^\s,.]+)", re.IGNORECASE)


def _extract_reset_time(text: str) -> str | None:
    m = _RESET_IN_RE.search(text) or _RESET_AT_TIME_RE.search(text) or _RESET_AT_ISO_RE.search(text)
    return m.group(1).strip() if m else None


def classify_refusal_text(text: str) -> dict[str, Any] | None:
    """Classify an explicit provider error field, never response or arbitrary log text."""
    text = re.sub(r"^error:\s*", "", text.strip(), flags=re.IGNORECASE)
    # 1. Quota / resource exhausted
    if re.match(r"(?:Individual\s+quota\s+reached|RESOURCE_EXHAUSTED)\b", text, re.IGNORECASE):
        reset = _extract_reset_time(text)
        return {
            "reason": "quota_exhausted",
            "reset_time": reset,
            "message": "Individual quota reached / resource exhausted",
        }
    # 2. Usage limit / rate limit (handles both ASCII and curly apostrophes)
    if re.match(r"(?:You['\u2019]ve\s+hit\s+your\s+usage\s+limit|usage\s+limit\s+reached|rate_limit_error|rate\s+limit\s+exceeded|exceeded\s+your\s+current\s+quota)\b", text, re.IGNORECASE):
        reset = _extract_reset_time(text)
        return {
            "reason": "usage_limit",
            "reset_time": reset,
            "message": "usage limit reached",
        }
    # 3. Invalid model selection
    if re.match(r"(?:invalid\s+model\s+selection|model\s+.*?\s+is\s+not\s+recognized|model_not_found|model\s+.*?\s+does\s+not\s+exist)\b", text, re.IGNORECASE):
        return {
            "reason": "invalid_model",
            "reset_time": None,
            "message": "invalid model selection",
        }
    # 4. Auth failure
    if re.match(r"authentication_error\b|UNAUTHENTICATED\b|auth\s+failure\b|authentication\s+required\b|Please\s+run\s+`agy\s+login`|login\s+required\b|invalid\s+api\s+key\b|API\s+key\s+not\s+found\b", text, re.IGNORECASE):
        return {
            "reason": "auth_failure",
            "reset_time": None,
            "message": "authentication failure",
        }
    return None


def _ag_read_only_refusal(res: ProcessResult, saw_stdout: bool, writable: bool) -> dict[str, Any] | None:
    """Agy does not enforce a read-only sandbox when --mode accept-edits is omitted (#140).

    Empty stdout and writable=False cannot prove zero tool effects once an agy
    process has started; only pre-spawn failures are NOT_STARTED / NOT_EXECUTED.
    """
    del res, saw_stdout, writable
    return None


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
            if event.get("type") not in ("system", "assistant", "user", "result", "error", "stream_event"):
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
                self.cc_results.append(event)
                if event.get("is_error") is False and event.get("subtype", "success") == "success":
                    self.has_success = True
                    self.cc_candidate_session_id = _session_id(event.get("session_id"))
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
                        r = classify_refusal_text(candidate)
                        if r is not None and self.refusal_event is None:
                            self.refusal_event = r
            if "session_id" in event:
                sid = _session_id(event.get("session_id"))
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
                tid = _session_id(event.get("thread_id"))
                if tid is not None:
                    if self._cx_observed_thread_id is None:
                        self._cx_observed_thread_id = tid
                    elif self._cx_observed_thread_id != tid:
                        self._cx_thread_conflict = True
                else:
                    self._cx_thread_conflict = True
            if ev_type == "thread.started":
                self.cx_candidate_thread_id = _session_id(event.get("thread_id"))
            elif ev_type in ("turn.failed", "error"):
                self.cx_errors.append(str(ev_type))
                msg = event.get("message")
                if not msg and isinstance(event.get("error"), dict):
                    msg = cast("dict[str, Any]", event["error"]).get("message")
                if isinstance(msg, str):
                    if msg.strip():
                        self.terminal_error_summary = msg.strip()
                    r = classify_refusal_text(msg)
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
                cid = _session_id(event.get("conversation_id"))
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
        if self.oversized_count > 0 and (self.kind != "cx" or self._cx_oversized_unresolved):
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

    def parse_usage(self) -> dict[str, int] | None:
        if self.parser_failed or self.oversized_count > 0:
            return None
        if self.kind == "cc":
            usage = self.cc_results[-1].get("usage") if self.cc_results else None
            counters = _usage_fields(usage, {"input_tokens": "input_tokens", "cached_input_tokens": "cache_read_input_tokens",
                                             "cache_write_tokens": "cache_creation_input_tokens", "output_tokens": "output_tokens"})
            if counters is not None:
                if all(key in counters for key in ("input_tokens", "cached_input_tokens", "cache_write_tokens")):
                    counters["input_tokens"] += counters["cached_input_tokens"] + counters["cache_write_tokens"]
                else:
                    counters.pop("input_tokens", None)
            return counters or None
        elif self.kind == "cx":
            usage = self.cx_turn_completed.get("usage") if self.cx_turn_completed else None
            return _usage_fields(usage,
                                 {"input_tokens": "input_tokens", "cached_input_tokens": "cached_input_tokens",
                                  "cache_write_tokens": "cache_write_input_tokens", "output_tokens": "output_tokens"})
        elif self.kind == "ag":
            if self._ag_invalid_top_level or not self.ag_results or len(self.ag_results) != 1:
                return None
            return _usage_fields(self.ag_results[0].get("usage"),
                                 {"input_tokens": "input_tokens", "cached_input_tokens": "cache_read_tokens",
                                  "output_tokens": "output_tokens"})
        return None

    def parse_session_id(self) -> str | None:
        if self.parser_failed or self.oversized_count > 0:
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
            cand = _session_id(self.ag_results[0].get("conversation_id"))
            if cand is None or (self._ag_observed_conv_id is not None and cand != self._ag_observed_conv_id):
                return None
            return cand
        return None


class CcSpec(ProviderSpec):
    kind, binary, resume_flag, stdin_prompt = "cc", "claude", "--resume", True
    can_resume = True

    def argv(self, model: str | None, effort: str | None, prompt: str, writable: bool = False,
             vendor_session_id: str | None = None) -> list[str]:
        a = ["-p", "-", "--output-format", "stream-json", "--verbose"]
        if vendor_session_id is not None:
            a += ["--resume", vendor_session_id]
        if model:
            a += ["--model", model]
        if effort:
            a += ["--effort", effort]
        if writable:
            a += ["--permission-mode", "acceptEdits"]
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

    def parse_session_id(self, stdout: str) -> str | None:
        events = _json_lines(stdout)
        results = [e for e in events if e.get("type") == "result"]
        successful = [e for e in results if e.get("is_error") is False and e.get("subtype", "success") == "success"]
        return _consistent_session_id(events, "session_id", successful[-1].get("session_id") if successful else None)

    def parse_usage(self, stdout: str) -> dict[str, int] | None:
        results = [e for e in _json_lines(stdout) if e.get("type") == "result"]
        usage = results[-1].get("usage") if results else None
        counters = _usage_fields(usage, {"input_tokens": "input_tokens", "cached_input_tokens": "cache_read_input_tokens",
                                         "cache_write_tokens": "cache_creation_input_tokens", "output_tokens": "output_tokens"})
        if counters is not None:
            # Claude reports uncached input separately; all three counters are needed for total input.
            if all(key in counters for key in ("input_tokens", "cached_input_tokens", "cache_write_tokens")):
                counters["input_tokens"] += counters["cached_input_tokens"] + counters["cache_write_tokens"]
            else:
                counters.pop("input_tokens", None)
        return counters or None


class CxSpec(ProviderSpec):
    kind, binary, resume_flag, stdin_prompt = "cx", "codex", "resume", True
    can_resume = True

    def argv(self, model: str | None, effort: str | None, prompt: str, writable: bool = False,
             vendor_session_id: str | None = None) -> list[str]:
        if vendor_session_id is not None:
            # exec resume does not accept -s; sandbox is configured via -c sandbox_mode=...
            sandbox = "workspace-write" if writable else "read-only"
            a = ["exec", "resume", vendor_session_id, "--skip-git-repo-check", "-c", f'sandbox_mode="{sandbox}"']
        else:
            a = ["exec", "--skip-git-repo-check", "-s", "workspace-write" if writable else "read-only"]
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
        activity_indices = [
            i for i, e in enumerate(events)
            if e.get("type") in ("turn.started", "item.started", "item.updated", "item.completed")
        ]
        if not activity_indices:
            raise ValueError("no agent_message in cx output")
        latest_activity = max(activity_indices)
        if not any(e.get("type") == "turn.completed" for e in events[latest_activity + 1:]):
            raise ValueError("cx output has no turn.completed after the latest activity (incomplete)")

        msgs = [e["item"]["text"] for e in events if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
                and e["item"].get("type") == "agent_message" and isinstance(e["item"].get("text"), str)]
        if not msgs:
            return ""
        phase_msgs = [e["item"]["text"] for e in events if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
                      and e["item"].get("type") == "agent_message" and e["item"].get("phase") == "final" and isinstance(e["item"].get("text"), str)]
        if phase_msgs:
            return "\n".join(phase_msgs)
        return "\n".join(msgs)

    def parse_session_id(self, stdout: str) -> str | None:
        events = _json_lines(stdout)
        started = [e for e in events if e.get("type") == "thread.started"]
        return _consistent_session_id(events, "thread_id", started[-1].get("thread_id") if started else None)

    def parse_usage(self, stdout: str) -> dict[str, int] | None:
        # On resumed threads these are raw cumulative counters; the Bridge will store baselines.
        results = [e for e in _json_lines(stdout) if e.get("type") == "turn.completed"]
        return _usage_fields(results[-1].get("usage") if results else None,
                             {"input_tokens": "input_tokens", "cached_input_tokens": "cached_input_tokens",
                              "cache_write_tokens": "cache_write_input_tokens", "output_tokens": "output_tokens"})


class AgSpec(ProviderSpec):
    can_resume = True
    kind, binary, resume_flag, stdin_prompt = "ag", "agy", "--conversation", False
    max_prompt_bytes = 30_000 if sys.platform == "win32" else 120_000  # prompt travels in argv: CreateProcess ~32k chars / MAX_ARG_STRLEN

    def argv(self, model: str | None, effort: str | None, prompt: str, writable: bool = False,
             vendor_session_id: str | None = None) -> list[str]:
        prompt = "\n" + prompt if prompt.startswith("-") else prompt
        a = ["-p", prompt, "--output-format", "json"]
        if model:
            a += ["--model", model]
        if effort:
            a += ["--effort", effort]
        if writable:
            a += ["--mode", "accept-edits"]
        if vendor_session_id is not None:
            a += ["--conversation", vendor_session_id]
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

    def parse_session_id(self, stdout: str) -> str | None:
        try:
            result: object = json.loads(stdout.strip())
        except ValueError:
            return None
        if not isinstance(result, dict):
            return None
        return _session_id(cast("dict[str, Any]", result).get("conversation_id"))

    def parse_usage(self, stdout: str) -> dict[str, int] | None:
        try:
            result: object = json.loads(stdout.strip())
        except ValueError:
            return None
        if not isinstance(result, dict):
            return None
        # AG usage may be cumulative per conversation on resume; the benchmark will determine this.
        return _usage_fields(cast("dict[str, Any]", result).get("usage"),
                             {"input_tokens": "input_tokens", "cached_input_tokens": "cache_read_tokens",
                              "output_tokens": "output_tokens"})


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

    supports_interrupt = False
    supports_terminate = True
    supports_steer = False

    def __init__(self, kind: str, workspace: str | Path, *, model: str | None = None, effort: str | None = None,
                 profile: str | None = None, silence_timeout_s: float | None = None, writable: bool = False,
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
        self._env = {**os.environ, **(env_extra or {})}
        self._san = Sanitizer(secrets=[*env_secrets(self._env), *extra_secrets])
        self.evidence: deque[dict[str, Any]] = deque(maxlen=100)  # sanitized and bounded

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
            raise SpawnFailure(f"{self._spec.binary} executable not found")
        return run_bounded([*base, *args], stdin=None, cwd=self._workspace, env=self._env,
                           timeout_s=timeout_s, max_bytes=self._max_bytes)

    @staticmethod
    def _text(b: bytes, san: Sanitizer, limit: int = EXCERPT_CHARS) -> str:
        return san.clean(decode_stderr(b), limit)

    def validate_execution_policy(self) -> None:
        """Fail closed before execution when the provider CLI cannot enforce the requested write policy (#140)."""
        if self.runtime_kind == "ag" and not self._writable:
            raise PrespawnError(
                "ag execution requires --writable: agy CLI does not enforce a read-only sandbox when "
                "--mode accept-edits is omitted"
            )

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
        return f"{binding}#{hashlib.sha256(scope.encode('utf-8')).hexdigest()[:12]}"

    def session_binding(self) -> dict[str, Any]:
        """Effective creation metadata; no cwd, credentials or vendor identity."""
        return dict(profile=self._profile, model=self._model, effort=self._effort, writable=self._writable)

    def create_session(self) -> str:
        return f"{self.runtime_kind}-local-{uuid.uuid4().hex}"  # a local handle: one process per delivery, no vendor call

    def resume_session(self, external_session_id: str, *, vendor_session_id: str | None = None) -> str:
        """Bind an explicit vendor id; the one-argument Bridge call remains valid."""
        if not self.resumable:
            self._note("resume_unsupported")
            return "unsupported"
        if vendor_session_id is None:
            return "missing"
        if _session_id(vendor_session_id) is None:
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
    def _prompt(record: Any, catch_up: list[Any]) -> str:
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
        parts.append(txt(record))
        return "\n\n".join(parts)

    def deliver(self, external_session_id: str, record: Any, catch_up: list[Any]) -> Iterable[RuntimeEvent]:
        prompt = self._prompt(record, catch_up)
        san = self._san.with_prompt(prompt)
        # ---- genuine pre-spawn failures: nothing was created
        self.validate_execution_policy()
        n_bytes, limit = len(prompt.encode("utf-8")), min(MAX_PROMPT_BYTES, self._spec.max_prompt_bytes)
        if n_bytes > limit:
            raise PrespawnError(f"prompt is {n_bytes} bytes, exceeding the {self.runtime_kind} inline limit of {limit} bytes "
                                f"(catch-up history is never truncated silently; lower the catch-up budget)")
        if not Path(self._workspace).is_dir():
            raise PrespawnError("workspace is not a directory")
        base = self._base()
        if base is None:
            raise PrespawnError(f"{self._spec.binary} executable not found")
        with self._active_lock:
            vendor_session_id = self._resume_sessions.get(external_session_id)
        argv = [*base, *self._spec.argv(self._model, self._effort, prompt, self._writable, vendor_session_id)]
        try:
            check_argv(argv)
        except ValueError as e:
            raise PrespawnError(san.clean(str(e), EXCERPT_CHARS)) from e
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


        proc = BoundedProcess(argv, stdin=prompt.encode("utf-8") if self._spec.stdin_prompt else None, cwd=self._workspace,
                              env=self._env, timeout_s=self._timeout_s, max_bytes=self._max_bytes,
                              silence_timeout_s=self._silence_timeout_s,
                              on_stdout=parser.feed,
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
            raise PrespawnError(san.clean(f"cannot spawn {self._spec.binary}: {e}", EXCERPT_CHARS)) from e
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
        refusal: dict[str, Any] | None = None
        if (not parser.has_tool_calls and not parser.has_success
                and not parser.refusal_evidence_incomplete and parser.oversized_count == 0):
            refusal = parser.refusal_event
            if self.runtime_kind == "ag":
                refusal = _ag_read_only_refusal(res, parser.saw_stdout, self._writable)

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
            excerpt = self._text(res.stderr or res.stdout, san)
            if parser.terminal_error_summary:
                term_err = san.clean(parser.terminal_error_summary, 200)
                if term_err and term_err not in excerpt:
                    excerpt = san.clean(f"{term_err} | {excerpt}" if excerpt else term_err, EXCERPT_CHARS)
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
                text = self._spec.parse(res.stdout.decode("utf-8", "replace"))
        except ValueError as e:
            err_msg = str(e)
            if parser.terminal_error_summary:
                term_err = san.clean(parser.terminal_error_summary, 200)
                if term_err and term_err not in err_msg:
                    err_msg = f"{err_msg} ({term_err})"
            raise RuntimeTargetError(san.clean(f"unparseable {self.runtime_kind} output: {err_msg}", EXCERPT_CHARS)) from e
        cleaned_response = self._san.clean(text)
        payload: dict[str, Any] = {"response": cleaned_response}  # secrets redacted; the prompt may legitimately be quoted
        if not cleaned_response.strip():
            payload["empty_response"] = True
            payload["task_effects"] = "unverified"
        if res.output_truncated or parser.truncated:
            payload["output_truncated"] = True
            if res.stdout_truncated or parser.truncated:
                usage = parser.parse_usage()
                vendor_session_id = parser.parse_session_id()
            else:
                usage = self._spec.parse_usage(res.stdout.decode("utf-8", "replace"))
                vendor_session_id = self._spec.parse_session_id(res.stdout.decode("utf-8", "replace"))
        else:
            usage = self._spec.parse_usage(res.stdout.decode("utf-8", "replace"))
            vendor_session_id = self._spec.parse_session_id(res.stdout.decode("utf-8", "replace"))
        if parser.oversized_count:
            payload["oversized_event_count"] = parser.oversized_count
        if display_truncated:
            payload["display_truncated"] = True
        if usage is not None:
            payload["usage"] = usage
        telemetry_text = res.stdout.decode("utf-8", "replace")
        from peerhub.extensions.adapters.telemetry import parse_provider_usage, unknown_usage
        payload["provider_telemetry"] = (
            unknown_usage("output_incomplete") if res.stdout_truncated or parser.truncated
            else parse_provider_usage(self.runtime_kind, telemetry_text))
        if vendor_session_id is not None:
            source = {"cc": "cc.result.session_id", "cx": "cx.thread.started",
                      "ag": "ag.conversation_id"}[self.runtime_kind]
            payload["vendor_session"] = {"id": vendor_session_id, "source": source}
        yield ("terminal", payload)
