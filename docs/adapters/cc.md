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

`cc.fable` (`claude-fable-5-1`, high) is an opt-in top profile with pool F-7D only;
whether it also draws on shared C pools is unverified ([benchmark](../benchmarks/profiles-2026-11-08.md)).

`ask --writable` adds `--permission-mode acceptEdits`. Compatible native resume
uses `--resume SESSION_ID` for compatible continuing asks by default.
Without `--writable`, `--permission-mode acceptEdits` is omitted; in headless
mode (`-p -`), Claude Code refuses tool calls that require write permissions,
preventing workspace edits on fresh and resumed asks (provider-controlled; no OS sandbox).
In v0.24.1, cc `result.permission_denials` are surfaced as `denied_actions`;
an empty reply with denials is reported as `denied_empty`.

In 0.24.1, configured pools omitted from a complete, successfully parsed usage report render as `n/a not reported (plan X)` (X is the reported plan or `unknown`).

## Verify and troubleshoot

```powershell
peerhub observation refresh
peerhub diag
peerhub monitor
```

The vendor CLI owns authentication, account quotas, network access, and model
availability. A live facts failure must be reviewed before changing packaged
defaults or adapter behavior.
