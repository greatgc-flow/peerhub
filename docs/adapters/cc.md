# Claude Code (`cc`) adapter

This page documents the current production contract. Historical migration notes live under
[`checkpoints/`](checkpoints/) and are not operational guidance.

## Current contract

- Executable: `claude.cmd` (observed version `2.1.283` on 2026-09-28)
- Transport: process pipe; the prompt is written to standard input
- Output: stream JSON; `result` is canonical text, `is_error` marks vendor
  failure.
- Inline prompt limit: 1,000,000 UTF-8 bytes
- Oversized evidence: rejected before spawn

Create invocations are equivalent to:

```text
claude.cmd -p - --output-format stream-json --verbose [--model MODEL] [--effort EFFORT]
```

PeerHub resolves model configuration centrally (workspace binding, then global
configuration, then packaged defaults). The adapter only translates a resolved
binding into CLI flags.

| Profile | Packaged model | Effort |
|---|---|---|
| `cc.standard` | `claude-haiku-4-5-20251001` | vendor default |
| `cc.effort` | `claude-sonnet-5-5` | `high` |
| `cc.deepthink` | `claude-opus-5-5` | `high` |
| `cc.pro` | `claude-fable-5-1` | `high` |

## Verify and troubleshoot

```powershell
peerhub observation refresh
peerhub diag
peerhub monitor
```

The vendor CLI owns authentication, account quotas, network access, and model
availability. A live facts failure must be reviewed before changing packaged
defaults or compatibility contracts.
