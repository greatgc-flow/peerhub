# Antigravity (`ag`) adapter

This page documents the current production contract implemented by
`peerhub.adapters.agy_adapter`. Historical migration notes live under
[`checkpoints/`](checkpoints/) and are not operational guidance.

## Current contract

- Executable: `agy.exe` (observed version `1.2.12` on 2026-09-28)
- Transport: process pipe; the prompt is passed with `-p`
- Output: flat JSON; `response` is canonical text and `conversation_id` is the
  resumable vendor session identifier
- Inline prompt limit: 1,000,000 UTF-8 bytes
- Artifact references: not supported

Create and resume invocations are equivalent to:

```text
agy.exe -p <prompt> --output-format json [--model MODEL] [--effort EFFORT]
agy.exe -p <prompt> --output-format json [--model MODEL] [--effort EFFORT] --conversation ID
```

PeerHub resolves model configuration centrally (workspace binding, then global
configuration, then packaged defaults). The adapter only translates a resolved
binding into CLI flags; it does not contain model-selection policy.

| Profile | Packaged model | Separate effort |
|---|---|---|
| `ag.standard` | `gemini-3.8-flash-low` | none |
| `ag.effort` | `gemini-3.8-flash-high` | none |
| `ag.deepthink` | `gemini-3.1-pro-high` | none |

## Verify and troubleshoot

```powershell
peerhub adapter discover --json
agy.exe --version
python -m tools.peerhub_facts --live
```

The vendor CLI owns authentication, account quotas, network access, and model
availability. A live facts failure must be reviewed before changing
`model-defaults.toml`; observations are never auto-approved as new contracts.
