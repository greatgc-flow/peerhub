"""Evidence sanitizer: secrets and prompts never reach retained evidence or exception text."""
from __future__ import annotations

import os
import re
from typing import Iterable, Mapping

REDACTED = "[REDACTED]"
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.I)
_TOKENS = [
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[abp])[-_][A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
]
_ASSIGN = re.compile(r"(?i)\b((?:api[_-]?key|token|secret|password|passwd)\"?\s*[=:]\s*\"?)[^\s\"',;]{4,}")
MIN_SECRET_LEN = 6


def env_secrets(env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    return [v for k, v in env.items() if _SECRET_NAME.search(k) and len(v) >= MIN_SECRET_LEN]


class Sanitizer:
    def __init__(self, secrets: Iterable[str] = (), prompts: Iterable[str] = ()) -> None:
        self._literals = sorted({s for s in (*secrets, *prompts) if s and len(s) >= 2}, key=len, reverse=True)

    def with_prompt(self, prompt: str) -> "Sanitizer":
        s = Sanitizer()
        s._literals = sorted({*self._literals, *([prompt] if len(prompt) >= 2 else [])}, key=len, reverse=True)
        return s

    def clean(self, text: str, limit: int | None = None) -> str:
        """Redact first, truncate after (a truncation must never leave a secret prefix)."""
        for lit in self._literals:
            text = text.replace(lit, REDACTED)
        for pat in _TOKENS:
            text = pat.sub(REDACTED, text)
        text = _ASSIGN.sub(lambda m: m.group(1) + REDACTED, text)
        if limit is not None and len(text) > limit:
            text = text[:limit] + "...[truncated]"
        return text
