"""Shared formatting and normalization for read models and their consumers."""
from __future__ import annotations

import math
import re
import unicodedata

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_BIDI = {"\u061c", "\u200e", "\u200f", *[chr(code) for code in range(0x202A, 0x202F)],
         *[chr(code) for code in range(0x2066, 0x206A)]}


def strip_controls(value: object) -> str:
    """Remove terminal controls from data before adding presentation escapes."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(value))


def clean_text(value: object) -> str:
    """Normalize untrusted text to a single printable monitor label."""
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = _CONTROL.sub("�", text)
    return "".join("�" if char in _BIDI or unicodedata.category(char) in {"Cf", "Cn", "Cs", "Zl", "Zp"}
                   and char != "\u200d" else char
                   for char in text).strip()


def format_duration(seconds: float, *, extra_unit: bool = False) -> str:
    total_minutes, secs = divmod(math.floor(seconds), 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d{hours:02d}h" + (f"{minutes:02d}m" if extra_unit else "")
    text = f"{hours}h{minutes:02d}m" if hours > 0 else f"{total_minutes}m"
    return text + (f"{secs:02d}s" if extra_unit else "")


def human_duration(seconds: float | None) -> str:
    """Round to minutes from two minutes onward, with an explicit marker for missing values."""
    if seconds is None or seconds < 0:
        return "?"
    whole = int(seconds) if seconds < 120 else int(seconds / 60 + 0.5) * 60
    if whole >= 3600:
        return format_duration(whole)
    minutes, secs = divmod(whole, 60)
    if not minutes:
        return f"{secs}s"
    return f"{minutes}m{secs:02d}s" if secs else f"{minutes}m"
