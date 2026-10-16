Reference notes for maintainers. Not an instruction file: open it only when needed.
The checkpoint below records verified state as of 2026-10-16; [STATUS](STATUS.md)
tracks the source release and remaining work.

## 0. Current checkpoint (2026-10-16)

- **Unanimous Consensus & Document Digests:**
  - Round 10 unanimous CC/CX/AG approval of monitor specifications, and Round 5 unanimous APPROVE-DB of `db-schema-v2`:
    - `db-schema-v2-2026-10-16.md`: `55604DF8807DC2EBC7C482C9A97203C9D8D4CDF1BE0FB1C55EE4B2602292BE04`
    - `live-evidence-archive-2026-10-16.md`: `5836FE148FD92373BD6FC1B5179638B5796288C7C455260E93FA89B1882923A0` (deferred)
    - `monitor-e2e-lineage-2026-10-16.md`: `10F04DC69312D4AAEC4203CAC1761A27D9DBA50CB416A5506C6F0AB510244047`
    - `monitor-expand-details-2026-10-16.md`: `A1BA27EDFEA28EFA967D09A2DDCE105249DAFE27AD116F4B7080A35790A24CDA`
    - `monitor-groups-usability-review-2026-10-16.md`: `9DDCFBA41AE742531BA6F07C5289462A14C670706FAC62EA60517637B4FA226F`
    - `monitor-human-dashboard-redesign-2026-10-16.md`: `F6BB519D206DE3ECE5EE6294FB8E29DEF34D938C94434094CB13FBCADF69882E`
    - `monitor-terminal-responsive-2026-10-16.md`: `315E195BD797E2D71C0F83E73CC33FBEA34200F57B80A9E2DDED5B51101DB03B`
    - `post-0160-traceability-2026-10-16.md`: `99E510C9FE3313772C582B72018B41BC340F7D8E1F966FBCD16DE5FBCC94736B`
- **Base State:** HEAD `c717ee00`, `origin/main` `d392fe4c`; no source changes yet.
- **Owner Decisions (2026-10-16):**
  - Monitor: improve monitor (keep grouped default via `effective_preferences()`).
  - Database: reconstruct as Schema v2 (in-place single-transaction migration, `PRAGMA user_version = 4` gate, verified backup first).
  - Workspace: holds only `peerhub` (extra worktrees, temp files, AG scratch, and engram session directories removed).
  - Compatibility & Downgrade Hazard: workspace `.peerhub` holds a v0.16.0-era store; newer-schema store preserved as `core.db.v018-bak`. Installed CLI is 0.16.0 wheel, while source tree is 0.18.1 (active downgrade hazard).
- **Execution Order & Collaboration:**
  - Order: DB v2 slices first (CX/AG), then monitor PR1 -> PR3, archive later.
  - Reviews: CC reviews only; use `*.effort` profiles (`cc.effort`, `cx.effort`, `ag.effort`).
  - Streams: `debate-monitor-2026-10-16-{cc,cx,ag}`, `db-audit-2026-10-16-{cc,cx,ag}`.
- **Open Items:**
  - Draft releases v0.17.0 and v0.18.1.
  - Remote branches cleanup (`docs/release-evidence-0.16.1`, `feat/peerhub-monitoring-20261015`, `release/0.16.1` are merged by PR).



## 1. Historical release checkpoint (2026-10-13)

`release/v0.16.0` is a local candidate based on main `22404eeb`: stuck-streams
PRs 1-3 (#95/#97/#98), model profile refresh (#96), and CI pushes narrowed to
main. Release preparation makes no commit, push, tag, or PR. Candidate CI,
live gates, publication, and clean-venv smoke remain for cc under the
[release procedure](../CONTRIBUTING.md#releasing).

The [current status and remaining queue](STATUS.md#remaining-work) tracks
stuck PRs 4-6, monitoring v2 PRs 1-7, audit WP-B/C/D/E, and session reuse.
Session PR1 is committed at `eec2cba4`. PR2 is implemented in the uncommitted
worktree (2026-10-14; 25 focused tests and Pyright pass locally): compatible retained
history, monotonic handles/generations, original-result replay and atomic
receipt/counter writers. The
[canonical sequence](design/analysis-session-reuse-as-is-2026-10-13.md#one-reconciled-pr-sequence)
owns remaining addressing, inspection/telemetry and monitoring work; selectors and session
inspection are not available yet.

See the [PR2 scope review and validation](implementation/PR2_SESSION_REUSE_VALIDATION.md)
for coverage, remaining checks and local results.

## 1. Collaboration practice (owner's preferences)

- Involve cc, cx and ag; balance the 7-day pace so `cc <= ag == cx`, using `peerhub diag quota` / `peerhub monitor`.
- Delegate even small edits via `peerhub ask <peer> --writable`; verify drafts and run pytest / Pyright yourself.
- Resolve ambiguity through independent consultation of ag and cx, followed by cross-critique.
- Use English for AI-to-AI prompts and the user's language for user-facing text; keep source, comments, docs, commits and PRs in English.
- Keep human-facing terminal output short.
- Peers leave scratch files: run `git status` and add explicit paths.
- Peer sandboxes cannot run pytest because of temp-directory ACLs.
- A permission classifier may require an explicit user yes for `gh pr merge` and releases, even after a general grant.
- Run only one xhigh cx review at a time; concurrent reviews exhausted cx quota.
- Batch related, locally reviewed changes into one PR when practical: CI is slow.

## 2. Tooling pitfalls

- Store discovery, explicit selection and missing-store behavior: [README](../README.md#store-selection-and-existing-data).
- A STARTED/uncertain request blocks its stream (`blocked_uncertain`); use a new `--stream`. Reusing `--request-id` never replays uncertain execution.
- Deleting `.peerhub` irrecoverably resets history, stuck records and workspace model overrides; back up `core.db` first.
- After adding CLI options, run `python -m tools.command_inventory --write` and add an effect proof in `tests/communication/e2e/cli_extension_effects.py`.
- Windows process and writable-workspace limits: [CONTRIBUTING](../CONTRIBUTING.md#decisions-and-lessons).
- ag and cx quota exhaustion: inspect the stored refusal reason/reset in ask or
  diag, then wait for reset or choose a peer/profile with available quota. PR 3
  settles eligible pre-work refusals as `NOT_EXECUTED`; failures after tools,
  incomplete evidence, and writable ag refusals still require uncertain-work
  recovery. A new stream does not restore provider quota.
- Packaged profiles are refreshed, but Agy model-catalog drift detection remains
  a TODO in [the audit](design/audit-2026-10-12.md#2026-10-13-progress).
- Windows pushes: a configured `gh.exe` path was missing; use the PATH-based
  credential override in [CONTRIBUTING](../CONTRIBUTING.md#development-notes).

## 3. Release lessons

- Release sequence, CHANGELOG-derived notes and required assets: [CONTRIBUTING](../CONTRIBUTING.md#releasing).
- Serial tests, opt-in xdist and disposable development venvs: [development setup](../CONTRIBUTING.md#development-setup).
- Commit the generated `docs/implementation/live_evidence/<date>.json` through a docs PR.
- Never type a SHA by hand; derive it from Git.
- Delete a blocked/aborted release and its tag before retrying.

Full verbatim history (checkpoints 2026-10-04 to 2026-10-11 and the owner's standing rules): [history/session-log-2026-10.md](history/session-log-2026-10.md).

Cross-repository verification snapshot (2026-10-16): [SecondBrain and Engram handoff audit](history/cross-repo-handoff-audit-2026-10-16.md).
