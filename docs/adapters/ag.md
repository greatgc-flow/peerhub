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

Profile models and efforts live in the packaged `model-defaults.toml`; list the effective ones with
`peerhub profile list`. The [refresh recipe](../model-profiles/model-profiles.json) explains how to update them.

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

PeerHub's `--writable` gate and agy's tool permissions are separate. PeerHub
requires `--writable` even for read tasks; it selects `--mode accept-edits`,
which grants workspace-wide edit approval. Pass it on every turn, including
resumed turns. Direct `agy` file reads need no accept-edits mode. Set ag's `-w`
to the trusted project folder listed in `trustedWorkspaces`, not a temporary or
scratch directory; edit approval does not establish workspace trust.

In headless mode agy auto-denies tools needing an interactive permission prompt
and can still exit successfully with an empty `response` and a `denied_actions`
list. PeerHub surfaces those names in the ask result and prints a one-line note.
Shell access needs a matching `command(...)` or broad `command(*)` allow rule
under `permissions.allow` in agy's `settings.json`; accept-edits alone does not
approve shell commands. The statement that **only exact command strings**
can be authorized is misleading: `command(*)` is also supported but gives
broad shell access. Prefer a scoped matching rule when shell access is
intended, and verify its actual matching semantics in agy rather than assuming
literal equality. PeerHub never passes `--dangerously-skip-permissions` (#195).

For an empty reply, check these three causes before changing permissions:

1. **Untrusted workspace:** check the actual `-w` against `trustedWorkspaces`.
   Use the trusted project folder, even when the task only reads files.
2. **Unreadable target:** binary, NUL-containing, or huge files may defeat the
   normal file reader and cause a shell fallback that headless permissions deny.
   Check `denied_actions` and inspect the target locally; use a bounded text
   extract or explicitly approve the intended shell command rather than assuming
   accept-edits permits it.
3. **Wrong branch:** confirm the path exists on the checked-out branch; a path
   present only on another branch cannot be read from this checkout.

Image reads are denied through `peerhub ask ag`. If an image read is intended,
use direct `agy` with an exact-path read allow rule; PeerHub has no
`--allow-read PATH` option. Do not broaden workspace edit or shell approval to
solve an image read denial.

The #268 incident guidance dated **2026-10-30** also cautions that Flash image
transcription may omit or alter text. Verify transcription against the source
before relying on exact wording; a nonempty reply does not prove fidelity. This
is a dated task-specific caveat, not a claim about every Flash model or version.
