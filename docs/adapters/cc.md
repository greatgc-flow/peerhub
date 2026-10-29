# Claude Code (`cc`) adapter

This page documents the current production contract. Historical migration notes live under
[`checkpoints/`](checkpoints/) and are not operational guidance.

## Current contract

- Executable: `claude.cmd` (observed version `2.1.299` on 2026-10-15; local probe)
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

Profile models and efforts live in the packaged `model-defaults.toml`; list the effective ones with
`peerhub profile list`. The [refresh recipe](../model-profiles/model-profiles.json) explains how to update them.

`ask --writable` adds `--permission-mode acceptEdits`. Compatible native resume
uses `--resume SESSION_ID` for compatible continuing asks by default.
Without `--writable`, `--permission-mode acceptEdits` is omitted; in headless
mode (`-p -`), Claude Code refuses tool calls that require write permissions,
preventing workspace edits on fresh and resumed asks (provider-controlled; no OS sandbox).

## Verify and troubleshoot

```powershell
peerhub observation refresh
peerhub diag
peerhub monitor
```

The vendor CLI owns authentication, account quotas, network access, and model
availability. A live facts failure must be reviewed before changing packaged
defaults or adapter behavior.
