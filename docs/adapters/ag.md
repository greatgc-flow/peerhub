# Antigravity (`ag`) adapter

This page documents the current production contract. Historical migration notes live under
[`checkpoints/`](checkpoints/) and are not operational guidance.

## Current contract

- Executable: `agy.exe` (observed version `1.2.12` on 2026-09-28)
- Transport: process pipe; the prompt is passed with `-p`
- Output: flat JSON; `response` is canonical text.
- Inline prompt limit: 30,000 UTF-8 bytes on Windows; 120,000 elsewhere
- Oversized evidence: rejected before spawn, including bounded catch-up
- Artifact references: not supported

Create invocations are equivalent to:

```text
agy.exe -p <prompt> --output-format json [--model MODEL] [--effort EFFORT] [--mode accept-edits]
```

PeerHub resolves model configuration centrally (workspace binding, then global
configuration, then packaged defaults). The adapter only translates a resolved
binding into CLI flags; it does not contain model-selection policy.

Profile models and efforts are defined in the
[model profile manifest](../model-profiles/model-profiles.json); see its `profiles`
object for this provider. The manifest is checked against packaged defaults.

`--mode accept-edits` is added only for `ask --writable`. Compatible native
resume uses `--conversation SESSION_ID` when `ask --resume` is requested.

## Verify and troubleshoot

```powershell
peerhub observation refresh
peerhub diag
peerhub monitor
```

The vendor CLI still owns authentication, account quotas, network access, and model
availability. A live facts failure must be reviewed before changing
`model-defaults.toml`; observations are never auto-approved as new contracts.
