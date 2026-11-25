"""Validate scalar values and sanitize public monitor text."""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime


SAFE_MODEL = re.compile(r"[A-Za-z0-9._+-]{1,128}\Z")
_SECRET_LIKE_BINDING = re.compile(
    r"(?i)(?:\b(?:sk|pk|rk|ghp|gho|ghs|xox[abp])[-_][A-Za-z0-9_-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}|"
    r"\b(?:api[_-]?key|token|secret|password|credential)[_-][A-Za-z0-9_-]{8,}\b)"
)


def public_binding_value(value: object) -> str | None:
    if isinstance(value, str) and SAFE_MODEL.fullmatch(value) and not _SECRET_LIKE_BINDING.search(value):
        return value
    return None


LINE_SHORT, LINE_LONG = 60, 200


# Control (Cc) and format (Cf) characters, except the line breaks LF and CR; a tab becomes a space. Format characters are listed
# explicitly (a test checks this table against unicodedata), so a secret split by a zero-width character is joined before redaction.
_FORMAT_RANGES = ((0x00AD, 0x00AD), (0x0600, 0x0605), (0x061C, 0x061C), (0x06DD, 0x06DD), (0x070F, 0x070F), (0x0890, 0x0891),
                  (0x08E2, 0x08E2), (0x180E, 0x180E), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0x2066, 0x206F),
                  (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB), (0x110BD, 0x110BD), (0x110CD, 0x110CD), (0x13430, 0x1343F),
                  (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0001, 0xE0001), (0xE0020, 0xE007F))
_CONTROL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f" + "".join(
    re.escape(chr(low)) if low == high else f"{re.escape(chr(low))}-{re.escape(chr(high))}" for low, high in _FORMAT_RANGES) + "]")
_UNSAFE_CATEGORIES = {"Cc", "Cf", "Cs", "Cn", "Co", "Zl", "Zp"}


def _strip_controls(text: str) -> str:
    return _CONTROL_RE.sub("", text.replace("\t", " "))


def fit_line(line: str, cap: int) -> str:
    """Collapse whitespace, replace what a terminal cannot show safely, cut to `cap` characters with an ellipsis."""
    head = " ".join(line.split())[:cap + 1]
    head = "".join("�" if unicodedata.category(ch) in _UNSAFE_CATEGORIES else ch for ch in head)
    return head if len(head) <= cap else head[:cap - 1] + "…"


def _alnum_line(text: str, *, last: bool) -> str | None:
    lines = text.splitlines()
    for line in (reversed(lines) if last else lines):
        if any(ch.isalnum() for ch in line):
            return line
    return None


def activity_lines(text: str, *, last: bool, secrets: list[str] | None = None) -> tuple[str | None, str | None]:
    """(short, long) display line of a request (first line with a letter or digit) or a reply (`last`: the last such line).
    Order: drop control and format characters from the COMPLETE text, redact the complete text, pick the line, fit, cap."""
    from peerhub.extensions.adapters.sanitize import Sanitizer, env_secrets
    cleaned = Sanitizer(env_secrets() if secrets is None else secrets).clean(_strip_controls(text))
    line = _alnum_line(cleaned, last=last)
    return (None, None) if line is None else (fit_line(line, LINE_SHORT), fit_line(line, LINE_LONG))


def display_text(value: object, cap: int, secrets: list[str]) -> str | None:
    if not isinstance(value, str):
        return None
    from peerhub.extensions.adapters.sanitize import Sanitizer
    shown = fit_line(Sanitizer(secrets).clean(_strip_controls(value)), cap)
    return shown or None


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:  # an integer of 2**1024 or more
        return None


def iso_seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp() if parsed.tzinfo is not None else None
