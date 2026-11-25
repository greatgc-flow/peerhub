"""Codex CLI arguments and terminal output."""
from __future__ import annotations

from peerhub.extensions.adapters.common import ProviderSpec, consistent_session_id, json_lines, usage_fields


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
        events = json_lines(stdout)
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
        events = json_lines(stdout)
        started = [e for e in events if e.get("type") == "thread.started"]
        return consistent_session_id(events, "thread_id", started[-1].get("thread_id") if started else None)

    def parse_usage(self, stdout: str) -> dict[str, int] | None:
        # On resumed threads these are raw cumulative counters; the Bridge will store baselines.
        results = [e for e in json_lines(stdout) if e.get("type") == "turn.completed"]
        return usage_fields(results[-1].get("usage") if results else None,
                             {"input_tokens": "input_tokens", "cached_input_tokens": "cached_input_tokens",
                              "cache_write_tokens": "cache_write_input_tokens", "output_tokens": "output_tokens"})
