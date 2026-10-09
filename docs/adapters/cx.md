# Codex (`cx`) adapter

This page documents the current production contract. Historical migration notes live under
[`checkpoints/`](checkpoints/) and are not operational guidance.

## Current contract

- Executable: `codex.cmd` (observed version `0.157.1` on 2026-09-28)
- Transport: process pipe; the prompt is a positional argument
- Output: JSON Lines events; completed `agent_message` items form canonical
  text.
- Inline prompt limit: 1,000,000 UTF-8 bytes
- Oversized evidence: rejected before spawn

Create invocations are equivalent to:

```text
codex.cmd exec --skip-git-repo-check -c model="MODEL" -c model_reasoning_effort="EFFORT" --json <prompt>
```

PeerHub resolves model configuration centrally (workspace binding, then global
configuration, then packaged defaults). The adapter only translates a resolved
binding into CLI flags.

| Profile | Packaged model | Reasoning effort |
|---|---|---|
| `cx.standard` | `gpt-6-luna` | `low` |
| `cx.effort` | `gpt-6.1-sol` | `high` |
| `cx.deepthink` | `gpt-6.1-sol` | `xhigh` |
| `cx.pro` | `gpt-6-astra` | `xhigh` |

## Verify and troubleshoot

```powershell
peerhub observation refresh
peerhub diag
peerhub monitor
```

The adapter intentionally inherits the vendor CLI's sandbox configuration.
Authentication, quotas, network access, and model availability also remain
vendor-owned.
