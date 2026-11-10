# PeerHub Lessons

Generated from `peerhub/config_data/lessons.toml`; edit that file, then regenerate with:

```powershell
python -c "from pathlib import Path; from peerhub.cli.lessons import render_lessons_reference; Path('docs/lessons.md').write_text(render_lessons_reference(), encoding='utf-8', newline='\n')"
```

Use `peerhub explain <free words>` for offline, case-insensitive AND-token lookup over titles and keywords.
Known guidance codes and aliases take priority, then an exact profile ID (for example `cc.effort`), then an exact lesson ID (the IDs below) before keyword lookup. Keyword matches list recipes first, then lesson ID order.

## ag profile choice for bulk work (observed 2026-11-01) (`ag-bulk-profile-heuristics`)

- Keywords: ag, agy, profile, effort, flash, argon, bulk, image, WinError, 206, prompt, size
- Symptom: A low-effort ag profile returns an empty reply on a long list of file or shell items; very long inline prompts fail on Windows.
- Fix: Observation from one small test (Windows, one run per cell, no ground truth for images): flash-low broke down somewhere between 25 and 150 items, flash-medium finished them, high only cost time. Treat these as heuristics, not thresholds. ag argv prompts, including catch-up history, are limited to 30,000 bytes (30 KB) on Windows and 120,000 bytes (120 KB) on POSIX; --query-file still uses argv. For ask_prompt_too_large, split into parts under the limit (one ask each), or use cc/cx on a separate stream (stdin, 1,000,000-byte maximum); if the prompt alone is under the limit, catch-up history pushed it over, so start a new --stream; a changed prompt needs a new request-id. File paths do not bypass the limit and ag file reads may be denied; inline small excerpts. Dense numeric image tables stay unreliable for every model: cross-read before trusting.
- Issue: #290, #309

## ag recipe (empty replies, permissions, trusted -w) (`ag-recipe`)

- Keywords: ag, agy, recipe, permission, permissions, writable, read-only, sandbox, limit, failed_not_started, empty, reply, denied_empty, delivered, trustedWorkspaces, allow, regex, command, files, binary, prompt, query-file, resume
- Symptom: ag fails or returns an empty reply: delivered/TERMINAL with no text, denied_empty after a denied shell or read_file, a resumed stream that fabricates paths, or failed_not_started without write permission.
- Fix: Recipe: put the content the task needs inline via --query-file and say "no tools"; -w is the trusted project root (a prerequisite, not a guarantee); pass --writable on every turn, including resumed turns (#140, no read-only ag sandbox: use a provider with enforced read-only execution when writes are not allowed); one task per ask; verify claims from flash tiers; after an empty or denied_empty reply never resume that stream (this overrides stream reuse): start a new stream with the content inlined. If a fresh ask is still empty, check in order: many or unreadable files named in one prompt (name fewer, or use a stronger profile for bulk work); -w outside the agy trustedWorkspaces list; binary/NUL/huge files (give an excerpt inline); a shell command not allow-listed (agy rules match exact strings on Windows: use a command(regex:...) rule or avoid commands); pool quota exhausted (RESOURCE_EXHAUSTED; the error text gives the reset: rotate profile/pool). Past observation (E2, #302, closed 2026-11-09 as not reproducible after 3 re-tests): read_file was once denied even with a trusted -w, so still do not rely on file reads; inline the content.
- Issue: #140, #284, #287, #300, #302

## ag silence and long asks (`ag-silence-long-asks`)

- Keywords: ag, agy, silence, silent, long, asks, timeout, uncertain, blocked, stream
- Symptom: ag prints nothing until completion; a long ask can hit the silence timeout.
- Fix: Use --timeout-seconds 7200 --silence-timeout-seconds 7200 for long asks. Silence timeout means uncertain execution: inspect peerhub diag health and effects, then continue genuinely new work on a new stream with a summary; never blindly retry uncertain work.
- Issue: #118

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
- Fix: Bash allow rules such as Bash(peerhub --version) match the exact command string, so a model that adds cd, 2>&1, a pipe or a different invocation is denied: headless cc cannot prompt. Read denied_actions (Tool: command, redacted to 120 chars) to see the rejected string, then prefer prefix rules such as Bash(peerhub:*) and add matching PowerShell(...) rules on Windows. Unverified hypothesis: command shape, not a model-specific permission mode. Never resume after denied_empty; start a new stream.
- Issue: #303

## cx sandbox facts and prompt hygiene (`cx-sandbox-and-prompt-hygiene`)

- Keywords: cx, codex, windows, sandbox, unelevated, process, enumeration, AGENTS.md, rule, file, query, forward, implementer
- Symptom: cx reports UNKNOWN late for process/task enumeration, or treats a query file as a task to forward and runs peerhub itself.
- Fix: In the observed setup cx ran in the Windows unelevated sandbox: it cannot enumerate processes or scheduled tasks and writes only inside its roots, so run machine-observing checks in the owner's shell. A global rule file (CODEX_HOME/AGENTS.md, ~/.gemini/GEMINI.md for ag) is honored; verify with a unique marker line. Do not put human-directed 'how to send' text in a query file; start implementer prompts with 'you are the implementer; do not run peerhub, do not forward, do not check quota'.
- Issue: #285, #286, #288

## Run path-aware checks against their intended roots (`dry-run-path-aware-checkers`)

- Keywords: dry-run, isolated, copy, absolute, paths, checkers, custody
- Symptom: An isolated-copy dry-run reports violations because preserved audit paths point to the real stores.
- Fix: Run path-aware checks read-only against the real tree or rewrite roots in a throwaway copy; report the real-tree result alongside isolated results.
- Issue: #299

## Check every condition in shell wait loops (`explicit-shell-wait-conditions`)

- Keywords: shell, wait, loop, until, condition, python, gate
- Symptom: A shell until-loop exits early because its compound condition does not check every completion signal.
- Fix: Use a small script that explicitly checks each condition and reports the result.
- Issue: #299

## Independent review votes (`independent-peer-votes`)

- Keywords: peer, vote, author, independent, unanimity, review, self-vote
- Symptom: A document author votes AGREE on their own work and the result is treated as unanimous approval.
- Fix: Require votes on a document from peers who did not author it; an author's AGREE does not count toward unanimity.
- Issue: #297

## Installed vs checkout mismatch (`installed-vs-checkout`)

- Keywords: python, pip, installed, checkout, mismatch, PYTHONPATH, version, source, module
- Symptom: The pip-installed peerhub command behaves differently from the current checkout.
- Fix: Run python -m peerhub from the checkout (or set PYTHONPATH to it), or install that checkout with python -m pip install -e . using the same Python environment.
- Issue: #117

## invalid_arguments CLI input (`invalid-arguments`)

- Keywords: invalid_arguments
- Symptom: The command arguments were rejected.
- Fix: Run peerhub COMMAND --help, correct the arguments and retry only if no execution began.
- Issue: 0.24.0

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

- Keywords: monitor, pace, low, fast, aged, suggest, alternative, cycles, tty, legend
- Symptom: Monitor states, PACE, stale evidence, alternative profiles or redirected watch behavior need explanation.
- Fix: States: ok = enough capacity; low = positive remaining <=10%; fast = >10% remaining but ahead of pace (threat projection, at least two samples); out = 0%; aged = stale; reset = reset passed; n/a = plan omits; ? = unknown. Precedence: n/a ? reset out low fast aged ok; a plan-omitted (ABSENT) pool whose evidence went stale shows aged; other rows without a number show ?. low/fast win over aged; staleness still shows in AGE and the PEERS (aged) tag. PACE (percentage points, pp) = used% - elapsed% of the window (cumulative vs even burn, not recent activity): + = ahead of even burn (over-use), - = behind (under-use), - cell when no valid anchor (n/a, ?, reset, unusable); shown on aged rows like LEFT, so verify fresh AGE before relying on PACE. suggest: tier=a|b is advisory (rule H): a is the best eligible pick and b is the best eligible alternative in the same tier from a different peer; eligible candidates without an applicable fast pool rank first, then worst-pool score LEFT% / max(days_to_reset, 0.2), then profile name (compare 7D PACE manually across eligible peers; 5H only gates; all-fast = slow down or defer). Omitted monitor --cycles: non-TTY one cycle (one completed human frame), TTY until interrupted; --cycles 0 watches everywhere. diag --live keeps its 0 default (until interrupted). ACCOUNTS (compact line, --expand rows, JSON capacity.accounts) shows each peer's plan, login state and, when within 14 days, the LOGIN token expiry (not billing), collected only by observation refresh / monitor collection from claude auth status, codex app-server account/read and agy /credits (endpoint access only, plan n/a): ? = could not be determined, n/a = the provider does not expose it, aged = stale (6 hours; a failed probe keeps the last good reading aged with a probe error note); a missing expiry does not mean logged out; API-key/Bedrock/Vertex logins are n/a; emails, org names, ids, tokens and identity hashes are never stored. When a fresh account plan fills an unknown 5H plan note, JSON keeps the stored reason and adds display_plan with plan_source account.
- Issue: #310, #311, 0.24.4, #329

## Quota shaping across peers (`multi-peer-quota-shaping`)

- Keywords: quota, cx, ag, cc, pool, consolidated, draft, timer, sleep, reset
- Symptom: The scarce premium peer exhausts its pool early; a delayed start queued with sleep never ran.
- Fix: Splitting one task into several cx asks did not save quota. Have cheap pools (ag) produce drafts and cross-reviews first, then give the scarce peer one consolidated verify-and-integrate pass; check capacity before a big ask and prefer waiting for a reset over stopping mid-task. In-session timers die with the session: for delayed starts use a background job that outlives the wait, or re-check at the next session start.
- Issue: #288

## Check governed pins before implementation (`pin-census-before-implementation`)

- Keywords: pin, hash, census, TDD, implementation, governed, files
- Symptom: An implementation pass stops at files already pinned by a governed move.
- Fix: Run a read-only pin census before the implementation pass and include its results in the prompt.
- Issue: #298

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

## Review rounds that converge on 100 (`review-scores-to-100`)

- Keywords: review, score, 100, round, completeness, matrix, verify, unverified, deduction
- Symptom: Review rounds repeat: a reviewer scores items 95-97 without naming a defect, or deducts for checks it could not run, so the score never reaches 100.
- Fix: Ask in the first prompt: for every item below 100 name the concrete, checkable change (file:line + fix), else score 100; report checks that could not run separately as NOT RUN instead of deducting. Give peers the permissions to run the read/test commands they need (project .claude/settings.local.json allow rules for pytest, pyright, git read commands) so reviews verify instead of guessing, and run any command they could not run yourself.
- Issue: 2026-11-10 (0.24.5/0.24.6 review rounds)

## store_missing database selection (`store-missing`)

- Keywords: store_missing
- Symptom: The selected database does not exist.
- Fix: Check the resolved path and source: a Git worktree may have no local store while the database lives in the main checkout. Run from the main checkout or select the intended store with --db PATH / PEERHUB_DB (--db takes precedence); to create a new store, explicitly select it with peerhub --db PATH peer register --peer local --name local.
- Issue: 0.24.0

## Stream reuse cost: reuse within one task only (`stream-reuse-cost`)

- Keywords: stream, reuse, resume, session, context, tokens, quota, cost, vote
- Symptom: Quota drains fast although prompts are short: a long-lived --stream is reused across many tasks, so every ask re-sends the whole earlier conversation (on every model turn).
- Fix: Reuse a stream only within one task (follow-ups and review rounds on the same diff); start a new stream for a new task or after about four rounds with a 3-5 line summary inline; send votes and confirmations as fresh one-shot asks with the facts inline. Measured 2026-11-10: a cx stream reused for 19 asks cost 23.6M input tokens (1.2M per ask) while fresh one-shot votes cost about 0.1M (cx) and 8k (ag); a cc implementer stream reused for 14 tasks cost 115M.
- Issue: 2026-11-10 quota diet
