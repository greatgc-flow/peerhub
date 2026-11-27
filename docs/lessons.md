# PeerHub Lessons

Generated from `peerhub/config_data/lessons.toml`; edit that file, then regenerate with:

```powershell
python -c "from pathlib import Path; from peerhub.cli.lessons import render_lessons_reference; Path('docs/lessons.md').write_text(render_lessons_reference(), encoding='utf-8', newline='\n')"
```

Use `peerhub explain <free words>` for offline, case-insensitive AND-token lookup over titles and keywords.
Guidance codes and result labels take priority, followed by exact profile IDs (for example `cc.effort`), the index (bare `peerhub explain`, `lessons` or `list`), exact lesson IDs and aliases, and keyword lookup. Each alias opens its lesson in full. Keyword matches list recipes first, then sort by ID. A single match prints in full. Open one of several matches with `peerhub explain <id>`.

## ag recipe for permissions, empty replies and long asks (`ag-recipe`)

- Aliases: `ag-requires-writable`, `ag-silence-long-asks`, `ag-bulk-profile-heuristics`, `ag-empty-reply-causes`, `ag-permission-limit`
- Keywords: ag, agy, recipe, permission, permissions, writable, read-only, sandbox, limit, failed_not_started, empty, reply, denied_empty, delivered, trustedWorkspaces, allow, allow-list, scope, glob, wildcard, grep, cat, inline, regex, command, files, binary, prompt, query-file, resume, pings, reviews, ask_delivery_failed, silence, silent, long, asks, timeout, uncertain, blocked, stream, profile, effort, flash, argon, bulk, image, WinError, 206, size
- Symptom: ag refuses an ask without write permission, returns an empty reply after a denied tool, stalls on a long ask, or rejects a large prompt.
- Fix: For votes, put the task text inline via --query-file and say "no tools" in a private empty workspace (independent replies; batch rejects overlapping writable workspaces). For repo work, ag reads files with its file reader when -w is under a folder in agy trustedWorkspaces, git worktrees included (measured 2026-11-27); files outside -w can be denied, so inline them. Pass the trusted project root with -w and use --writable on every turn, including pings and reviews, because agy lacks a read-only sandbox. After an empty or denied_empty reply, start a new stream with the content inlined. See [docs/adapters/ag.md#empty-responses-and-denied-tools](adapters/ag.md#empty-responses-and-denied-tools).
  - ag can stay silent until completion. For long asks, use --timeout-seconds 7200 --silence-timeout-seconds 7200. Check for partial effects after an uncertain reply. See [docs/guidance-reference.md#ask_uncertain](guidance-reference.md#ask_uncertain).
  - Try flash-medium if flash-low returns an empty reply on bulk work. Prompts and catch-up history go over stdin up to 1,000,000 bytes. ag uses argv if agy lacks stream-json input or if PEERHUB_AG_TRANSPORT=argv is set. The argv limits are 30,000 bytes on Windows and 120,000 on POSIX. Inlining a file still counts toward the limit. Split an oversized prompt, or start a new stream if catch-up history pushed it over. Use a new request ID for a changed prompt. For an ag argv limit, cc/cx can use stdin on a separate stream. Cross-check dense numeric image tables.
- Issue: #118, #140, #284, #287, #290, #300, #302, #309, #359, #391, #406

## API peers ca, oa and ga: text-only asks on API credits (`api-peers`)

- Keywords: ca, oa, ga, api, ant, openai, gemini, OPENAI_API_KEY, GEMINI_API_KEY, RESOURCE_EXHAUSTED, credits, credit, balance, stateless, truncated, incomplete, request_rejected, inline, vote, votes, text
- Symptom: A vote, text review or documentation check spends a subscription pool of cc, cx or ag although it needs no tools or files; or a ca/oa/ga ask is refused (request_rejected), ends truncated or incomplete, or does not see a file; or the credit balance of an API is wanted.
- Fix: Inline text with --query-file and choose --profile or --model. Use one ask per stream. See [docs/api-credits.md](api-credits.md) for API credits, credentials and outcomes.
- Issue: #378, #381, #384

## Ask basics: stream reuse, long asks, tiers, transport (`ask-basics`)

- Keywords: ask, basics, example, request-id, foreground, tiers, transport, blocks
- Symptom: A caller needs the reuse pattern, the tier choice, the long-ask behaviour or the transport options that the short guide states only as decisions.
- Fix: Use the same stream for follow-up turns in one task, with a new request ID per turn; start a new stream for a new task or each independent vote. Use --dry-run to preview the ask. See [docs/commands.md#ask](commands.md#ask) for flags, transport and timeouts.
- Issue: 0.25.0 plan D5

## Ask receipts, outcomes, and profile measurements (`ask-metrics`)

- Keywords: receipt, outcome, profile stats, profile check, measurement, coverage, stream budget, query file
- Symptom: You want to compare profiles, mark an answer's outcome, or understand an ask receipt or a stream budget warning.
- Fix: Run peerhub profile stats --json. Mark the result with peerhub outcome using requested_record_id or the ID from the terminal receipt. See [docs/config-hierarchy.md#profile-measurements-and-stream-budgets](config-hierarchy.md#profile-measurements-and-stream-budgets).
- Issue: 0.25.0 plan M2

## Explicit timeouts for implementer and review asks (`ask-timeouts`)

- Keywords: timeout, timeout-seconds, 600, implementer, review, writer, ask_uncertain, runtime_timeout, cc, cx
- Symptom: A long implementer or review ask ends as ask_uncertain (evidence runtime_timeout) with partial edits in the workspace.
- Fix: Raise the timeout above the 600-second default for long asks. Inspect the working tree after an uncertain result. See [docs/commands.md#ask](commands.md#ask) and [docs/guidance-reference.md#ask_uncertain](guidance-reference.md#ask_uncertain).
- Issue: #350

## Shell batching on Windows (CRLF, last line) (`batch-shell-crlf`)

- Keywords: batch, shell, crlf, profile, newline, windows, read, job, ALL_DONE, dry-run, bash, heredoc, backslash, escaping
- Symptom: A shell loop passes a profile with a trailing carriage return, drops the final line, or prints ALL_DONE despite failures. A Windows Bash runner passed a doubled backslash through a quoted heredoc as a single backslash.
- Fix: Validate with peerhub batch run --dry-run. Stage scripts that contain escapes in a file and use newline="\n" for Python text writes that require LF. See [docs/windows.md#job-lists](windows.md#job-lists) and [docs/windows.md#script-files-and-escapes](windows.md#script-files-and-escapes).
- Issue: #289 and Windows shell check, 2026-11-14

## Identify your session's running asks (`caller-ownership`)

- Keywords: caller, ownership, running, owner, session, PEERHUB_OWNER
- Symptom: A shared store shows running asks from several lead sessions.
- Fix: Generate a random PEERHUB_OWNER once at the start of each lead session and export it to every shell that runs PeerHub for that session. In PowerShell, run `$env:PEERHUB_OWNER = (python -c "import uuid; print(uuid.uuid4().hex)")`. In a POSIX shell, run `export PEERHUB_OWNER=$(python -c 'import uuid; print(uuid.uuid4().hex)')`. Keep the value for the entire session. Never share it or set it in a shell profile. Set PEERHUB_LEAD to a readable label and use PEERHUB_BATCH_ID and PEERHUB_BATCH_TASK for batch work. `peerhub diag running` reports how the stored caller relates to the current reader. The first applicable rule wins. A difference in known hosts or PID namespaces takes precedence and means other. If both owner tokens are valid, equality means mine and a difference means other. Otherwise, two valid CLAUDE_CODE_SESSION_ID values are compared the same way. With insufficient evidence the result is unknown. Labels, paths, batch IDs and task IDs are descriptive. Mine includes asks started by this session and by processes it launched, including nested provider asks that inherit its identity. For other and unknown asks, only observe and report them. Never cancel them or reuse their streams.
- Issue: Monitor design record M10

## A cut-off reply usually means your tool timed out, not peerhub (`caller-timeout-claim-held`)

- Keywords: claim, claim_held, ask_claim_held, preamble, cut off, partial, timeout, timed out, background, request-id, replay, cx, long
- Symptom: An ask shows only a preamble ("I'll audit ..."), then the next ask on the same stream fails with ask_claim_held.
- Fix: Wait for the claim to end, then rerun the identical command with the same request ID. A settled result replays without a new call. See [docs/guidance-reference.md#ask_claim_held](guidance-reference.md#ask_claim_held).
- Issue: #312

## cc and cx permissions, sandbox limits and long asks (`cc-allow-rules`)

- Aliases: `cx-sandbox-and-prompt-hygiene`, `concurrent-cc-lanes`
- Keywords: cc, claude, allow, rules, rule, allowlist, bash, permission, permissions, denied, denied_actions, model, fable, haiku, headless, prefix, exact, settings, cx, codex, windows, sandbox, unelevated, process, enumeration, AGENTS.md, file, query, forward, implementer, pytest, tmp_path, acl, basetemp, store, reader, orchestrator, checkpoint, receipts, runtime_error, silence, partial, concurrent, lanes, memory, reaper, supervisor, parent, shell
- Symptom: Headless cc denies a command or loses its parent shell, or a cx lane cannot read the store, run tmp_path tests or finish a long ask.
- Fix: For cc, check denied_actions. Bash rules match the exact command string. Models can wrap the command differently. Use a narrow prefix such as Bash(peerhub:*) and matching PowerShell rules when appropriate. See [docs/adapters/cc.md#headless-cc-peers-readtest-allow-rules](adapters/cc.md#headless-cc-peers-readtest-allow-rules).
  - For cx, report tmp_path tests as NOT RUN for the lead. Unelevated process and task enumeration can report UNKNOWN after a delay on Windows. When cx sandbox lanes cannot open the peerhub store, reader asks must be launched by the orchestrator. Give the lane the task directly in the query file to avoid forwarding it as another ask. For long asks, use checkpoint receipts and a silence timeout. A runtime_error can leave partial edits. See [docs/adapters/cx.md#windows-sandbox-workflow](adapters/cx.md#windows-sandbox-workflow).
  - Size memory-heavy lanes from the load line in peerhub diag --plan. Run long jobs under a background supervisor and check in short polls. If the supervisor is reaped, check peerhub diag running and inspect receipts and partial effects before starting new work.
- Issue: #285, #286, #288, #303, #352, #391

## Discovery and inspection commands (`discovery-commands`)

- Keywords: discovery, inspection, commands, diag, model, models, slugs, sessions, facts, needs_recheck
- Symptom: Which read-only commands show quota, pools, sessions, accounts and models, and how the line-1 facts alert clears.
- Fix: Use peerhub explain lessons to find a topic, peerhub diag --plan for machine and quota evidence, and peerhub profile list for models. See [docs/commands.md](commands.md) for inspection commands and [docs/model-profiles/README.md#refresh-from-installed-clis](model-profiles/README.md#refresh-from-installed-clis) for fact drift.
- Issue: 0.25.0 plan D5

## CLI exit codes and error output (`exit-codes`)

- Keywords: exit, code, error, stderr, json
- Symptom: A caller is interpreting a process exit or an error report.
- Fix: Ask returns 0 for delivery, terminal replay or a valid dry run, 1 for uncertainty or delivery failure (including denied_empty or session_lost), 1 for an unregistered peer (peer_not_found), 2 for syntax, invalid selectors or idempotency conflict, and 3 for a compare-and-swap conflict. Batch run returns 0 for a valid dry run or all tasks completed and 1 for an invalid manifest or unsuccessful tasks. An empty terminal reply without an error exits 0 in ask and 1 in batch (UNVERIFIED). Diag health, quota, readiness, running, --plan and integrity use 0 for OK, 4 for a storage/read fault (including PARTIAL reports), 5 for an unavailable store, and 6 for an unsupported schema version. A missing alternate --observation-db with a healthy main store gives health 4 (PARTIAL) and quota 5 (UNAVAILABLE). Diag health returns 1 for a missing stream. Update --check returns 0. Update --plan returns 0 when ready, 1 if an ask is or may be running, and 4/5/6 for an unreadable/missing/incompatible store. Explain returns 0 for a match or 2 for an invalid topic or profile. Monitor snapshot/watch use 0 for success, 1 for an agy token-use stop or a failed final refresh in finite watch, and the diag store codes. Monitor open returns 1 for an unknown ask ID (not_found). Commands that emit invalid_arguments exit 2. Observation refresh returns 0 for OK or 1 for a non-OK refresh. Shared store errors use the diag store codes as applicable. Coded errors appear on stderr as [code] message; next: ... With --json, ask errors and invalid manifests can include an error object with code and message on stdout. Other reports use command-specific JSON. Run peerhub explain CODE for guidance.
- Issue: #446

## Independent review votes (`independent-peer-votes`)

- Aliases: `review-recipe`, `batch-review-and-verify`, `review-loop`, `review-scores-to-100`
- Keywords: peer, vote, votes, author, independent, unanimity, review, self-vote, batch, responses, verdict, fresh, consequential, review-loop, review-scores-to-100, loop, score, round, brief, materiality, contract, narrow, defects, deduction, unverified, suite, targeted, ports, WinError, worktree, cross-family, final, tag, gitlink, findings, mechanical, sweep, draft, verify, script
- Symptom: You need independent votes on a fixed diff, a complete sweep of findings, or a release review with deductions tied to defects.
- Fix: Vote policy: routine check = ONE reviewer, FRESH; consequential (release, schema, deletion, public API) = 3 independent peers, unanimity. Use a new stream per voter: --fresh on a used stream can still include up to 100 earlier records (24 KB), even when a stream name is given. A named stream is for review rounds on one diff only. Require voters did not author the document. There is no `vote` command: run peerhub batch run votes.json --batch-id ID --max-parallel n --responses. cc/cx use the repository workspace, not writable; ag uses a private empty directory as workspace (always writable). Keep prompts inline. Reply VERDICT / BLOCKERS / NITS (<= 300 tokens). See [docs/commands.md#ask](commands.md#ask).
  - Ask for the complete mechanical sweep in one list and compare identifiers with a small script before voting. Check drafts against files and command output, then have a second reviewer triage objections. Give editing lanes disjoint files, keep reviewers read-only, and give Git writes to one lead. See [CONTRIBUTING.md#working-with-ai-peers](../CONTRIBUTING.md#working-with-ai-peers).
  - Use the release bar in [CONTRIBUTING.md#release-lessons](../CONTRIBUTING.md#release-lessons). Paste this reviewer brief: "Score each item 0-100. Deduct only for a defect that violates a documented contract or is reachable from supported inputs. Label other findings as recommendations. For each deduction give the exact fix (file:line + change). Report unrun checks as NOT RUN." Re-review the exact shipped tag and confirm the final candidate with a reviewer from another model family. The coordinator runs the full suite once and shares the result. Reviewers run only targeted tests. Give them allow rules and run checks they could not. Record the commit and time for each live measurement. After two rounds finding new defects in one new component, narrow its contract (fail closed). Avoid `git add -A` in a checkout with agent worktrees because it stages them as gitlinks.
- Issue: #297, #298, #345, #366, 0.25.0 plan D6

## Installed vs checkout mismatch (`installed-vs-checkout`)

- Keywords: python, pip, installed, checkout, mismatch, PYTHONPATH, version, source, module
- Symptom: The pip-installed peerhub command behaves differently from the current checkout.
- Fix: Run python -m peerhub from the checkout (or set PYTHONPATH to it), or install that checkout with python -m pip install -e . using the same Python environment.
- Issue: #117

## Keep peer instructions, memory and quota spend small (`instruction-diet`)

- Keywords: instruction, diet, memory, quota, observation, refresh, snapshot, profile, ladder, pytest, launcher, allow, headless, cc, tokens, global, workspace, votes, deepthink
- Symptom: Every peer ask re-reads a large memory index or instruction file, a per-turn observation refresh burns quota, headless cc cannot run pytest, or 60-word votes run on the heaviest profile.
- Fix: For headless cc, allow a single repo-local pytest launcher that sets the environment and forwards arguments. Prefix rules miss environment-prefixed commands. Refresh observations every 30-60 minutes. AI agents use peerhub diag --plan each turn. People use peerhub monitor. Keep peer memory and instructions small and workspace-scoped. Keep global rules generic. Use standard for bulk work and votes, effort for code, and deepthink for review and design.
- Issue: #337

## invalid_arguments CLI input (`invalid-arguments`)

- Keywords: invalid_arguments
- Symptom: The command arguments were rejected.
- Fix: Run peerhub COMMAND --help, correct the arguments and retry only if no execution began.
- Issue: 0.24.0

## Monitor layout, quota states and alerts (`monitor-states`)

- Aliases: `monitor-view`, `monitor-accounts`, `monitor-alerts`, `monitor-insuff`, `monitor-stale-window`, `monitor-probe-age`, `monitor-drift`, `plan-windows`, `cx-plan-windows`, `a1`, `a2`, `a3`, `a4`, `a5`, `a6`
- Keywords: monitor, pace, low, fast, aged, cycles, tty, legend, quota, unknown, est, estimated, gone, overdue, orphan, view, order, rich, plain, glyph, glyphs, colour, color, colours, bar, pill, rule, rules, meter, palette, NO_COLOR, unicode, accounts, account, login, token, expiry, credits, display_plan, plan_changed, alerts, a1, a2, a3, a4, a5, a6, INSUFF, STALE, window, PROBE, age, DRIFT, cc, ag, cx, absent, not reported, plan, windows, cx-plan-windows, codex, claude, agy, pool, 5h, 7d, X-5H, X-7D, prolite, reported, incomplete
- Symptom: You want to interpret the terminal frame, account rows, quota readings or alerts.
- Fix: Use --expand for the full DB path and detail rows. PEERHUB_NO_LINKS disables OSC 8 and prints a copyable Guide: line when it fits. See [docs/monitor.md](monitor.md) for layout and refresh timing.
  - Pool states: ok enough | low >0-10% | fast >10% + threat (2+ samples) | out 0% | aged stale | reset passed | n/a plan no longer reports it (diag quota may keep an older value) | ? unknown | blocked child pool whose parent is out. Check the reading's age before acting on LEFT or PACE; a reset short window under a weekly parent shows `reset · refresh pending`. In --expand, quota notes explain windows the provider omitted or disabled (n/a). For aged/reset evidence or a failed probe, run peerhub observation refresh --peers PEER; if refresh fails again, check authentication and the installed CLI. For a6 facts drift (such as `<peer>: version_drift` or `ag: unknown_pool`; expanded footer `DRIFT:<peers>`), refresh the packaged model facts or upgrade PeerHub. See [docs/monitor.md#pools](monitor.md#pools) and [docs/model-profiles/README.md#refresh-from-installed-clis](model-profiles/README.md#refresh-from-installed-clis).
  - Read ACCOUNTS in --expand or JSON capacity.accounts for the plan, login and token expiry. See [docs/monitor.md#accounts](monitor.md#accounts).
  - For gone, orphaned and overdue asks, see [docs/monitor.md#running](monitor.md#running). Alert meanings are in [docs/monitor.md#alerts](monitor.md#alerts).
  - a1 Ask ended badly.
  - a2 Ask is gone, orphaned or overdue.
  - a3 Quota pool is exhausted.
  - a4 Reserved.
  - a5 Account is logged out or its token has expired or is expiring.
  - a6 A group read failed or model facts need a recheck.
- Issue: #310, #311, #328, #329, #376, #378, #412, #414, #422

## Spread parallel work over the least quota (`parallel-quota`)

- Aliases: `quota-aware-peer-runs`, `multi-peer-quota-shaping`, `batch-and-load`, `stream-reuse-cost`
- Keywords: parallel, quota, pace, pools, batch, loop, distribute, minimal, ram, free, memory, slots, load, diag, plan, resource, resources, 429, reset, stream, pool, flash, workers, capacity, cx, ag, cc, consolidated, draft, timer, sleep, concurrency, balance, cancel, manifest, cost, side-effects, reuse, resume, session, context, tokens, vote
- Symptom: You need to size a parallel wave, a shared pool is close to exhaustion, or a peer returns HTTP 429.
- Fix: Read peerhub diag --plan and choose usable pools by lowest 7D PACE first. Spread work across eligible pools, including distinct pools of one peer. Size the wave from the load line. Validate with peerhub batch run tasks.json --batch-id ID --max-parallel n --dry-run. Execution spends quota.
  - Refresh only the peers the next wave may use (peerhub observation refresh --peers cx ag) and read the plan after each wave. Defer exhausted pools and reconcile uncertain work before retrying. Use spare quota near reset on needed review or preparation. See [docs/batch.md#parallel-work-over-the-least-quota](batch.md#parallel-work-over-the-least-quota).
  - Reserve a scarce pool for consolidated final verification. Run delayed jobs in a supervised process so they can outlive the caller session. Reuse a stream within one task to limit catch-up context. Start a new stream for a new task or independent vote.
- Issue: #288, #297, #298, #299, #340, 0.25.0 plan D5, owner decisions 2026-11-22 and 2026-11-25

## Peer sub-agents inside one ask (`peer-subagents`)

- Keywords: subagent, subagents, sub-agent, sub-agents, Agent, spawn_agent, invoke_subagent, multi_agent, fan-out, loop
- Symptom: A large task could run as parallel helpers inside one peer ask, or a prompt expects /loop-style persistence inside an ask.
- Fix: Every peer can fan out sub-agents inside one ask: cc through its Agent tool; cx through spawn_agent only when the prompt names that tool explicitly (with features.multi_agent enabled); ag through invoke_subagent. There is no /loop-style persistence inside an ask: the ask ends with the peer's reply. Sub-agents spend the same peer quota: give each a disjoint task and ask for one merged final report.
- Issue: #361

## Report an issue (`report-issues`)

- Keywords: report, issue, issues, github, bug
- Symptom: Something behaves wrongly and the problem should be filed without leaking secrets.
- Fix: Check for an existing report. Include peerhub --version, the OS, the exact command, the outcome or exit code, and any workaround. Use neutral examples and redact shared-store paths, stream names, labels and prompt or reply text. See [CONTRIBUTING.md#reporting-issues-and-requesting-features](../CONTRIBUTING.md#reporting-issues-and-requesting-features).
- Issue: 0.25.0 plan D5

## Setup: what each peer needs (tool, sign-in or API key) (`setup`)

- Keywords: setup, install, installation, prerequisites, requirements, needs, missing, executable, not, found, tool, cli, claude, codex, agy, ant, sign-in, signed, login, PATH, version, OPENAI_API_KEY, GEMINI_API_KEY, key, official, page, link
- Symptom: An ask ends failed_not_started with 'claude executable not found' (or codex, agy, ant), with 'set OPENAI_API_KEY' or with 'set GEMINI_API_KEY'; or PeerHub is installed and it is not clear what a peer needs before the first ask.
- Fix: Install the missing CLI and sign in, or set OPENAI_API_KEY or GEMINI_API_KEY in PeerHub's environment. Keep keys off the command line and out of files. Retry after the prerequisite is fixed. See [README.md#what-each-peer-needs](../README.md#what-each-peer-needs) for each peer.
- Issue: #385

## Store selection, upgrade and restore hazards (`store-and-upgrade`)

- Keywords: store, upgrade, backup, core.db, schema, restore, dev-builds, fenced
- Symptom: Which database an ask uses, what to do before a PeerHub upgrade or restore, and why an old version cannot open a new store.
- Fix: Run peerhub update --plan. Back up the store before upgrading and reopen clients after restoring. See [README.md#upgrade](../README.md#upgrade) and [docs/config-hierarchy.md#store](config-hierarchy.md#store).
- Issue: 0.25.0 plan D5; #123

## store_missing database selection (`store-missing`)

- Keywords: store_missing
- Symptom: The selected database does not exist.
- Fix: Use --db PATH or PEERHUB_DB to name the intended store, or run from the main checkout. To initialize a new store, use peerhub --db PATH peer register --peer local --name local. See [docs/config-hierarchy.md#store](config-hierarchy.md#store).
- Issue: 0.24.0

## Writes, uncertain results and secrets (`writes-and-uncertain`)

- Keywords: writes, uncertain, pending_earlier_record, secrets, blind-retry, partial, edits, claims
- Symptom: A result is uncertain, a pending_earlier_record blocks a retry, or it is unclear which peers enforce read-only and what PeerHub stores.
- Fix: Stop the stream and inspect peerhub diag health and the working tree for partial edits. Reuse the same request ID only when the recovery guidance permits replay. Keep secrets out of prompts that persist in the store. See [docs/guidance-reference.md#ask_uncertain](guidance-reference.md#ask_uncertain) and [docs/adapters/peer-cli-reference.md#sandbox-and-event-limits](adapters/peer-cli-reference.md#sandbox-and-event-limits).
- Issue: 0.25.0 plan D5; #140, #325
