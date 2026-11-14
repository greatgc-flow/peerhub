# Claude Code (`cc`) adapter

This page documents the current production contract. Historical migration notes live under
[`checkpoints/`](checkpoints/) and are not operational guidance.

## Current contract

- Executable: `claude.cmd` (observed version `2.1.323` on 2026-11-13; local probe)
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
`peerhub profile list`. The [refresh recipe](../model-profiles/model-profiles.json) explains how to update them. `python -m tools.model_ping` proves the answering model (stream `modelUsage`), not the effort. Each profile has an evidence card: `peerhub explain cc.<profile>`.

`cc.fable` (`claude-fable-5-2`, high) is an opt-in top profile with pools F-7D and C-7D: Fable use also counts
in the weekly all-models window (measured from the `claude /usage` payload, `weekly_scoped_shares`: Fable may use up
to 50% of the weekly window; #334). Owner reset coupons (Fable and weekly, shown in the Claude app) are not listed by `/usage` or `/help`; the CLI only caches a boolean in its config (`unusedLimitResetStatusCache.holdsUnusedReset`, re-checked about every three days), with no count, type or redeem command (claude 2.1.323, 2026-11-13).

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

## Headless cc peers: read/test allow rules

Headless cc cannot answer approval prompts, so a read or test command without an
allow rule is denied and the review reports NOT RUN. For a project, add
`.claude/settings.local.json` with prefix rules for read/test commands only:

```json
{
  "permissions": {
    "allow": [
      "Bash(python -m pytest:*)",
      "Bash(python -m pyright:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(git show:*)",
      "Bash(gh issue view:*)",
      "Bash(gh pr view:*)",
      "Bash(gh run view:*)",
      "Bash(python -m peerhub guide:*)",
      "Bash(python -m peerhub explain:*)",
      "Bash(python -m peerhub profile:*)",
      "Bash(git blame:*)",
      "Bash(git rev-parse:*)",
      "Bash(git merge-base:*)",
      "Bash(git rev-list:*)",
      "Bash(git ls-tree:*)",
      "Bash(gh pr diff:*)",
      "Bash(gh pr checks:*)",
      "PowerShell(python -m pytest:*)",
      "PowerShell(python -m pyright:*)",
      "PowerShell(git status:*)",
      "PowerShell(git diff:*)",
      "PowerShell(git log:*)",
      "PowerShell(git show:*)",
      "PowerShell(gh issue view:*)",
      "PowerShell(gh pr view:*)",
      "PowerShell(gh run view:*)"
    ]
  }
}
```

The `guide`, `explain` and `profile` rules, `git blame|rev-parse|merge-base|rev-list|ls-tree` and `gh pr diff|checks` are offline or read-only reads; add the same entries as `PowerShell(...)` for PowerShell.
In reviewer prompts tell the cc reviewer "one plain command per call; no cd, &&, pipes": a compound command does not match an allow rule and is denied.

Prefix rules do not match a command that starts with an environment assignment
(`PYTHONPATH=src python -m pytest ...`), and a PowerShell `$env:` edit is refused.
Use a tiny repo-local launcher instead, for example `pt.py`, which sets the
environment itself and forwards its arguments to pytest
(`python pt.py --src DIR -- <pytest args>`), and add one allow rule for it:
`"Bash(python pt.py:*)"`. Keep the list to read/test commands; never allow writes
or network-changing commands for headless peers.

## Upgrading peerhub on Windows

`pip install -U peerhub` fails with WinError 32 while a running `peerhub.exe` locks
the target venv. Upgrade only when:

1. `peerhub diag running` lists no lease-active asks.
2. No `peerhub.exe` from the SAME venv is running. Filter by executable path, not by
   name; a long `monitor` from another venv does not lock this venv's exe. In
   PowerShell, for example:
   `Get-CimInstance Win32_Process -Filter "Name='peerhub.exe'" | Where-Object { $_.ExecutablePath -like '<venv>\Scripts\*' }`

Never rename, delete or replace a running exe; wait for it to exit, then upgrade
(`--no-cache-dir` avoids a stale wheel cache).
