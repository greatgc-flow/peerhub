Reference notes for maintainers. Not an instruction file: open it only when needed.
[STATUS](STATUS.md) is the single authoritative owner of release status, published versions,
and the active development queue; this document provides maintainer operating guidance.

## 0. Current status and active queue; details: [STATUS](STATUS.md)

- Release status, active queue and open issues: [STATUS](STATUS.md).
- Deferred work and start triggers: [STATUS](STATUS.md#deferred-backlog-start-triggers).
- Release procedure and release lessons: [CONTRIBUTING](../CONTRIBUTING.md#releasing).
- Superseded checkpoints (2026-10-13 to 2026-10-26) were removed on 2026-11-10; they are in Git history (`git show 77422a6e82cb3879de207baf9d97dd79acf76aa8:docs/HANDOFF.md`).

## 1. Collaboration practice (owner's preferences)

- **Owner top rule & COMPACT:** so-very-simple. Apply COMPACT: minimize abstraction and document duplication; all claims require exact empirical evidence.
- **Unanimity & debate:** Involve cc, cx, and ag. Unlimited critical rounds until unanimity; no feature implementation before complete design and consensus. Cross-critique objectively; encode objections honestly and never fabricate agreement.
- **Role proposals:** Propose roles until unanimity (`ag.effort` sole editor, `cx.effort` independent control-flow/safety reviewer, `cc.effort` concise veto/reviewer). Configuration and observed task outcomes are not comparative ability benchmarks; do not assert universal limits or unsupported model superiority/throughput claims.
- **Profile facts & shared pools:** packaged profiles and pool assignments are defined in [`model-defaults.toml`](../peerhub/config_data/model-defaults.toml); balancing guidance is in [`docs/batch.md`](batch.md). Benchmark evidence: [`profiles-2026-11-08.md`](benchmarks/profiles-2026-11-08.md).
- **Quota balance & conservation:** Monitor 7-day and 5-hour pools (`peerhub diag quota` / `peerhub monitor`). Zero provider calls or live probes unless explicitly authorized for memory preservation; no profile hopping or deepthink to evade pool limits.
- **Sandboxing & execution constraints:** Peer sandboxes cannot run pytest reliably due to temp-directory ACL restrictions (observed on cx); headless ag read-only is not enforced at engine level (issue #140). Verify drafts and run tests/typing locally.
- **Delegation of edits:** Delegate even small edits via `peerhub ask <peer> --writable`; verify drafts and run pytest / Pyright yourself.
- **Language policy:** Use English for AI-to-AI prompts, source code, comments, docs, commits, and PRs; use Korean (terse) for human user updates.
- **Session management:** Practice session reuse across turns; preserve history; retain selectors (`--session`, `--fresh`). Clean up stale scratch files.
- **Token boundaries:** Near root token limit, write a durable handoff, then output exactly ONE resume-prompt line.
- **Permissions & release discipline:** General permissions are pre-granted. Update dependencies to latest in an isolated venv before any release. Require full suite and candidate-matched G3 live gate. Post-deploy verification and temp cleanup required.
- **Issue reporting:** File lessons, incidents, and bugs directly on corresponding GitHub issues (peerhub.md retired).

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

Full verbatim history (checkpoints 2026-10-04 to 2026-10-11 and the owner's standing rules): [history/session-log-2026-10.md](history/session-log-2026-10.md).

Cross-repository verification snapshot (2026-10-16): [SecondBrain and Engram handoff audit](history/cross-repo-handoff-audit-2026-10-16.md).
