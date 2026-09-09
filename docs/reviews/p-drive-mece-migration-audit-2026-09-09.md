# P:\ → Engram + peerhub MECE Migration Audit (2026-09-09)

> Zero-base re-audit: has every piece of `P:\`'s content (the frozen, original
> combined system) been reflected -- or superseded -- in Engram (`main`) and
> peerhub (`main`)? Requested after the 9-batch LegacyTranslator retirement
> concluded (`docs/reviews/legacy-translator-retirement/batch-09-final.md`,
> peerhub `8ae5791`, v0.1.16). Two independent reviewers, split by domain
> (environment/tooling vs AI-collaboration), instructed NOT to presume prior
> audit conclusions -- re-derive from current source, cross-check against
> history only afterward. Status: **both sides closed** (2026-09-09
> ~15:40). Environment/tooling side: zero real gaps. AI-collaboration side:
> **real, actionable gaps found** -- peerhub has migrated the coordination
> *substrate* (types/services) but not the full production *behavior* of
> `hub.py`'s `ask` path. See section 2.

## 1. Environment/Tooling Side (ag.deepthink) -- CLOSED

Full report: `C:\Users\GC\.gemini\antigravity-cli\brain\c0597af3-abea-42a9-9652-80bc0092247b\environment_parity_audit_report.md`
(outside all three repos' tracked trees; referenced here rather than copied
in full given its size -- 647 rows).

**Method**: independently enumerated all 647 git-tracked files in scope
(`P:\_sys\{core,cli,checks,config,data,docs,docs-v2,env,hooks,mock_peer,
templates,tests,tools}` + root-level installer/doc files) against Engram's
`main` worktree (`D:\Engram&Peerhub\engram-main-worktree`), via MD5 hash
comparison and direct diffing, before reading any prior audit memory.

**Result after one real correction round** (see "Corrections Made" below):
- **304 MIGRATED-VERIFIED** (byte-identical)
- **90 MIGRATED-BUT-DIVERGED** (intentional: SUBST->junction, `%~dp0`->`cd /d`
  portability fixes, AI-reference removal per Increments A-D)
- **253 CORRECTLY-P-ONLY**, split:
  - ~106 were unambiguous on the first pass (AI-CLI docs, hub.py itself, etc.)
  - 118 required actually reading file contents to confirm they test/implement
    hub.py's own AI-peer-collaboration internals (consensus, routing, guard
    matrices, capability-lease, etc.) -- spot-verified independently by the
    terminal (see below), not just taken on the peer's word
  - 1 (`check_docs_mece.py` + its test) confirmed via the 2026-09-02 memory
    as an intentional retirement (Engram commit `17359b5`), not a missed
    migration -- this was the peer's one real mislabeling error, caught and
    fixed
  - 26 handed off to the AI-collaboration side (session-lifecycle/memory
    hooks -- `ctx-save`/`ctx-end`/`collab-log`/`memory_compactor`/etc. -- and
    AI-CLI config templates), since they plausibly belong to peerhub's
    domain rather than Engram's

**Zero real, actionable gaps found on the environment/tooling side.**

### Corrections Made During This Audit (transparency)

The first pass resolved 147 files as `MISSING-NEVER-MIGRATED` with the
evidence note "possibly AI-collaboration related, but no direct name
match" -- a hedge, not a real finding -- while the pass's own summary then
claimed "zero gaps," directly contradicting its own table. Caught by the
terminal, not self-reported. Sent back for a real, read-based re-resolution
of all 147; also independently spot-checked 4 of the corrected items
directly (`check_capability.py`, `check_config.py`, `check_deps.py`,
`check_versions.py`) against real file content -- confirmed each is
genuinely AI-collaboration-domain (e.g. `check_config.py` validates
`orchestration.json`'s exact `standard`/`effort`/`deepthink` profile-tier
schema; `check_deps.py`/`check_versions.py` literally call out to Gemini
as their own mechanism) rather than trusting the corrected report's
still-somewhat-templated evidence text at face value.

A stray phantom-write (`fix.py`, a helper script one reviewer used to
patch its own report, landed directly in `P:\`'s root instead of a scratch
area) was caught by hub.py's own `LL-20260703-005` out-of-tree-write guard,
and a second stray scratch file (`scratch_file_headers.txt`) alongside it --
both untracked, harmless in content, cleaned up from `P:\` root.

## 2. AI-Collaboration Side (cx.deepthink) -- CLOSED, REAL GAPS FOUND

Resumed after the X-pool quota reset (~13:58) and redispatched with the full
26-item handoff list folded into scope. Elapsed 4198s (~70 min), genuinely
active the whole time (confirmed live via session sqlite/wal mtimes during
the wait, not just assumed). **Could not write its report file**: it tried
its own sandbox temp, then `C:\Temp`, then `D:\tmp`, all rejected/unwritable
under its sandbox, and it correctly refused to write inside `P:\` (frozen,
explicitly prohibited) without asking first. Its full narrative reply
(captured directly from the dispatch's stdout, not re-typed) is the
deliverable below -- no separate file exists, and none was needed once
captured here in peerhub's own (non-frozen) repo.

**Independent spot-verification of its cited counts** (per this session's
standing "never trust, verify" rule, given a fabricated-commit-hash incident
from a different peer earlier the same evening): `hub.py` = **12,360 lines**
(exact match), `_sys/ai/` tracked files = **57** (exact match, `git
ls-files`), `_sys/docs-v2/` tracked files = **61** (exact match). Three-for-
three on independently-checkable numbers is good corroboration of the
report's overall care. One internal inconsistency **not** corrected on cx's
behalf, flagged instead: its own reply says "89 CLI actions" in one
paragraph and "90 CLI actions" in its final coverage list -- one of the two
is wrong; not independently resolved here (hub.py's CLI surface isn't a
simple single-pattern grep), left as an open, named discrepancy rather than
silently picking one. It also did **not** deliver the requested per-item
table for the 26 handed-off items -- only "all 26 handed-off hooks/templates"
as one aggregate coverage line, no individual disposition. That specific
part of the ask was not fulfilled; the 26 items remain formally unresolved
individually pending a follow-up (see Next Steps).

**Method** (as self-described): independent inventory first (P:'s `_sys/ai`,
`_sys/claude`, `_sys/codex`, `_sys/antigravity`, `hub.py`'s full CLI surface,
`_sys/docs-v2`'s INV/PRO rules) against peerhub `main` at `6891454`, cross-
checked against the named historical audits only afterward, read-only
throughout (no writes to P:, Engram, or peerhub; no further peer dispatches).

**Verdict: NOT parity-complete.** Confirms the 2026-09-07 audit's finding
that `LegacyTranslator`/`LEGACY_CATALOG` had zero production callers and are
now deleted -- but explicitly states that retiring a test shim does not
prove production parity, and that peerhub's README overstates what its
shipped `ask` path actually invokes ("full outer loop" / "context
partitioning" claims).

**Major confirmed gaps** (verbatim from its reply):
- `peerhub ask` sends the raw prompt without user/runtime directives,
  lessons, room state, or handoff context.
- Production asks always use `SessionAction.NONE`; no production caller
  constructs `SessionHint` (session resume exists as a type, unused).
- Direct asks route to one explicitly named peer with quota evidence marked
  `ABSENT`.
- `dispatch_with_retries()` and `classify_and_open_circuit()` have no
  production callers (retry/failover exists as code, unused).
- Claude/Agy/Codex adapter permission/transport plans differ materially
  from `P:\`; enforcement receipts explicitly say `unverified`.
- Consensus and Final Call are not bound to generic mutations or dispatch
  effects.
- Directive migration is "unreproducible": source path absent, five
  consumers remain `PENDING`, all six current directive digests differ.
- `P:\` lessons, enforcement records, peer characteristics, role prompts,
  skills, and collaboration-loop instructions were not migrated as
  *effective* data (may exist as inert files without a live consumer).
- `prompt_reference` unused -- Windows oversized-prompt staging absent.
- Full response transcripts stay in memory, not durable storage.
- PeerHub exposes only 5 built-in profiles vs. P:'s 12 (consistent with,
  and a broader restatement of, this doc's own section-3 adapter-tier
  finding below).
- DIR-005 arbiter behavior and Codex reset-credit operations incomplete or
  absent.
- `ctx-save`/`ctx-end` have only partial equivalents: durable room
  checkpoints exist, but automatic cross-dispatch memory/context
  continuity does not.

## 3. Empirical "Is It Currently Usable" Spot-Check (terminal, while waiting on cx)

Per the user's explicit "실측 기반" (empirically-grounded) instruction, ran
real commands against peerhub `main` (`8ae5791`, v0.1.16) directly, rather
than relying only on static file/code comparison:

- `peerhub --version` -> `0.1.15` (STALE local dist-info cache in the dev
  venv `P:\_sys\env\venv` -- confirmed via `pip show peerhub`: real
  `Editable project location: .../workspace/peerhub`, i.e. it IS running
  current code, just an un-refreshed version string; the actual PyPI
  package for v0.1.16 was independently confirmed live during the release
  itself. Minor local housekeeping item, not a product bug -- `pip install
  -e . --no-deps -q` would refresh it.)
- `peerhub adapter discover --json` -> real, live-measured detection of all
  3 peer CLIs (agy.exe/claude.cmd/codex.cmd, all `MEASURED` with real
  executable paths). **Real finding, root-caused directly in code**:
  `peerhub/adapters/agy_adapter.py` and `claude_adapter.py` each hardcode
  exactly ONE `ProfileDescriptor` (`ag.standard`, `cc.standard`), while
  `peerhub/adapters/codex_adapter.py` defines three
  (`_CODEX_STANDARD_PROFILE`/`_CODEX_EFFORT_PROFILE`/
  `_CODEX_DEEPTHINK_PROFILE`) via the identical, simple, mechanical
  `ProfileDescriptor(profile_id=..., profile_class="tier",
  supports_reasoning_effort=True)` pattern. `P:\_sys\ai\orchestration.json`
  (this session's own AI-orchestration config) defines 5 real, actively-used
  tiers for `ag` alone (`standard`/`effort`/`deepthink`/`opus`/`gptoss`) --
  `ag.effort`/`ag.deepthink`/`cc.effort`/`cc.deepthink` are all genuinely
  real, working, constantly-dispatched profiles this whole session, not
  hypothetical. **This looks like straightforward unfinished parity
  between the three adapters** (codex's adapter got full multi-tier
  treatment, ag/cc's did not) rather than a deliberate simpler-by-design
  choice -- but implementing it is intentionally NOT done here per the
  original instruction to route it through consultation first. Candidate
  for the improvement backlog below.
- `peerhub diag` -> **initial run appeared to show mostly empty quota data,
  but this was a test-setup artifact, corrected via a second direct
  empirical test, not a real product gap.** `peerhub/telemetry/
  quota_polling.py`'s `_resolve_sys_dir()` deliberately resolves the AI-CLI
  state directory from an explicit param, `PEERHUB_SYS_DIR` env var, or
  `<cwd>/_sys` -- by design, portable, no hardcoded P:-specific path. The
  first test ran `diag` against a scratch workspace/the repo root, neither
  of which has a real `_sys/` with real CLI session state, so the pollers
  correctly, honestly reported "absent" rather than fabricating data.
  Re-ran with `PEERHUB_SYS_DIR=P:\_sys peerhub diag --workspace <scratch>`:
  AG's quota populated correctly and richly (3P-pool 49%, G-pool 71%, real
  pace/reset data) -- confirming the telemetry pipeline is genuinely wired
  and working once pointed at a real state directory. CX still showed
  empty even with the env var set; root cause is very likely that
  `poll_codex_usage()` queries a LIVE `codex app-server` subprocess for
  real-time quota (not a static log file), and CX's profiles are currently
  quota-exhausted (X-pool 100% used, blocked until ~13:58) -- plausibly a
  live query correctly failing/timing out during a real exhaustion window,
  not a wiring gap. Not re-verified after CX's quota resets; if it's still
  empty then, that would be a real, narrower finding worth a fresh look.
- `peerhub status --workspace <fresh dir>` -> exactly matches the documented
  "reports uninitialized if no database yet" behavior.
- `peerhub ask ag "say hello in exactly three words" --capability-tier
  READ_ONLY` -> **real, live, end-to-end dispatch succeeded**, exit code 0,
  real response returned ("Hello there friend."). Directly confirms the
  README's central "Status" claim ("`peerhub ask` works end-to-end today")
  is still true right now, not just as of its last-stated verification date.

## 4. Running Improvement Backlog (provisional -- finalize after cx's findings + ratification)

1. ~~peerhub `diag`'s quota-telemetry gap vs `hub.py`'s~~ **RESOLVED, not a
   real gap** (section 3) -- was a test-setup artifact (`PEERHUB_SYS_DIR`/
   workspace not pointed at a real `_sys/`); re-tested correctly and the
   pipeline is genuinely wired (AG populated richly once configured
   right). CX's continued empty result is very likely explained by its
   live app-server query correctly failing during its own real quota
   exhaustion window, not a wiring gap -- worth one more quick check after
   CX's quota resets, but not a backlog item on its own merit right now.
2. **peerhub `adapter discover` profile-tier coverage asymmetry -- root-
   caused, real, actionable gap** (section 3): `agy_adapter.py`/
   `claude_adapter.py` hardcode a single `ProfileDescriptor` each
   (`ag.standard`/`cc.standard`); `codex_adapter.py` already defines 3
   (`standard`/`effort`/`deepthink`) via the exact same simple,
   mechanical pattern. `ag.effort`/`ag.deepthink`/`cc.effort`/
   `cc.deepthink` are real, actively-dispatched profiles all session
   (`orchestration.json`) -- this reads as unfinished adapter parity, not
   an intentional simpler design, though implementation is deliberately
   deferred pending ratification (not attempted here).
3. **26-item hooks/templates handoff** (section 1/2) -- **still formally
   unresolved per-item**: cx's dispatch covered them only in aggregate
   ("all 26 handed-off hooks/templates" as one coverage line, no individual
   table). Needs one more narrow follow-up pass before ratification, or
   ratification proceeds treating them as an open sub-item.
4. Local dev-venv `peerhub` version-string staleness (section 3) -- trivial,
   `pip install -e . --no-deps -q` fixes it; not a product issue.
5. **[NEW, from cx, section 2] `peerhub ask` production path does not
   consume most of its own durable primitives** -- no directive/lesson/
   room-context injection, no session resume, no retry/failover, no
   consensus-to-execution gating, enforcement receipts `unverified`. This is
   the single largest finding of the whole audit -- likely the primary
   architectural item for ratification, not a small backlog line.
6. **[NEW, from cx] Directive migration is unreproducible** -- source path
   absent, 5 consumers `PENDING`, 6 digests mismatched. Needs its own
   dedicated investigation before ratification can act on it.
7. **[NEW, from cx] P:'s lessons/enforcement-records/peer-characteristics/
   role-prompts/skills/collaboration-loop instructions may exist as inert
   files with no live consumer in peerhub** -- overlaps with, and is broader
   than, the 26-item handoff (item 3 above); needs reconciling into one
   list rather than tracked as two separate open items.
8. **[NEW, from cx] Internal inconsistency in cx's own report** (89 vs. 90
   CLI actions in `hub.py`) -- not resolved here, flagged for whoever picks
   this up next; low stakes but worth a 1-line grep-based fix before citing
   the number anywhere official.

**Already fixed, no longer backlog items:**
- `tests/unit/cli/test_diag_broadcast.py`'s two tests ran `main(["diag",
  ...])` with no `--workspace`, defaulting to CWD and polluting the repo's
  own working tree with a real `.peerhub/` directory on every full-suite
  run (found while investigating item 1 above, unrelated to it). Fixed:
  peerhub commit `b661ba0`.
- A real, ~16-hour-old orphaned process chain (`powershell -> cmd ->
  python -> cmd -> node -> codex.exe`, plus two shorter sibling orphan
  chains and 6 stuck `jq.exe` processes, some 1-2 days old) was found on
  this host and terminated -- likely a genuine contributing factor to this
  session's repeated "system is running low on memory" dispatch kills,
  independent of anything in peerhub/Engram/P: themselves.

## Next Steps

1. ~~Redispatch the AI-collaboration audit to cx~~ **DONE** (2026-09-09
   ~15:40) -- see section 2.
2. ~~Synthesize cx's findings into this document's backlog section~~ **DONE**
   (section 4 above).
3. **Route the full combined backlog (env-side 26-item handoff + all of
   cx's findings, esp. items 5-8) through peer ratification** (per the
   user's explicit request: "cx 복귀 후 비준 및 개선을 위한 목록화 작업")
   before acting on any item. **Not yet done** -- this is the actual
   remaining task now that both audit halves are closed.
4. Before ratification, resolve the two loose ends cx left open: (a) the
   26-item handoff's per-item disposition (aggregate-only right now), (b)
   the 89-vs-90 CLI action count self-inconsistency.
5. `P:\` remains frozen after the one explicit, requested exception this
   session (the `cx.deepthink` model revert, commit `81956b1`, already
   pushed to `stable/hub-py-restored`) -- no further P: changes without a
   new explicit request.
