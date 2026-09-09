# P:\ → Engram + peerhub MECE Migration Audit (2026-09-09)

> Zero-base re-audit: has every piece of `P:\`'s content (the frozen, original
> combined system) been reflected -- or superseded -- in Engram (`main`) and
> peerhub (`main`)? Requested after the 9-batch LegacyTranslator retirement
> concluded (`docs/reviews/legacy-translator-retirement/batch-09-final.md`,
> peerhub `8ae5791`, v0.1.16). Two independent reviewers, split by domain
> (environment/tooling vs AI-collaboration), instructed NOT to presume prior
> audit conclusions -- re-derive from current source, cross-check against
> history only afterward. Status: environment side closed; AI-collaboration
> side blocked on cx quota (X-pool 100% used, resets ~2026-09-09T13:58),
> resuming after reset per explicit user instruction ("cx 복귀할 때까지 exh
> 살피면서 페이스 조절하면서 이어서 진행해줘").

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

## 2. AI-Collaboration Side (cx) -- IN PROGRESS, BLOCKED ON QUOTA

Two dispatch attempts to `cx.deepthink` and one fallback to `cx.effort` all
hit the same shared X-pool quota exhaustion (confirmed via `diag`: X-pool
100% used, `RAW 99.99x`, resets `2026-09-09T13:58` -- ~4h9m from the block
being discovered). Per explicit user decision (asked via AskUserQuestion),
waiting for the reset rather than substituting a different reviewer or
having the terminal do this half solo -- the independent-second-voice
cross-check value was the point of splitting this audit in the first place.

**Handoff list for the AI-collaboration reviewer** (26 items from the
environment side, to be resolved when cx resumes): `_sys/hooks/ai-check.bat`,
`ai_check.py`, `archive-data.bat`, `collab-log.bat`, `collab_log.py`,
`ctx-end.bat`, `ctx-save.bat`, `ctx_end.py`, `ctx_save.py`, `log-write.bat`,
`memory_compactor.py`, `raw-log.bat`, `raw_log.py`, `session-end.bat`;
`_sys/templates/CLAUDE_global.md`, `CLAUDE_project.md`,
`workspace-base/CLAUDE.md`, `workspace-base/GEMINI.md`,
`workspace-base/config/settings.json`,
`workspace-base/specific/config/workspace-config.json`,
`workspace-base/specific/skills/.gitkeep`, `workspace-base/src/.gitkeep`,
`workspace/CONTEXT.md`, `workspace/knowledge/bindings.template.json`,
`workspace/knowledge/workspace-profile.template.json`,
`workspace/src/.gitkeep`.

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
  executable paths). **Real finding**: profile counts are uneven across
  peers -- `cx` reports 3 profiles (`standard`/`effort`/`deepthink`), `ag`
  and `cc` each report only 1 (`standard`). `P:\_sys\ai\orchestration.json`
  (this session's own AI-orchestration config, actively maintained all
  session) defines 5 tiers for `ag` alone (`standard`/`effort`/`deepthink`/
  `opus`/`gptoss`). **Open question, not yet resolved**: is peerhub's
  adapter-profile registry supposed to mirror hub.py's/`orchestration.json`'s
  full tier set, or is this an intentional simpler default-profile design
  peerhub has for its own adapter layer? Needs a design-intent answer, not
  an assumption -- candidate for the improvement backlog below pending that
  answer.
- `peerhub diag` -> runs, correct structural output, but almost all
  quota/pace numbers render as `--`/`Unknown` (`Quota Status: Unknown (No
  quota data available)`), a stark contrast with `hub.py`'s own `diag.py`
  (run moments earlier, same machine, same peers) which shows rich,
  populated EXH/pace/pool data for the exact same peers. **Real finding**:
  peerhub's own quota-telemetry backend is either not wired to a real data
  source yet, or reads a different quota-tracking format than the one
  `hub.py`'s `diag.py` populates (which this whole session's dispatches have
  been feeding all night) -- candidate for the improvement backlog, pending
  investigation of which.
- `peerhub status --workspace <fresh dir>` -> exactly matches the documented
  "reports uninitialized if no database yet" behavior.
- `peerhub ask ag "say hello in exactly three words" --capability-tier
  READ_ONLY` -> **real, live, end-to-end dispatch succeeded**, exit code 0,
  real response returned ("Hello there friend."). Directly confirms the
  README's central "Status" claim ("`peerhub ask` works end-to-end today")
  is still true right now, not just as of its last-stated verification date.

## 4. Running Improvement Backlog (provisional -- finalize after cx's findings + ratification)

1. **peerhub `diag`'s quota-telemetry gap vs `hub.py`'s** (found empirically,
   section 3). Needs investigation: is this unwired, or reading a different
   (currently-unpopulated) data source? Real, user-visible completeness gap
   if peerhub is meant to be usable as hub.py's actual quota-dashboard
   replacement.
2. **peerhub `adapter discover` profile-tier coverage asymmetry** (found
   empirically, section 3) -- `ag`/`cc` show only their `standard` profile,
   `cx` shows all 3 tiers. Needs a design-intent decision, not an assumption.
3. **26-item hooks/templates handoff** (section 1/2) -- pending cx's
   resolution: real gap, or correctly out of peerhub's product scope (e.g.
   `ctx-save`/`ctx-end` may be a terminal-session-workflow convention for
   THIS multi-peer development process specifically, not a peerhub feature).
4. Local dev-venv `peerhub` version-string staleness (section 3) -- trivial,
   `pip install -e . --no-deps -q` fixes it; not a product issue.
5. *(Pending cx's AI-collaboration-side findings once quota resets.)*

## Next Steps

1. Redispatch the AI-collaboration audit to cx once its X-pool quota window
   resets (~2026-09-09T13:58), including the 26-item handoff list above.
2. Synthesize cx's findings into this document's backlog section.
3. Route the resulting backlog through peer ratification (per the user's
   explicit request: "cx 복귀 후 비준 및 개선을 위한 목록화 작업") before
   acting on any item.
4. `P:\` remains frozen after the one explicit, requested exception this
   session (the `cx.deepthink` model revert, commit `81956b1`, already
   pushed to `stable/hub-py-restored`) -- no further P: changes without a
   new explicit request.
