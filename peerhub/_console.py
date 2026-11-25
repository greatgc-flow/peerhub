"""Terminal output and display cells."""
from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from collections.abc import Mapping
from typing import Any, TextIO

from peerhub.core.text import strip_controls

_warned_encoding = False
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;[^\x07\x1b]*(?:\x1b\\|\x07)")  # SGR, and OSC 8 (a hyperlink's open and close)


class _EncodingWarningStream:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def write(self, text: str) -> int:
        global _warned_encoding
        encoding = self._stream.encoding
        if encoding and self._stream.errors == "replace" and not _warned_encoding:
            try:
                text.encode(encoding, "strict")
            except UnicodeEncodeError:
                _warned_encoding = True
                sys.stderr.write(f"WARNING: output encoding {encoding} cannot show some characters; "
                                 "they are replaced; use UTF-8 or --json\n")
        return self._stream.write(text)

    def writelines(self, lines: list[str]) -> None:
        for line in lines:
            self.write(line)


def tolerant_streams() -> None:
    """Never crash on text the console encoding cannot represent (REL-002/REL-011), without forcing an encoding.

    The stream keeps the user's encoding (locale default or an explicit PYTHONIOENCODING); only characters that cannot be encoded
    are replaced ('?'), unless the user chose an error handler explicitly (PYTHONIOENCODING=enc:errors)."""
    explicit_errors = ":" in os.environ.get("PYTHONIOENCODING", "")
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if isinstance(stream, _EncodingWarningStream):
            continue
        try:
            if not explicit_errors:
                stream.reconfigure(errors="replace")  # type: ignore[union-attr]
            if stream.errors == "replace":
                setattr(sys, name, _EncodingWarningStream(stream))
        except (AttributeError, ValueError, OSError):
            pass


def enable_vt() -> bool:
    """Enable Windows VT processing; a failed console call means append mode."""
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.GetStdHandle(-11)
        mode = wintypes.DWORD()
        return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode))
                    and kernel.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def _console_mode() -> int | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetConsoleMode.restype = wintypes.BOOL
        mode = wintypes.DWORD()
        return int(mode.value) if kernel.GetConsoleMode(kernel.GetStdHandle(-11), ctypes.byref(mode)) else None
    except (AttributeError, OSError):
        return None


def _restore_console_mode(mode: int | None) -> None:
    if mode is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetConsoleMode.restype = wintypes.BOOL
        kernel.SetConsoleMode(kernel.GetStdHandle(-11), mode)
    except (AttributeError, OSError):
        pass


NO_AUTOWRAP, AUTOWRAP = "\x1b[?7l", "\x1b[?7h"  # DECAWM off while a row-anchored frame is live
FULL_REPAINT_FRAMES = 60  # live frames between full repaints; the others rewrite only changed rows


class FrameOutput:
    """Overwrite VT terminals and append elsewhere, with exception-safe cleanup."""

    def __init__(self, interactive: bool = True, *, overwrite: bool = True, anchor_rows: bool = False,
                 alternate: bool | None = None) -> None:
        self.original_console_mode = _console_mode() if interactive and os.name == "nt" else None
        self.vt = (interactive and sys.stdout.isatty()
                   and os.environ.get("TERM", "").lower() != "dumb" and enable_vt())
        self.live = self.vt and overwrite
        self.anchor_rows = anchor_rows
        # Row-anchored frames (the monitor) own the whole screen: the alternate screen has no command line above the
        # frame, so a pinned command (VS Code sticky scroll) can never cover the first line (ATTN).
        self.alt_screen = anchor_rows if alternate is None else alternate
        self.started = False
        self.last_frame = ""
        self.painted_rows: list[str] = []
        self.frames = 0
        self.painted_size: os.terminal_size | None = None

    def write(self, frame: str) -> None:
        if self.live:
            self.last_frame = frame
            if not self.started:
                self.started = True
                # Row-anchored frames turn auto-wrap off (DECAWM): a row wider than the terminal (ambiguous-width
                # glyphs drawn double-width on CJK consoles) is clipped at the edge instead of spilling onto the next
                # row, where a row that did not change would keep the spill until the next full repaint.
                prefix = (("\x1b[?1049h" if self.alt_screen else "") + "\x1b[?25l"
                          + (NO_AUTOWRAP if self.anchor_rows else "") + "\x1b[H\x1b[2J")
            else:
                prefix = "\x1b[H"
            if self.anchor_rows:
                # For height-bounded frames only: a wider rich glyph must not push
                # later rows down and scroll ATTN away. Unbounded diag frames keep
                # CRLF below, since terminal row addresses clamp at the bottom.
                # Only changed rows are rewritten: a full repaint every second (colour
                # and hyperlinks included) piled up in the VS Code terminal until the
                # window hung after a day of watching.
                # A changed row count (resize) or every FULL_REPAINT_FRAMES-th frame repaints in full,
                # which also repairs anything a resize reflowed.
                lines = frame.splitlines()
                self.frames += 1
                # A resize clears the screen: the terminal reflows or keeps old cells beyond the new width, and a
                # row that did not change would keep those leftovers.
                size = shutil.get_terminal_size((100, 24))
                if self.painted_size is not None and size != self.painted_size:
                    prefix = "\x1b[H\x1b[2J"
                self.painted_size = size
                full = (prefix != "\x1b[H" or len(lines) != len(self.painted_rows)
                        or self.frames % FULL_REPAINT_FRAMES == 0)
                old = self.painted_rows
                self.painted_rows = lines
                body = "".join(f"\x1b[{row};1H{line}\x1b[K"
                               for row, line in enumerate(lines, 1) if full or old[row - 1] != line)
                if not full:
                    if not body:
                        return
                    prefix = ""
                body += "\x1b[J" if full else ""
            else:
                body = "\x1b[K\r\n".join(frame.splitlines()) + "\x1b[K\x1b[J"
            # Buffer one complete frame and erase shorter rows and old tail rows.
            sys.stdout.write(prefix + body)
            sys.stdout.flush()
        else:
            print(frame, flush=True)

    def close(self) -> None:
        try:
            if self.started:
                wrap = AUTOWRAP if self.anchor_rows else ""
                if self.alt_screen:
                    sys.stdout.write("\x1b[?1049l" + wrap + "\x1b[0m\x1b[?25h" + self.last_frame)
                else:
                    sys.stdout.write(wrap + "\x1b[0m\x1b[?25h" + ("\r\n" if self.anchor_rows else ""))
                sys.stdout.flush()
                self.started = False
        finally:
            _restore_console_mode(self.original_console_mode)

    @staticmethod
    def width() -> int:
        """Keep the append-mode width policy, including an explicit COLUMNS override."""
        return shutil.get_terminal_size((100, 24)).columns

    @staticmethod
    def geometry() -> tuple[int, int]:
        """Read live TTY dimensions without honoring stale COLUMNS/LINES overrides."""
        try:
            if not sys.stdout.isatty():
                return 100, 24
        except (AttributeError, OSError):
            return 100, 24
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
            if size.columns > 0 and size.lines > 0:
                return size.columns, size.lines
        except (AttributeError, OSError, ValueError):
            return 100, 24
        return 100, 24


def visible_len(text: str) -> int:
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in ANSI_ESCAPE.sub("", text))


def ellipsize(text: str, limit: int, unicode: bool = True) -> str:
    if visible_len(text) <= limit:
        return text
    mark = "…" if unicode else "..."
    mark = mark[:max(0, limit)]
    kept = ""
    for char in text:
        if visible_len(kept + char + mark) > limit:
            break
        kept += char
    return kept + mark


def pad_cell(text: str, width: int) -> str:
    return text + " " * max(0, width - visible_len(text))


def format_text_cell(text: str, width: int) -> str:
    """Fit a data label to a fixed number of terminal columns."""
    return pad_cell(ellipsize(strip_controls(text), width, unicode=False), width)


def use_color(stream_is_tty: bool, env: Mapping[str, str]) -> bool:
    """Colour requires a terminal; presence of NO_COLOR always wins."""
    return stream_is_tty and "NO_COLOR" not in env
