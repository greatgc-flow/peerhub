Reference notes for maintainers. Not an instruction file: open it only when needed.

## 0. Release checkpoint (2026-10-13)

`release/v0.16.0` is a local candidate based on main `22404eeb`: stuck-streams
PRs 1-3 (#95/#97/#98), model profile refresh (#96), and CI pushes narrowed to
main. Release preparation makes no commit, push, tag, or PR. Candidate CI,
live gates, publication, and clean-venv smoke remain for cc under the
[release procedure](../CONTRIBUTING.md#releasing).

The [current status and remaining queue](STATUS.md#remaining-work) tracks
stuck PRs 4-6, monitoring v2 PRs 1-7, audit WP-B/C/D/E, and session reuse.
Session reuse is design-approved by owner+cc+cx (2026-10-13), with implementation
not started; the shipped CLI still uses explicit `--resume`. The
[session plan](design/plan-session-reuse-2026-10-13.md) owns the future decision:
default reuse, `--fresh`, and exact `--session`, removing public `--resume`.

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
