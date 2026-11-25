"""Bounded line decoding for best-effort provider output display."""
from __future__ import annotations

from typing import Any, Callable, cast

from peerhub.extensions.adapters import common


class LiveOutput:
    """Decode display text independently of the terminal response and usage parser."""

    def __init__(self, kind: str, ag_stream: bool, max_bytes: int, emit: Callable[[str], bool],
                 on_truncated: Callable[[], None]) -> None:
        self._kind = kind
        self._ag_stream = ag_stream
        self._max_bytes = max_bytes
        self._emit = emit
        self._on_truncated = on_truncated
        self._pending = bytearray()
        self._emitted = False
        self._oversized = False
        self._truncated = False

    @property
    def truncated(self) -> bool:
        return self._truncated or bool(self._pending)

    def mark_truncated(self) -> None:
        self._truncated = True
        self._on_truncated()

    def _text(self, event: dict[str, Any]) -> object:
        if self._kind == "cx" and event.get("type") == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and cast("dict[str, Any]", item).get("type") == "agent_message":
                return cast("dict[str, Any]", item).get("text")
        elif self._kind == "cc":
            if event.get("type") == "assistant":
                message = event.get("message", {})
                content: Any = cast("dict[str, Any]", message).get("content", []) if isinstance(message, dict) else []
                if isinstance(content, list):
                    parts: list[str] = []
                    for item in cast("list[Any]", content):
                        if isinstance(item, dict):
                            block = cast("dict[str, Any]", item)
                            piece = block.get("text")
                            if block.get("type") == "text" and isinstance(piece, str):
                                parts.append(piece)
                    return "".join(parts)
            elif not self._emitted and event.get("type") == "result" and event.get("is_error") is False:
                return event.get("result")
        elif self._kind == "ag" and self._ag_stream:
            result = event.get("result") if event.get("event") == "result" else None
            if isinstance(result, dict) and cast("dict[str, Any]", result).get("status") == "SUCCESS":
                return cast("dict[str, Any]", result).get("response")
        elif self._kind == "ag" and event.get("is_error") not in (True,):
            return event.get("response")
        return None

    def feed(self, chunk: bytes) -> None:
        start = 0
        while start < len(chunk):
            newline = chunk.find(b"\n", start)
            if newline != -1:
                part = chunk[start:newline]
                start = newline + 1
                if self._oversized or len(self._pending) + len(part) > self._max_bytes:
                    self._oversized = False
                    self.mark_truncated()
                    self._pending.clear()
                    continue
                self._pending.extend(part)
                line = bytes(self._pending)
                self._pending.clear()
                events = common.json_lines(line.decode("utf-8", "replace"))
                if events:
                    text = self._text(events[0])
                    if isinstance(text, str) and text and self._emit(text):
                        self._emitted = True
            else:
                part = chunk[start:]
                start = len(chunk)
                if self._oversized:
                    self.mark_truncated()
                elif len(self._pending) + len(part) > self._max_bytes:
                    self._oversized = True
                    self.mark_truncated()
                    self._pending.clear()
                else:
                    self._pending.extend(part)
