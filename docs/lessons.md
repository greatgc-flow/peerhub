# PeerHub Lessons

Generated from `peerhub/config_data/lessons.toml`; edit that file, then regenerate with:

```powershell
python -c "from pathlib import Path; from peerhub.cli.lessons import render_lessons_reference; Path('docs/lessons.md').write_text(render_lessons_reference(), encoding='utf-8', newline='\n')"
```

Use `peerhub explain <free words>` for offline, case-insensitive AND-token lookup over titles and keywords.
Known guidance codes and aliases take priority, then an exact profile ID (for example `cc.effort`), then `lessons` (an index: one `id: title` line per lesson), then an exact lesson ID (the IDs below) before keyword lookup. Keyword matches list recipes first, then lesson ID order; one match prints in full, several print one `id: title` line each (open one with `peerhub explain <id>`).

## ag profile choice for bulk work (observed 2026-11-01) (`ag-bulk-profile-heuristics`)

- Keywords: ag, agy, profile, effort, flash, argon, bulk, image, WinError, 206, prompt, size
- Symptom: A low-effort ag profile returns an empty reply on a long list of file or shell items; very long inline prompts fail on Windows.
- Fix: Observation from one small test (Windows, one run per cell, no ground truth for images): flash-low broke down somewhere between 25 and 150 items, flash-medium finished them, high only cost time. Treat these as heuristics, not thresholds. ag prompts, including catch-up history, go over stdin up to 1,000,000 bytes; only an agy without stream-json input or PEERHUB_AG_TRANSPORT=argv uses argv, limited to 30,000 bytes (30 KB) on Windows and 120,000 bytes (120 KB) on POSIX. For ask_prompt_too_large, split into parts under the limit (one ask each), or for an ag argv limit use cc/cx on a separate stream (stdin, 1,000,000-byte maximum); if the prompt alone is under the limit, catch-up history pushed it over, so start a new --stream; a changed prompt needs a new request-id. File paths do not bypass the limit and ag file reads may be denied; inline small excerpts. Dense numeric image tables stay unreliable for every model: cross-read before trusting.
- Issue: #290, #309

## ag recipe (empty replies, permissions, trusted -w) (`ag-recipe`)

- Keywords: ag, agy, recipe, permission, permissions, writable, read-only, sandbox, limit, failed_not_started, empty, reply, denied_empty, delivered, trustedWorkspaces, allow, allow-list, scope, glob, wildcard, grep, cat, inline, regex, command, files, binary, prompt, query-file, resume
- Symptom: ag fails or returns an empty reply: delivered/TERMINAL with no text, denied_empty after a denied shell or read_file, a resumed stream that fabricates paths, or failed_not_started without write permission.
- Fix: Put the task text inline via --query-file and say "no tools". Use the trusted project root and pass --writable on every turn. After an empty or denied_empty reply, start a new stream with the content inlined. See docs/adapters/ag.md#empty-responses-and-denied-tools.
- Issue: #140, #284, #287, #300, #302, #391, #406

## ag asks require explicit --writable, including pings and reviews (`ag-requires-writable`)

- Keywords: ag, agy, writable, pings, reviews, ask_delivery_failed
- Symptom: [ask_delivery_failed] ag execution requires --writable: agy CLI does not enforce a read-only sandbox when --mode accept-edits is omitted.
- Fix: Add --writable. The intentional #140 gate applies to pings and reviews because agy lacks a read-only sandbox. Use trusted workspaces only. See docs/adapters/ag.md#current-contract.
- Issue: #140, #359

## ag silence and long asks (`ag-silence-long-asks`)

- Keywords: ag, agy, silence, silent, long, asks, timeout, uncertain, blocked, stream
- Symptom: ag prints nothing until completion; a long ask can hit the silence timeout.
- Fix: Use --timeout-seconds 7200 --silence-timeout-seconds 7200 for long asks. Silence timeout means uncertain execution: inspect peerhub diag health and effects, then continue genuinely new work on a new stream with a summary; never blindly retry uncertain work. See docs/adapters/peer-cli-reference.md#sandbox-and-event-limits.
- Issue: #118

## API peers ca, oa and ga: text-only asks on API credits (`api-peers`)

- Keywords: ca, oa, ga, api, ant, openai, gemini, OPENAI_API_KEY, GEMINI_API_KEY, RESOURCE_EXHAUSTED, credits, credit, balance, stateless, truncated, incomplete, request_rejected, inline, vote, votes, text
- Symptom: A vote, text review or documentation check spends a subscription pool of cc, cx or ag although it needs no tools or files; or a ca/oa/ga ask is refused (request_rejected), ends truncated or incomplete, or does not see a file; or the credit balance of an API is wanted.
- Fix: Inline the text with --query-file and choose --profile or --model. Use one ask per stream and leave out --writable and --effort. API peers spend API credits. For credentials, balance checks and outcomes, see docs/api-credits.md.
- Issue: #378, #381, #384

## Ask basics: stream reuse, long asks, tiers, transport (`ask-basics`)

- Keywords: ask, basics, example, request-id, foreground, tiers, transport, blocks
- Symptom: A caller needs the reuse pattern, the tier choice, the long-ask behaviour or the transport options that the short guide states only as decisions.
- Fix: Reuse the stream within one task with a separate request ID per turn. Use a new stream for a new task and the lowest tier that fits. Use --dry-run to preview the ask. See docs/commands.md#ask for flags, transport and timeouts.
- Issue: 0.25.0 plan D5

## Ask receipts, outcomes, and profile measurements (`ask-metrics`)

- Keywords: receipt, outcome, profile stats, profile check, measurement, coverage, stream budget, handoff, query file
- Symptom: You want to compare profiles, mark an answer's outcome, or understand an ask receipt or a stream budget warning.
- Fix: Use peerhub profile stats --json for ask counts, coverage by metric, and reported measurements. Mark an answer with peerhub outcome <ask-id> accepted|rejected|retried. Find the ID at the start of the terminal receipt or in requested_record_id in ask JSON. The latest correction wins. A dash in the receipt means the metric was unreported. Set PEERHUB_NO_RECEIPT=1 to silence the receipt. Run peerhub profile check to find stale measurements and model IDs missing from the available Codex or agy catalog. If the stream budget warning appears, start a fresh stream with a handoff covering decisions, open work, and file references. Configure prompt_tokens and resumed_turns in [stream_budget] in models.toml. For an inlined query file over 64 KB, consider sending paths or a diff.
- Issue: 0.25.0 plan M2

## Explicit timeouts for implementer and review asks (`ask-timeouts`)

- Keywords: timeout, timeout-seconds, 600, implementer, review, writer, ask_uncertain, runtime_timeout, cc, cx
- Symptom: A long implementer or review ask ends as ask_uncertain (evidence runtime_timeout) with partial edits in the workspace.
- Fix: --timeout-seconds defaults to 600 (as batch). Long implementer or review asks still need a larger value (cc/cx 1800-3600; ag up to 7200 with --silence-timeout-seconds unset or >= it). After a timeout or uncertain result never retry on the same stream: check the working tree for partial edits, then continue on a new stream. See docs/commands.md#ask.
- Issue: #350

## Batch runs, load and cost side effects (`batch-and-load`)

- Keywords: batch, load, concurrency, balance, cancel, manifest, cost, side-effects
- Symptom: How many asks to run at once, what a batch spends, how suggest: and PACE guide the choice of a peer, and which quota and account side effects exist.
- Fix: Validate with peerhub batch run tasks.json --batch-id review-001 --max-parallel n --dry-run. PeerHub does not cap the count and pauses new launches while RAM is low or CPU load is high. Execution spends quota. Keep the manifest and batch ID for recovery. See docs/batch.md.
- Issue: 0.25.0 plan D5; docs/batch.md

## Batch review findings and verify drafts (`batch-review-and-verify`)

- Keywords: review, findings, batch, mechanical, sweep, draft, verify, script
- Symptom: Reviewers surface one mechanical discrepancy per round, or a draft evidence table contains unverified errors.
- Fix: Ask for the complete mechanical sweep in one list and compare identifiers with a small script before voting. Treat drafts as unverified; check evidence and values against live files with commands, and have a second reviewer triage objections. See CONTRIBUTING.md#working-with-ai-peers.
- Issue: #297, #298

## Shell batching on Windows (CRLF, last line) (`batch-shell-crlf`)

- Keywords: batch, shell, crlf, profile, newline, windows, read, job, ALL_DONE, dry-run
- Symptom: A shell loop over a job list passes the profile with a trailing carriage return (unknown profile), drops the last line when it has no trailing newline, and prints ALL_DONE although jobs failed.
- Fix: Prefer peerhub batch run with --dry-run. If using a shell loop, strip CR, read the last line even without a newline, and check each job status. See docs/windows.md#job-lists and docs/batch.md.
- Issue: #289

## Identify your session's running asks (`caller-ownership`)

- Keywords: caller, ownership, running, owner, session, PEERHUB_OWNER
- Symptom: A shared store shows running asks from several lead sessions.
- Fix: Generate a random PEERHUB_OWNER once at the start of each lead session and export it to every shell that runs PeerHub for that session. In PowerShell, run `$env:PEERHUB_OWNER = (python -c "import uuid; print(uuid.uuid4().hex)")`. In a POSIX shell, run `export PEERHUB_OWNER=$(python -c 'import uuid; print(uuid.uuid4().hex)')`. Keep the value for the entire session. Never share it or set it in a shell profile. Set PEERHUB_LEAD to a readable label and use PEERHUB_BATCH_ID and PEERHUB_BATCH_TASK for batch work. `peerhub diag running` reports how the stored caller relates to the current reader. The first applicable rule wins. A difference in known hosts or PID namespaces takes precedence and means other. If both owner tokens are valid, equality means mine and a difference means other. Otherwise, two valid CLAUDE_CODE_SESSION_ID values are compared the same way. With insufficient evidence the result is unknown. Labels, paths, batch IDs and task IDs are descriptive. Mine includes asks started by this session and by processes it launched, including nested provider asks that inherit its identity. For other and unknown asks, only observe and report them. Never cancel them or reuse their streams.
- Issue: Monitor design record M10

## A cut-off reply usually means your tool timed out, not peerhub (`caller-timeout-claim-held`)

- Keywords: claim, claim_held, ask_claim_held, preamble, cut off, partial, timeout, timed out, background, request-id, replay, cx, long
- Symptom: An ask shows only a preamble ("I'll audit ..."), then the next ask on the same stream fails with ask_claim_held.
- Fix: Confirmed in #312 from the store: the ask finished with a full reply and released its claim when the turn ended; only the caller had stopped waiting. If this happens, do not open a new stream or retry with a new request-id. Wait for the claim to end, then rerun the identical command with the same --request-id: a completed turn replays the stored reply without a new provider call; otherwise follow the returned uncertainty guidance (ask_uncertain or ask_blocked_uncertain). Run long asks in the background and wait for completion. See docs/guidance-reference.md#ask_claim_held.
- Issue: #312

## cc allow rules are exact-match and model-dependent (`cc-allow-rules`)

- Keywords: cc, claude, allow, rules, rule, allowlist, bash, permission, permissions, denied, denied_actions, model, fable, haiku, headless, prefix, exact, settings
- Symptom: A headless cc ask with one model denies a Bash command that .claude/settings.local.json allows and another model runs (observed on #303: fable denied, haiku ran; not reproduced), or cc returns no usable answer with denied_actions set.
- Fix: Check denied_actions. Bash rules match the exact command string. Use a narrow prefix such as Bash(peerhub:*) and matching PowerShell rules when appropriate. See docs/adapters/cc.md#headless-cc-peers-readtest-allow-rules.
- Issue: #303

## Concurrent cc lanes and parent shell lifetime (`concurrent-cc-lanes`)

- Keywords: cc, concurrent, lanes, memory, reaper, supervisor, parent, shell
- Symptom: On a 32 GB Windows host, memory pressure reaped parent shells and killed long cc asks when about six lanes ran and one loaded a large index (PeerHub 0.24.8; checked 2026-11-12, #352).
- Fix: Keep 2-3 concurrent cc lanes on a 32 GB host when a lane may load large indexes. Long asks die with their parent shell: run them from one supervisor, wait for completion, and do not restart reaped work blindly. Check peerhub diag running, then inspect results and effects before deciding how to continue. This is an operational lesson, not an enforced concurrency or free-memory guard.
- Issue: #352

## cx sandbox facts and prompt hygiene (`cx-sandbox-and-prompt-hygiene`)

- Keywords: cx, codex, windows, sandbox, unelevated, process, enumeration, AGENTS.md, rule, file, query, forward, implementer, pytest, tmp_path, acl, basetemp, store, reader, orchestrator, checkpoint, receipts, runtime_error, silence, partial
- Symptom: cx reports UNKNOWN late for process/task enumeration, treats a query file as a task to forward and runs peerhub itself, pytest tmp_path tests fail with a temp-directory ACL error, a cx lane cannot open the peerhub store, or a long cx ask ends in runtime_error with partial edits.
- Fix: Report tmp_path tests as NOT RUN for the lead. When cx sandbox lanes cannot open the peerhub store, reader asks must be launched by the orchestrator. For long asks, use checkpoint receipts and a silence timeout. A runtime_error can leave partial edits. See docs/adapters/cx.md#windows-sandbox-workflow.
- Issue: #285, #286, #288, #391

## Discovery and inspection commands (`discovery-commands`)

- Keywords: discovery, inspection, commands, diag, model, models, slugs, sessions, facts, needs_recheck
- Symptom: Which read-only commands show quota, pools, sessions, accounts and models, and how the line-1 facts alert clears.
- Fix: peerhub explain lessons: lesson index (id: title); then peerhub explain <id> or free words. peerhub explain setup: what each peer needs. AI sessions: peerhub diag --plan for per-pool spend/hold/rest until reset from stored quota, plus active asks, free RAM and CPU load. peerhub monitor: the human screen. peerhub monitor --group capacity: verdict line + pools; suggest a|b = best + alternative peer; peerhub explain monitor-states. peerhub monitor --expand: everything: alerts, asks, leads, pools, peers, accounts, sessions, usage (or --snapshot). peerhub diag: read-only diagnostics. Recommendations: peerhub suggest --tier standard (peerhub explain suggest). Models: peerhub profile list; slugs: agy models; --model <slug>. peerhub batch run tasks.json --batch-id review-001 --max-parallel n --dry-run: validate a batch. peerhub session list: retained sessions; accounts: peerhub account list. peerhub observation refresh: re-probe provider quota. peerhub explain peer-subagents: sub-agents inside one ask (same peer quota). Model facts: the line-1 'facts:' alert (needs_recheck) clears via refresh-model-profiles skill or release upgrade.
- Issue: 0.25.0 plan D5

## CLI exit codes and error output (`exit-codes`)

- Keywords: exit, code, error, stderr, json
- Symptom: A caller is interpreting a process exit or an error report.
- Fix: Ask returns 0 for delivery, terminal replay or a valid dry run, 1 for uncertainty or delivery failure (including denied_empty or session_lost), 2 for syntax, invalid selectors or idempotency conflict, and 3 for a compare-and-swap conflict. Batch run returns 0 for a valid dry run or all tasks completed and 1 for an invalid manifest or unsuccessful tasks. An empty terminal reply without an error exits 0 in ask and 1 in batch (UNVERIFIED). Diag health, quota, readiness, running, --plan and integrity use 0 for OK, 4 for a storage/read fault (including PARTIAL reports), 5 for an unavailable store, and 6 for an unsupported schema version. A missing alternate --observation-db with a healthy main store gives health 4 (PARTIAL) and quota 5 (UNAVAILABLE). Diag health returns 1 for a missing stream. Update --check returns 0. Update --plan returns 0 when ready, 1 if an ask is or may be running, and 4/5/6 for an unreadable/missing/incompatible store. Suggest returns 0 for a suggestion or 1 for unavailable. Explain returns 0 for a match or 1 for no match. Monitor snapshot/watch use 0 for success, 1 for invalid defaults, an agy token-use stop or a failed final refresh in finite watch, and the diag store codes. Observation refresh returns 0 for OK or 1 for a non-OK refresh. Shared store errors use the diag store codes as applicable. Coded errors appear on stderr as [code] message; next: ... With --json, ask errors and invalid manifests can include an error object with code and message on stdout. Other reports use command-specific JSON. Run peerhub explain CODE for guidance.
- Issue: #446

## Independent review votes (`independent-peer-votes`)

- Keywords: peer, vote, votes, author, independent, unanimity, review, self-vote, batch, responses, verdict, fresh, consequential
- Symptom: A document author votes AGREE on their own work and the result is treated as unanimous approval, votes are collected one ask at a time, or a routine check is sent to three peers or to a named stream.
- Fix: Vote policy: routine check = ONE reviewer, FRESH; consequential (release, schema, deletion, public API) = 3 independent peers, unanimity. Routine checks and all three consequential votes always run FRESH, even when a stream name is given: a named stream is for review rounds on one diff only. Require votes on a document from peers who did not author it; an author's AGREE does not count toward unanimity. There is no `vote` command: votes and reviews run in one command, peerhub batch run votes.json --batch-id ID --max-parallel n --responses (the report then includes each reply). cc and cx tasks use workspace = the repository, not writable; the ag task uses a private empty directory as workspace (ag tasks are always writable; the empty directory satisfies the overlapping-writers check); prompts carry all facts inline. Verdict template for every voter, one reply of <= 300 tokens: VERDICT / BLOCKERS / NITS (VERDICT: AGREE or DISAGREE; BLOCKERS: each with file:line and the exact fix, or none; NITS: optional, one line each). Measured 2026-11-14: three peers answered in 11 s.
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
- Fix: Read ACCOUNTS (--expand rows or JSON capacity.accounts) for the plan, login and LOGIN token expiry. A logged-out or expiring login reaches line 1 as an alert. See docs/monitor.md#accounts.
- Issue: #329

## DRIFT peers (`monitor-drift`)

- Keywords: DRIFT
- Symptom: Observed provider CLI or model facts differ from configured observations.
- Fix: Refresh packaged model facts or upgrade PeerHub. See docs/model-profiles/README.md#refresh-from-installed-clis.
- Issue: 0.24.0

## INSUFF projection data (`monitor-insuff`)

- Keywords: INSUFF
- Symptom: A measured quota window has too few samples or too little use for projection.
- Fix: Capacity remains measured; forecast is unavailable until the projection support gates are met. Do not interpret INSUFF as healthy or unlimited. See docs/monitor.md#pools.
- Issue: 0.24.0

## PROBE age (`monitor-probe-age`)

- Keywords: PROBE, age
- Symptom: A provider probe failed or has no current usable evidence; the suffix is its age.
- Fix: Run peerhub observation refresh --peers PEER. If it fails again, check authentication and the installed CLI. See docs/monitor.md#alerts.
- Issue: 0.24.0, #328

## STALE:window (`monitor-stale-window`)

- Keywords: STALE, window
- Symptom: Quota evidence has expired.
- Fix: Run peerhub observation refresh --peers PEER to collect fresh evidence. Expired capacity is unavailable, never zero or unlimited. See docs/monitor.md#alerts.
- Issue: 0.24.0

## Monitor states, PACE, suggestions and cycles (`monitor-states`)

- Keywords: monitor, pace, low, fast, aged, suggest, alternative, cycles, tty, legend, quota, unknown, est, estimated
- Symptom: Monitor states, PACE, stale evidence, alternative profiles or redirected watch behavior need explanation.
- Fix: Check quota age before acting on LEFT or PACE. Use peerhub diag --plan when planning parallel work. See docs/monitor.md#pools for states, parent limits and forecast fields.
- Issue: #310, #311, 0.24.4, #376, #378

## Monitor view: frame order and the rich look (`monitor-view`)

- Keywords: monitor, view, order, rich, plain, glyph, glyphs, colour, color, colours, bar, pill, rule, rules, meter, palette, NO_COLOR, unicode
- Symptom: The order of the compact monitor frame, the plain or rich view, or a glyph, bar, pill, rule or colour of the rich view needs explanation.
- Fix: Use --expand for the full DB path and detail rows. PEERHUB_NO_LINKS disables OSC 8 links and uses the copyable Guide: line when it fits. See docs/monitor.md for frame order, glyphs and watch modes.
- Issue: #376 (P21, 0.24.18), P23, #412, #414, #422

## Quota shaping across peers (`multi-peer-quota-shaping`)

- Keywords: quota, cx, ag, cc, pool, consolidated, draft, timer, sleep, reset
- Symptom: The scarce premium peer exhausts its pool early; a delayed start queued with sleep never ran.
- Fix: Consolidate final verification in one scarce-pool ask. In the observed run, splitting cx work did not save quota and an in-session timer died with its session. See docs/batch.md for planning a wave.
- Issue: #288

## Spread parallel work over the least quota (`parallel-quota`)

- Keywords: parallel, quota, pace, pools, batch, loop, distribute, minimal, ram, free, memory, slots, load, diag, plan, resource, resources
- Symptom: Several tasks can run at once and the caller wants them spread over the pools with the most spare quota, spending as little as possible.
- Fix: You decide the wave size and pools. Read peerhub diag --plan, never peerhub monitor (the human screen). Check the load line for free RAM, CPU and in-flight asks. With --json, use the load object and pool profiles; each pool row also reports age_seconds, ttl_seconds, fast, family, parent and gates. A profile listed under several pools spends each. For a default wave, split independent tasks with no shared files. Use the lowest tier that fits, fresh asks with facts inline and a different model per vote. Use the lowest 7D PACE first (spend, then hold). Skip stale, rest, blocked, <5% and fast pools. 5H only gates. Use free RAM >= 6 GB, CPU below 90% to size the wave. PeerHub does not cap the count. Validate with peerhub batch run tasks.json --batch-id ID --max-parallel n --dry-run. Start the wave, then after each wave run peerhub observation refresh, then diag --plan again. For parallel ag asks, watch G-5H after each wave. Keep a margin for asks in flight (including n/a pools and unmapped asks). When one task is refused for quota, the others still run. The same --batch-id replays settled work. If throttled, reduce concurrency and do not reroute the same work. After uncertainty, check peerhub diag running and peerhub diag health --stream STREAM and reconcile the workspace. If no pool is usable, defer. See docs/batch.md for probe costs and plan details.
- Issue: owner request 2026-11-22

## Peer sub-agents inside one ask (`peer-subagents`)

- Keywords: subagent, subagents, sub-agent, sub-agents, Agent, spawn_agent, invoke_subagent, multi_agent, fan-out, loop
- Symptom: A large task could run as parallel helpers inside one peer ask, or a prompt expects /loop-style persistence inside an ask.
- Fix: Every peer can fan out sub-agents inside one ask: cc through its Agent tool; cx through spawn_agent only when the prompt names that tool explicitly (codex 0.167.2, features.multi_agent on); ag through invoke_subagent. There is no /loop-style persistence inside an ask: the ask ends with the peer's reply. Sub-agents spend the same peer quota: give each a disjoint task and ask for one merged final report.
- Issue: #361 (checked 2026-11-13)

## Quota windows depend on the peer plan (`plan-windows`)

- Keywords: cc, ag, cx, absent, not reported, plan, window, windows, cx-plan-windows, codex, claude, agy, quota, pool, 5h, 7d, X-5H, X-7D, prolite, reported, monitor, incomplete
- Symptom: peerhub monitor shows a cc, ag or cx pool as n/a not reported (plan X), and agents read the peer as 0% or unusable.
- Fix: Pools come from the provider response: a plan may omit a configured window (observed cx prolite: 7-day only, no 5h window; cc ultra still reports its 5h window C-5H, measured 2026-11-09). Since 0.24.1, all peers record omitted configured pools from complete, successfully parsed reports as ABSENT; malformed or partial reports do not establish absence. The monitor draws no POOLS or QUOTA row for them (an n/a pool is named after its peer's QUOTA row, as in (C-5H n/a)) and skips them when computing peer quota: neither exhausted nor unlimited. Use monitor --expand --show-quota-notes to show the plan omission or provider-disabled explanation (hidden by default in compact and expanded output). Normal freshness TTL applies, then STALE. An ag bucket the provider flags disabled (for example the 5h limit while the weekly limit is hit) is recorded ABSENT with reason disabled by provider plus its bounded description, treated as n/a (no row, no capacity or reset), and skipped the same way; it returns to MEASURED when the provider re-enables it. Run peerhub observation refresh --peers cc ag cx and read the reported pools before assuming 0%; profile pool declarations stay unchanged.
- Issue: 0.24.0 (cx); 0.24.1 (all peers); #328 (disabled)

## Quota-aware peer runs (`quota-aware-peer-runs`)

- Keywords: quota, 429, reset, stream, pool, parallel, flash, workers, capacity
- Symptom: A peer returns HTTP 429 and an uncertain ask blocks its stream, or parallel work exhausts a shared quota pool.
- Fix: Read peerhub diag --plan and defer when the pool is exhausted. Reconcile uncertain work before retrying. See docs/batch.md for quota-aware parallelism.
- Issue: #297, #298, #299

## Report an issue (`report-issues`)

- Keywords: report, issue, issues, github, bug
- Symptom: Something behaves wrongly and the problem should be filed without leaking secrets.
- Fix: File at github.com/greatgc-flow/peerhub/issues after checking 'gh issue list -R greatgc-flow/peerhub --search KEYWORD'. Include peerhub --version, OS, exact command, status/exit code, and workaround (no secrets/private paths).
- Issue: 0.25.0 plan D5

## Review recipe: scores that converge on 100 (`review-recipe`)

- Keywords: review, review-loop, review-scores-to-100, loop, score, 100, round, brief, materiality, contract, narrow, defects, deduction, unverified, suite, targeted, ports, WinError, worktree, cross-family, final, tag, gitlink
- Symptom: Review rounds repeat or oscillate (99.86, 99.79, 99.67): a reviewer scores 95-97 without naming a defect, deducts for checks it could not run, keeps finding edge cases in the same new component, or parallel full suites exhaust Windows ports (WinError 10048).
- Fix: Paste this reviewer brief: "Score each item 0-100. Deduct only for a defect that violates a documented contract or is reachable from supported inputs; label anything else a recommendation. For every score below 100 give the exact fix (file:line + change). Report checks you could not run as NOT RUN instead of deducting. Re-score until 100." Score each deliverable item separately and re-review the exact shipped tag; confirm the final candidate with a reviewer from another model family. The coordinator runs the full suite once and passes the result on; reviewers run only targeted tests (give them allow rules for the read/test commands they need; run any they could not run yourself). Every live measurement is recorded with the commit it ran on and its time. After two rounds that find new defects in the same new component, narrow its contract (fail closed) instead of patching it again. Never `git add -A` in a checkout that contains agent worktrees: they are committed as gitlinks.
- Issue: #345; 2026-11-10 (0.24.5/0.24.6 review rounds)

## Setup: what each peer needs (tool, sign-in or API key) (`setup`)

- Keywords: setup, install, installation, prerequisites, requirements, needs, missing, executable, not, found, tool, cli, claude, codex, agy, ant, sign-in, signed, login, PATH, version, OPENAI_API_KEY, GEMINI_API_KEY, key, official, page, link
- Symptom: An ask ends failed_not_started with 'claude executable not found' (or codex, agy, ant), with 'set OPENAI_API_KEY' or with 'set GEMINI_API_KEY'; or PeerHub is installed and it is not clear what a peer needs before the first ask.
- Fix: Install the missing CLI and sign in, or set OPENAI_API_KEY or GEMINI_API_KEY in PeerHub's environment. Keep keys off the command line and out of files. Retry after the prerequisite is fixed. See README.md#what-each-peer-needs for each peer.
- Issue: #385

## Store selection, upgrade and restore hazards (`store-and-upgrade`)

- Keywords: store, upgrade, backup, core.db, schema, restore, dev-builds, fenced
- Symptom: Which database an ask uses, what to do before a PeerHub upgrade or restore, and why an old version cannot open a new store.
- Fix: Use peerhub update --plan. Back up the store, wait for running asks and stop monitor before upgrading. Reopen clients after restoring a backup. See README.md#upgrade and docs/config-hierarchy.md#store.
- Issue: 0.25.0 plan D5; #123

## store_missing database selection (`store-missing`)

- Keywords: store_missing
- Symptom: The selected database does not exist.
- Fix: Use --db PATH or PEERHUB_DB to name the intended store, or run from the main checkout. To initialize a new store, use peerhub --db PATH peer register --peer local --name local. See docs/config-hierarchy.md#store.
- Issue: 0.24.0

## Stream reuse cost: reuse within one task only (`stream-reuse-cost`)

- Keywords: stream, reuse, resume, session, context, tokens, quota, cost, vote
- Symptom: Quota drains fast although prompts are short: a long-lived --stream is reused across many tasks, so every ask re-sends the whole earlier conversation (on every model turn).
- Fix: Reuse only within one task. Move a new task to a new stream and send votes fresh with facts inline. A 19-ask cx stream used 23.6M input tokens in one 2026-11-10 measurement. See docs/commands.md#ask.
- Issue: 2026-11-10 quota diet

## Recommend a profile and ask command (`suggest`)

- Keywords: suggest, recommend, profile choice, 7d pace, 5h usage
- Symptom: Choose a profile in the requested tier using quota evidence.
- Fix: Run peerhub suggest --tier TIER --candidates to inspect eligibility, then omit --candidates to print an ask. See docs/batch.md#suggest-an-ask for prompt files, ranking and refresh costs.
- Issue: 0.25.0 plan D7/D8/A6/A7

## Writes, uncertain results and secrets (`writes-and-uncertain`)

- Keywords: writes, uncertain, pending_earlier_record, secrets, blind-retry, partial, edits, claims
- Symptom: A result is uncertain, a pending_earlier_record blocks a retry, or it is unclear which peers enforce read-only and what PeerHub stores.
- Fix: Stop the stream and inspect peerhub diag health and the working tree for partial edits. Reuse the same request ID only when the recovery guidance permits replay. Keep secrets out of prompts that persist in the store. See docs/guidance-reference.md#ask_uncertain and docs/adapters/peer-cli-reference.md#sandbox-and-event-limits.
- Issue: 0.25.0 plan D5; #140, #325
