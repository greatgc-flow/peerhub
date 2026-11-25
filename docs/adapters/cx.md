# Codex (`cx`) adapter

This page documents the current production contract. Historical migration notes live under
[`checkpoints/`](../history/adapter-checkpoints/) and are not operational guidance.

## Current contract

- Executable: `codex.cmd` ([observed version](../../peerhub/config_data/model-defaults.toml))
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
`peerhub profile list`. The [refresh recipe](../model-profiles/model-profiles.json) explains how to update them. `python -m tools.model_ping` proves the answering model and effort (session log). Each profile has an evidence card: `peerhub explain cx.<profile>`. See [Compatibility](../commands.md#compatibility) for the deprecated Codex profile alias.

`cx.astra` (`gpt-6.2-astra`, high) is an opt-in top profile on X-5H/X-7D; `ultra` gave the same benchmark score
for more latency ([benchmark](../benchmarks/profiles-2026-11-08.md)). Plans may omit a window (see lesson `plan-windows`).

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

## Windows sandbox workflow

In restricted unelevated Windows environments, `ask cx --writable` can block
writes to `.git` (including branch creation and commits), venv creation, and
pip installs. Observed restrictions also include a refused sandbox proxy, unavailable GitHub credentials and about 30 seconds per shell command. Use a writable session for edits and a supervised background job for long tasks. Tests that use `tmp_path` cannot run in this sandbox: pytest
creates every temporary directory, also the one named by `--basetemp`, with an
owner-only ACL that the restricted token cannot enter (measured 2026-11-14 on
Python 3.15). cx runs the other targeted tests and reports `tmp_path` tests as
NOT RUN; the moderator runs those tests outside the restricted session. Never
add shims or temporary folders to the repository.

Run process and scheduled-task checks in the lead's shell when the sandbox cannot enumerate them. If a cx lane cannot open the store, launch reader asks from the lead. For long asks, request checkpoint receipts and check the working tree for partial edits after a runtime error.

Start a query file with the actual task. Tell an implementer to do the work directly, without forwarding the prompt, running PeerHub or checking quota. Tell cx these constraints in the task prompt: cx edits files and runs focused
tests using an existing development venv; the moderator creates branches,
commits, pushes, and runs the full suite. Keep the sandbox restrictions in place.
