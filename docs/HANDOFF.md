Reference notes for maintainers. Not an instruction file: open it only when needed.
[STATUS](STATUS.md) is the single authoritative owner of release status, published versions,
and the active development queue; this document provides maintainer operating guidance
and coordination checkpoints.

## 0. Current checkpoint (2026-10-29 — post-v0.23.0)

**State:** v0.23.0 is published (tag v0.23.0, target `c98baee9`, PR #265; PyPI confirmed; G3 passed; clean-venv smoke passed). Profiles live only in `model-defaults.toml`; the recipe is `docs/model-profiles/model-profiles.json` (schema_version 2); design `design/profile-ssot-2026-10-29.md`. Owner opt-in `cx.terra` is in `~/.peerhub/config/models.toml` (gpt-6.1-terra, tier/cost unevaluated). Open: dependabot PR #257 (actions bump, CI green, merge pending owner ack).

Previous: v0.22.3 is published (tag v0.22.3, target `bae1b675`; PR #261, #262; PyPI wheel + sdist; clean-virtualenv smoke passed; G3 passed with packaged ag/cc/cx profiles). #256 and #260 are closed. No release in preparation.
[STATUS](STATUS.md) owns release evidence and the open queue: #248 (deferred scope index), #258 (credit/quota spec), #259 (pricing research) - all start-triggered, none authorized for implementation.
**Next:** none pending. The #260 backlog (evidence chain, outcome vs delivery, advisory routing, monitor cockpit) stays deferred with triggers in STATUS.
ag is usable again (owner lifted the ban 2026-10-26); each release still needs the G3 gate from a clean checkout of the merged commit.

- Release lessons: `tests/communication/package/test_release_notes.py` pins the version and must change in the bump PR; a CHANGELOG heading for a never-published version needs `(superseded ...)`; py3.15 has no pyyaml wheel, so run the full suite in CI, not locally.

### Superseded checkpoint (2026-10-26 — post-v0.22.2)

v0.22.2 published; #256 fix and documentation cleanup were the next step (done in v0.22.3).

### Superseded checkpoint (2026-10-21 — post-v0.22.0)

**State:** v0.22.0 is published (tag v0.22.0, target `697e15743b50d9f3b02fec4ed2c3ee896c21dae9`, PR #247; PyPI wheel + sdist and clean-virtualenv install smoke confirmed). No release in preparation.
Design and usage: [batch runs](batch.md) and [accounts](accounts.md).
[STATUS](STATUS.md) records publication and validation. Open follow-ups: #248 (deferred scope index), #249 (`workspace.generation` first-use race). **Next:** none pending.

- ag multi-account is unsupported: authentication lives in the OS keyring; `GEMINI_DIR` is inert.
- Bindings are immutable: no rebind/unbind; a new login or root requires a new peer and root.
- Bound children scrub provider override variables case-insensitively and disclose removed names, never values, in results and durable evidence.
- Batches use one public-CLI subprocess per task with explicit bounded parallelism.
- Launched worker outcomes default to UNCERTAIN without positive execution evidence; never retry uncertainty blindly.
- P3 quota/monitor support for bound peers is deferred; default-account quota does not describe bound peers.
- Engram work is deferred except the `GEMINI_DIR` correction.
- G3 passed bound to `697e1574`: ag used `gemini-4-argon-medium` via `PEERHUB_LIVE_AG_MODEL` because G pools were exhausted; packaged `ag.standard` was not exercised (#191), while cc/cx used packaged profiles. Owner decision (2026-10-21): ag banned again after G3.

### Superseded checkpoint (2026-10-20 — post-v0.21.6)

- **State:** v0.21.6 is published (tag v0.21.6, PyPI confirmed). No release in preparation; no open issues. Audit triage #241: 7 applied, 2 invalid, 31 deferred (docs/STATUS.md index).
- **Scope:** #195 denied tool actions and #189 monitor leftovers. Decision (user, 2026-10-20): NO peer-scoped live gate; full fail-closed G3 only.
- **Next:** none pending. Optional: re-run G3 on the packaged ag.standard model after ag G-7D resets (#191). Decision: no peer-scoped live gate.

### Superseded checkpoint (2026-10-17 — v0.19.0 candidate preparation)

- **Branch & commits:** branch `release/0.19.0` prepared for v0.19.0 candidate from `origin/main` (`7158909a`); remote branch pushed, awaiting owner live gate G3 and PR/publish workflow.
- **Owner top rule:** so-very-simple.
- **Design status:** v0.19.0 candidate incorporates store discovery cleanup (removed implicit `<cwd>/peerhub` subfolder walk), bare monitor prioritization (capacity/peers always visible), `monitor --expand`, offline `peerhub guide` subcommand (safety, efficiency, report issues), model-facts drift line, multi-pool quota accounting, cx resume `--writable` fix, and bounded delta reads.
- **Hazards:** store hazard (.peerhub copy at user_version 3 vs source v4) — running source v4 against it migrates it to v4 and the installed 0.16.0 CLI then refuses it; use a copy (`--db`) for dev; `core.db.v018-bak` keeps the backup store.
- **Known:** `test_rel_005` POSIX PTY smoke skipped on Windows (ConPTY/manual test coverage).
- **Peers:** one persistent stream per peer (`dev-cx`, `dev-cc`, `peers-pr1-ag`/`dev-ag`) with `*.effort` profiles; peers cannot run pytest reliably; cx writes no files.
- **Next:** none pending. Optional: re-run G3 on the packaged ag.standard model after the ag G-7D quota resets (#191); see STATUS deferred backlog.
- **Resume prompt:** Run local live gate G3 for v0.19.0 candidate, then proceed with GitHub release PR and publish workflow.



Deferred work and start triggers live in [STATUS](STATUS.md#deferred-backlog-start-triggers).

Stale-stream note: `s2a-ag` is OK; no action.

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

## 2. Collaboration practice (owner's preferences)

- **Owner top rule & COMPACT:** so-very-simple. Apply COMPACT: minimize abstraction and document duplication; all claims require exact empirical evidence.
- **Unanimity & debate:** Involve cc, cx, and ag. Unlimited critical rounds until unanimity; no feature implementation before complete design and consensus. Cross-critique objectively; encode objections honestly and never fabricate agreement.
- **Role proposals:** Propose roles until unanimity (`ag.effort` sole editor, `cx.effort` independent control-flow/safety reviewer, `cc.effort` concise veto/reviewer). Configuration and observed task outcomes are not comparative ability benchmarks; do not assert universal limits or unsupported model superiority/throughput claims.
- **Profile facts & shared pools:**
  - `ag standard flash-low / effort flash-medium` share Google (G) pools; `deepthink sonnet-high` consumes Anthropic 3P.
  - `cc standard haiku / effort sonnet high / deepthink opus high` share Claude (C) pools.
  - `cx standard luna low / effort 6.1-sol medium / deepthink 6.1-sol xhigh` share Codex (X) pools.
  - Note: Facts require periodic recheck due to unconfirmed models (e.g., `gemini-argon-wtu-weekly`) and CLI drift. Standard profile is not guaranteed cheapest; no benchmarks at this time.
- **Quota balance & conservation:** Monitor 7-day and 5-hour pools (`peerhub diag quota` / `peerhub monitor`). Zero provider calls or live probes unless explicitly authorized for memory preservation; no profile hopping or deepthink to evade pool limits.
- **Sandboxing & execution constraints:** Peer sandboxes cannot run pytest reliably due to temp-directory ACL restrictions (observed on cx); headless ag read-only is not enforced at engine level (issue #140). Verify drafts and run tests/typing locally.
- **Delegation of edits:** Delegate even small edits via `peerhub ask <peer> --writable`; verify drafts and run pytest / Pyright yourself.
- **Language policy:** Use English for AI-to-AI prompts, source code, comments, docs, commits, and PRs; use Korean (terse) for human user updates.
- **Session management:** Practice session reuse across turns; preserve history; retain selectors (`--session`, `--fresh`). Clean up stale scratch files.
- **Token boundaries:** Near root token limit, write a durable handoff, then output exactly ONE resume-prompt line.
- **Permissions & release discipline:** General permissions are pre-granted. Update dependencies to latest in an isolated venv before any release. Require full suite and candidate-matched G3 live gate. Post-deploy verification and temp cleanup required.
- **Issue reporting:** File lessons, incidents, and bugs directly on corresponding GitHub issues (peerhub.md retired).

## 3. Tooling pitfalls

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

## 4. Release lessons

- Release sequence, CHANGELOG-derived notes and required assets: [CONTRIBUTING](../CONTRIBUTING.md#releasing).
- Serial tests, opt-in xdist and disposable development venvs: [development setup](../CONTRIBUTING.md#development-setup).
- Commit the generated `docs/implementation/live_evidence/<date>.json` through a docs PR.
- Never type a SHA by hand; derive it from Git.
- Delete a blocked/aborted release and its tag before retrying.

Full verbatim history (checkpoints 2026-10-04 to 2026-10-11 and the owner's standing rules): [history/session-log-2026-10.md](history/session-log-2026-10.md).

Cross-repository verification snapshot (2026-10-16): [SecondBrain and Engram handoff audit](history/cross-repo-handoff-audit-2026-10-16.md).
