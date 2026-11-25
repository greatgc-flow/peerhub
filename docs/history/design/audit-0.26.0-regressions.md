# Regression audit 0.24.19..0.25.0 (raw peer replies, 2026-11-21)

batch audit1 max-parallel 2
a-ag                     ag       COMPLETED
a-cx                     cx       COMPLETED
--- a-ag reply ---
### Regressions (0.24.19..0.25.0)

1. **Monitor watch quota probing flipped to opt-in (`--probe`), starving monitor, `diag`, `suggest`, non-default accounts, and 0.25.0 quota-delta telemetry**
   - **Evidence:** `0.25.0` Changed — *"Monitor watch reads committed quota by default. Pass `--probe` to collect during watch. CLI dispatch for subscription peers on default accounts and `suggest` attempt a quota refresh only when the snapshot is missing or older than 15 minutes."* + *"Quota intervals require a single observed in-flight ask on the pool and comparable readings from the same reset window."*
   - **Why worse:** A live monitor left open drifts to `aged` indefinitely without `--probe`; `diag quota`/`diag readiness` never trigger refresh; non-default subscription accounts never auto-refresh on dispatch; bursty usage goes up to 15 min without refreshing; and missing before/after snapshots starve 0.25.0's new `profile stats` quota-delta telemetry (which D12's freeze waits on).
   - **Smallest fix:** Restore periodic quota probing as the default in `peerhub monitor` watch (add `--no-probe` opt-out), trigger staleness refresh in `diag quota`/`diag readiness`, and include non-default subscription accounts in dispatch refresh.
   - **Severity:** S1

2. **Monitor re-layout demotes `RUNNING` and `ALERTS` below `RECENT` and evicts them before `RECENT` on bounded terminals**
   - **Evidence:** `0.24.21` (`#388`) had `ALERTS, RUNNING, LEADS, QUOTA, ...` first and `RECENT` last (and severity-based allocation in plain `--expand`). `0.24.29` (`#412`) Changed — order became `POOLS`, `RECENT`, `RUNNING`, `ALERTS`, `LEADS`, `QUOTA`, ...; compact drops lower sections before `RECENT`; `--expand` allocates up to 8 two-line `RECENT` rows before lower sections and evicts bottom-up (`+N rows hidden`); plain `--expand` *"per-section allocation by severity is gone (the sections are cut from the bottom)."*
   - **Why worse:** High-urgency active failures (`ALERTS`) and in-flight work (`RUNNING`) are now pushed off-screen into `+N rows hidden` by finished history (`RECENT`) on standard 24–40 row terminals.
   - **Smallest fix:** Render non-empty `ALERTS` and `RUNNING` above `RECENT` and protect them ahead of `RECENT` in the height-eviction ladder (both compact and `--expand`, rich and plain).
   - **Severity:** S1

3. **`n/a` pools vanished from `POOLS` and `QUOTA` rows, and quota notes hidden by default even in `--expand`**
   - **Evidence:** `0.24.22` Changed — *"Quota notes such as `not reported by plan pro` and `disabled by provider` are hidden by default in compact and expanded output. The `n/a` state and family stay visible. Add `--show-quota-notes` to display those explanations"*. `0.24.26` (`#400`) Changed — *"a pool whose state is `n/a` ... is never a row in POOLS or QUOTA, compact or `--expand`, plain or rich; the POOLS count is the rows drawn, QUOTA names each hidden pool after its peer's row (`(C-5H n/a)`, `(X-30D n/a)`), and `--show-quota-notes` ... are unchanged."*
   - **Why worse:** Compact mode stops after `LEADS` (no `QUOTA`), so a provider-disabled or plan-unreported pool now disappears completely with zero indicator and a silently reduced `POOLS` count; `--expand` still hides quota notes by default; and `--show-quota-notes` has no `n/a` row in compact/`POOLS` to explain.
   - **Smallest fix:** Show `(POOL n/a)` inline in `POOLS` (matching `QUOTA`), and render `n/a` rows with their notes whenever `--show-quota-notes` is passed (and show notes by default in `--expand`).
   - **Severity:** S2

4. **Live monitor unconditionally enters alternate screen and erases the last frame on exit**
   - **Evidence:** `0.24.28` (`#410`) Fixed — *"`peerhub monitor` now draws on the terminal's alternate screen (like `top`) and restores the screen on exit."*
   - **Why worse:** Exiting `peerhub monitor` (Ctrl-C) wipes the dashboard instead of leaving the last frame in scrollback, destroying copy-pasteable `suggest:` commands, `ATTN` alerts, and `RECENT` ask IDs right when the user exits to act on them; there is no `--no-alt-screen` opt-out and no scrollback during watch.
   - **Smallest fix:** Print the final rendered frame to the normal screen on exit (or add `--no-alt-screen`).
   - **Severity:** S2

5. **`RECENT` drops `cx`/`ag` context estimates (`-`) and stamps the stream's current newest session state onto older asks**
   - **Evidence:** `0.24.29` (`#412`) Changed — *"The session values (mode, uses, generation, context) are those of the stream's newest session now; fresh, tombstoned and missing sessions show `-`, never 0, and `cx` and `ag` show `-` for the context."* + *"The old RECENT context estimate that the view joined from the `SESSIONS` sample (`ctx 2% est, 3m ago` on a row) is gone; the SESSIONS, CONTEXT and PEERS sections keep it."*
   - **Why worse:** `cx` and `ag` lose all context visibility in `RECENT` (and completely in compact mode, which omits `SESSIONS`/`CONTEXT`/`PEERS`), and historical asks in a stream display the *current* session's stats (or `-` after a later tombstone/reset) instead of their own state.
   - **Smallest fix:** Fall back to the existing `SESSIONS` context estimate (`est`) for `cx`/`ag` in `RECENT`, and stamp live-session `now` values only on the newest ask of each stream.
   - **Severity:** S2

6. **`--expand` moved the full `DB:` path from the protected top to "near the end" where height-bounding hides it**
   - **Evidence:** `0.24.21` (`#388`) Changed — *"One line with the store follows (`DB:env (<path>)`; it wraps on a narrow terminal and is never cut)"*. `0.24.29` (`#412`) Changed — *"the title line (it carries `DB:<source>`; `--expand` prints the full path as one line near the end)"*.
   - **Why worse:** On any height-bounded terminal, the full SQLite path now sits in the bottom eviction zone and disappears under `+N rows hidden`, forcing a manual `--snapshot` just to confirm which DB file is active.
   - **Smallest fix:** Move the full `DB:<source> (<path>)` line back into the protected header block in `--expand` (directly under the title or `POOLS`/`suggest:`).
   - **Severity:** S2

7. **`peerhub ask` emits unconditional stderr noise (terminal receipt + `cx.deepthink` alias notice)**
   - **Evidence:** `0.25.0` Added — *"Asks print a one-line terminal receipt on stderr. `PEERHUB_NO_RECEIPT=1` silences it."* `0.25.0` Changed (D11/A9) — *"`cx.deepthink` is now an alias of `cx.effort` with effort `xhigh` ... and prints one stderr line when an ask uses it."*
   - **Why worse:** Previously, successful asks produced clean stderr; now every ask writes to stderr unless `PEERHUB_NO_RECEIPT=1` is manually set (no TTY/`--json` suppression documented, and no opt-out for the `cx.deepthink` line), breaking wrappers/CI that check stderr or capture `2>&1`.
   - **Smallest fix:** Emit the receipt (and alias notice) only when stderr is a TTY and `--json` is not set, and have `PEERHUB_NO_RECEIPT=1` silence both.
   - **Severity:** S2

8. **`peerhub guide` stripped of profile table, `setup` pointer, and operational rules behind 6 unenumerated `explain` topics**
   - **Evidence:** `0.24.20` (`#385`) Added — *"`peerhub guide` has one line that points to the lesson [setup] (58 lines in all)."* `0.25.0` Changed (D5) — *"`peerhub guide` ... now at most 1600 bytes of decisions only ... and one pointer line to `peerhub profile list` and `peerhub explain <topic>`. The profile table is gone ... The rare hazards, the store and upgrade steps, the load limits, the cost side effects, the accounts rules, the discovery commands and the issue-report steps moved to six new `explain` topics ... every earlier guide fact is reachable through `peerhub explain lessons`."*
   - **Why worse:** One `peerhub guide` call previously gave agents/users profiles, setup, hazards, cost side effects, load limits, and account rules. Now callers need multiple manual follow-up commands and aren't even shown the topic names (`setup`, `writes-and-uncertain`, `batch-and-load`, etc.), causing silent misses by LLM agents.
   - **Smallest fix:** List the key `explain` topic names (including `setup`) in the guide's pointer line, and add an additive `peerhub guide --full` flag for one-shot retrieval.
   - **Severity:** S2

9. **`--view plain --expand` broke its 0.24.20 byte-for-byte stability contract and severity-based layout**
   - **Evidence:** `0.24.21` (`#388`) Changed — *"Unchanged: ... `--view plain --expand`, which is byte for byte the 0.24.20 output"*. `0.24.29` (`#412`) Changed — *"The plain `--expand` frame no longer starts with ALERTS and a grouped ACTIVITY section: it has the rich frame's top, order and ladder ... and its per-section allocation by severity is gone"*. `0.25.0` Changed — *"its `--expand` frame, printed whole (not height-bounded), ends with a plain `Guide: <url>` line."*
   - **Why worse:** Breaks scripts, accessibility readers, and docs relying on the pinned plain layout and alert-first severity allocation; also appends an unsolicited `Guide:` trailer line.
   - **Smallest fix:** Restore `ALERTS`/`RUNNING`-first ordering in plain `--expand` and omit the trailing `Guide:` line when `PEERHUB_NO_LINKS=1` or stdout is not a TTY.
   - **Severity:** S2

10. **Monitor `suggest:` (and `peerhub suggest`) now excludes `cc.deepthink`, `ag.effort`, and `cx.deepthink` by default (`suggest = false`)**
    - **Evidence:** `0.25.0` Changed — *"The monitor's `suggest:` line honors `suggest = false` in profile configuration, matching `peerhub suggest`."* + (D11/A9) *"`cc.deepthink`, `ag.effort` and `cx.deepthink` have `suggest = false`"*
    - **Why worse:** Profiles previously eligible in monitor's `suggest:` line are silently dropped without manual config edits (`suggest = true`), shrinking automatic fallback options and leaving `--tier deepthink` with almost no auto-suggested subscription peers.
    - **Smallest fix:** Keep `cc.deepthink` and `ag.effort` eligible (or fall back to `suggest = false` non-alias profiles with a note when a requested tier has no other eligible candidates).
    - **Severity:** S2

11. **Permanent 2-row terminal height tax (`height - 2`) retained even on alternate screen**
    - **Evidence:** `0.24.27` (`#408`) Fixed — *"live and bounded frames now use two rows less than the terminal height, so the first line (`ATTN`) no longer scrolls off the top when the command line stays on screen (VS Code terminal, #408)."* `0.24.28` (`#410`) Fixed — *"there is no command line on the alternate screen (#410)."* `0.24.29` (`#412`) Changed — *"The height bound still applies (two rows less than the terminal)."*
    - **Why worse:** Every terminal running live monitor needlessly loses 2 rows of vertical space—causing earlier truncation—for a normal-screen workaround that does not apply on the alternate screen.
    - **Smallest fix:** Use full terminal height on the alternate screen; keep the 2-row margin only for normal-screen bounded frames.
    - **Severity:** S3

12. **Stream budget warnings silently degrade to `unknown` on missing baseline or session counter reset**
    - **Evidence:** `0.25.0` Fixed — *"Profile stats, terminal ask receipts, and stream budget warnings use per-ask token counts. Session-cumulative counters contribute deltas from the previous delivery of the same session. A missing baseline or a counter reset leaves that metric unknown."*
    - **Why worse:** If a session-cumulative baseline is missing or resets, the metric becomes `unknown`, silently failing to trigger stream budget warnings instead of warning on the reported cumulative total.
    - **Smallest fix:** Keep per-ask delta `unknown` for stats/receipts, but allow stream budget checks to fall back to the reported cumulative session counter (or emit a `budget unknown` hint).
    - **Severity:** S3

13. **`peerhub monitor defaults show` flipped default output from JSON to a table**
    - **Evidence:** `0.24.26` (`#401`) Changed — *"`monitor defaults show` prints a table (JSON only with `--json`)"* (plus `peer get`/`stream show` `None` -> `-`, `account list` empty -> `(no accounts)`, and `ask` unregistered peer exit `2` -> `1`).
    - **Why worse:** Any script or workflow calling `peerhub monitor defaults show` without `--json` expecting the pre-0.24.26 JSON output now fails to parse.
    - **Smallest fix:** Audit docs/scripts to pass `--json`, and document the breaking CLI output change in upgrade notes (or only render table on a TTY if strict script compatibility is needed).
    - **Severity:** S3

14. **Reply sanitizer weakened secret-redaction coverage while still corrupting digit-bearing identifiers**
    - **Evidence:** `0.24.26` (`#398`) Fixed — *"An assignment to `api_key`, `token`, `secret`, `password` or `passwd` is redacted only when the value looks like a credential ... Residual risk: unquoted all-letter values, quoted values with spaces and short values are not redacted, and a digit-bearing identifier such as `value2` can be."*
    - **Why worse:** Unquoted all-letter credentials, quoted passphrases with spaces, and `<6`-char secrets are no longer redacted compared to 0.24.25, while normal code like `token = [REDACTED]` is still silently corrupted to `[REDACTED]`.
    - **Smallest fix:** Match known key prefixes or higher minimum length/entropy for unquoted values (avoiding `value2` corruption while catching unquoted keys), and keep quoted values with spaces redacted for `password`/`passwd`/`secret`.
    - **Severity:** S3

---

### UNVERIFIED (requires reading code/docs/tests)

- **Tests pinning worse behaviour:** Check whether the [plain expanded fixture](../../../tests/communication/fixtures/monitor/plain_expand_sections.json) was deleted or rewritten in `0.24.29`, and identify tests pinning `--probe` opt-in, `RECENT`-before-`ALERTS`/`RUNNING`, hidden `n/a` rows, alt-screen, and `height - 2`.
- **Stale docs:** Check `docs/implementation/OBSERVATION_PROBES.md`, `docs/monitor.md`, `README.md`, and `peerhub explain monitor-view` for leftover `0.24.21` layout descriptions (`ALERTS` first, `DB:` at top, `plain --expand` byte-for-byte `0.24.20`) or pre-`0.25.0` auto-probing statements.
- **`peerhub ask` stderr gating (`0.25.0`):** Verify whether terminal receipts and the `cx.deepthink` alias notice are suppressed under `--json` or non-TTY stderr.
- **`plain --expand` height-bounding contradiction (`0.24.29` vs `0.25.0`):** `0.24.29` says sections are cut from the bottom while `0.25.0` says plain `--expand` is printed whole (not height-bounded); also check whether plain `Guide: <url>` obeys `PEERHUB_NO_LINKS=1` and dev-build omission.
- **Layout flags rejecting `--json` (`0.24.22`):** Verify whether `peerhub monitor --expand --json` worked before `0.24.22` (if so, rejecting it broke existing callers).
- **`PEERS` 8-peer cap (`0.24.24` `#394`):** Verify whether the 8-peer cap was newly added in `0.24.24` (hiding custom peers beyond 8) or pre-existing and only gained the `showing N of M peers` label.
- **`cc F-7D` limited by `C-7D` (`0.24.24` `#394`):** Verify whether `F-7D`'s own measured remainder/reset/pace is still shown in `QUOTA` and `diag quota` human output, and whether `--show-quota-notes` hides the `POOLS` note holding `F-7D`'s own value.
- **`peerhub suggest --tier deepthink` eligibility (`0.25.0`):** Verify which profiles remain eligible when both `cc.deepthink` and `cx.deepthink` have `suggest = false` (e.g., whether `deepthink` degrades to empty whenever `ag` is unavailable).
- **Stream budget check implementation (`0.25.0`):** Confirm whether missing baseline / counter reset actually suppresses the over-budget warning or falls back to cumulative tokens.
- **Bounded `--expand` `| more` hint (`0.24.21` vs `0.24.29`):** Verify whether `0.24.29` kept the `peerhub monitor --snapshot --group all --expand | more` footer hint or silently dropped it.
- **Prompt template reordering (`0.25.0`):** Fixed instructions moved before task/stream facts; cache savings and instruction-adherence impact remain unmeasured.

--- a-cx reply ---
I’ll compare `v0.24.19` with HEAD and trace changes in defaults, visibility, and CLI output back to the code, docs, and tests.
The comparison confirms that `--probe` changed the behavior of schema 1.1 watches too. I’m checking which monitor details disappeared from expanded output and which existing commands now depend on quota or telemetry reads.
The read-only checks reproduced three more regressions. Expanded output drops older and superseded asks, `profile list` fails on an unreadable store, and the packaged `cx.deepthink` alias overrides a workspace effort setting. Forty focused tests passed, including tests that pin several of these behavior changes.
Read-only audit of `v0.24.19` through HEAD `bdc2db33`. Forty focused offline tests passed. Some pin the regressions below.

1. **S2 · Monitor watch stopped refreshing quota by default.** Since 0.25.0, existing watches silently skip collection, including schema 1.1 and commands that already pass `--collect-every`. Idle dashboards age out, and `diag` reads the same stale evidence. The test pins the flip in all three output modes, while the implementation guide still describes automatic collection. **Fix** — Restore quota refresh on watch, with an explicit read-only opt-out. Keep snapshots read-only. `CHANGELOG.md:29`, `peerhub/cli/monitor.py:323`, `peerhub/cli/monitor_schema11.py:52`, `tests/communication/diag/test_monitor_cycles.py:17`, `docs/implementation/OBSERVATION_PROBES.md:35`.

2. **S2 · A quota probe can delay or abort an ordinary ask.** Since 0.25.0, default-account asks can run a provider quota probe after persisting the prompt and before dispatching it. A malformed profile config encountered by the collector can escape as `ValueError` and leave the prompt queued. A fresh error row also suppresses another attempt for 15 minutes. **Fix** — Remove the synchronous ask-side probe. Use the monitor or explicit refresh to maintain the existing observations. `peerhub/extensions/ask.py:300`, `peerhub/extensions/ask.py:316`, `peerhub/cli/support.py:56`, `peerhub/extensions/quota_capture.py:156`.

3. **S2 · `suggest` treats missing short-window evidence as `n/a`.** The baseline monitor rejected profiles with missing or stale required readings. A local fixture showed the new command choosing `cc.effort` with no C-5H reading over a peer with both windows measured, and printing `5H n/a`. **Fix** — Skip the 5H gate only for explicit provider-reported absence. Rank unknown evidence behind eligible measured candidates and report it as unknown. `v0.24.19:peerhub/cli/monitor_view.py:1505`, `peerhub/cli/suggest.py:89`.

4. **S2 · Expanded monitor lost ask history even when piped.** The baseline printed up to 20 finished asks plus sampled superseded rows. Since 0.24.29, both views stop at eight and only count superseded rows. Piping to `more` cannot recover them. The new test asserts their omission. **Fix** — Keep the eight-row layout on a bounded terminal and restore the full sampled history in unbounded `--expand` output. `v0.24.19:peerhub/cli/monitor_view.py:1166`, `peerhub/cli/monitor_view.py:1481`, `tests/communication/diag/test_monitor_expand_rich.py:459`.

5. **S2 · Finished asks can crowd running work off the screen.** With the same 26-row fixture, baseline plain `--expand` showed RUNNING. HEAD showed eight finished asks and hid RUNNING. Alert details also sit below RECENT in the clipping order. **Fix** — Reserve room for active asks and alert details before allocating rows to finished asks. `CHANGELOG.md:43`, `peerhub/cli/monitor_view.py:3137`, `peerhub/cli/monitor_view.py:3210`.

6. **S2 · `cx.deepthink` silently ignores an existing effort override.** A workspace `reasoning_effort = "medium"` resolved to `medium` on the tag and packaged `xhigh` on HEAD. The alias applies its packaged override after merging workspace settings. **Fix** — Resolve effort by config-layer precedence, including across `reasoning_effort` and `effort_override`. `docs/config-hierarchy.md:18`, `peerhub/config_data/model-defaults.toml:58`, `peerhub/extensions/adapters/profiles.py:144`.

7. **S2 · The Codex model refresh can break replay of a completed ask.** 0.24.25 changed the packaged `cx.effort`, `cx.deepthink` and `cx.astra` bindings. An identical command using the old request ID now computes a different record digest and can hit an idempotency conflict before checking for the saved terminal result. **Fix** — For a matching, already-settled request, recover the saved result using its persisted binding and keep the existing prompt and selector checks. `CHANGELOG.md:76`, `peerhub/extensions/ask.py:187`, `peerhub/extensions/ask.py:300`, `peerhub/core/store.py:483`.

8. **S2 · `profile list` lost its independence from the store.** Using a non-SQLite file as `--db`, the tag returned the profile catalog as JSON. HEAD returned exit 4 with empty stdout because listing now reads telemetry first. **Fix** — Return the catalog with stats marked unavailable when telemetry cannot be read. Keep telemetry failures visible in `profile stats`. `v0.24.19:peerhub/cli/app.py:800`, `peerhub/cli/app.py:874`, `peerhub/cli/profile_stats.py:101`.

9. **S2 · Assignment redaction covers fewer credential values.** Since 0.24.26, an unlabeled-to-the-environment, unquoted alphabetic password assignment that the tag masked passes through. A synthetic comparison reproduced this, and the test requires such values to survive. **Fix** — Restore conservative assignment masking and exempt recognizable code references, calls and indexing expressions. `CHANGELOG.md:65`, `peerhub/extensions/adapters/sanitize.py:16`, `tests/communication/adapters/test_sanitizer_assign.py:15`.

10. **S3 · Expanded quota hides why a window is unavailable.** The tag showed the explanation with the pool row. Since 0.24.22–0.24.26, expanded QUOTA folds the window into `n/a` and requires `--show-quota-notes` for the reason. The implementation guide still says `n/a` and the family stay visible either way. **Fix** — Show the folded window’s reason by default in `--expand`. Keep compact output terse. `CHANGELOG.md:69`, `CHANGELOG.md:105`, `peerhub/cli/monitor_view.py:117`, `docs/implementation/OBSERVATION_PROBES.md:55`.

11. **S3 · Finite VT watches erase their final frame on exit.** Since 0.24.28, `monitor --cycles 1` enters the alternate screen and immediately restores the prior screen on completion. The baseline left the result available for inspection. **Fix** — Use the alternate screen only for continuous watches. `CHANGELOG.md:49`, `peerhub/cli/monitor.py:249`, `peerhub/cli/view.py:98`, `peerhub/cli/view.py:118`.

12. **S3 · `monitor defaults show` stopped emitting JSON by default.** 0.24.26 switched the bare command from parseable JSON to a table. Existing redirected consumers need a flag change. The test requires the new behavior. **Fix** — Keep the table on TTYs and preserve JSON for redirected output. `v0.24.19:peerhub/cli/monitor_preferences.py:83`, `peerhub/cli/monitor_preferences.py:122`, `tests/communication/e2e/test_cli_audit_401.py:41`.

13. **S3 · Unknown-peer asks changed their exit and JSON error contract.** 0.24.26 changed exit 2 to 1 and changed the guidance code to `peer_not_found`. Existing handlers can miss the error or route it differently. **Fix** — Preserve the original exit and machine-readable classification, with the specific peer-registration hint added to the recovery guidance. `CHANGELOG.md:70`, `peerhub/cli/app.py:1407`, `tests/communication/e2e/test_cli_audit_401.py:21`.

14. **S3 · Catch-up text can push a previously valid prompt over the limit.** The 0.25.0 preamble adds 91 bytes with catch-up. A local comparison turned a valid 1,000,000-byte rendered prompt into a rejected 1,000,091-byte prompt. **Fix** — Omit the new preamble when it alone would cross the existing transport limit. `peerhub/extensions/adapters/base.py:1384`, `peerhub/extensions/adapters/base.py:1403`.

15. **S3 · The guide says batch dry-run spends quota.** The compact 0.25.0 guide attaches that warning to a `--dry-run` command. The baseline separated validation from execution, and dry-run still returns before dispatch. **Fix** — Label dry-run as validation and attach the quota warning to execution. `peerhub/cli/guide.py:25`, `peerhub/cli/app.py:1187`, `docs/batch.md:12`.

16. **S3 · Publishing gained a manual prerequisite checked after live validation.** The baseline publish workflow ran its own source matrix. HEAD requires a prior exact-SHA CI dispatch with retained reports and checks for them after live validation and package smoke. A missing or expired run can waste the live gate. **Fix** — Validate the exact-SHA run and its reports before starting the live gate. `v0.24.19:.github/workflows/publish.yml:207`, `CONTRIBUTING.md:150`, `.github/workflows/publish.yml:207`, `.github/workflows/publish.yml:223`.

**UNVERIFIED**

- Live probe cost and latency, multi-day quota aging, and installed vendor model availability.
- Cross-version replay against a real retained store. The binding and digest change was reproduced locally.
- Native terminal behavior across VT implementations, including final-frame visibility and file links.
- Hosted release artifact handoff, expiration, and failed-only reruns.
- Full suite, Pyright, and `tmp_path` cases **NOT RUN**.
