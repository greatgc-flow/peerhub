# Peer CLI option audit (2026-10-15)

Status: source/help audit and pre-TDD recommendations. No provider prompt was
sent and no runtime option behavior beyond local `--help` / `--version` was
canaried. This is not a request to change adapter argv before its contracts
and live canary matrix are ready.

## Evidence and current commands

The authoritative current command builder is
[`peerhub/extensions/adapters/base.py`](../../peerhub/extensions/adapters/base.py).
On 2026-10-15, local executable probes reported Claude Code `2.1.299`, Codex
CLI `0.162.1`, and `agy` `1.3.5`. `--help` confirmed the flags listed below.
These versions identify this machine's observation only; they are not
minimum-version policy or a CI guarantee. Existing adapter docs still said
Claude `2.1.296` and `agy` `1.3.3`; update those observed-version labels when
this audit is accepted.

| Peer | Current PeerHub argv contract | Useful available options checked locally |
|---|---|---|
| CC / Claude Code | `-p - --output-format stream-json --verbose`; explicit `--resume ID`; resolved `--model`, `--effort`; `--permission-mode acceptEdits` only for writable asks | `--permission-mode` (`plan`, `acceptEdits`, `auto`, `manual`, etc.), `--allowedTools`, `--disallowedTools`, `--max-turns`, `--include-partial-messages`, `--no-session-persistence`, `--tools`, `--disable-slash-commands` |
| CX / Codex CLI | `exec --skip-git-repo-check -s read-only` for fresh read-only; `exec resume ID` for resume; resolved `-m`, reasoning-effort config; `--json -` | `--output-schema`, `--output-last-message`, `--add-dir`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`; resume inherits the initial sandbox and does not accept `-s` in the installed version |
| AG / `agy` | `-p PROMPT --output-format json`; resolved `--model`, `--effort`; `--mode accept-edits` only for writable; explicit `--conversation ID` | `--mode plan`, `accept-edits`, `auto`; `--sandbox`; `--input-format stream-json` (requires `--output-format stream-json`); `--json-schema`; `--print-timeout`; `--disable-slash-commands`; ambiguous `--continue` |

All peers already use explicit model/effort profiles and explicit native
session identifiers on resume. PeerHub owns end-to-end timeout, output-size
limits, process-tree cancellation, session binding, and uncertainty handling.
Provider flags must not create competing owners for those policies.

## Per-option disposition

### CC

- **Candidate for TDD: explicitly pin read-only mode to `--permission-mode
  plan`.** Today the read-only invocation omits permission mode and can inherit
  CLI configuration; writable calls explicitly select `acceptEdits`. A
  consistent `plan` / `acceptEdits` pair may make the permission intent
  reproducible. First verify `plan` preserves required read/search tools,
  current output events, native resume, and session binding. Include effective
  mode in the binding/fingerprint if it changes the resumed session's
  capability. Do not silently apply to existing sessions.
- **Do not copy legacy `--permission-mode default`.** The legacy hardening
  report recommended it with `--allowedTools`, but local Claude `2.1.299`
  help no longer lists `default` as a valid value. Applying that historical
  spelling now risks pre-start CLI rejection. Re-probe on every supported
  version before considering equivalent behavior.
- **Do not add `--allowedTools` alone as a deny-list.** Its help says allowed
  tools are allowed without prompting; it does not establish that all other
  tools are denied. A real allowlist also needs an audited capability matrix
  per read-only/writable profile and negative tests. `--tools` is a separate
  surface that can restrict the available tool set, but may remove tools
  expected by profiles.
- **Defer `--max-turns`.** It could cap a runaway agentic loop, but it can
  truncate a valid answer and is not cross-peer equivalent. PeerHub already
  enforces wall-clock and process/output bounds. Consider only as an explicit
  per-profile policy with a distinguishable provider-limit result and live
  completion canaries.
- **Defer `--include-partial-messages`.** It could expose earlier progress,
  but adds high-volume partial text to stream JSON and does not reduce model
  latency. Adopt only with incremental event parsing, privacy/redaction rules,
  bounded handling, and a demonstrated silence-timeout benefit.
- **Reject `--no-session-persistence`.** Claude help says sessions cannot be
  resumed when enabled; that conflicts directly with PeerHub native session
  reuse.
- Keep `-p -`, `stream-json`, and `--verbose` for the established noninteractive
  result/event contract. Do not replace `--resume ID` with `--continue`, which
  selects a directory's latest conversation rather than PeerHub's exact
  retained session.

### CX

- **Keep `--skip-git-repo-check`** because PeerHub workspaces are not required
  to be Git repositories. This only bypasses Codex's repository precondition;
  it is not a permission or sandbox bypass. Continue pinning the subprocess
  working directory to the selected workspace.
- **Keep explicit `-s read-only` / `workspace-write` on fresh sessions.** On
  resume, the installed CLI inherits the original sandbox and rejects `-s`;
  PeerHub must continue enforcing writable-mode compatibility in the session
  binding before dispatch.
- **Do not use `--ephemeral`.** The installed help explicitly says it avoids
  persisting session files; that defeats durable native resume.
- **Do not add `--ignore-rules` or `--ignore-user-config` by default.** The
  first disables configured execution policy; the second changes ambient
  runtime and authentication/config behavior. Neither is a general speed
  option. Keep the existing local security rules active.
- **Defer `--output-schema`** to a future structured-answer feature. It
  constrains the final model response but does not improve ordinary free-text
  `ask`, and would need cross-peer API semantics and distinct validation
  failures.
- **Do not add `--output-last-message`** while PeerHub already derives the
  final answer and telemetry/session evidence from JSONL events. A second
  output channel risks disagreement without removing required event parsing.
- Avoid `--add-dir` unless a user explicitly requests a wider workspace; it
  broadens the filesystem scope, especially under `workspace-write`.

### AG

- **Candidate for TDD: explicitly pin read-only mode to `--mode plan`.** Like
  CC, the current command omits a mode for read-only asks and only pins
  `accept-edits` for writable asks. Verify tool availability, response shape,
  resume behavior, and mode binding with the installed CLI before adoption.
- **Do not treat `--sandbox` as filesystem confinement.** The legacy branch's
  empirical security review found it restricts terminal behavior but does not
  confine filesystem access. It may be tested as defense in depth if the
  terminal restriction is acceptable, but OS-level workspace confinement and
  explicit permission mode remain separate requirements.
- **Promising but larger follow-up: stream-json input/output.** Installed help
  says `--input-format stream-json` accepts one NDJSON message per line and
  requires `--output-format stream-json`. This could remove the Windows
  command-line prompt ceiling and enable progress/step telemetry. Current
  PeerHub instead places the prompt in argv (30,000 UTF-8 bytes on Windows),
  expects one flat JSON result, and treats malformed/partial output
  conservatively. A migration needs a typed event allowlist, redaction,
  session-ID conflict checks, byte bounds, and a compatibility matrix; do not
  merely flip the output flag.
- **Do not use `--continue`.** It resumes the latest conversation implicitly;
  PeerHub already passes an exact `--conversation ID`, which preserves session
  identity and avoids wrong-session selection.
- **Defer `--json-schema`.** It is valuable for explicit structured-output
  use cases but does not improve the current canonical free-text response.
- **Keep timeout ownership in PeerHub.** `--print-timeout` duplicates the
  process supervisor's timeout and cancellation path; consider wiring it
  only if a measured CLI shutdown defect shows the parent deadline is
  insufficient.
- Keep explicit `--output-format json` until stream-json support is designed
  end-to-end. Do not add slash-command suppression absent evidence that prompt
  slash text is being interpreted as CLI commands.

## Cross-peer decisions

1. **No generic “fast” flag is justified.** `--max-turns`, lower effort, and
   provider-specific stream modes change completion semantics. Use the
   existing profile's model/effort and PeerHub timeout controls; measure wall
   latency by peer/model/profile before changing defaults.
2. **Session identity takes precedence over convenience flags.** Keep explicit
   exact-ID resume; reject “latest” selectors. Preserve native transcript
   persistence and permission-mode fingerprints.
3. **Permission intent should be explicit, subject to canary.** Read-only
   `plan` mode for CC/AG is the strongest candidate. This is not yet proven to
   preserve current tool capability; `--writable` remains explicit and is
   bound to the session. Codex already pins fresh sandbox mode.
4. **Avoid duplicate controls.** PeerHub remains authoritative for deadlines,
   process-tree cancellation, retries, byte caps, session selection, and
   output acceptance. Vendor-level controls are added only for a distinct
   verified guarantee.
5. **No provider canary has been run in this audit.** `--version` and `--help`
   are local measurements; they incur no model request. Option behavior,
   latency, token/quota impact, native-resume recall, and permission behavior
   remain unmeasured here.

## Required TDD / canary before changing argv

- Generate command snapshots for every provider across fresh/resumed,
  read-only/writable, profile/model/effort, Windows/non-Windows, and supported
  minimum/current CLI versions.
- For CC/AG `plan`, prove read-only file inspection succeeds, workspace write
  is refused, expected tools remain available, stream/result decoding works,
  resume stays bound to the same mode, and `--writable` can still make an
  authorized sentinel change.
- For CC allowlisting, test both allowed and disallowed tools; prove the
  effective permission mode fails closed, not merely that flag parsing passes.
- For AG stream-json, test long prompts near/over Windows argv limits,
  malformed/truncated events, retries, tool/text privacy, output caps,
  session-ID conflicts, usage scope, process cancellation, and refusal
  certainty.
- Record wall latency and bytes, not only model-reported duration; separate
  process startup, CLI setup, model response, tool execution, and PeerHub
  orchestration where directly measurable. Compare the same profile and task
  cohort; do not infer an option made the model faster from one request.
- Update `peerhub/extensions/adapters/base.py`, adapter docs, static command
  contracts, session fingerprints where relevant, and live-gate matrix
  together. No option is approved merely because the installed parser accepts
  it.

## Research lineage

Legacy branch examined: `origin/legacy/v0-main-final` at
`57a137cd6a0cc7e89124ea2b87b72995087627a5`. Relevant prior evidence includes
`docs/history/from-engram-repo/protocol-docs/ops/overnight-hardening-2026-07-03.md`
(CC allowlist proposal, Codex `--ignore-rules` removal, and AG sandbox
limitation) and
`docs/design/HUB-REPLACEMENT-GAP7-DIAGNOSTICS-2026-08-24.md` (diagnostic
source boundaries). Legacy options were rechecked against installed help;
notably, the old Claude `default` permission-mode recommendation is now stale.

Current official references: [Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage),
[Codex CLI source](https://github.com/openai/codex/blob/main/codex-rs/exec/src/cli.rs),
[Gemini CLI headless mode reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md).
The local installed `agy` help is authoritative for `agy 1.3.5`; the Gemini
reference is supporting lineage only and may not describe every fork/version
behavior.
