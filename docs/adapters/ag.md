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
| `ag.deepthink` | `gemini-3.1-pro-low` | none |
| `ag.pro` | `gemini-3.1-pro-high` | none |

## Verify and troubleshoot

```powershell
peerhub adapter discover --json
agy.exe --version
agy.exe -p "/usage" --output-format json --print-timeout 15s
peerhub diag --fresh --json
python -m tools.peerhub_facts --live
```

`peerhub diag --fresh` invokes Agy's local `/usage` slash command in print mode.
It records four independent quota windows: Gemini 5-hour (`G-5H`), Gemini weekly
(`G-7D`), Claude/GPT 5-hour (`3P-5H`), and Claude/GPT weekly (`3P-7D`). The JSON
surface preserves each exact `remaining_fraction`, UTC `resets_at`, and computed
`reset_in`; the text dashboard prints both the 5-hour and weekly countdowns.
The observed Agy 1.2.12 command reports zero model tokens. Collection is bounded
by a timeout and falls back to the older statusline cache when the slash-command
payload is unavailable.

The vendor CLI still owns authentication, account quotas, network access, and model
availability. A live facts failure must be reviewed before changing
`model-defaults.toml`; observations are never auto-approved as new contracts.
