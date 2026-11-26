# PeerHub Lessons

Generated from `peerhub/config_data/lessons.toml`; edit that file, then regenerate with:

```powershell
python -c "from pathlib import Path; from peerhub.cli.lessons import render_lessons_reference; Path('docs/lessons.md').write_text(render_lessons_reference(), encoding='utf-8', newline='\n')"
```

Use `peerhub explain <free words>` for offline, case-insensitive AND-token lookup over titles and keywords.
Guidance codes and result labels take priority, followed by exact profile IDs (for example `cc.effort`), the index (bare `peerhub explain`, `lessons` or `list`), exact lesson IDs, and keyword lookup. Keyword matches list recipes first, then sort by ID. A single match prints in full. Open one of several matches with `peerhub explain <id>`.

## ag profile choice for bulk work (`ag-bulk-profile-heuristics`)

- Keywords: ag, agy, profile, effort, flash, argon, bulk, image, WinError, 206, prompt, size
- Symptom: A low-effort ag profile returns an empty reply on a long list of file or shell items; very long inline prompts fail on Windows.
- Fix: Try flash-medium if flash-low returns an empty reply on bulk work. ag prompts, including catch-up history, go over stdin up to 1,000,000 bytes; only an agy without stream-json input or PEERHUB_AG_TRANSPORT=argv uses argv, limited to 30,000 bytes (30 KB) on Windows and 120,000 bytes (120 KB) on POSIX. For ask_prompt_too_large, split into parts under the limit (one ask each), or for an ag argv limit use cc/cx on a separate stream (stdin, 1,000,000-byte maximum); if the prompt alone is under the limit, catch-up history pushed it over, so start a new --stream; a changed prompt needs a new request-id. File paths do not bypass the limit and ag file reads may be denied; inline small excerpts. Dense numeric image tables stay unreliable for every model: cross-read before trusting.
- Issue: #290, #309

## ag recipe (empty replies, permissions, trusted -w) (`ag-recipe`)

- Keywords: ag, agy, recipe, permission, permissions, writable, read-only, sandbox, limit, failed_not_started, empty, reply, denied_empty, delivered, trustedWorkspaces, allow, allow-list, scope, glob, wildcard, grep, cat, inline, regex, command, files, binary, prompt, query-file, resume
- Symptom: ag fails or returns an empty reply: delivered/TERMINAL with no text, denied_empty after a denied shell or read_file, a resumed stream that fabricates paths, or failed_not_started without write permission.
- Fix: Put the task text inline via --query-file and say "no tools". Use the trusted project root and pass --writable on every turn. After an empty or denied_empty reply, start a new stream with the content inlined. See [docs/adapters/ag.md#empty-responses-and-denied-tools](adapters/ag.md#empty-responses-and-denied-tools).
- Issue: #140, #284, #287, #300, #302, #391, #406

## ag asks require explicit --writable, including pings and reviews (`ag-requires-writable`)

- Keywords: ag, agy, writable, pings, reviews, ask_delivery_failed
- Symptom: [ask_delivery_failed] ag execution requires --writable: agy CLI does not enforce a read-only sandbox when --mode accept-edits is omitted.
- Fix: Add --writable. The intentional #140 gate applies to pings and reviews because agy lacks a read-only sandbox. Use trusted workspaces only. See [docs/adapters/ag.md#current-contract](adapters/ag.md#current-contract).
- Issue: #140, #359

## ag silence and long asks (`ag-silence-long-asks`)

- Keywords: ag, agy, silence, silent, long, asks, timeout, uncertain, blocked, stream
- Symptom: ag prints nothing until completion; a long ask can hit the silence timeout.
- Fix: Use --timeout-seconds 7200 --silence-timeout-seconds 7200 for long asks. Check for partial effects after an uncertain reply. See [docs/commands.md#ask](commands.md#ask) and [docs/guidance-reference.md#ask_uncertain](guidance-reference.md#ask_uncertain).
- Issue: #118

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

- Keywords: receipt, outcome, profile stats, profile check, measurement, coverage, stream budget, handoff, query file
- Symptom: You want to compare profiles, mark an answer's outcome, or understand an ask receipt or a stream budget warning.
- Fix: Run peerhub profile stats --json. Mark the result with peerhub outcome using requested_record_id or the ID from the terminal receipt. See [docs/config-hierarchy.md#profile-measurements-and-stream-budgets](config-hierarchy.md#profile-measurements-and-stream-budgets).
- Issue: 0.25.0 plan M2

## Explicit timeouts for implementer and review asks (`ask-timeouts`)

- Keywords: timeout, timeout-seconds, 600, implementer, review, writer, ask_uncertain, runtime_timeout, cc, cx
- Symptom: A long implementer or review ask ends as ask_uncertain (evidence runtime_timeout) with partial edits in the workspace.
- Fix: Raise the timeout above the 600-second default for long asks. Inspect the working tree after an uncertain result. See [docs/commands.md#ask](commands.md#ask) and [docs/guidance-reference.md#ask_uncertain](guidance-reference.md#ask_uncertain).
- Issue: #350

## Batch runs, load and cost side effects (`batch-and-load`)

- Keywords: batch, load, concurrency, balance, cancel, manifest, cost, side-effects
- Symptom: How many asks to run at once, what a batch spends, how suggest: and PACE guide the choice of a peer, and which quota and account side effects exist.
- Fix: Validate with peerhub batch run tasks.json --batch-id review-001 --max-parallel n --dry-run. Execution spends quota. See [docs/batch.md](batch.md) for sizing and resuming a wave.
- Issue: 0.25.0 plan D5; docs/batch.md

## Batch review findings and verify drafts (`batch-review-and-verify`)

- Keywords: review, findings, batch, mechanical, sweep, draft, verify, script
- Symptom: Reviewers surface one mechanical discrepancy per round, or a draft evidence table contains unverified errors.
- Fix: Ask for the complete mechanical sweep in one list and compare identifiers with a small script before voting. Treat drafts as unverified; check evidence and values against live files with commands, and have a second reviewer triage objections. See [CONTRIBUTING.md#working-with-ai-peers](../CONTRIBUTING.md#working-with-ai-peers).
- Issue: #297, #298

## Shell batching on Windows (CRLF, last line) (`batch-shell-crlf`)

- Keywords: batch, shell, crlf, profile, newline, windows, read, job, ALL_DONE, dry-run
- Symptom: A shell loop over a job list passes the profile with a trailing carriage return (unknown profile), drops the last line when it has no trailing newline, and prints ALL_DONE although jobs failed.
- Fix: Validate with peerhub batch run --dry-run ([docs/batch.md](batch.md)). See [docs/windows.md#job-lists](windows.md#job-lists) for shell loops that handle CRLF and the final line.
- Issue: #289

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

## cc allow rules are exact-match and model-dependent (`cc-allow-rules`)

- Keywords: cc, claude, allow, rules, rule, allowlist, bash, permission, permissions, denied, denied_actions, model, fable, haiku, headless, prefix, exact, settings
- Symptom: A headless cc ask with one model denies a Bash command that .claude/settings.local.json allows and another model runs (observed on #303: fable denied, haiku ran; not reproduced), or cc returns no usable answer with denied_actions set.
- Fix: Check denied_actions. Bash rules match the exact command string. Use a narrow prefix such as Bash(peerhub:*) and matching PowerShell rules when appropriate. See [docs/adapters/cc.md#headless-cc-peers-readtest-allow-rules](adapters/cc.md#headless-cc-peers-readtest-allow-rules).
- Issue: #303

## Concurrent cc lanes and parent shell lifetime (`concurrent-cc-lanes`)

- Keywords: cc, concurrent, lanes, memory, reaper, supervisor, parent, shell
- Symptom: On Windows, memory pressure can reap parent shells and kill long cc asks when concurrent lanes load large indexes.
- Fix: Keep 2-3 concurrent cc lanes on a 32 GB host when a lane may load large indexes. Long asks die with their parent shell: run them from one supervisor, wait for completion, and do not restart reaped work blindly. Check peerhub diag running, then inspect results and effects before deciding how to continue. This is an operational lesson, not an enforced concurrency or free-memory guard.
- Issue: #352

## cx sandbox facts and prompt hygiene (`cx-sandbox-and-prompt-hygiene`)

- Keywords: cx, codex, windows, sandbox, unelevated, process, enumeration, AGENTS.md, rule, file, query, forward, implementer, pytest, tmp_path, acl, basetemp, store, reader, orchestrator, checkpoint, receipts, runtime_error, silence, partial
- Symptom: cx reports UNKNOWN late for process/task enumeration, treats a query file as a task to forward and runs peerhub itself, pytest tmp_path tests fail with a temp-directory ACL error, a cx lane cannot open the peerhub store, or a long cx ask ends in runtime_error with partial edits.
- Fix: Report tmp_path tests as NOT RUN for the lead. When cx sandbox lanes cannot open the peerhub store, reader asks must be launched by the orchestrator. For long asks, use checkpoint receipts and a silence timeout. A runtime_error can leave partial edits. See [docs/adapters/cx.md#windows-sandbox-workflow](adapters/cx.md#windows-sandbox-workflow).
- Issue: #285, #286, #288, #391

## Discovery and inspection commands (`discovery-commands`)

- Keywords: discovery, inspection, commands, diag, model, models, slugs, sessions, facts, needs_recheck
- Symptom: Which read-only commands show quota, pools, sessions, accounts and models, and how the line-1 facts alert clears.
- Fix: Use peerhub explain lessons to find a topic, peerhub diag --plan for machine and quota evidence, and peerhub profile list for models. See [docs/commands.md](commands.md) for inspection commands and [docs/model-profiles/README.md#refresh-from-installed-clis](model-profiles/README.md#refresh-from-installed-clis) for fact drift.
- Issue: 0.25.0 plan D5

## CLI exit codes and error output (`exit-codes`)

- Keywords: exit, code, error, stderr, json
- Symptom: A caller is interpreting a process exit or an error report.
- Fix: Ask returns 0 for delivery, terminal replay or a valid dry run, 1 for uncertainty or delivery failure (including denied_empty or session_lost), 2 for syntax, invalid selectors or idempotency conflict, and 3 for a compare-and-swap conflict. Batch run returns 0 for a valid dry run or all tasks completed and 1 for an invalid manifest or unsuccessful tasks. An empty terminal reply without an error exits 0 in ask and 1 in batch (UNVERIFIED). Diag health, quota, readiness, running, --plan and integrity use 0 for OK, 4 for a storage/read fault (including PARTIAL reports), 5 for an unavailable store, and 6 for an unsupported schema version. A missing alternate --observation-db with a healthy main store gives health 4 (PARTIAL) and quota 5 (UNAVAILABLE). Diag health returns 1 for a missing stream. Update --check returns 0. Update --plan returns 0 when ready, 1 if an ask is or may be running, and 4/5/6 for an unreadable/missing/incompatible store. Suggest returns 0 for a suggestion or 1 for unavailable. Explain returns 0 for a match or 1 for no match. Monitor snapshot/watch use 0 for success, 1 for invalid defaults, an agy token-use stop or a failed final refresh in finite watch, and the diag store codes. Observation refresh returns 0 for OK or 1 for a non-OK refresh. Shared store errors use the diag store codes as applicable. Coded errors appear on stderr as [code] message; next: ... With --json, ask errors and invalid manifests can include an error object with code and message on stdout. Other reports use command-specific JSON. Run peerhub explain CODE for guidance.
- Issue: #446

## Independent review votes (`independent-peer-votes`)

- Keywords: peer, vote, votes, author, independent, unanimity, review, self-vote, batch, responses, verdict, fresh, consequential
- Symptom: A document author votes AGREE on their own work and the result is treated as unanimous approval, votes are collected one ask at a time, or a routine check is sent to three peers or to a named stream.
- Fix: Vote policy: routine check = ONE reviewer, FRESH; consequential (release, schema, deletion, public API) = 3 independent peers, unanimity. Use a new stream per voter: --fresh on a used stream can still include up to 100 earlier records (24 KB), even when a stream name is given. A named stream is for review rounds on one diff only. Require voters did not author the document. There is no `vote` command: run peerhub batch run votes.json --batch-id ID --max-parallel n --responses. cc/cx use the repository workspace, not writable; ag uses a private empty directory as workspace (always writable). Keep prompts inline. Reply VERDICT / BLOCKERS / NITS (<= 300 tokens). See [docs/commands.md#ask](commands.md#ask).
- Issue: #297, #366, 0.25.0 plan D6

## Installed vs checkout mismatch (`installed-vs-checkout`)

- Keywords: python, pip, installed, checkout, mismatch, PYTHONPATH, version, source, module
- Symptom: The pip-installed peerhub command behaves differently from the current checkout.
- Fix: Run python -m peerhub from the checkout (or set PYTHONPATH to it), or install that checkout with python -m pip install -e . using the same Python environment.
- Issue: #117

## Keep peer instructions, memory and quota spend small (`instruction-diet`)

- Keywords: instruction, diet, memory, quota, observation, refresh, snapshot, profile, ladder, pytest, launcher, allow, headless, cc, tokens, global, workspace, votes, deepthink
- Symptom: Every peer ask re-reads a large memory index or instruction file, a per-turn observation refresh burns quota, headless cc cannot run pytest, or 60-word votes run on the heaviest profile.
- Fix: Headless cc: run pytest through one repo-local launcher (for example pt.py, which sets the environment and forwards its arguments to pytest) and allow that single command, because prefix rules miss env-prefixed commands. Refresh observation every 30-60 minutes and use peerhub monitor --snapshot each turn. Keep peer-loaded memory and instructions tiny and workspace-scoped; global files hold only generic rules. Profile ladder: bulk/votes -> standard, code -> effort, review/design -> deepthink.
- Issue: #337

## invalid_arguments CLI input (`invalid-arguments`)

- Keywords: invalid_arguments
- Symptom: The command arguments were rejected.
- Fix: Run peerhub COMMAND --help, correct the arguments and retry only if no execution began.
- Issue: 0.24.0

## Monitor ACCOUNTS: plan, login and token expiry (`monitor-accounts`)

- Keywords: monitor, accounts, account, login, token, expiry, credits, display_plan, plan_changed
- Symptom: The monitor ACCOUNTS --expand rows or JSON capacity.accounts need explanation (plan, login state, token expiry, ?, n/a, aged, plan changed).
- Fix: Read ACCOUNTS in --expand or JSON capacity.accounts for the plan, login and token expiry. See [docs/monitor.md#accounts](monitor.md#accounts).
- Issue: #329

## DRIFT peers (`monitor-drift`)

- Keywords: DRIFT
- Symptom: Observed provider CLI or model facts differ from configured observations.
- Fix: Refresh packaged model facts or upgrade PeerHub. See [docs/model-profiles/README.md#refresh-from-installed-clis](model-profiles/README.md#refresh-from-installed-clis).
- Issue: 0.24.0

## INSUFF projection data (`monitor-insuff`)

- Keywords: INSUFF
- Symptom: A measured quota window has too few samples or too little use for projection.
- Fix: Capacity remains measured; forecast is unavailable until the projection support gates are met. Do not interpret INSUFF as healthy or unlimited. See [docs/monitor.md#pools](monitor.md#pools).
- Issue: 0.24.0

## PROBE age (`monitor-probe-age`)

- Keywords: PROBE, age
- Symptom: A provider probe failed or has no current usable evidence; the suffix is its age.
- Fix: Run peerhub observation refresh --peers PEER. If it fails again, check authentication and the installed CLI. See [docs/monitor.md#alerts](monitor.md#alerts).
- Issue: 0.24.0, #328

## STALE:window (`monitor-stale-window`)

- Keywords: STALE, window
- Symptom: Quota evidence has expired.
- Fix: Run peerhub observation refresh --peers PEER to collect fresh evidence. Expired capacity is unavailable, never zero or unlimited. See [docs/monitor.md#alerts](monitor.md#alerts).
- Issue: 0.24.0

## Monitor states, PACE, suggestions and cycles (`monitor-states`)

- Keywords: monitor, pace, low, fast, aged, suggest, alternative, cycles, tty, legend, quota, unknown, est, estimated, gone, overdue, orphan
- Symptom: Monitor states, including gone and overdue asks, quota pace, or redirected watch behavior need explanation.
- Fix: Check the reading's age before acting on LEFT or PACE. Use --show-quota-notes to see omitted windows. See [docs/monitor.md#pools](monitor.md#pools) for quota states and [docs/monitor.md#running](monitor.md#running) for activity states and alert timing.
- Issue: #310, #311, 0.24.4, #376, #378, 2026-11-25 gone ask follow-up

## Monitor view: frame order and the rich look (`monitor-view`)

- Keywords: monitor, view, order, rich, plain, glyph, glyphs, colour, color, colours, bar, pill, rule, rules, meter, palette, NO_COLOR, unicode
- Symptom: The order of the compact monitor frame, the plain or rich view, or a glyph, bar, pill, rule or colour of the rich view needs explanation.
- Fix: Use --expand for the full DB path and detail rows. PEERHUB_NO_LINKS disables OSC 8 and prints a copyable Guide: line when it fits. See [docs/monitor.md](monitor.md) for layout and refresh timing.
- Issue: #376 (P21, 0.24.18), P23, #412, #414, #422

## Quota shaping across peers (`multi-peer-quota-shaping`)

- Keywords: quota, cx, ag, cc, pool, consolidated, draft, timer, sleep, reset
- Symptom: The scarce premium peer exhausts its pool early; a delayed start queued with sleep never ran.
- Fix: Consolidate final verification in one scarce-pool ask. In the observed run, splitting cx work did not save quota and an in-session timer died with its session. See [docs/batch.md](batch.md) for planning a wave.
- Issue: #288

## Spread parallel work over the least quota (`parallel-quota`)

- Keywords: parallel, quota, pace, pools, batch, loop, distribute, minimal, ram, free, memory, slots, load, diag, plan, resource, resources
- Symptom: Several tasks can run at once and the caller wants them spread over the pools with the most spare quota, spending as little as possible.
- Fix: Read peerhub diag --plan and choose usable pools by lowest 7D PACE first. Size the wave from the load line. Validate with peerhub batch run tasks.json --batch-id ID --max-parallel n --dry-run. Refresh only the peers the next wave may use (peerhub observation refresh --peers cx ag) and read the plan after each wave. See [docs/batch.md#parallel-work-over-the-least-quota](batch.md#parallel-work-over-the-least-quota).
- Issue: owner decisions 2026-11-22 and 2026-11-25

## Peer sub-agents inside one ask (`peer-subagents`)

- Keywords: subagent, subagents, sub-agent, sub-agents, Agent, spawn_agent, invoke_subagent, multi_agent, fan-out, loop
- Symptom: A large task could run as parallel helpers inside one peer ask, or a prompt expects /loop-style persistence inside an ask.
- Fix: Every peer can fan out sub-agents inside one ask: cc through its Agent tool; cx through spawn_agent only when the prompt names that tool explicitly (with features.multi_agent enabled); ag through invoke_subagent. There is no /loop-style persistence inside an ask: the ask ends with the peer's reply. Sub-agents spend the same peer quota: give each a disjoint task and ask for one merged final report.
- Issue: #361

## Quota windows depend on the peer plan (`plan-windows`)

- Keywords: cc, ag, cx, absent, not reported, plan, window, windows, cx-plan-windows, codex, claude, agy, quota, pool, 5h, 7d, X-5H, X-7D, prolite, reported, monitor, incomplete
- Symptom: peerhub monitor --show-quota-notes labels a pool n/a, and agents read it as exhausted or unusable.
- Fix: Use --show-quota-notes to see why the provider omitted or disabled a window. See [docs/monitor.md#pools](monitor.md#pools).
- Issue: 0.24.0 (cx); 0.24.1 (all peers); #328 (disabled)

## Quota-aware peer runs (`quota-aware-peer-runs`)

- Keywords: quota, 429, reset, stream, pool, parallel, flash, workers, capacity
- Symptom: A peer returns HTTP 429 and an uncertain ask blocks its stream, or parallel work exhausts a shared quota pool.
- Fix: Read peerhub diag --plan and defer when the pool is exhausted. Reconcile uncertain work before retrying. See [docs/batch.md](batch.md) for quota-aware parallelism.
- Issue: #297, #298, #299

## Report an issue (`report-issues`)

- Keywords: report, issue, issues, github, bug
- Symptom: Something behaves wrongly and the problem should be filed without leaking secrets.
- Fix: Check for an existing report. Include peerhub --version, the OS, the exact command, the outcome or exit code, and any workaround. See [CONTRIBUTING.md#reporting-issues-and-requesting-features](../CONTRIBUTING.md#reporting-issues-and-requesting-features).
- Issue: 0.25.0 plan D5

## Review recipe: scores that converge on 100 (`review-recipe`)

- Keywords: review, review-loop, review-scores-to-100, loop, score, 100, round, brief, materiality, contract, narrow, defects, deduction, unverified, suite, targeted, ports, WinError, worktree, cross-family, final, tag, gitlink
- Symptom: Review rounds repeat or oscillate (99.86, 99.79, 99.67): a reviewer scores 95-97 without naming a defect, deducts for checks it could not run, keeps finding edge cases in the same new component, or parallel full suites exhaust Windows ports (WinError 10048).
- Fix: Paste this reviewer brief: "Score each item 0-100. Deduct only for a defect that violates a documented contract or is reachable from supported inputs; label anything else a recommendation. For every score below 100 give the exact fix (file:line + change). Report checks you could not run as NOT RUN instead of deducting. Re-score until 100." Score each deliverable item separately and re-review the exact shipped tag; confirm the final candidate with a reviewer from another model family. The coordinator runs the full suite once and passes the result on; reviewers run only targeted tests (give them allow rules for the read/test commands they need; run any they could not run yourself). Every live measurement is recorded with the commit it ran on and its time. After two rounds that find new defects in the same new component, narrow its contract (fail closed) instead of patching it again. Never `git add -A` in a checkout that contains agent worktrees: they are committed as gitlinks.
- Issue: #345; 2026-11-10 (0.24.5/0.24.6 review rounds)

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

## Stream reuse cost: reuse within one task only (`stream-reuse-cost`)

- Keywords: stream, reuse, resume, session, context, tokens, quota, cost, vote
- Symptom: Quota drains fast although prompts are short: a long-lived --stream is reused across many tasks, so later asks can resend earlier context.
- Fix: Reuse only within one task. Move a new task to a new stream. See [docs/commands.md#ask](commands.md#ask).
- Issue: #340

## Recommend a profile and ask command (`suggest`)

- Keywords: suggest, recommend, profile choice, 7d pace, 5h usage
- Symptom: Choose a profile in the requested tier using quota evidence.
- Fix: Run peerhub suggest --tier TIER --candidates to inspect eligibility, then omit --candidates to print an ask. See [docs/batch.md#suggest-an-ask](batch.md#suggest-an-ask) for prompt files, ranking and refresh costs.
- Issue: 0.25.0 plan D7/D8/A6/A7

## Writes, uncertain results and secrets (`writes-and-uncertain`)

- Keywords: writes, uncertain, pending_earlier_record, secrets, blind-retry, partial, edits, claims
- Symptom: A result is uncertain, a pending_earlier_record blocks a retry, or it is unclear which peers enforce read-only and what PeerHub stores.
- Fix: Stop the stream and inspect peerhub diag health and the working tree for partial edits. Reuse the same request ID only when the recovery guidance permits replay. Keep secrets out of prompts that persist in the store. See [docs/guidance-reference.md#ask_uncertain](guidance-reference.md#ask_uncertain) and [docs/adapters/peer-cli-reference.md#sandbox-and-event-limits](adapters/peer-cli-reference.md#sandbox-and-event-limits).
- Issue: 0.25.0 plan D5; #140, #325
