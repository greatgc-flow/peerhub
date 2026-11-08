# PeerHub Lessons

Generated from `peerhub/config_data/lessons.toml`; edit that file, then regenerate with:

```powershell
python -c "from pathlib import Path; from peerhub.cli.lessons import render_lessons_reference; Path('docs/lessons.md').write_text(render_lessons_reference(), encoding='utf-8', newline='\n')"
```

Use `peerhub explain <free words>` for offline, case-insensitive AND-token lookup over titles and keywords.
Known guidance codes and aliases take priority. Matches are ordered by lesson ID.

## ag profile choice for bulk work (observed 2026-11-01) (`ag-bulk-profile-heuristics`)

- Keywords: ag, agy, profile, effort, flash, argon, bulk, image, WinError, 206, prompt, size
- Symptom: A low-effort ag profile returns an empty reply on a long list of file or shell items; very long inline prompts fail on Windows.
- Fix: Observation from one small test (Windows, one run per cell, no ground truth for images): flash-low broke down somewhere between 25 and 150 items, flash-medium finished them, high only cost time. Treat these as heuristics, not thresholds. Keep inline prompts small (a 30000-byte cap applies; WinError 206 was seen with long command lines) and give ag file paths instead. Dense numeric image tables stay unreliable for every model: cross-read before trusting.
- Issue: #290

## ag empty replies and allow rules (`ag-empty-reply-causes`)

- Keywords: ag, agy, empty, reply, delivered, trustedWorkspaces, allow, regex, command, files, binary, prompt
- Symptom: A headless ag ask ends delivered/TERMINAL but the reply is empty.
- Fix: Check in order: many or unreadable files named in one prompt (name fewer; use a stronger profile for bulk work); -w outside the agy trustedWorkspaces list; binary/NUL/huge files (give an excerpt inline); a shell command needed but not allow-listed (agy rules match exact strings on Windows, use a command(regex:...) rule or avoid commands); pool quota exhausted (RESOURCE_EXHAUSTED, the error text gives the reset: rotate profile/pool). Verify claims from flash tiers before use.
- Issue: #284, #287

## ag permission limit (`ag-permission-limit`)

- Keywords: ag, agy, permission, permissions, writable, read-only, sandbox, limit, failed_not_started
- Symptom: Native ag execution refuses to start without explicit write permission.
- Fix: ag needs --writable on every turn, including resumed turns. There is no read-only ag sandbox; use a provider with enforced read-only execution when writes are not allowed.
- Issue: #140

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

## Quota-aware peer runs (`quota-aware-peer-runs`)

- Keywords: quota, 429, reset, stream, pool, parallel, flash, workers, capacity
- Symptom: A peer returns HTTP 429 and an uncertain ask blocks its stream, or parallel work exhausts a shared quota pool.
- Fix: Check capacity and reset times before work; rotate pools early, reserve scarce capacity for final review, and limit parallel workers to fit the reset window. After a quota failure, continue on a new stream when capacity is available; do not blindly retry an uncertain ask.
- Issue: #297, #298, #299
