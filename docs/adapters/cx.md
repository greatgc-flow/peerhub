# Codex (`cx`) adapter

This page documents the current production contract. Historical migration notes live under
[`checkpoints/`](checkpoints/) and are not operational guidance.

## Current contract

- Executable: `codex.cmd` (observed version `0.162.1` on 2026-10-15; local probe)
- Transport: process pipe; the UTF-8 prompt is written to standard input (`-`)
- Output: JSON Lines events; completed `agent_message` items form canonical
  text.
- Inline prompt limit: 1,000,000 UTF-8 bytes
- Oversized evidence: rejected before spawn

Create invocations are equivalent to:

```text
codex.cmd exec --skip-git-repo-check -s read-only [-m MODEL] [-c model_reasoning_effort=EFFORT] --json -
```

PeerHub resolves model configuration centrally (workspace binding, then global
configuration, then packaged defaults). The adapter only translates a resolved
binding into CLI flags.

Profile models and efforts live in the packaged `model-defaults.toml`; list the effective ones with
`peerhub profile list`. The [refresh recipe](../model-profiles/model-profiles.json) explains how to update them.

## Verify and troubleshoot

```powershell
peerhub observation refresh
peerhub diag
peerhub monitor
```

`ask --writable` selects `-s workspace-write`; fresh sessions otherwise use
`-s read-only`. Compatible continuing asks use `exec resume SESSION_ID` with
`-c sandbox_mode="workspace-write"` (or `"read-only"` when not writable) because
resume does not accept `-s`. Changing writable mode starts a fresh generation.
Codex is the only adapter providing an OS/filesystem sandbox-enforced read-only boundary.
Environment restrictions still apply.
Authentication, quotas, network access, and model availability also remain
vendor-owned.
