"""Contracts and helpers shared by the provider adapters."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, cast

from peerhub.extensions.adapters.process import ProcessResult, decode_stderr, run_bounded
from peerhub.extensions.adapters.sanitize import Sanitizer

MAX_PROMPT_BYTES = 1_000_000
EXCERPT_CHARS = 300
SETUP_HINT = "(peerhub explain setup)"  # the lesson `setup`: what each peer needs, the official page and the check (#385)
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,200}")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+[0-9A-Za-z.+-]*")


def render_prompt(record: Any, catch_up: list[Any], *, limit: int | None = None) -> str:
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
        parts.append(f"[{getattr(r, 'position', '?')} {getattr(r, 'kind', '?')}] {txt(r)}")
    has_context = bool(parts)
    parts.append(txt(record))
    prompt = "\n\n".join(parts)
    if has_context:
        preamble = "Use the stream context below for the final request.\n\n"
        if limit is not None and limit - len(preamble) < len(prompt.encode("utf-8")) <= limit:
            return prompt
        return preamble + prompt
    return prompt


def executable_not_found(binary: str) -> str:
    """The message for a tool that is not on PATH. It names the next step and keeps the text `executable not found`:
    `peerhub.extensions.recovery_classification` classifies a launch failure by it."""
    return f"{binary} executable not found: install it and sign in {SETUP_HINT}"


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


def session_id(value: object) -> str | None:
    return value if isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) else None


def consistent_session_id(events: list[dict[str, Any]], key: str, candidate: object) -> str | None:
    selected = session_id(candidate)
    if selected is None:
        return None
    for event in events:
        if key in event and session_id(event[key]) != selected:
            return None
    return selected


def usage_fields(usage: object, fields: Mapping[str, str]) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    source = cast("dict[str, Any]", usage)
    out: dict[str, int] = {}
    for key, vendor_key in fields.items():
        value = source.get(vendor_key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    return out or None


def json_lines(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # LF only (#373): str.splitlines also cuts at U+0085, U+2028 and U+2029, which JSON allows raw inside a string.
    for line in text.split("\n"):
        line = line.removesuffix("\r").strip()
        if line.startswith("{"):
            try:
                v: object = json.loads(line)
            except ValueError:
                continue
            if isinstance(v, dict):
                out.append(cast("dict[str, Any]", v))  # json.loads objects always have str keys
    return out


_RESET_IN_RE = re.compile(r"(?:resets?\s+in|try\s+again\s+in)\s+([0-9]+[hdwms][0-9hdwms]*)", re.IGNORECASE)
_RESET_AT_TIME_RE = re.compile(r"(?:try\s+again\s+at|resets?(?:\s+at)?)\s+([0-9]{1,2}(?::[0-9]{2}(?::[0-9]{2})?\s*(?:AM|PM)?|\s*(?:AM|PM)))\b", re.IGNORECASE)
_RESET_AT_ISO_RE = re.compile(r"(?:resets?\s+at|try\s+again\s+at)\s+([0-9]{4}-[0-9]{2}-[0-9]{2}T[^\s,.]+)", re.IGNORECASE)


def reset_match(text: str) -> re.Match[str] | None:
    """The first reset match in priority order (in, at time, at ISO); group(1) is the reset, group(0) its phrase."""
    return _RESET_IN_RE.search(text) or _RESET_AT_TIME_RE.search(text) or _RESET_AT_ISO_RE.search(text)


def extract_reset_time(text: str) -> str | None:
    """Return provider reset text verbatim, without timezone or rollover inference."""
    m = reset_match(text)
    return m.group(1).strip() if m else None


def classify_refusal_text(text: str) -> dict[str, Any] | None:
    """Classify an explicit provider error field, never response or arbitrary log text."""
    text = re.sub(r"^error:\s*", "", text.strip(), flags=re.IGNORECASE)
    # 1. Quota / resource exhausted
    if re.match(r"(?:Individual\s+quota\s+reached|RESOURCE_EXHAUSTED)\b", text, re.IGNORECASE):
        reset = extract_reset_time(text)
        return {
            "reason": "quota_exhausted",
            "reset_time": reset,
            "message": "Individual quota reached / resource exhausted",
        }
    # 2. Usage limit / rate limit (handles both ASCII and curly apostrophes)
    if re.match(r"(?:You['\u2019]ve\s+hit\s+your\s+usage\s+limit|(?:claude(?:\s+ai)?\s+)?usage\s+limit\s+reached|you['’]ve\s+hit\s+your\s+(?:(?:\w+|\d+-hour)\s+)?limit|(?:\d+-hour|weekly|session)\s+limit\s+reached|rate_limit_error|rate\s+limit\s+exceeded|exceeded\s+your\s+current\s+quota)\b", text, re.IGNORECASE):
        reset = extract_reset_time(text)
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


def probe_cli(base: list[str], args: list[str], *, env: Mapping[str, str], cwd: str, timeout_s: float,
              max_bytes: int) -> ProcessResult:
    """A 0-token CLI probe (`--version`, `--help`), shared by discover() and ag transport detection."""
    return run_bounded([*base, *args], stdin=None, cwd=cwd, env=env, timeout_s=timeout_s, max_bytes=max_bytes)


def provider_excerpt(b: bytes, san: Sanitizer, limit: int = EXCERPT_CHARS) -> str:
    return san.clean(decode_stderr(b), limit)
