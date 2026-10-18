# Antigravity (`ag`) adapter

This page documents the current production contract. Historical migration notes live under
[`checkpoints/`](checkpoints/) and are not operational guidance.

## Current contract

- Executable: `agy.exe` (observed version `1.3.5` on 2026-10-15; local probe)
- Transport: process pipe; the prompt is passed with `-p`
- Output: flat JSON; `response` is canonical text.
- Inline prompt limit: 30,000 UTF-8 bytes on Windows; 120,000 elsewhere
- Oversized evidence: rejected before spawn, including bounded catch-up
- Artifact references: not supported

Create invocations are equivalent to:

```text
agy.exe -p <prompt> --output-format json [--model MODEL] [--effort EFFORT] --mode accept-edits
```

PeerHub resolves model configuration centrally (workspace binding, then global
configuration, then packaged defaults). The adapter only translates a resolved
binding into CLI flags; it does not contain model-selection policy.

Profile models and efforts are defined in the
[model profile manifest](../model-profiles/model-profiles.json); see its `profiles`
object for this provider. The manifest is checked against packaged defaults.

Native `ag` execution requires `ask --writable`, which adds `--mode accept-edits`.
Without `--writable`, PeerHub refuses execution before spawning the CLI (#140),
because headless `agy.exe` (`-p`) does not enforce a read-only sandbox. Cached
terminal replay remains available without launching the provider. Compatible
native resume uses `--conversation SESSION_ID` for continuing asks by default.

## Verify and troubleshoot

```powershell
peerhub observation refresh
peerhub diag
peerhub monitor
```

The vendor CLI still owns authentication, account quotas, network access, and model
availability. A live facts failure must be reviewed before changing
`model-defaults.toml`; observations are never auto-approved as new contracts.
