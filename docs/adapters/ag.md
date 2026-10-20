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

`ag.argon` selects `gemini-4-argon-medium` on A-7D. Discover exact slugs and their
declared pools with `peerhub profile list` (JSON: `known_models`), or vendor slugs
with `agy models`; choose one with `--model <slug>`. Registry completeness does
not integrate arbitrary model selections into monitor/readiness. Unverified pool
mappings remain explicit; configured availability is not measured availability.

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

A quota refusal after possible execution stays uncertain. Reset text is shown
raw (including hour-only times such as `3pm`); PeerHub does not infer timezone or
rollover. The stream may remain quota-blocked after reset: inspect partial edits
and `peerhub diag health`, then follow uncertain-stream recovery before continuing
new work on a new stream. Never blindly replay the original work.

## Empty responses and denied tools

In headless mode agy auto-denies any tool that needs a permission it cannot prompt for (for example `command`) and still exits successfully with an empty `response` and a `denied_actions` list. PeerHub surfaces the names as `denied_actions` in the ask result and prints a one-line note. Read-only file tools work. Avoid shell tools in ag prompts, or add an allow-rule under `permissions.allow` in agy's `settings.json`; PeerHub never passes `--dangerously-skip-permissions` (#195).
