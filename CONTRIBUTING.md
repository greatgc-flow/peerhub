# Contributing to peerhub

Thanks for helping improve peerhub, the Python library and CLI for coordinating
multiple AI peers.

## Development setup

peerhub requires Python 3.11 or newer. Clone the repository, then install its
development dependencies with the editable install documented in README Option C:

```bash
pip install -e .[dev]
```

Before opening a pull request, run:

```bash
pytest -q
pyright
```

## Code and tests

Read [CONVENTION.md](CONVENTION.md) before making code changes. It records the
project's coding rules. Runtime code is grouped into `core`, `extensions` and
`cli`; tests are grouped by communication, continuity and collaboration.
Shared fixtures and fake CLIs live under `tests/communication/harness`.

## Branches, commits, and pull requests

Start from `main` and use a focused, lower-case topic branch. Existing branches
use slash namespaces and commonly include a date, such as
`docs/feedback-loop-2026-09-23`.

Use concise Conventional Commit subjects, following the repository's existing
history: `fix(cli): correct adapter detection`, `docs: clarify setup`, or
`feat(governance): add a workflow`. Open a focused pull request to `main` that
explains the change and the checks you ran.

## Reporting issues and requesting features

Use the GitHub bug-report or feature-request templates when applicable. They
collect the reproduction and environment details needed to work on an issue.
