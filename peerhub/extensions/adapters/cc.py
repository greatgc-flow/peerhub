"""Claude Code CLI arguments and terminal output."""
from __future__ import annotations

from typing import Any, cast

from peerhub.extensions.adapters.common import ProviderSpec, consistent_session_id, json_lines, usage_fields


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
        results = [e for e in json_lines(stdout) if e.get("type") == "result"]
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
        events = json_lines(stdout)
        results = [e for e in events if e.get("type") == "result"]
        successful = [e for e in results if e.get("is_error") is False and e.get("subtype", "success") == "success"]
        return consistent_session_id(events, "session_id", successful[-1].get("session_id") if successful else None)

    def parse_usage(self, stdout: str) -> dict[str, int] | None:
        results = [e for e in json_lines(stdout) if e.get("type") == "result"]
        usage = results[-1].get("usage") if results else None
        counters = usage_fields(usage, {"input_tokens": "input_tokens", "cached_input_tokens": "cache_read_input_tokens",
                                         "cache_write_tokens": "cache_creation_input_tokens", "output_tokens": "output_tokens"})
        if counters is not None:
            # Claude reports uncached input separately; all three counters are needed for total input.
            if all(key in counters for key in ("input_tokens", "cached_input_tokens", "cache_write_tokens")):
                counters["input_tokens"] += counters["cached_input_tokens"] + counters["cache_write_tokens"]
            else:
                counters.pop("input_tokens", None)
        return counters or None


_CC_DENIAL_DETAIL_KEYS = ("command", "file_path", "path", "pattern", "url")
MAX_CC_DENIALS = 20
CC_DENIAL_CHARS = 120


def cc_permission_denials(stdout: str) -> list[str]:
    """`cc_result_denials` of the final result event in `stdout`."""
    results = [e for e in json_lines(stdout) if e.get("type") == "result"]
    return cc_result_denials(results[-1] if results else None)


def cc_result_denials(result: dict[str, Any] | None) -> list[str]:
    """`Tool: detail` strings for the tool calls cc auto-denied (`result.permission_denials`), order-preserving, de-duplicated (#303).

    Strings are raw: the caller must redact (Sanitizer.clean) BEFORE truncating to CC_DENIAL_CHARS.
    """
    denials: Any = result.get("permission_denials") if result else None
    out: list[str] = []
    for item in cast("list[Any]", denials) if isinstance(denials, list) else []:
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, Any]", item)
        tool: Any = entry.get("tool_name")
        if not isinstance(tool, str) or not tool:
            continue
        tool_input: Any = entry.get("tool_input")
        detail = ""
        if isinstance(tool_input, dict):
            for key in _CC_DENIAL_DETAIL_KEYS:
                value: Any = cast("dict[str, Any]", tool_input).get(key)
                if isinstance(value, str) and value:
                    detail = value
                    break
        text = f"{tool}: {detail}" if detail else tool
        if text not in out:
            out.append(text)
        if len(out) >= MAX_CC_DENIALS:
            break
    return out
