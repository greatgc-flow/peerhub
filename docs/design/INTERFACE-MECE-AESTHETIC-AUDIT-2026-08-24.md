# Interface MECE / Aesthetic / Robustness Audit (2026-08-24)

Status: kickoff. Triggered by user request: "ai cli들 및 peerhub의
cli,문서,폴더구조 등 전체 구성요소(인간 포함) 간 접점(인터페이스,
파라메터, ui 등)이 상호mece하고 알기 쉽고, 미적, 기능적으로 충분히
아름답고 견고한 지 상세 검토 및 필요시 보완해줘. 상호 이견 없을때까지
영원히 보완." (Review whether every touchpoint — interfaces, parameters,
UI — between all components, including humans, is mutually MECE, easy to
understand, and sufficiently aesthetically/functionally beautiful and
robust; fix as needed; keep refining until no disagreement remains.)

Scope: peerhub's CLI/docs/folder structure (already substantially covered
by the 7-gap reconciliation work — see `HUB-REPLACEMENT-*` docs), PLUS the
other real AI CLI entry points under `P:\_sys\cli\` (`claude_entry.py`,
`codex_entry.py`, `agy_entry.py`, `console_runner.py`), which haven't been
audited yet. This doc starts that second half.

## First concrete finding (terminal, direct read of all 3 real entry-point files)

`claude_entry.py`, `codex_entry.py`, `agy_entry.py` are structurally
near-identical (same imports, same `ConsoleSessionSpec`/`run_console_session`
pattern, same `_env()`/`_set_title()`/`main()` shape) but have real,
**unjustified inconsistencies** — not the deliberate, commented kind (ag's
`keyboard_interrupt_is_success=False` IS deliberately commented and
justified: "ag's original pre-migration convention always marked health
RED on Ctrl+C... preserve that here (independent cross-verification found
this)" — that one is fine, a real peer-specific quirk, correctly
documented).

**Unjustified inconsistencies found:**

1. **`_set_title()` is triplicated VERBATIM** across all 3 files (same 12
   lines, byte-for-byte identical logic) — a plain DRY violation. Should
   be factored into `console_runner.py` (already the shared import) or a
   new small shared module, not copy-pasted 3 times.
2. **`claude_entry.py` has NO executable-existence check** before
   launching (`_CLAUDE_CMD.exists()`), while `codex_entry.py` and
   `agy_entry.py` both check and print a clean `[ERROR] ... not found`
   message before exiting. If `claude.cmd` is missing, `claude_entry.py`
   will fail with whatever raw OS error `cmd /c <missing path>` produces,
   not the same clean message its siblings give — a real robustness/
   consistency gap, not a stylistic one.
3. **`claude_entry.py` does not pass `health_json_path`** to
   `ConsoleSessionSpec`, while `codex_entry.py` and `agy_entry.py` both
   do (pointing at `_sys/codex/health.json` and
   `_sys/antigravity/health.json` respectively). No equivalent
   `_sys/claude/health.json` path is wired in. Worth checking whether
   this is intentional (cc's health is tracked differently, e.g. via the
   terminal session itself rather than a spawned subprocess) or a real
   gap — **not yet determined, needs a `console_runner.py` read to see
   what `health_json_path` actually does when absent vs present.**
4. **`agy_entry.py`'s `_env()` loads peer-specific env vars from
   `peers.json`** (`antigravity.env_vars`), while `claude_entry.py`/
   `codex_entry.py`'s `_env()` are simpler (venv PATH only, codex adds a
   single hardcoded `CODEX_HOME`). This asymmetry might be justified (ag
   genuinely needs more env wiring) or might mean codex/claude's env
   needs are under-modeled compared to ag's more general
   config-driven approach — **not yet determined.**

## What this means for the broader audit

This is exactly the kind of finding the user is asking for across the
WHOLE system — the pattern (3 near-identical files, some real
duplication, some real unexplained divergence, some correctly-justified
divergence) is a useful template for scanning the rest of `_sys/cli/`,
peerhub's own module boundaries, and the docs/folder structure.

**This is a large, open-ended review scope** ("영원히" — forever, until
no disagreement). The terminal will continue this audit in further
rounds, delegating the broader comparative/synthesis work to `cx` (per
standing quota policy) while continuing to verify concrete claims against
real files directly, the same discipline used throughout the 7-gap
reconciliation effort.

## Next steps (not yet done)

1. Read `console_runner.py` (375 lines, the shared runner all 3 entry
   points call into) to resolve items 3-4 above and find the REAL shared
   contract these 3 thin wrappers are supposed to honor.
2. Dispatch a broader cross-CLI consistency/aesthetics review to `cx`,
   grounded in real file contents (not guessed), covering: the 3 entry
   points + `console_runner.py`, peerhub's `cli.py` (already surveyed),
   the folder structure of both `P:\_sys\` and `P:\workspace\peerhub\`,
   and doc-naming/organization conventions.
3. Extend to peerhub's own internal module boundaries (is `dispatch/`
   vs `application/` vs `governance/` a clean, MECE separation, or is
   there overlap — e.g. `application/legacy.py`'s `LEGACY_CATALOG`
   duplicating naming concerns that might belong in `core/`?).

## CONFIRMED (2026-08-24): finding #3 is a real structural gap, not just a wiring omission

Direct read of `console_runner.py`'s `_update_peer_health_json` confirms:
this function has explicit branches for `spec.peer_id == "cx"` and
`== "ag"` only — **there is no `cc` branch at all.** Even if
`claude_entry.py` passed a `health_json_path`, nothing would happen for
`cc` today. This is a genuine gap in `console_runner.py` itself (missing
a `cc`-specific update branch — presumably `_sys/claude/health.json` gets
updated through a different path, e.g. the terminal session's own
tracking or a `hub.py health-update` call elsewhere, not this
console-runner flow), not merely `claude_entry.py` forgetting to pass a
field. **Worth fixing for consistency** once this reaches implementation
— but confirming WHERE `_sys/claude/health.json` actually gets updated
today is a prerequisite (not yet checked) before concluding cc's health
tracking is actually broken vs. just handled elsewhere.

Also confirmed: this function has a real, well-documented historical
bug-fix comment for `cx` (a hard-killed wrapper could previously leave a
false successful-invocation record; fixed to only write at "finish", not
"start") — a good example of this project's own documentation discipline
already being followed here. The `cc` gap and the `claude_entry.py`
existence-check gap are the concrete, real inconsistencies; the `ag`
env-var asymmetry (finding #4) remains open — ag's peers.json-driven env
loading may simply reflect that ag genuinely needs more runtime config
than the other two, which would make it correctly NOT symmetric, not a
bug.

## `cx` independent review (2026-08-24) — confirms all 3 findings, adds a 4th

`cx` had real file access this round (confirmed reading `peers.json` and
`_sys/claude/health.json` directly) and independently verified the 3
findings as structural facts, plus found a **4th**: `peers.json` declares
`CLAUDE_CONFIG_DIR` for Claude, but `claude_entry.py` never consumes it
— the env-var asymmetry (original finding #4) is broader than just
Antigravity being special; Claude has an unused declared config path too.

### Recommendations (not yet applied — design/audit only, per this project's standing "no implementation before architecture is settled" rule; these are NOT peerhub design docs, they're live `_sys/cli/` scripts)

1. **`_set_title()`**: extract to a new `_console_helpers.py`, NOT into
   `console_runner.py` — title-setting is entry-point/UI setup;
   `console_runner.py` should stay scoped to session lifecycle/leases/
   spawning/health. All 3 wrappers import the helper.
2. **`claude_entry.py` existence check**: add the same pattern as its
   siblings (`if not _CLAUDE_CMD.exists(): print("[ERROR] claude.cmd not
   found at ..."); print("  Install: npm install -g @anthropic-ai/claude-code");
   sys.exit(1)`).
3. **`cc` health branch**: **genuine gap, confirmed** — real
   `_sys/claude/health.json` already has the same `availability.
   last_invocation_*` fields `cx`'s branch writes for Codex, so this
   isn't a schema-incompatibility issue. Recommended: wire
   `health_json_path=_SYS_DIR/"claude"/"health.json"` into
   `claude_entry.py`'s spec, then either add a `cc` branch mirroring
   `cx`'s "finish" logic, or (cx's stronger recommendation) **generalize
   `_update_peer_health_json` to a data-driven/strategy pattern instead
   of accumulating more `if peer_id == ...` branches** — this is the
   MECE-correct fix, not just a copy of the `cx` branch. **Caution before
   implementing** (cx's own flag): verify whether some OTHER process
   already writes to `_sys/claude/health.json`'s invocation fields (cc is
   sometimes the terminal itself, not just a spawned subprocess like
   ag/cx) — a second writer could conflict. Add a regression test for
   this before landing the change.
4. **Env-var asymmetry (broader than first thought)**: Antigravity's
   `peers.json`-driven loading is justified in principle (real declared
   vars: `AGY_CONFIG_HOME`, `GEMINI_DIR`) but the CURRENT implementation
   is itself sloppy — loads only Antigravity's keys, and (real bug)
   **assigns every declared key the SAME computed directory value**,
   ignoring what each key's value should actually resolve to. Codex
   hardcodes `CODEX_HOME` instead of reading the same registry. Claude's
   declared `CLAUDE_CONFIG_DIR` is unused entirely. **Recommended: a
   shared env-resolver reading `peers.json` uniformly for all 3 peers,
   preserving genuinely peer-specific variables, not a blanket
   normalization that pretends all 3 need identical env vars.**
5. **Naming**: `_CLAUDE_CMD`/`_CODEX_CMD` (`.cmd` launchers) vs
   `_AGY_EXE` (native exe) — the distinction is technically real but
   visually inconsistent. Recommended: rename all 3 to a uniform `_PATH`
   suffix (`_CLAUDE_PATH`/`_CODEX_PATH`/`_AGY_PATH`) since the variables
   ARE filesystem paths regardless of launcher type; keep the launcher-type
   distinction in comments, not the variable name.

### MECE assessment

Current split is "mostly sound": entry points own peer-specific
executable path/env/cwd/context-options/health-path/historical-behavior-
flags; `console_runner.py` owns common launch classification, hub
lifecycle, lease handling, heartbeat, process execution, final health
status, shared bookkeeping. **Real gaps**: duplicated title logic,
inconsistent executable preflight, health bookkeeping keyed by hardcoded
peer IDs (should be data-driven), env config split between code and
registry inconsistently. `console_runner.py` should NOT absorb
title-setting (wrong layer) but SHOULD make health bookkeeping
strategy-based rather than growing more `if peer_id == ...` branches.

### `cx`'s recommended next-check sequence (highest-value first)

1. Verify environment-loader ownership: inspect `launcher.py`,
   `manage.py`, `hub.py`, and any env-loader modules; compare their
   `peers.json` resolution against the 3 entry points — determine if the
   wrappers are bypassing an existing canonical resolver that should be
   reused instead of building a new one.
2. Verify health-file writer ownership: `rg -n "health\.json|
   last_invocation|active_pid|health-update" _sys` — identify whether
   multiple processes write the same peer's health file (directly
   relevant to the `cc`-branch caution in item 3 above).
3. Only then: extract the title helper + add Claude's executable/health
   parity.
4. Broader survey: all `_sys/cli/*.py` for direct subprocess/Popen/
   os.system/cmd.exe launches, duplicated env/path/title/health/error
   logic, import direction into `launcher.py`/`peer_console.py`/
   `console_runner.py`; check for import cycles before extracting a new
   shared module; build a responsibility matrix across all 12 CLI files
   (inputs, subprocess ownership, config source, health writes,
   user-facing errors); check existing test coverage for entry-point
   structure/launcher contracts/health bookkeeping/env loading/missing-
   executable behavior.

## Health-writer ownership check (terminal, direct read, 2026-08-24)

Per `cx`'s recommended next-check #2: `grep`'d for health.json writers
across `_sys/cli/` and `_sys/core/`. Found: `agy_entry.py`,
`codex_entry.py`, `console_runner.py` (the 3 already known), PLUS
`_sys/core/hub.py`, `hub_error.py`, `hub_health.py`, `snapshot.py`.
`hub_health.py`'s `PeerHealthState`/health-registry class is **peer-
agnostic** (`_peer_dirs()` iterates all peers uniformly, no cc-specific
branch, no ag/cx-specific branch either) — this is very likely the SAME
mechanism that already keeps `_sys/claude/health.json` populated today
(via `hub.py health-update`, called generically for any peer including
cc), independent of `console_runner.py`'s narrower per-invocation
`last_invocation_*`/`active_pid` bookkeeping.

**This meaningfully de-risks finding 3's proposed fix**: adding a `cc`
branch to `console_runner.py`'s `_update_peer_health_json` would likely
be recording the SAME KIND of data (`last_invocation_duration_ms`/
`last_invocation_exit_code`) that `hub_health.py`'s generic path doesn't
appear to touch (that class looks read/summary-oriented — `context_status`,
`gate_open`, `consecutive_failures`, `availability` as a read property —
not obviously a per-invocation writer for those specific fields). Still
**not fully confirmed** — `hub_health.py`'s actual write paths (not just
its read-oriented class methods shown by this grep) weren't traced
line-by-line. Recommend this specific confirmation before implementing
finding 3's fix, not a blocker to documenting the recommendation.
