# PeerHub Lessons

Generated from `peerhub/config_data/lessons.toml`; edit that file, then regenerate with:

```powershell
python -c "from pathlib import Path; from peerhub.cli.lessons import render_lessons_reference; Path('docs/lessons.md').write_text(render_lessons_reference(), encoding='utf-8', newline='\n')"
```

Use `peerhub explain <free words>` for offline, case-insensitive AND-token lookup over titles and keywords.
Known guidance codes and aliases take priority, then an exact profile ID (for example `cc.effort`), then `lessons` (an index: one `id: title` line per lesson), then an exact lesson ID (the IDs below) before keyword lookup. Keyword matches list recipes first, then lesson ID order.

## ag profile choice for bulk work (observed 2026-11-01) (`ag-bulk-profile-heuristics`)

- Keywords: ag, agy, profile, effort, flash, argon, bulk, image, WinError, 206, prompt, size
- Symptom: A low-effort ag profile returns an empty reply on a long list of file or shell items; very long inline prompts fail on Windows.
- Fix: Observation from one small test (Windows, one run per cell, no ground truth for images): flash-low broke down somewhere between 25 and 150 items, flash-medium finished them, high only cost time. Treat these as heuristics, not thresholds. ag prompts, including catch-up history, go over stdin up to 1,000,000 bytes; only an agy without stream-json input or PEERHUB_AG_TRANSPORT=argv uses argv, limited to 30,000 bytes (30 KB) on Windows and 120,000 bytes (120 KB) on POSIX. For ask_prompt_too_large, split into parts under the limit (one ask each), or for an ag argv limit use cc/cx on a separate stream (stdin, 1,000,000-byte maximum); if the prompt alone is under the limit, catch-up history pushed it over, so start a new --stream; a changed prompt needs a new request-id. File paths do not bypass the limit and ag file reads may be denied; inline small excerpts. Dense numeric image tables stay unreliable for every model: cross-read before trusting.
- Issue: #290, #309

## ag recipe (empty replies, permissions, trusted -w) (`ag-recipe`)

- Keywords: ag, agy, recipe, permission, permissions, writable, read-only, sandbox, limit, failed_not_started, empty, reply, denied_empty, delivered, trustedWorkspaces, allow, regex, command, files, binary, prompt, query-file, resume
- Symptom: ag fails or returns an empty reply: delivered/TERMINAL with no text, denied_empty after a denied shell or read_file, a resumed stream that fabricates paths, or failed_not_started without write permission.
- Fix: Recipe: put the content the task needs inline via --query-file and say "no tools"; -w is the trusted project root, not a temp or scratch folder (a prerequisite, not a guarantee); pass --writable on every turn, including resumed turns: PeerHub requires it even for read tasks (accept-edits, workspace-wide; #140, no read-only ag sandbox: use a provider with enforced read-only execution when writes are not allowed); one task per ask; verify claims from flash tiers; the prompt goes over stdin (stream-json) up to 1,000,000 bytes like cc/cx; only an agy without stream-json input or PEERHUB_AG_TRANSPORT=argv uses argv, limited to 30,000 bytes on Windows and 120,000 bytes on POSIX: when over, split it or use cc/cx on a separate stream, and a changed prompt needs a new request-id; after an empty or denied_empty reply never resume that stream (this overrides stream reuse): start a new stream with the content inlined. Headless prompts auto-deny: a shell command needs matching command(...) or command(*) allow rules. If a fresh ask is still empty, check in order: many or unreadable files named in one prompt (name fewer, or use a stronger profile for bulk work); -w outside the agy trustedWorkspaces list; binary/NUL/huge files (give an excerpt inline); a shell command not allow-listed (agy rules match exact strings on Windows: use a command(regex:...) rule or avoid commands); pool quota exhausted (RESOURCE_EXHAUSTED; the error text gives the reset: rotate profile/pool). Past observation (E2, #302, closed 2026-11-09 as not reproducible after 3 re-tests): read_file was once denied even with a trusted -w, so still do not rely on file reads; inline the content.
- Issue: #140, #284, #287, #300, #302

## ag asks require explicit --writable, including pings and reviews (`ag-requires-writable`)

- Keywords: ag, agy, writable, pings, reviews, ask_delivery_failed
- Symptom: [ask_delivery_failed] ag execution requires --writable: agy CLI does not enforce a read-only sandbox when --mode accept-edits is omitted.
- Fix: The #140 gate is intentional: agy has no read-only sandbox, so PeerHub rejects native ag asks before spawning unless write access is explicit. Add --writable to your command; pings and reviews need it too. It allows edits: trusted workspaces only. If write access is not acceptable, use a provider with an enforced read-only sandbox.
- Issue: #140, #359

## ag silence and long asks (`ag-silence-long-asks`)

- Keywords: ag, agy, silence, silent, long, asks, timeout, uncertain, blocked, stream
- Symptom: ag prints nothing until completion; a long ask can hit the silence timeout.
- Fix: Use --timeout-seconds 7200 --silence-timeout-seconds 7200 for long asks. Silence timeout means uncertain execution: inspect peerhub diag health and effects, then continue genuinely new work on a new stream with a summary; never blindly retry uncertain work.
- Issue: #118

## API peers ca, oa and ga: text-only asks on API credits (`api-peers`)

- Keywords: ca, oa, ga, api, ant, openai, gemini, OPENAI_API_KEY, GEMINI_API_KEY, RESOURCE_EXHAUSTED, credits, credit, balance, stateless, truncated, incomplete, request_rejected, inline, vote, votes, text
- Symptom: A vote, text review or documentation check spends a subscription pool of cc, cx or ag although it needs no tools or files; or a ca/oa/ga ask is refused (request_rejected), ends truncated or incomplete, or does not see a file; or the credit balance of an API is wanted.
- Fix: Use ca (Claude API through the ant CLI; sign in once with ant auth login), oa (OpenAI Responses API; the key only in the environment variable OPENAI_API_KEY) or ga (Gemini API; the key only in the environment variable GEMINI_API_KEY) instead of cc/cx/ag when the whole task is text that fits in the prompt: votes, text reviews, documentation checks. Never put a key on a command line or in a file. They spend API credits, not a subscription pool (no POOLS row, never in suggest:). PeerHub shows no credit balance: none of the three APIs gives it to the login or key that PeerHub uses; read it in the Claude Console, the OpenAI Platform or Google AI Studio. Use cc/cx/ag when the task needs tools, files, the workspace, edits or several turns. Inline the text (--query-file): ca, oa and ga read no file and have no tools and no session. One ask per stream: any other record on the stream refuses the ask (request_rejected: ca is stateless; use a new stream). Without --stream every ask gets its own stream peer:<peer>:<request-id> (printed as a stream: line before session:), so leave --stream out or give every vote its own; only the same request-id with the same author (--author-peer) replays the stored result, and repeating it after an interrupted truncated or incomplete ask returns that saved status. A reply of the wrong shape ends uncertain. Not signed in: failed_not_started with 'ant is not signed in or its login expired (run: ant auth login)'; run ant auth login and repeat the command. The @ rule: ant inlines the file named by a string that starts with @; PeerHub puts the fixed line 'PeerHub ask (text only, no tools, no files).' before the prompt, so a leading @path is sent as literal text and no file is read. Also refused: --writable, --effort, no --profile and no --model, a --model that does not match ^claude-[a-z0-9.-]{1,64}$ (ca), ^gpt-[a-z0-9.-]{1,64}$ (oa) or ^gemini-[a-z0-9.-]{1,64}$ (ga). Replies stop at 16,000 output tokens for ca and oa and at 32,000 for ga, where thinking counts against the limit: truncated (cut at the limit) and incomplete (ended for another reason, for example a refusal or, for ga, a blocked prompt) are not replies; the text is in partial_response; ask again on a new stream with a narrower question (peerhub explain truncated, peerhub explain incomplete). Used-up credits end not_executed (quota_exhausted, no reset time); for ga this is HTTP 429 RESOURCE_EXHAUSTED, which a rate limit and a spent or missing quota give alike: wait and ask again on a new stream, or check the plan and billing of the key. oa and ga ignore the proxy variables (oa also OPENAI_BASE_URL): a network that needs a proxy is not supported. Timeouts: for oa and ga, --timeout-seconds is a total over the whole call (connecting, headers and body; when it passes the ask ends uncertain), and --silence-timeout-seconds, when given, limits one network operation; for ca both bound the ant process as for the other CLI peers. An API key in the environment is visible to every peer process that PeerHub starts (an account-bound cc or cx peer gets no OPENAI_* variable): set the keys only where the API peers are used.
- Issue: #378, #381

## Explicit timeouts for implementer and review asks (`ask-timeouts`)

- Keywords: timeout, timeout-seconds, 600, implementer, review, writer, ask_uncertain, runtime_timeout, cc, cx
- Symptom: A long implementer or review ask ends as ask_uncertain (evidence runtime_timeout) with partial edits in the workspace.
- Fix: --timeout-seconds defaults to 600 (as batch). Long implementer or review asks still need a larger value (cc/cx 1800-3600; ag up to 7200 with --silence-timeout-seconds unset or >= it). After a timeout or uncertain result never retry on the same stream: check the working tree for partial edits, then continue on a new stream.
- Issue: #350

## Batch review findings and verify drafts (`batch-review-and-verify`)

- Keywords: review, findings, batch, mechanical, sweep, draft, verify, script
- Symptom: Reviewers surface one mechanical discrepancy per round, or a draft evidence table contains unverified errors.
- Fix: Ask for the complete mechanical sweep in one list and compare identifiers with a small script before voting. Treat drafts as unverified; check evidence and values against live files with commands, and have a second reviewer triage objections.
- Issue: #297, #298

## Shell batching on Windows (CRLF, last line) (`batch-shell-crlf`)

- Keywords: batch, shell, crlf, profile, newline, windows, read, job, ALL_DONE, dry-run
- Symptom: A shell loop over a job list passes the profile with a trailing carriage return (unknown profile), drops the last line when it has no trailing newline, and prints ALL_DONE although jobs failed.
- Fix: Prefer peerhub batch run with --dry-run (it validates profiles first). If looping in a shell: strip CR from the list (tr -d CR), read with while read -r k p || [ -n "$k" ], use one unique stream per job and check each output status instead of trusting wait.
- Issue: #289

## A cut-off reply usually means your tool timed out, not peerhub (`caller-timeout-claim-held`)

- Keywords: claim, claim_held, ask_claim_held, preamble, cut off, partial, timeout, timed out, background, request-id, replay, cx, long
- Symptom: An ask shows only a preamble ("I'll audit ..."), then the next ask on the same stream fails with ask_claim_held.
- Fix: Confirmed in #312 from the store: the ask finished with a full reply and released its claim when the turn ended; only the caller had stopped waiting. If this happens, do not open a new stream or retry with a new request-id. Wait for the claim to end, then rerun the identical command with the same --request-id: a completed turn replays the stored reply without a new provider call; otherwise follow the returned uncertainty guidance (ask_uncertain or ask_blocked_uncertain). Run long asks in the background and wait for completion.
- Issue: #312

## cc allow rules are exact-match and model-dependent (`cc-allow-rules`)

- Keywords: cc, claude, allow, rules, rule, allowlist, bash, permission, permissions, denied, denied_actions, model, fable, haiku, headless, prefix, exact, settings
- Symptom: A headless cc ask with one model denies a Bash command that .claude/settings.local.json allows and another model runs (observed on #303: fable denied, haiku ran; not reproduced), or cc returns no usable answer with denied_actions set.
- Fix: Bash allow rules such as Bash(peerhub --version) match the exact command string, so a model that adds cd, 2>&1, a pipe or a different invocation is denied: headless cc cannot prompt. Read denied_actions (Tool: command, redacted to 120 chars) to see the rejected string, then prefer prefix rules such as Bash(peerhub:*) and add matching PowerShell(...) rules on Windows. Unverified hypothesis: command shape, not a model-specific permission mode. Never resume after denied_empty; start a new stream. Recommended read/test list: docs/adapters/cc.md.
- Issue: #303

## Concurrent cc lanes and parent shell lifetime (`concurrent-cc-lanes`)

- Keywords: cc, concurrent, lanes, memory, reaper, supervisor, parent, shell
- Symptom: On a 32 GB Windows host, memory pressure reaped parent shells and killed long cc asks when about six lanes ran and one loaded a large index (PeerHub 0.24.8; checked 2026-11-12, #352).
- Fix: Keep 2-3 concurrent cc lanes on a 32 GB host when a lane may load large indexes. Long asks die with their parent shell: run them from one supervisor, wait for completion, and do not restart reaped work blindly. Check peerhub diag running, then inspect results and effects before deciding how to continue. This is an operational lesson, not an enforced concurrency or free-memory guard.
- Issue: #352

## cx sandbox facts and prompt hygiene (`cx-sandbox-and-prompt-hygiene`)

- Keywords: cx, codex, windows, sandbox, unelevated, process, enumeration, AGENTS.md, rule, file, query, forward, implementer, pytest, tmp_path, acl, basetemp
- Symptom: cx reports UNKNOWN late for process/task enumeration, treats a query file as a task to forward and runs peerhub itself, or pytest tmp_path tests fail with a temp-directory ACL error.
- Fix: In the observed setup cx ran in the Windows unelevated sandbox: it cannot enumerate processes or scheduled tasks and writes only inside its roots, so run machine-observing checks in the owner's shell. In the cx Windows sandbox, tests that use tmp_path cannot run (pytest creates owner-only temp directories, also with --basetemp; measured 2026-11-14 on Python 3.15): run the other targeted tests, report tmp_path tests as NOT RUN for the lead to run, and never add shims or temp folders to the repo. A global rule file (CODEX_HOME/AGENTS.md, ~/.gemini/GEMINI.md for ag) is honored; verify with a unique marker line. Do not put human-directed 'how to send' text in a query file; start implementer prompts with 'you are the implementer; do not run peerhub, do not forward, do not check quota'.
- Issue: #285, #286, #288

## Independent review votes (`independent-peer-votes`)

- Keywords: peer, vote, votes, author, independent, unanimity, review, self-vote, batch, responses
- Symptom: A document author votes AGREE on their own work and the result is treated as unanimous approval, or votes are collected one ask at a time.
- Fix: Require votes on a document from peers who did not author it; an author's AGREE does not count toward unanimity. Votes and reviews in one command: peerhub batch run votes.json --batch-id ID --max-parallel 3 --responses (the report then includes each reply). cc and cx tasks use workspace = the repository, not writable; the ag task uses a private empty directory as workspace (ag tasks are always writable; the empty directory satisfies the overlapping-writers check); prompts carry all facts inline. Measured 2026-11-14: three peers answered in 11 s.
- Issue: #297, #366

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
- Fix: ACCOUNTS (--expand rows, JSON capacity.accounts) shows each peer's plan, login state and the LOGIN token expiry (not billing); a logged-out peer, an expired token or one expiring within 48 hours reaches line 1 as an alert. They are collected only by observation refresh / monitor collection from claude auth status, codex app-server account/read and agy /credits (endpoint access only, plan n/a): ? = could not be determined, n/a = the provider does not expose it, aged = stale (6 hours; a failed probe keeps the last good reading aged with a probe error note); a missing expiry does not mean logged out; API-key/Bedrock/Vertex logins are n/a; emails, org names, ids, tokens and identity hashes are never stored. When a fresh account plan fills an unknown 5H plan note, JSON keeps the stored reason and adds display_plan with plan_source account. When a newer ABSENT quota reason reports a different plan for the same peer/login, ACCOUNTS shows the quota plan with a plan changed note; JSON adds plan_changed: true and plan_source: "quota". Account re-probes wait until the account reading is at least five minutes old, including persistent mismatches.
- Issue: #329

## DRIFT peers (`monitor-drift`)

- Keywords: DRIFT
- Symptom: Observed provider CLI or model facts differ from configured observations.
- Fix: Run the refresh-model-profiles skill or upgrade PeerHub. Review model facts before changing profile policy; observation refresh alone does not approve defaults.
- Issue: 0.24.0

## INSUFF projection data (`monitor-insuff`)

- Keywords: INSUFF
- Symptom: A measured quota window has too few samples or too little use for projection.
- Fix: Capacity remains measured; forecast is unavailable until the projection support gates are met. Do not interpret INSUFF as healthy or unlimited.
- Issue: 0.24.0

## PROBE age (`monitor-probe-age`)

- Keywords: PROBE, age
- Symptom: A provider probe failed or has no current usable evidence; the suffix is its age.
- Fix: Run peerhub observation refresh --peers PEER. Inspect authentication and installed CLI if the probe still fails. A peer-level probe failure is hidden once a strictly newer MEASURED pool row exists for the same peer and the same probe source (a poller is one source: ag /usage and its statusline fallback, and a poller that raised); another probe source, another peer, a failure from the same run, or an agy refusal / token-use / conflict row still shows. Stored history is kept.
- Issue: 0.24.0, #328

## STALE:window (`monitor-stale-window`)

- Keywords: STALE, window
- Symptom: Quota evidence has expired.
- Fix: Run peerhub observation refresh --peers PEER to collect fresh evidence. Expired capacity is unavailable, never zero or unlimited.
- Issue: 0.24.0

## Monitor states, PACE, suggestions and cycles (`monitor-states`)

- Keywords: monitor, pace, low, fast, aged, suggest, alternative, cycles, tty, legend, quota, unknown, est, estimated
- Symptom: Monitor states, PACE, stale evidence, alternative profiles or redirected watch behavior need explanation.
- Fix: States: ok = enough capacity; low = positive remaining <=10%; fast = >10% remaining but ahead of pace (threat projection, at least two samples); out = 0%; aged = stale; reset = reset passed; n/a = plan omits; ? = unknown. Precedence: n/a ? reset out low fast aged ok; a plan-omitted (ABSENT) pool whose evidence went stale shows aged; other rows without a number show ?. low/fast win over aged; staleness shows in the line-2 quota age, in `quota stale` and the `(aged)` tag on line 1, and with --expand in AGE and the PEERS (aged) tag. OK? (quota unknown) on line 1 = no 7-day pool that the provider reports has a number (all unknown or in error); a store that never had a quota refresh is not an error: POOLS 0, no quota evidence, OK? (quota, accounts, facts not read), exit 0. The order of the frame, the plain and rich views, glyphs and colours: peerhub explain monitor-view. Compact POOLS order: out, low, binding, fast, then the rest (a 7-day pool takes the most severe class of itself and its shown 5-hour row); a 5-hour row shows under its 7-day pool only when it has less left or is low or out. PACE (percentage points, pp) = used% - elapsed% of the window (cumulative vs even burn, not recent activity): + = ahead of even burn (over-use), - = behind (under-use), - cell when no valid anchor (n/a, ?, reset, unusable); shown on aged rows like LEFT, so verify the quota age before relying on PACE. suggest: tier=a|b is advisory (rule H): a is the best eligible pick and b is the best eligible alternative in the same tier from a different peer; eligible candidates without an applicable fast pool rank first, then worst-pool score LEFT% / max(days_to_reset, 0.2), then profile name (compare 7D PACE manually across eligible peers; 5H only gates; all-fast = slow down or defer). The API peers ca, oa and ga have no pool: they are paid from API credits and never appear in POOLS or suggest: (peerhub explain api-peers). Omitted monitor --cycles: non-TTY one cycle (one completed human frame), TTY until interrupted; --cycles 0 watches everywhere. diag --live keeps its 0 default (until interrupted). LEADS (Claude Code lead sessions, read from the local files of the registered folders: CLAUDE_CONFIG_DIR or ~/.claude, plus PEERHUB_LEAD_DIRS): columns from the left are label (16), state, age of the last activity, ctx, running asks, model, title; narrow terminals drop title, model and run before ctx. State: busy; ?busy = alive not confirmed (alive is confirmed on Windows only, by the exact creation time of the process; elsewhere the flag is ?); ? = unknown; - = no session found. ctx 11% = share of the model's maximum input (a plan may cut it); ctx 106k = tokens, window unknown; ctx ? = tokens unknown; ! = 80% or more (a mark, not an alert). Estimated context of cc asks (--expand only: SESSIONS, CONTEXT, PEERS, and the row of the newest ended ask of a stream and peer when that ask produced it, i.e. the estimate was read at or after the ask started): ctx 2% est, 3m ago = an estimate of the session's context at the end of its last ask, as a share of the model's maximum input, with its age (age ? in SESSIONS, CONTEXT and PEERS when its time is unknown); not a live measurement; never on a running row, nor on an ask that failed before it started or ended without a reading; cx and ag asks have none. Unknown state or context = ?; unknown age or model = -; a missing title is left out; Claude Code's files are private formats and a format change shows ?. The same session id in several files: a row whose process could not be confirmed is replaced by a later file of that id whose process is confirmed, and an unreadable transcript counts once per folder. Header forms: LEADS n, LEADS n | partial (1 unreadable, 2 not read), LEADS n | read failed, LEADS ? | partial (...), LEADS ? | read failed (the count is ? whenever the read was not complete and no row can be shown). PEERHUB_MONITOR_TEXT=off hides name, title, prompt and said. Account rows: peerhub explain monitor-accounts.
- Issue: #310, #311, 0.24.4, #376, #378

## Monitor view: frame order and the rich look (`monitor-view`)

- Keywords: monitor, view, order, rich, plain, glyph, glyphs, colour, color, colours, bar, pill, rule, rules, meter, palette, NO_COLOR, unicode
- Symptom: The order of the compact monitor frame, the plain or rich view, or a glyph, bar, pill, rule or colour of the rich view needs explanation.
- Fix: Frame order (both views, since 0.24.18): line 1 (verdict), line 2 (header), POOLS, suggest:, RUNNING, LEADS, RECENT; a small terminal still cuts recent, lead and running rows before pool rows, and line 1 last. The plain view (--view plain, and the default for pipes and AI sessions) is the 0.24.17 text in this order: ASCII, no colour. The rich view (--view rich, the default on a VT terminal) has the same lines and rows in the same budget (24 lines, 100 columns, 8,192 bytes with the colour codes): line 1 starts with a status pill (OK green, OK? yellow, ATTN n red), its facts are separated by a dim dot and the time stands at the right edge; line 2 is dim; a section head is a rule (── POOLS 5 ─ STATE ... ───) that keeps the head's content and never adds a line. A POOLS row puts a glyph beside the state word: ● (*) ok, ▲ (^) fast, ▼ (v) low, × (x) out, ○ (o) aged, reset, n/a or ?; then a 20-cell bar, █ (#) left and ░ (.) used, ─ (-) when there is no number; the bar shrinks to 4 cells, then goes, before a column drops. State, bar and PACE arrow share one colour: green above 50% left, yellow above 25%, orange above 10%, red at 10% or less or out or threatened, dim for aged, unknown, reset or n/a (never green); FAMILY is dim. RUNNING rows start with ► (>) green, × (x) gone red, ! overdue yellow or ? yellow. LEADS rows draw ctx as a five-cell meter, ctx ■■□□□ 46% (# and .), red with ! at 80% or more, a dim ───── for ctx 106k or ctx ?. RECENT rows start with a dot, ● (*), and the state: green ok, yellow unsure, red empty or error. The words of the plain view stay, so no state is told by colour or glyph alone. Colours come from the 256-colour palette with semicolon SGR codes only; COLORTERM changes nothing. NO_COLOR keeps the rich layout and glyphs without colour; output that cannot show Unicode gets the ASCII glyphs in brackets above; a terminal that draws the glyphs two cells wide should use --view plain or PEERHUB_MONITOR_VIEW=plain. --expand keeps its content and gets only the pill and the rules. States, PACE and suggest: peerhub explain monitor-states.
- Issue: #376 (P21, 0.24.18)

## Quota shaping across peers (`multi-peer-quota-shaping`)

- Keywords: quota, cx, ag, cc, pool, consolidated, draft, timer, sleep, reset
- Symptom: The scarce premium peer exhausts its pool early; a delayed start queued with sleep never ran.
- Fix: Splitting one task into several cx asks did not save quota. Have cheap pools (ag) produce drafts and cross-reviews first, then give the scarce peer one consolidated verify-and-integrate pass; check capacity before a big ask and prefer waiting for a reset over stopping mid-task. In-session timers die with the session: for delayed starts use a background job that outlives the wait, or re-check at the next session start.
- Issue: #288

## Peer sub-agents inside one ask (`peer-subagents`)

- Keywords: subagent, subagents, sub-agent, sub-agents, Agent, spawn_agent, invoke_subagent, multi_agent, fan-out, loop
- Symptom: A large task could run as parallel helpers inside one peer ask, or a prompt expects /loop-style persistence inside an ask.
- Fix: Every peer can fan out sub-agents inside one ask: cc through its Agent tool; cx through spawn_agent only when the prompt names that tool explicitly (codex 0.167.2, features.multi_agent on); ag through invoke_subagent. There is no /loop-style persistence inside an ask: the ask ends with the peer's reply. Sub-agents spend the same peer quota: give each a disjoint task and ask for one merged final report.
- Issue: #361 (checked 2026-11-13)

## Quota windows depend on the peer plan (`plan-windows`)

- Keywords: cc, ag, cx, absent, not reported, plan, window, windows, cx-plan-windows, codex, claude, agy, quota, pool, 5h, 7d, X-5H, X-7D, prolite, reported, monitor, incomplete
- Symptom: peerhub monitor shows a cc, ag or cx pool as n/a not reported (plan X), and agents read the peer as 0% or unusable.
- Fix: Pools come from the provider response: a plan may omit a configured window (observed cx prolite: 7-day only, no 5h window; cc ultra still reports its 5h window C-5H, measured 2026-11-09). Since 0.24.1, all peers record omitted configured pools from complete, successfully parsed reports as ABSENT; malformed or partial reports do not establish absence. The monitor shows n/a not reported (plan X), where X is the reported plan or unknown, and skips those pools when computing peer quota: neither exhausted nor unlimited. Normal freshness TTL applies, then STALE. An ag bucket the provider flags disabled (for example the 5h limit while the weekly limit is hit) is recorded ABSENT with reason disabled by provider plus its bounded description, shown as n/a with no capacity or reset, and skipped the same way; it returns to MEASURED when the provider re-enables it. Run peerhub observation refresh --peers cc ag cx and read the reported pools before assuming 0%; profile pool declarations stay unchanged.
- Issue: 0.24.0 (cx); 0.24.1 (all peers); #328 (disabled)

## Quota-aware peer runs (`quota-aware-peer-runs`)

- Keywords: quota, 429, reset, stream, pool, parallel, flash, workers, capacity
- Symptom: A peer returns HTTP 429 and an uncertain ask blocks its stream, or parallel work exhausts a shared quota pool.
- Fix: Check capacity and reset times before work; rotate pools early, reserve scarce capacity for final review, and limit parallel workers to fit the reset window. After a quota failure, continue on a new stream when capacity is available; do not blindly retry an uncertain ask.
- Issue: #297, #298, #299

## Review recipe: scores that converge on 100 (`review-recipe`)

- Keywords: review, review-loop, review-scores-to-100, loop, score, 100, round, brief, materiality, contract, narrow, defects, deduction, unverified, suite, targeted, ports, WinError, worktree, cross-family, final, tag, gitlink
- Symptom: Review rounds repeat or oscillate (99.86, 99.79, 99.67): a reviewer scores 95-97 without naming a defect, deducts for checks it could not run, keeps finding edge cases in the same new component, or parallel full suites exhaust Windows ports (WinError 10048).
- Fix: Paste this reviewer brief: "Score each item 0-100. Deduct only for a defect that violates a documented contract or is reachable from supported inputs; label anything else a recommendation. For every score below 100 give the exact fix (file:line + change). Report checks you could not run as NOT RUN instead of deducting. Re-score until 100." Score each deliverable item separately and re-review the exact shipped tag; confirm the final candidate with a reviewer from another model family. The coordinator runs the full suite once and passes the result on; reviewers run only targeted tests (give them allow rules for the read/test commands they need; run any they could not run yourself). Every live measurement is recorded with the commit it ran on and its time. After two rounds that find new defects in the same new component, narrow its contract (fail closed) instead of patching it again. Never `git add -A` in a checkout that contains agent worktrees: they are committed as gitlinks.
- Issue: #345; 2026-11-10 (0.24.5/0.24.6 review rounds)

## store_missing database selection (`store-missing`)

- Keywords: store_missing
- Symptom: The selected database does not exist.
- Fix: Check the resolved path and source: a Git worktree may have no local store while the database lives in the main checkout. On a discovery miss PeerHub searches the main checkout, then its parents (Git >=2.36; ask -w: main checkout only); a miss exits 5. Run from the main checkout or select the intended store with --db PATH / PEERHUB_DB (--db takes precedence); to create a new store, explicitly select it with peerhub --db PATH peer register --peer local --name local.
- Issue: 0.24.0

## Stream reuse cost: reuse within one task only (`stream-reuse-cost`)

- Keywords: stream, reuse, resume, session, context, tokens, quota, cost, vote
- Symptom: Quota drains fast although prompts are short: a long-lived --stream is reused across many tasks, so every ask re-sends the whole earlier conversation (on every model turn).
- Fix: Reuse a stream only within one task (follow-ups and review rounds on the same diff); start a new stream for a new task or after about four rounds with a 3-5 line summary inline; send votes and confirmations as fresh one-shot asks with the facts inline. Measured 2026-11-10: a cx stream reused for 19 asks cost 23.6M input tokens (1.2M per ask) while fresh one-shot votes cost about 0.1M (cx) and 8k (ag); a cc implementer stream reused for 14 tasks cost 115M.
- Issue: 2026-11-10 quota diet
