Reference notes for maintainers. Not an instruction file: open it only when needed.

## 1. Collaboration practice (owner's preferences)

- Involve cc, cx and ag; balance the 7-day pace so `cc <= ag == cx`, using `peerhub diag quota` / `peerhub monitor`.
- Delegate even small edits via `peerhub ask <peer> --writable`; verify drafts and run pytest / Pyright yourself.
- Resolve ambiguity through independent consultation of ag and cx, followed by cross-critique.
- Use English for AI-to-AI prompts and the user's language for user-facing text; keep source, comments, docs, commits and PRs in English.
- Keep human-facing terminal output short.
- Peers leave scratch files: run `git status` and add explicit paths.
- Peer sandboxes cannot run pytest because of temp-directory ACLs.
- A permission classifier may require an explicit user yes for `gh pr merge` and releases, even after a general grant.

## 2. Tooling pitfalls

- Store discovery, explicit selection and missing-store behavior: [README](../README.md#store-selection-and-existing-data).
- A STARTED/uncertain request blocks its stream (`blocked_uncertain`); use a new `--stream`. Reusing `--request-id` never replays uncertain execution.
- Deleting `.peerhub` irrecoverably resets history, stuck records and workspace model overrides; back up `core.db` first.
- After adding CLI options, run `python -m tools.command_inventory --write` and add an effect proof in `tests/communication/e2e/cli_extension_effects.py`.
- Windows process and writable-workspace limits: [CONTRIBUTING](../CONTRIBUTING.md#decisions-and-lessons).

## 3. Release lessons

- Release sequence, CHANGELOG-derived notes and required assets: [CONTRIBUTING](../CONTRIBUTING.md#releasing).
- Serial tests, opt-in xdist and disposable development venvs: [development setup](../CONTRIBUTING.md#development-setup).
- Commit the generated `docs/implementation/live_evidence/<date>.json` through a docs PR.
- Never type a SHA by hand; derive it from Git.
- Delete a blocked/aborted release and its tag before retrying.

Full verbatim history (checkpoints 2026-10-04 to 2026-10-11 and the owner's standing rules): [history/session-log-2026-10.md](history/session-log-2026-10.md).
