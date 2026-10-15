# Peer CLI option audit (2026-10-15)

Status: source/help audit plus bounded live canaries; recommendations are
pre-TDD. Production adapter argv is unchanged. Provider behavior findings are
dated, machine-specific observations and not guaranteed performance claims.

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
- **Promote to a high-priority TDD prototype: stream-json input/output.**
  Installed help and official headless docs say `--input-format stream-json`
  accepts one NDJSON message per line and requires `--output-format
  stream-json`. In direct live probes, both a two-turn CC process and a
  two-turn AG process returned two successful results and recalled a unique
  marker from turn one. This could remove AG's Windows command-line prompt
  ceiling, expose safe progress/step usage, and avoid repeated process startup
  when the same retained session has consecutive deliveries. Current PeerHub
  instead places AG's prompt in argv (30,000 UTF-8 bytes on Windows), expects
  one flat JSON result, and treats malformed/partial output conservatively.
  A transport change needs typed event allowlists, redaction, session-ID
  conflict checks, byte bounds, per-turn result framing, and a compatibility
  matrix; do not merely flip the output flag.
- **Observed startup signal, not a benchmark:** for one matched two-prompt AG
  scenario, one streaming process took 10.41 seconds wall time. Two processes
  with explicit `--conversation` took 19.32 seconds total (7.41 + 11.90); both
  recalled their unique marker. For one CC scenario, one streaming process
  took 5.47 seconds; two processes using explicit `--resume` took 9.78 seconds
  total (5.05 + 4.73), with successful recall in both. These are single
  sequential samples; service load, cache state, project instructions, and
  timing noise are uncontrolled. They justify a paired repeated benchmark,
  not a production latency claim or default change.
- **Usage semantics measured during streaming:** AG's per-turn
  `step_update.usage` and terminal `result.usage` are different scopes. The
  second result's `num_turns` was 2 and result usage was cumulative across both
  turns; its agent-response step usage was only that turn's 13,745 tokens.
  Separate-process resume also reported cumulative `num_turns`/usage. Never
  store AG result totals as per-delivery usage without exact baseline
  subtraction; prefer validated per-turn step usage when the event is
  complete, and retain source/scope/coverage. CC emitted a separate terminal
  result for each input turn (`num_turns=1` each in this probe), with usage
  corresponding to that result/turn. This behavior must be locked with
  versioned fixtures before replacing current parser semantics.
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
5. **Evidence has different confidence levels.** Local `--version`/`--help`
   probes cost no quota. The public ask canary and direct plan/stream probes
   spent quota and provide functional evidence, but not a statistically useful
   latency estimate. Exact provider billing/quota conversion remains unknown;
   reported token counters are not an invoice.

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
- For one-process streaming, test several sequential requests on the same
  retained session, exact result-to-delivery association, per-peer queue
  serialization, process lease/liveness, crash/restart fallback to explicit
  native resume, cancellation of one active turn without losing later
  deliveries, and no duplicate terminal commit after process reuse. Benchmark
  matched same-profile tasks against per-delivery process launch with repeated
  randomized A/B order and report distributions, not a single winner.
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
[Antigravity CLI headless mode](https://www.antigravity.google/docs/cli/headless/),
and [Gemini CLI headless mode](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md).
The Antigravity documentation matches the installed `agy` product and is the
primary source for streamed input, output, scope, and permission semantics;
local `agy 1.3.5 --help` remains the observed version contract.

## Live evidence log

Append one dated record for every paid option investigation. Do not overwrite
prior measurements when a CLI version changes. Store sanitized observations
only: never commit prompts with user data, session IDs, raw stdout, tool
arguments/results, authentication details, or vendor request identifiers.

### 2026-10-15 / run `CLI-OPT-20261015-A`

- **Environment:** local Windows workspace `D:\PkgDev\workspace\peerhub`,
  branch `docs/peer-review-20261015`, HEAD `adab24a9` at probe start. Local
  CLI versions: CC `2.1.299`, CX `0.162.1`, AG `1.3.5`; profiles used were
  CC standard `claude-haiku-5-5`, AG standard `gemini-3.8-flash-low`.
- **Existing public `peerhub ask` canary:**
  `python -m tools.live_gate --yes --only canary`; result **12 passed, 3
  skipped, 12 deselected** in 99.40 s. The skipped cases are the separately
  opted-in fresh/exact four-turn selector matrix, not failures. It verifies
  basic text+positive usage, native default resume recall, writable sentinel
  creation, and committed-start cancellation for CC/CX/AG. Per-test wall
  seconds (pytest measured; one sample each):

  | Peer | Basic ask | Native resume recall | Writable sentinel | Cancel |
  |---|---:|---:|---:|---:|
  | CC | 5.458 | 10.727 | 7.676 | 1.008 |
  | CX | 7.435 | 14.417 | 10.602 | 1.114 |
  | AG | 9.320 | 18.180 | 11.590 | 1.151 |

  These are end-to-end test-case durations, not model-only latency. The
  scenarios differ in work; cross-peer or feature causality must not be
  inferred. Cancellation timings include intentional early cancellation and
  are not answer latency.
- **Permission-mode probes:** direct CC `--permission-mode plan` and AG
  `--mode plan` were each asked to create a uniquely named file in an ignored
  probe directory. Both commands exited 0 with a successful text result;
  neither created the file. The explicit `plan` option therefore worked as a
  write-denial mode on this installation. CC's refusal response reported 403
  output tokens; AG returned 365 response characters (the AG write-denial run
  did not retain a separately inspected usage object). A second short,
  no-tool prompt succeeded in each plan mode. Direct runs were launched from
  a directory under the repository and may have inherited project
  instructions; do not use their token counts as a clean-workspace baseline.
  CC output usage for that short run was input 2, cached input 16,362,
  cache-creation input 10,025, output 50; AG usage was input 12,852, cached
  input 0, output 1, total 12,853. No speed conclusion follows from these
  single observations.
- **Persistent input stream, two turns each:** CC returned 7 JSON events and
  two terminal successes in 5.47 s; the second answer recalled its marker.
  Result usage was per-turn in these events (`num_turns=1` per result). AG
  returned 10 JSON events and two terminal successes in 10.41 s; the second
  answer recalled its marker; both results shared one conversation ID.
  Per-turn step usage and cumulative result usage differed as recorded above.
- **Separate-process explicit resume, two turns each:** CC `--resume` calls
  took 5.05 s and 4.73 s (9.78 s total); AG `--conversation` calls took 7.41 s
  and 11.90 s (19.32 s total). Both second turns recalled their first-turn
  markers. AG's second result reported cumulative `num_turns=2` and cumulative
  usage; CC's result counters remained turn-scoped in this probe. These
  single-run comparisons support a persistent-process prototype, but do not
  establish a stable 44–46% speedup.
- **Limits:** provider token/quota was consumed, but these CLI results do not
  expose authoritative subscription quota depletion or invoice cost. No CX
  optional-flag candidate was identified or changed; CX's JSONL, explicit
  sandbox, and exact resume passed the public ask canary. No `--output-schema`,
  AG `--sandbox`, `--max-turns`, or settings-bypass option was enabled. This
  evidence is local and bound to the versions above, not a release/G3 result.

### 2026-10-15 / run `SESSION-SELECT-20261015-B`

- **Command:** with `PEERHUB_LIVE=1` and
  `PEERHUB_SESSION_SELECTION_LIVE=1`, ran only
  `test_fresh_exact_four_turn_session_matrix` for CC/CX/AG.
- **Result:** **3 passed in 121.63 s** (four asks per peer, 12 provider turns
  total). Every peer verified initial remember, default native resume and
  recall, explicit fresh handle/generation change, then explicit selection of
  the original handle and recall across the other session. This closes the
  three skips from the ordinary canary run for this separate live test; it is
  still outside the release/G3 artifact and is not bound as release evidence.
- **Implication:** exact retained-session selection and native provider
  identity continuity work for these installed versions on this host. This
  does not prove crash recovery, cross-host filesystem durability, or
  long-lived child-process pooling.

### Update procedure

On each relevant provider CLI upgrade or adapter change: record local
`--version`/`--help` and command-builder commit; check official primary docs;
rerun the narrow option canary; append a new evidence-log entry; update the
current recommendation and version table without deleting earlier results;
then update linked TDD traceability. Rerun the full provider canary only when
the change affects invocation, permissions, session resume, event framing, or
telemetry. Keep the four-call selector matrix a distinct opt-in because it
makes additional paid turns.
