# PeerHub Development Status

> **Status Authority:** This document (`docs/STATUS.md`) is the single authoritative source of truth for current release status, published version history, and the remaining development queue. Navigation hubs (`docs/index.md`, `docs/design/README.md`) and maintainer guidance (`docs/HANDOFF.md`) point here.

## Current source: v0.22.3 (release candidate; latest published: v0.22.2)

v0.22.3 fixes #256 (batch worker setup failure keeps one row per task) and doc drift (#260 P2-1).

v0.22.2 (supersedes v0.22.1, which never reached PyPI) fixes Python 3.15 installs (`requires-python <3.16`) and the first-use `workspace.generation` race (#249). Published 2026-10-21; previous published release: v0.22.0. Open follow-ups: #248 (deferred scope index), #256 (batch worker setup failure), #258 (credit/quota specification), #259 (pricing research), #260 (review/backlog mapping); #249 is fixed.

v0.22.0 adds flat parallel `batch run` and immutable account-bound peers for cc/cx. See [batch runs](batch.md) and [accounts](accounts.md). Published 2026-10-21; previous published release: v0.21.6. Open follow-ups: #248 (deferred scope index) and #249 (`workspace.generation` first-use race).

v0.21.6: seven audit fixes (#205 #210 #229 #230 #232 #239 #240) and the Known deferred defects index (#241). Previous: v0.21.5.

v0.21.5: ag empty responses name denied tool actions (`denied_actions`, #195); monitor `<1%` and relative reset credits (#189). Previous: v0.21.4.

v0.21.4: ag model registry completeness and `ag.argon` profile, `profile list` known models, multi-line limit/reset detection, cc 5-hour phrasing, live override applied consistently, monitor `past forecast` wording (#194). Previous: v0.21.3.

v0.21.3 (live gate ran the ag canary on the Argon model because the default ag pool was exhausted; packaged `ag.standard` model not exercised, #191): provider quota/usage limits surface as `provider_limit` on uncertain asks, cc limit phrasings are classified (#186), and monitor display fixes (#189). Earlier: v0.21.2 incorporates the MECE fixes and follow-up (#177, #178): restore fencing on both lineage markers with first-generation adoption preserved, shared receipt validation, monitor workspace resolution, unified profile loading, and bounded benchmark subprocesses with Windows shim support. It also updates upgrade and maintainer documentation (#173, #175, #181). Candidate validation and publication remain pending.

### Release attempts

| Attempt | Target commit / ref | PR | Status | Notes |
|---|---|---|---|---|
| **v0.22.2** | `7fa5fd06` | PR #251, #252 | Published | Published 2026-10-21; tag `v0.22.2`; G3 passed bound to 7fa5fd06 (ag canary on gemini-4-argon-medium, #191); PyPI wheel + sdist; Python 3.15 support, #249 fix |
| **v0.22.1** | `d45d4d06` | PR #251 | NEVER PUBLISHED TO PYPI | `publish.yml` `release-evidence` refused: gate still expected 8 matrix cells, 3.15 made it 10; superseded by v0.22.2; the GitHub release and tag were deleted 2026-10-22 |
| **v0.22.0** | `697e1574` | PR #247 | Published | Published 2026-10-21; tag `v0.22.0`; G3 passed bound to 697e1574 (ag canary on gemini-4-argon-medium, #191); PyPI wheel + sdist |
| **v0.17.0** draft | `04eab676` (`release/0.17.0-monitor-groups`) | PR #106 | NEVER PUBLISHED | Content shipped in v0.18.0 |
| **v0.18.0** | `dc510f4f` | PR #107 | Published | Published 2026-10-16 |
| **v0.18.1** draft + tag | `75677b07` | PR #109 | NEVER PUBLISHED | Content shipped in v0.18.2 |
| **v0.18.2** | `8274e9f5` | PR #111 | Published | Published 2026-10-16T16:17:37Z |
| **v0.19.0** | `32da9284` | PR #144 | Published | Published 2026-10-17T14:39:58Z; workflow run 39041714673; live gate G3 passed bound to `32da9284`; PyPI 0.19.0 |
| **v0.20.0** | `732996c6` | PR #163 | Published | Tag `v0.20.0` |
| **v0.21.0** | `920931db` | PR #170 | Published | Tag `v0.21.0` |
| **v0.21.1** | `9aa06d30` | PR #172 | Published | Tag `v0.21.1` |
| **v0.21.2** | `c34959c3` | PR #182 | Published | Tag `v0.21.2` |
| **v0.21.4** | `01f8f894` | PR #196 | Published | Tag `v0.21.4`; G3 passed bound to 01f8f894 (ag canary on gemini-4-argon-medium, #191) |
| **v0.21.6** | `89055fe4` | PR #242 | Published | Tag `v0.21.6`; G3 passed bound to 89055fe4 (ag canary on gemini-4-argon-medium, #191) |
| **v0.21.5** | `1c99c134` | PR #198 | Published | Tag `v0.21.5`; G3 passed bound to 1c99c134 (ag canary on gemini-4-argon-medium, #191) |
| **v0.21.3** | `9692777e` | PR #190, #191 | Published | Tag `v0.21.3`; G3 passed bound to 9692777e (ag canary on gemini-4-argon-low, #191) |

Both drafts and the v0.18.1 tag were deleted on 2026-10-17 after v0.18.2 (superseded). CHANGELOG entries 0.17.0/0.18.1 describe changes that first shipped in 0.18.0/0.18.2.

## Latest published release: v0.22.2 (tag v0.22.2, target 7fa5fd06c6acf82dd5b042c132650f4b02a217e0; PR #251, #252)

Published 2026-10-21. `publish.yml` succeeded through verify, build, live-validation, release-matrix (Python 3.11-3.15 x Ubuntu/Windows), release-evidence and publish. PyPI has the 0.22.2 wheel and sdist (`requires-python >=3.11,<3.16`); a clean-virtualenv install from PyPI on CPython 3.15.0 passed `peerhub --version` (0.22.2) and `batch run --help`.

G3 passed bound to `7fa5fd06`. ag G pools were exhausted, so the ag canary used `gemini-4-argon-medium` via `PEERHUB_LIVE_AG_MODEL`; the packaged `ag.standard` model was **not exercised** (#191). The owner allowed ag for this gate only and banned it again afterwards. Lesson: when the supported Python matrix changes, the `-ne N` cell count in `publish.yml` must change with it (v0.22.1 failed on this).

## Previous published release: v0.22.0 (tag v0.22.0, target 697e15743b50d9f3b02fec4ed2c3ee896c21dae9; PR #247)

Published 2026-10-21. `publish.yml` succeeded through verify, build, live-validation, release-matrix, release-evidence and publish. PyPI has the 0.22.0 wheel and sdist; a clean-virtualenv install smoke passed `peerhub --version` (0.22.0), `batch run --help` and `account --help`.

G3 passed bound to `697e1574`. ag G pools were exhausted, so the ag canary used `gemini-4-argon-medium` via `PEERHUB_LIVE_AG_MODEL`; the packaged `ag.standard` model was **not exercised** (#191). cc and cx used packaged profiles. See [live evidence](implementation/live_evidence/2026-10-21.json). The owner banned ag again after G3.

## Prior published release: v0.21.6 (tag v0.21.6, target 89055fe4; PR #242; G3 passed bound to 89055fe4; ag canary on gemini-4-argon-medium, packaged ag.standard model not exercised)

## Prior published release: v0.21.2 (tag v0.21.2, target c34959c3; PR #182)

## Prior published release: v0.21.1 (tag v0.21.1, target 9aa06d30; PR #172)

v0.21.1 updates uncertain ask guidance and the guide to direct default-timeout asks to a new stream with explicit `--timeout-seconds`, preserving retry cautions (#169, PR #171). See the [published release](https://github.com/greatgc-flow/peerhub/releases/tag/v0.21.1).

## Prior published release: v0.19.0 (published 2026-10-17T14:39:58Z, tag v0.19.0, target 32da9284; workflow run 39041714673; live gate G3 passed bound to 32da9284; PyPI 0.19.0 confirmed; PR #144)

v0.19.0 removes implicit store discovery under `<cwd>/peerhub`, reprioritizes bare monitor output to keep quota always visible, adds `peerhub monitor --expand`, introduces the offline `peerhub guide` subcommand, multi-pool quota accounting and drift tracking, fixes Codex session resume write access, and adds bounded delta reads in Session Bridge.

## Prior published release: v0.18.2 (published 2026-10-16T16:17:37Z, tag v0.18.2, target 8274e9f5; live gate G3 6+3+3 passed bound to 8274e9f5; PR #111)

Benchmark v0.16.0 vs v0.18.2 (live, MECE; cc/cx/ag unanimous): `docs/benchmarks/v0.16.0-vs-v0.18.2-2026-10-17.md`.

v0.18.2 adds the monitor peers group (linking quota, profile/model, session
reuse, and context), model-fact provenance reporting, and SQLite store schema
version gate 4 (central connection factory). It also incorporates the responsive
terminal dashboard fix from PR #108.

## Prior published release: v0.18.0

The tagged v0.17.0 candidate is superseded before publication. v0.18.0 keeps the
grouped monitor contract, renders it as the default human dashboard, and moves
machine output behind `--json` with explicit schema selection. The captured
schema 1.1 contract remains available; its hidden `--legacy` alias is deprecated
through 0.19.x and will be removed in 0.20.0. See the [human dashboard design](design/monitor-human-dashboard-redesign-2026-10-16.md).

**Released on 2026-10-16.** The [v0.18.0 publication workflow](https://github.com/greatgc-flow/peerhub/actions/runs/38848746748)
passed verification, live validation, all Ubuntu/Windows Python 3.11–3.14
release-matrix jobs, release evidence, and PyPI publication. The published
release includes `g3.xml`, `g3-e2e.xml`, and `g3-slow.xml` evidence assets.

**Released on 2026-10-15.** [v0.16.0](https://github.com/greatgc-flow/peerhub/releases/tag/v0.16.0)
is tagged at `5951d0f2a32aa85151f6733f286eea03281fec55`. Session reuse and
CLI/monitoring work merged through PR #100, and the live-gate oracle correction
merged through PR #101. The release includes stuck-streams PRs 1-3
(#95/#97/#98), session reuse PRs 1-3, model-profile changes, and CLI/monitoring
inspection. Final-commit GitHub CI passed all eight Ubuntu/Windows and Python
3.11–3.14 combinations. The full local G3 gate, all jobs in the
[publication workflow](https://github.com/greatgc-flow/peerhub/actions/runs/38644198537),
and a clean-environment installation of the PyPI wheel passed. PyPI lists
both the wheel and sdist for 0.16.0. [CHANGELOG](../CHANGELOG.md) owns the
user-facing release notes.

Eligible pre-work refusals settle as `NOT_EXECUTED` while preserving `STARTED`
certainty. Timeouts and failures with possible effects remain guarded; neither
automatic timeout settlement nor `stream resolve` ships in v0.16.0.
Session PR1 (`eec2cba4`), PR2 (`7cdc6b76`), PR3 (`12129517`), and health check
fixes (`cfb28aba`) are included in `main`. Retained selectors
(`--session`, `--fresh`), call receipts, and durable snapshots are committed
and functional. The CLI/monitoring work implements bounded list/session inspection,
read-only Core access, shared snapshots, and observation/activity projections.
Attributable runtime telemetry, alerts, retention, replay refinements, and
recovery remain proposed work pending their design gates. See the
[Renewal](design/peerhub-pre-tdd-renewal-2026-10-14.md), [Debate Ledger](design/peerhub-deepthink-debate-2026-10-14.md),
and [CLI/Monitoring Addendum](design/peerhub-cli-monitoring-inspection-2026-10-14.md).
The bounded four-turn live
session matrix passed for cc, cx and ag. The first cx selected-session recall
used an ambiguous prompt after cross-session catch-up; the clarified prompt
passed without changing product code. This canary is not a substitute for the
candidate-matched full G3 release gate, which also passed at the tagged commit.

0.15.2 is the previous published release; 0.15.1 was never published. Historical
live evidence applies only to its recorded revision. The v0.16.0 release
completed the gates in [CONTRIBUTING](../CONTRIBUTING.md#releasing).

**Released on 2026-10-16.** [v0.16.1](https://github.com/greatgc-flow/peerhub/releases/tag/v0.16.1) adds grouped monitoring for session, delivery, usage, context, runtime provenance, capacity, coverage, and store health. Monitoring uses a shared snapshot, bounds scope reads, and never invokes provider CLIs. Capacity preserves typed quota projections and provenance; unsupported context values remain unknown. The candidate-matched G3 gate passed all 12 live cases on commit `ab3aef83f047f495c1a33e7848c6262d15e38e83`; sanitized provider details are recorded in [live evidence](implementation/live_evidence/2026-10-15.json). See the [CHANGELOG](../CHANGELOG.md) for user-facing notes.
## Remaining work

Operational design ratified (S1-S4 consensus): [issue-resolution-2026-10-18](design/issue-resolution-2026-10-18.md). Slices S1, S2, S3, and S4 implemented and verified offline on branch `fix/issue-resolution-020`.

| Track | Done / remaining after v0.19.0 | Design SSOT |
|---|---|---|
| Slice S1 (Safety & Correctness) | **Done on `fix/issue-resolution-020`**: Native `ag` fail-closed pre-spawn without `--writable` while preserving cached terminal replay (#140); single-peer ask stream fencing via `StreamPeerMismatchError` (#125); factual empty-response evidence (`empty_response: true`, `task_effects: "unverified"`) (#126); structured terminal error summary preservation and `cx` pre-tool narration segregation (#127); `gemini-argon-wtu-weekly` mapped to `A-7D` (#149). | [Issue Resolution Note](design/issue-resolution-2026-10-18.md), issues #125, #126, #127, #140, #149 |
| Slice S2 (CLI UX, Decoding & Guidance) | **Done on `fix/issue-resolution-020`**: Guidance registry + `peerhub explain` (#117); bare `peerhub` prints `peerhub guide` (exit 0) and missing-store error includes source, quoted global `--db` command, and `Learn: peerhub guide` (#121); UTF-8-first with locale fallback (`decode_stderr`) and `ask_launch_failed` / `ask_stream_peer_mismatch` guidance (#120, #125). | [Issue Resolution Note](design/issue-resolution-2026-10-18.md), issues #117, #120, #121, #125 |
| Slice S3 (Monitor UX & Telemetry Display) | **Done on `fix/issue-resolution-020`**: Contiguous `CAPACITY` rendering under `CAPACITY [OK]`, bar fill aligned with remaining %, reset countdown separated from burn `ETA` (#146), and `USAGE` display fallback (`provider_input_tokens + output_tokens`) + cache hit % when `provider_total_tokens` is absent (#147). | Issues #146, #147 |
| Slice S4 (Docs & Guide Alignment) | **Done on `fix/issue-resolution-020`**: Updated `peerhub/cli/guide.py`, `README.md`, `docs/remote-ask.md`, `docs/migrations.md`, `docs/STATUS.md`, `docs/HANDOFF.md`, and `CHANGELOG.md` (#118, #123, #130, #134-#136, #148). | [Issue Resolution Note](design/issue-resolution-2026-10-18.md), issues #118, #123, #130, #134-#136, #148 |
| Deferred by Design | Top-level `peerhub readiness` (#145; cached `peerhub diag readiness` delivered in v0.21.0 part 1), lazy imports, queue/provider timing split, db schema slices 2-5, arbitrary-model monitor/readiness integration (`ag.argon` profile and exact registry delivered in #194), TUI interactive. | [Issue Resolution Note](design/issue-resolution-2026-10-18.md), issues #145, #194 |

### Deferred backlog (start triggers)

| Item | Start trigger / decision |
|---|---|
| Interactive TUI for monitor | Owner asks after the visual refresh settles. |
| Store schema v2 slices | A feature needs a non-additive schema change; current schema is v4. |
| DB lineage/provenance | Cross-store comparison becomes a recurring need. |
| Opt-in `ag.argon` profile | Owner decides quality/cost; needs cx/cc vote. The A-7D pool is already visible. |
| `peerhub diag peers` capability probe | [Peer limits](peer-limits.md) drifts too often. |
| `peerhub diag readiness --probe`: paid active probe + before/after benchmark (#145 part 2) | NOT implemented on purpose: diag stays read-only; the manual [smoke recipe](smoke-recipe.md) is enough. Revisit if the all-profile ping recurs often. |
| cx/ag/cc response `final` field separating narration from the final answer (#164 part B) | NOT implemented on purpose: v0.20.0 keeps the aggregate cx response unless explicit phase metadata marks commentary; a final-only response could drop answer content. Revisit with a concrete mismatch case and a schema decision. |

Operational lessons and coordinator handoff: [HANDOFF](HANDOFF.md).

## History

*(Moved out of the main README on 2026-09-12 as part of the UX-simplification backlog's P5 — see [`peerhub-ux-simplification-RATIFIED-2026-09-12.md`](history/design/peerhub-ux-simplification-RATIFIED-2026-09-12.md). [`../README.md`](../README.md) has the current quick-start and command reference; this file is the detailed development history.)*

## Archived v0 status (last empirically re-verified 2026-09-28)

**At that archived v0 snapshot, `peerhub ask` worked end-to-end** — it genuinely dispatches a prompt to a real peer CLI (agy/claude/codex) through peerhub's own governance, admission, and process-supervision layers, and returns the response. See the README's [Everyday commands](../README.md#everyday-commands) section for the current command reference.

> **2026-09-07**: a real, 100%-reproducible regression made this claim false on any *fresh*
> workspace for a period (cli-probe evidence was unconditionally rejected by the admission
> gate, before any prompt dispatch was attempted) — found via empirical live-dispatch testing,
> not caught by the existing 1453-test suite (which never exercised a genuinely fresh
> bootstrap). Fixed and re-verified live for all three peers (cc/ag/cx) the same night; see
> `tests/e2e/` (the new empirical test tier this fix introduced) and the "peerhub ask Bootstrap
> Deadlock" project memory for the full account. The claim above is accurate again as of this
> commit, confirmed by direct execution, not by re-reading old test results.

- **Implemented**:
  - Coordination kernel: dispatch, process supervision, heartbeat/liveness, routing, health, telemetry, SQLite persistence.
  - GovernanceBroker's outbox/delivery-tracking split is fully completed end-to-end (legacy mirror writes removed, tables dropped).
  - Capability-lease enforcement (all 5 increments): required capability tier threaded end-to-end, with an atomic pre-spawn enforcement gate and explicitly documented evidence audit.
  - Persistence UoW split (read/write separation) and a migration-runner sequence-derivation fix that fast-fails on FK violations.
  - A typed command boundary (`ApplicationAPI`/`Client`) with Pydantic v2 strict validation at the wire edge.
  - **Real peer adapters** for all 3 target CLIs — `RealAgyAdapter`, `RealClaudeAdapter`, `RealCodexAdapter` — each proven both standalone and through the full supervised `dispatch_and_execute()` pipeline (not a bypass), plus a `FakePeerAdapter` for tests. Selectable via a peer-kind registry (`peerhub.adapters.registry`).
  - **A real CLI**: `peerhub --version`, `peerhub status [--workspace PATH] [--peer/--all]`, and `peerhub ask PEER PROMPT [options]` — the last one performs a genuine end-to-end dispatch (admission → routing → supervised process execution → decoded response), not a stub. See "Try it" below.
  - A direct-ask admission bootstrap (`peerhub.direct-ask/v1`) that auto-provisions a real, measured-readiness health/routing configuration for a single requested peer on a fresh workspace — no manual policy setup required.
  - Static type checking (Pyright, 0 errors) and CI (GitHub Actions: pytest + pyright on every push/PR).
  - A ratified traceability convention and fact-refresh procedure (`docs/history/design/2026-08/TRACEABILITY-CONVENTION-R1.md`, `docs/history/design/2026-08/FACT-REFRESH-PROCEDURE-R1.md`) — the fact-refresh tool (`tools/peerhub_facts/`) is built, functional, and handles drift reporting via live CLI probes.
  - The full T1 Phase 3 outer loop: `dispatch_with_retries()`, session resume, streaming, tool-call capture, and failover routing. *(Correction, 2026-09-09: this line previously overstated reality — the primitives existed but `peerhub ask`'s production path had zero caller for any of them until the fix noted below. Genuinely true now.)*
  - Multi-peer broadcast primitive A: Correlation schema and a working `BroadcastCoordinator.fan_out()` loop (T3).
  - `EvidenceArtifact` / 3-tier context partitioning (completed for Claude and Codex adapters).
  - Health/quota tracking CLI surface: `peerhub status --peer/--all` and quota telemetry persistence.
  - Ctrl-C during `peerhub ask` walks the real cancellation ladder (`SOFT_CANCEL` → `TERMINATE_TREE` → `KILL_TREE`) via a proper background-thread dispatch and cancellation hook.
  - **`peerhub ask` genuinely consumes its own durable primitives** (2026-09-09, see below): directive/lesson/room-context injection into the outgoing prompt, session resume via `dispatch_with_retries()`/`classify_and_open_circuit()`, a real resilient-dispatch retry loop with health-circuit-breaker integration, consensus/Final-Call resolutions bound to observable effect intents, oversized-prompt staging to a file (`prompt_reference`, instead of failing), and durable per-dispatch transcript storage. Adapter profile-tier parity (`ag`/`cc` now expose `effort`/`deepthink` tiers, matching `codex`), a reusable directive-digest canonicalization function, an automatic high-risk-mutation trigger for the DIR-005 arbiter, an enforcement gate on lesson activation, and Codex reset-credit read/consume primitives were added alongside it.
- **Designed, but not yet built**:
  - Health/quota tracking's periodic background polling as an ambient daemon process. Poll-on-demand (refreshing usage projections synchronously as part of a live command, e.g. `_refresh_usage_projections()`) is already implemented and shipping; only an always-running background daemon variant remains deferred.
  - Windows-native Brokered Read-Only Reducers — blocked pending a policy call on required OS privileges.
- **Explicitly deferred (with named triggers)**:
  - Alembic runtime cutover: Ratified as HOLD. The bespoke runner remains the sole runtime migration engine. Will revisit only if peerhub adopts SQLAlchemy ORM or is about to become the primary dispatch path.
  - Formal multi-peer consensus (voting machinery / Primitive B): Deferred until the first `r10_requires_finalized_for` decision class is actually routed to peerhub.
  - Durable response transcripts for broadcast: Deferred until a dispatch-layer durability mechanism is ratified.
  - Capability-lease enforcement evidence: Changing adapter receipts to claim positive enforcement is deferred until a machine-owned launcher, plan-bound digest, empirical negative probe, and post-plan corroboration gate exist.
  - Parallel fan-out: Deferred (blocked on measuring SQLite write contention).
- **Not yet implemented / honest gaps**:
  - Phase 4 shadow-by-ownership-cluster validation and same-revision comparison + rollback proof.
  - Crash-linkage recovery (resuming an interrupted round after a coordinator crash).
  - Detailed per-vendor error-taxonomy mapping, and PTY transport are deliberately out of scope for the current adapter slice.
  - No shadow-mode validation yet (routing a subset of real traffic through peerhub in parallel with `hub.py` for comparison before any real cutover) — `hub.py` remains the authoritative system for real multi-peer coordination work today; `peerhub ask` is a real, working command, not yet a production replacement.

**v0.14.0.** Added quota exhaustion projections, `monitor --system-dir` and
store path display. The projection policy and screen are superseded by 0.15.0.
Windows cancel diagnostics clarify taskkill reporting an unkillable child;
writable canaries use normally created directories.

**v0.12.6 (v0.12.0 was the extension-first CLI cutover; 0.12.1 fixed Windows process-tree cancel confirmation; 0.12.2 added `ask --writable`, A2A 1.0 wire, retired v0 surface removed; 0.12.3 simplifies the release pipeline: one verify job, tested bytes = published bytes, slow/e2e live tests enforced; 0.12.4 puts the M2/M3 catalog tests into the candidate-bound release evidence and aligns the spec with the implementation; 0.12.5 adds per-ask usage telemetry, a byte-bounded catch-up with truncation notes, native process-tree cancel for CLI peers, and English docs; 0.12.6 adds explicit native resume for cc/cx/ag).** One `peerhub` entrypoint with a parser-derived command inventory; `ask`, `observation refresh`, `diag` (dashboard, `--live`, `--view rich`, `quota`, `health`) and `monitor` (collect quota evidence and watch a rich per-peer dashboard in one loop, with per-peer ask history). **Breaking CLI renames** (no aliases): `--peer`/`--stream` (was `--id`), `--author-peer` (was `--author`), `--idempotency-key` (was `--idemp-key`), `--cycles` (was `--count`), `--observation-db` (was `--obs-db`), `--system-dir` (was `--sys-dir`); the store is `.peerhub/core.db`. G3 live-provider evidence is produced locally only (`python -m tools.live_gate --yes`, bound to the release commit and attached to the GitHub Release); no remote runner holds provider logins. Claude `/usage` probe failures now record why (`claude_usage_timeout`, ...). Artifact dedup is streamed, `stage_stream(max_size)`, GC grace period; projection rebuilds are atomic; exact metadata search. Release steps: clean checkout of the release commit, `tools.live_gate`, one CI run, merge, tag, publish.

**Released as v0.11.0 (2026-10-04): M1 Very Simple Renewal.** Adds the M1 Core (Peer/Stream/Record/Offset on SQLite WAL), first-party extensions (Session Bridge, Observation, Readonly Diag), real cc/cx/ag runtime adapters and release gates (210/210 catalog tests; CI green on ubuntu/windows x py3.11-3.14). The previous v0.x main is preserved as branch `legacy/v0-main-final` / tag `v0-legacy-final`. See `M1_PROGRESS_TRACKER.md` and `docs/m1_impl/`.

Previously, v0.10.1 (2026-10-02): Packaged model profiles refreshed against current peer CLIs (revision `2026-10-01.1`): `cx.effort`/`cx.deepthink` -> `gpt-6.1-sol`, `cc.effort` -> `claude-sonnet-5-5`, `cc.deepthink` -> `claude-opus-5-5` (`xhigh`), `cc.pro` -> `claude-fable-5-1`; `ag.*` unchanged. Adds `docs/model-profiles/` (JSON Schema + manifest an AI can read to repeat the refresh) and a static test that keeps the manifest, `model-defaults.toml` and the adapter doc tables in sync; model literals in the profile tests now come from the manifest. Known pre-existing failure, not addressed here: `tests/static/test_production_call_map.py` (call-map lacks `workspace reset` and one other command added in v0.10.0).

**Released as v0.10.0 (2026-09-30).** Complete smart lifecycle parity release:
- `ALLOWED_CONFIG_FILES` expanded to include `models.toml`, `dispatch-policy.toml`, and `routing.toml` (preventing silent exclusion of critical configs).
- Added `peerhub backup global` to bundle global configurations (`~/.peerhub/config`).
- Added Dry-Run by default for `peerhub backup restore` (requiring `--apply` to activate).
- Added `peerhub workspace reset` sub-command with 2PC Fail-Closed pre-reset safety snapshots and clean on-demand self-healing.
- 100% strict zero-coupling with Engram verified: runs completely standalone in any Python environment. Full deterministic suite: 28/28 integration tests passing.

**Released as v0.9.0 (2026-09-28).** The packaged profiles were
refreshed against current AG, Claude Code, and Codex CLIs; all twelve profiles
(`standard`, `effort`, `deepthink`, and `pro` for each peer) returned live PONG
evidence. Diagnostics now distinguish CLI reachability from
missing quota telemetry, and the recursive `/?` help tree covers all 107 leaf
commands with descriptions and examples. The tagged release's deterministic
suite reports 2,404 passed with only four OS/privilege skips. Post-release live
validation returned PONG evidence from all twelve profiles, passed all 6 e2e
tests, and passed 9 of 10 slow tests; the remaining Claude probe received a 429
after the account exhausted its weekly quota. That empirical response shape triggered a
two-commit fix: the decoder now classifies the 429 response as quota
exhaustion (5053bcf), and the dispatch layer propagates `QUOTA_EXHAUSTED`
all the way to the `terminal_error_code` field instead of falling back to
the generic `PROTOCOL_ASSESSMENT_FAILED` sentinel (9a6c994). Wheel
and sdist pass strict package validation. See
[`docs/history/reviews/recursive-mece-release-audit-2026-09-28.md`](history/reviews/recursive-mece-release-audit-2026-09-28.md).

**Released as v0.6.0 (2026-09-20).** No breaking changes. Completes R4/P4b governance gateway convergence: every remaining gap v0.5.0 left open is now closed. `task` checkpoint's sibling actions (create/claim-start/complete/fail/cancel), `room`'s final 2 actions (create, rebuild-session-bindings — the latter threads broker/session access through `ApplicationAPI` since it wraps a free function, not a service method), `lesson`'s remaining actions (approve/supersede/quarantine/sweep, plus `propose` after adding its previously-missing `expires_at` field), `duty`'s 4 mutating actions (claim/heartbeat/close/sweep, via a design-first pass restoring a lossless 9-field lease receipt and preserving `close --close-session`'s composite partial-failure semantics), and `error review resolve` (via a new `ResolveQuarantineReviewCommand`) all now route through the gateway. `error review list` and 26 other commands remain `DIRECT_CLI_BYPASS` by deliberate scope boundary, not an oversight: 21 are bootstrap/session/config/health/routing/directive/node-level administrative primitives (pre-authorization-context, not governance decisions), and 5 (`ask`, `broadcast`, `diag`, `status`, `alert raise`) are the core peer-dispatch primitives that intentionally sit outside the governance gateway — see the call-map's own `known_gaps`/`scope_boundary` fields. (Corrected 2026-09-20: an earlier pass here undercounted this as "18" by only matching entries with `effect` exactly `"MUTATING"`, missing the `MUTATING_EXTERNAL`/`CONDITIONAL_MUTATION_EXTERNAL` variants — found via an independent MECE cross-review.) Two real pre-existing bugs were found and fixed during migration, not introduced by it: `LessonService.approve()` never updated `state["lifecycle"]` to `"APPROVED"` (unlike every sibling transition), and `duty close --close-session`'s `--json` output path never checked `session_close.status`, always returning exit 0 even when the session-close half failed. Full suite: 1770 passed, 5 skipped, 0 regressions. Also relocates the project's leftover AI-collaboration-process history (old multi-peer protocol docs, dev session archives, a 129-item development backlog) from the Engram product repo — where it had nothing to do with the shipped installer product and was needlessly bloating every release zip — into `docs/history/from-engram-repo/`, where it semantically belongs.

**Released as v0.5.0 (2026-09-20).** No breaking changes. Two independent work arcs completed: (1) **R4/P4b governance gateway convergence** — the one item v0.4.0 left unstarted — is now real: `verify_credential_for_actor` is elevated from a consensus-only check into a uniform `GovernanceAuthorizer` (`peerhub/application/governance_authorizer.py`) invoked by `ApplicationAPI.submit()` for every gateway-routed command, replacing a near-tautological client-identity stub. 9 domains (`operational-error`, `feedback`, `artifact`, `file-lock`, `task` checkpoint, `room` (13 of 15 actions), `lesson` (activate/retire/broadcast), `role`, `leadership`, and `consensus vote` — the first command to use the gateway's verified-credential path) now route real CLI traffic through `ApplicationAPI.submit()` instead of bypassing it; `docs/design/peerhub-production-call-map-R1.json` (archived in git history; see [ledger](history/ledger-2026-10-17.md#21-docsdesignpeerhub-production-call-map-r1json)) records the remaining unmigrated actions and exactly why each is deferred (no registered wire command yet, or a registered command missing a real field) rather than leaving the gap unexplained. Ratified 2026-09-19 as a one-time DIR-006 exception (`ag.effort` substituting for a real-vendor-rate-limited `cx`, with every citation independently re-verified) — full record: `docs/history/design/peerhub-r4-p4b-converged-design-2026-09-17.md`. (2) A whole-package **quality audit** (`docs/history/reviews/quality-audit-2026-09-19.md`) is fully resolved: 8 bare `except Exception` blocks narrowed to their real expected exception types (and a genuine sqlite connection leak fixed along the way), 2 of 3 flagged test-coverage gaps closed with 11 new unit tests, the 462-line/16-argument `dispatch_and_execute` God function decomposed into 5 independently-testable phases with zero behavior change (all 46 pre-existing tests pass unmodified), and a "dead code" finding corrected after investigation showed none of the 5 flagged functions were actually orphaned -- each is a deliberately-built capability never wired to its call site, or already explicitly documented as intentionally deferred. Full suite: 1730 passed, 0 regressions throughout. See [PR #2](https://github.com/greatgc-flow/peerhub/pull/2) for the complete diff.

**Released as v0.4.0 (2026-09-16).** No breaking changes. Two independent work arcs completed: (1) the `peerhub-holistic-renewal-RATIFIED-cx-astra-2026-09-13.md` roadmap's R0 (release-tag-vs-package-version CI guard), R1 (P4a production-call-map extraction), R2 (pure workspace resolution, opaque identity/activation-epoch minting), R3 (journaled atomic backup/restore with real crash/rollback tests), and R5 (docs/tools/history reorganization, 231→34 files in `docs/design/`) are all shipped — only R4 (P4b governance convergence + typed call, gated on D1) remains unstarted; (2) D-CTX (D0 independent review through D1 Increment 1) closes the "no record of which vote was real" and "any local process can claim to be any peer" gaps identified in the original proposal: a workspace-scoped credential table (`dispatch_context_credentials`, keyed to R2's opaque `workspace_home_id`/`activation_epoch`) is minted at real admission time, delivered to the dispatched child process via a secured per-attempt context file (`PEERHUB_CONTEXT_FILE`, live-verified to actually reach real Agy/Codex subprocess environments), and checked by an opt-in `verified_required` policy on both the native `consensus vote`/Final-Call-ACK path and the legacy-compatible `proposal-vote` path — with `--actor`/`--voter` now optional (derivable from a valid credential) while an explicit-actor-plus-credential mismatch is still rejected. Increment 0 (forensic write-provenance) is additive and doesn't change quorum counting; Increment 2 (high-risk-round enforcement) and Increment 3 (extending the gateway to every other governance mutation) remain deliberately unstarted future work. Full record: `docs/design/README.md`'s Tier B index, and this session's archive at `Engram`'s `_sys/data/sessions/2026-09-16_peerhub.md`.

See [`docs/history/design/2026-08/HUB-REPLACEMENT-ROADMAP-2026-08-09.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/history/design/2026-08/HUB-REPLACEMENT-ROADMAP-2026-08-09.md) for the full phased plan toward functional hub.py parity, and [`docs/history/design/2026-08/PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/history/design/2026-08/PEERHUB-P-DRIVE-ISOLATION-2026-08-09.md) for how peerhub's own runtime state relates to (and is deliberately isolated from) the wider P: development environment this repo happens to live inside during development. For the Engram/peerhub architectural separation itself (Engram is a portable dev-environment package, peerhub is the standalone AI-collaboration layer that used to live inside it) — what's done, what's verified clean in both directions, and what's left on either side — see [Engram's `2026-09-03_separation-completion-backlog.md`](https://github.com/greatgc-flow/Engram/blob/main/_sys/data/sessions/2026-09-03_separation-completion-backlog.md).

**hub.py-replacement TDD (2026-08-27 to 2026-09-02)**: real, tested code exists for gap-2 (consensus), gap-4 (duty-lease), gap-5 (task lifecycle), gap-6 (governance/lessons, capability matching) in full, and gap-3/gap-7 partially. **All 6 domains have real, runnable CLI commands** — `peerhub consensus|task|lesson|room|duty|session`, see "Try it" below — backed by dedicated services (`RoomParticipationCoordinator`, `ArbiterReviewCoordinator`, `PeerRegistryService`, `RoleAssignmentService`, `FeedbackService`, `OperationalErrorService`, `LeadershipService`, `RoomsService`, `ProposalCoordinator`, `ArtifactRecordService`, `CapabilityMatchingCoordinator`, and others). See [`docs/history/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/HUB-REPLACEMENT-TDD-PROGRESS-2026-08-27.md) for exactly what's real, and [`docs/history/design/PEERHUB-BACKLOG-2026-08-27.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/PEERHUB-BACKLOG-2026-08-27.md) for the full consolidated remaining-work backlog, organized by how ready each item is (mechanical wiring vs. needs new component code vs. needs a design round vs. entirely undesigned domain), and [`docs/history/design/SESSION-LESSONS-INDEX-2026-09-02.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/SESSION-LESSONS-INDEX-2026-09-02.md) for a topic-indexed pointer into this session's recurring bug classes and process lessons.

**LegacyTranslator fully retired (2026-09-09)**: `hub.py`'s legacy CLI action names used to be exercised through a test-only translation shim (`LegacyTranslator`/`LEGACY_CATALOG` in `peerhub/application/legacy.py`) that mapped ~90 legacy action names to typed native commands. An empirical audit (2026-09-07) had already found this shim had zero production callers — `peerhub/cli.py`'s real, shipped CLI never called it; every native subcommand talks to its service layer directly — so it was a tested-but-unreachable compatibility layer, not a live parity surface. Since it had no production role, the shim (`LegacyTranslator`, `LEGACY_CATALOG`, and their supporting types) has now been deleted outright, across a 9-batch effort documented in [`docs/history/reviews/legacy-translator-retirement/`](https://github.com/greatgc-flow/peerhub/tree/main/docs/history/reviews/legacy-translator-retirement) (batch-01 through batch-09-final). The wire-protocol contracts it used to exercise indirectly are preserved directly, natively, in `tests/unit/application/test_command_wire_contracts.py`. `docs/history/design/phase0/migration-ledger-v2.json` (the legacy-action-to-command mapping ledger) is kept as a historical record, no longer authoritative.

**AI-collaboration production-integration gaps closed (2026-09-09).** A zero-base MECE audit comparing `P:\`'s legacy `hub.py` against peerhub's own shipped `ask` path found that peerhub had built most of `hub.py`'s durable coordination *primitives* (directive/lesson services, session-lease/binding machinery, retry/circuit-breaker code, consensus effect intents) but its production `ask` path didn't actually call most of them — a real production-parity gap, not the LegacyTranslator's already-closed compatibility-shim question above. Independently ratified via a dialectical review (two independent peer reviews, adjudicated by a third), then implemented end-to-end: `peerhub ask` now injects user/runtime directives, peer-filtered lessons, and room/task context into the outgoing prompt; performs real session resume with model-fingerprint validation; dispatches through the resilient retry loop with health-circuit-breaker integration instead of a single direct call; binds consensus/Final-Call resolutions to real, observable effect intents instead of a no-op default; stages oversized prompts to a file instead of failing; and durably persists the full dispatch transcript. Alongside this: `ag`/`cc` adapters gained `effort`/`deepthink` profile tiers (parity with `cx`, and with what `P:\`'s `orchestration.json` actually dispatches), a reusable directive-digest canonicalization function replaced an unreproducible hardcoded-digest table, the DIR-005 arbiter gained an automatic high-risk-mutation trigger (previously dissent-only and manually invoked), lesson activation is now gated on a real enforcement check (previously approval alone was sufficient), and Codex reset-credit read/consume primitives were added (mirroring `hub.py`'s `CodexAccountClient`; CLI exposure deliberately deferred). Full record, including the dialectical ratification and independent verification of every claim: [`docs/history/reviews/p-drive-mece-migration-audit-2026-09-09.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/reviews/p-drive-mece-migration-audit-2026-09-09.md).

**Released as v0.3.0.** Breaking change: `arbiter.json`/`proposals.json` now require a top-level `"schema_version": 1` at every layer, including an unmigrated legacy `.peerhub/*.json` file (add the key by hand, or run `peerhub config migrate`).

**Config dotdir consolidation (2026-09-09).** `arbiter.json` and `proposals.json` moved from `.peerhub/*.json` to the same `config/` tier `ask.toml` already used, and both files gained a global fallback layer (workspace → global → built-in safe default) with a required `schema_version` and diagnostics reporting which layer won each field. New commands: `peerhub config validate` (exercises every config family's real loader and reports OK/ERROR, surfacing the winning-layer diagnostics), `peerhub config init --scope global|workspace` (scaffolds a tier's `config/` directory with a commented starter file), `peerhub config migrate` (moves a legacy file to `config/` after validating it, refusing outright if both locations exist), and `peerhub backup workspace`/`restore` (a live SQLite-online-backup-API snapshot bundled with `config/`, with dispatch-transcript inclusion an explicit opt-in, never implicit). Full hierarchy, precedence, and privacy rules: [`docs/config-hierarchy.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/config-hierarchy.md); full design record: [`docs/history/design/dotdir-consolidation-RATIFIED-2026-09-09.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/dotdir-consolidation-RATIFIED-2026-09-09.md).

**hub.py-replacement design phase (2026-08-23 to 2026-08-26): DESIGN-complete, TDD-ready.** The 7 functional categories `hub.py` covers beyond basic dispatch — compat/cutover strategy, consensus, session/room/thread continuity, health/leadership/duty-lease, task lifecycle/approval, governance/learning, and diagnostics parity — each now have either a concrete `TargetState` JSON schema or a concrete dedicated design, all converged and ratified (52 of 53 remaining open items resolved by design-consistency reasoning, 1 genuine business decision resolved by the user, 1 shared infrastructure prerequisite scoped as its own task). Start at [`docs/history/design/HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/HUB-REPLACEMENT-PRE-TDD-FINAL-RATIFICATION-2026-08-26.md) (supersedes older per-doc "Unresolved" lists), then [`docs/history/design/HUB-REPLACEMENT-DESIGN-REINFORCEMENT-INDEX-2026-08-24.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/HUB-REPLACEMENT-DESIGN-REINFORCEMENT-INDEX-2026-08-24.md) for the full per-gap breakdown. **Update**: most of this design has since been implemented during the TDD phase below (consensus, task, lessons, duty-lease, and half of session/room/thread) — see [`docs/history/design/PEERHUB-BACKLOG-2026-08-27.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/design/PEERHUB-BACKLOG-2026-08-27.md) for exactly what from this design phase is still unimplemented.

The target architecture was designed and converged through a 9-round adversarial review (`ag`/`cx`/`cc`) documented in [`docs/history/design/2026-07/ARCHITECTURE.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/history/design/2026-07/ARCHITECTURE.md). The full debate record, including rejected alternatives and evidence citations, is in [`docs/history/design/2026-07/peerhub-architecture-debate.md`](https://github.com/greatgc-flow/peerhub/blob/main/docs/history/design/2026-07/peerhub-architecture-debate.md). Later design decisions are under [`docs/design/`](https://github.com/greatgc-flow/peerhub/tree/main/docs/design), dated by filename.

## Known deferred defects (triage #241, 2026-10-20)

Valid-but-deliberately-unimplemented findings; each is closed `not planned` with label `deferred`. Library-only components (A2A, MCP, ExtensionHost, Work/Skills, Search, Approval, durable plans) have no CLI wiring. **Gate:** #225, #234 and #238 must be fixed before any A2A/Skills/ExtensionHost wiring merges; #231 is the first to promote if large probe output is reported.

| Issue | Revisit trigger / reason |
|---|---|
| #202 | L: Router ignores cost sensitivity; revisit when an actual consumer requires defined cost semantics. |
| #203 | L: zero-quota candidate demonstrably wins, but Router never dispatches; revisit before automated routing adoption. |
| #206 | C: known AG error flags are rejected; revisit only with a supported, sanitized flat-status failure fixture. |
| #207 | Closed-loop balancing is unimplemented product scope; revisit only on an explicit routing requirement. |
| #208 | C: watch/snapshot already work; revisit interactive TUI only after a demonstrated operator workflow need. |
| #209 | Universal ledger/benchmark expansion is disproportionate; revisit after a concrete escaped regression exposes a missing gate. |
| #211 | C: fresh catch-up retains all redirect pins before trimming; revisit on measured memory/latency trouble in realistic streams. |
| #212 | L: late EOF bypasses deadline recheck; revisit before A2A deployment or support for affected response wrappers. |
| #213 | L: budget omission and conservative byte accounting are intentional; revisit when a consumer requires pin-coverage reporting. |
| #214 | L: runtime temp output is unbounded until completion; revisit before adopting ProcessRuntimeAdapter for unattended commands. |
| #216 | L: host transition has a real stale-state race; revisit before concurrent ExtensionHost use. |
| #217 | L: MCP duplicates result data, but compatibility is intentional surface area and token impact unproved; revisit with measured client overhead. |
| #218 | L: sanitizer demonstrably drops prefix wildcard; revisit when shipped Search has an actual user or CLI integration. |
| #219 | L: scores collapse but SQL ranking survives; revisit when a consumer uses numeric relevance. |
| #220 | L: metadata search fetches all lexical hits; revisit on an adopted search workload showing material memory cost. |
| #221 | C: ask guard scans history under write lock; revisit on measured latency/contention rather than add binding projections speculatively. |
| #222 | L: replay and N+1 reads exist; revisit with substantial Work/Skill usage and measured bottlenecks. |
| #223 | L: source iterator eagerly loads stream IDs; pinned snapshot is intentional; revisit on large exports or WAL pressure. |
| #224 | L: re-index falsely adopts changed content, but execution integrity still rejects drift; revisit when SkillCatalog is adopted. |
| #225 | L: reducer accepts initial ACTIVE state; revisit before Skills authorization consumes externally authored/imported records. |
| #226 | C/L: constructor reinstalls triggers, but CLI readers bypass it; revisit on measured ask/refresh lock overhead. |
| #227 | L: rebuild mixes snapshots under concurrent writes; current restore requires quiescence; revisit before supporting online rebuild. |
| #228 | L: YAML/fallback meanings diverge; revisit before portable Skills manifests become a supported user workflow. |
| #231 | C: probe buffers lack byte caps, but a cross-provider streaming rewrite lacks demonstrated need; revisit on measured excessive output/RSS. |
| #233 | L: malformed proof raises AttributeError, but verification still stops before replacement; revisit on supported backup-import use. |
| #234 | L: A2A owner check is outside append transaction; revisit before concurrent durable A2A deployment. |
| #235 | L: A2A/plan journals repeatedly replay history; revisit with adopted workloads demonstrating coordination latency. |
| #236 | L: observations grow indefinitely; revisit before long-lived A2A monitoring is deployed. |
| #237 | L: approval can cross TTL, but consumption checks expiry atomically; revisit when stale approval state affects a real operator flow. |
| #238 | L: concurrent same-status evidence can be falsely acknowledged; revisit before multi-writer durable A2A use. |
| #243 | Monitor UX v2 design spec: no demonstrated reading problem (existing monitor covers most of it). Revisit when the owner supplies a recurring confusion or a disliked capture; then one S-sized follow-up with visual signoff (SOURCE/SIGNAL columns, attention strip). |

### Open post-v0.22.2 follow-ups (2026-10-26)

These issues remain open; they are separate from the closed deferred triage findings above.

| Issue | Scope / status |
|---|---|
| #248 | Deferred batch/account/Engram scope index; implementation remains deferred. |
| #249 | Fixed in v0.22.1/v0.22.2 (closed). |
| #256 | Confirmed batch worker setup failure can lose a task row and abort aggregation; bounded fix and offline fault-injection coverage in preparation, not released. |
| #258 | Credit/quota specification; deferred until an owner-approved observability requirement. |
| #259 | Dated pricing research ledger; no implementation. Revisit for an explicit pricing decision; retain separately from #258. |
| #260 | Review findings and backlog mapping; only P0-1 (#256) and P2-1 factual documentation drift are in the current fix scope. |

Backlog with triggers (#260): P0-2 (evidence chain), P0-3 (semantic outcome versus delivery), and remaining P1/P2 findings are deliberately not implemented. Keep scope indexed by #248; revisit evidence/credit/quota work with #258 and #215 only for an owner-approved consumer and evidence contract. Pricing stays research in #259, triggered by a concrete pricing decision. Routing/cost/balancing stays under #202/#203/#207 until automated routing is adopted with defined cost semantics. Monitor UX stays under #243 until recurring confusion or an owner-provided capture warrants one small change. Close #260 as review completed only after mappings and any otherwise untracked triggers are recorded; closure does not imply implementation.
