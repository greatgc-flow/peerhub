"""Incremental provider event parsing for bounded stdout capture."""
from __future__ import annotations

import json
from collections import deque
from typing import Any, cast

from peerhub.extensions.adapters import common
from peerhub.extensions.adapters.process import DEFAULT_MAX_BYTES
from peerhub.extensions.adapters.telemetry import CC_EVENT_TYPES

MAX_EVENT_BYTES = 262_144  # 256 KB


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
