Reference notes for maintainers. Not an instruction file: open it only when needed.
[STATUS](STATUS.md) is the single authoritative owner of release status, published versions,
and the active development queue; this document provides maintainer operating guidance
and coordination checkpoints.

## 0. Current checkpoint (2026-10-18) — Slices S1–S4 Implemented, Verified Offline & Deployed Locally

- **Authorization & Execution:** Slices S1, S2, S3, and S4 (#117, #118, #120, #121, #123, #125, #126, #127, #130, #134–#136, #140, #146, #147, #148, #149) implemented and verified across the offline test suite on branch `fix/issue-resolution-020`, pushed to `origin/fix/issue-resolution-020`, and installed into `C:\AI\_sys\env\venv`. Only `#145` (`peerhub readiness`) remains deferred.
- **Branch & State:** Branch `fix/issue-resolution-020` pushed to `origin` and locally installed in `C:\AI\_sys\env\venv`.
- **Design SSOT:** [docs/design/issue-resolution-2026-10-18.md](design/issue-resolution-2026-10-18.md). Authoritative release status in [STATUS.md](STATUS.md).
- **Delivered Scope (S1–S4):**
  - **S1:** `#140` native `ag` fail-closed pre-spawn without `--writable` (cached replay preserved), `#125` single-peer ask stream fencing (`StreamPeerMismatchError`), `#126` factual tool-only turn completion (`empty_response: true`, `task_effects: "unverified"`), `#127` terminal error summary preservation & `cx` pre-tool narration segregation, `#149` `gemini-argon-wtu-weekly` -> `A-7D` quota pool mapping.
  - **S2:** `#117` recovery guidance wording, `#121` bare `peerhub` -> `peerhub guide` (exit 0) and improved missing-store guidance with source + quoted `--db` command, `#120` UTF-8-first with locale fallback (`decode_stderr`) and `ask_launch_failed` guidance.
  - **S3:** `#146` contiguous `CAPACITY` rendering in `monitor_view.py`, consistent bar fill direction, and `reset` duration separated from `ETA`; `#147` `USAGE` token display fallback (`provider_input_tokens + output_tokens`) + cache hit % when `provider_total_tokens` is `None`.
  - **S4:** `#118, #123, #130, #134–#136, #148` documentation and `peerhub guide` alignment.
- **Next action (after `cx` 03:50 KST and `cc` 06:20 KST quota reset):** Run cross-peer review on `fix/issue-resolution-020`, merge PR to `main`, run `python -m tools.live_gate --yes` for v0.20.0 release, and close `#145` if desired.
- **Resume prompt:** `C:\AI\workspace\peerhub의 fix/issue-resolution-020 브랜치(docs/HANDOFF.md 및 docs/STATUS.md 참조)를 확인하고, cx/cc 쿼터 리셋 상태를 점검한 뒤 main 병합 및 live_gate(v0.20.0 배포)와 남은 #145 이슈를 마무리해줘.`

### Superseded checkpoint (2026-10-17 — v0.19.0 candidate preparation)

- **Branch & commits:** branch `release/0.19.0` prepared for v0.19.0 candidate from `origin/main` (`7158909a`); remote branch pushed, awaiting owner live gate G3 and PR/publish workflow.
- **Owner top rule:** so-very-simple.
- **Design status:** v0.19.0 candidate incorporates store discovery cleanup (removed implicit `<cwd>/peerhub` subfolder walk), bare monitor prioritization (capacity/peers always visible), `monitor --expand`, offline `peerhub guide` subcommand (safety, efficiency, report issues), model-facts drift line, multi-pool quota accounting, cx resume `--writable` fix, and bounded delta reads.
- **Hazards:** store hazard (.peerhub copy at user_version 3 vs source v4) — running source v4 against it migrates it to v4 and the installed 0.16.0 CLI then refuses it; use a copy (`--db`) for dev; `core.db.v018-bak` keeps the backup store.
- **Known:** `test_rel_005` POSIX PTY smoke skipped on Windows (ConPTY/manual test coverage).
- **Peers:** one persistent stream per peer (`dev-cx`, `dev-cc`, `peers-pr1-ag`/`dev-ag`) with `*.effort` profiles; peers cannot run pytest reliably; cx writes no files.
- **Next:** owner runs live gate G3 (local), reviews PR, merges and triggers publication workflow.
- **Resume prompt:** Run local live gate G3 for v0.19.0 candidate, then proceed with GitHub release PR and publish workflow.



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
