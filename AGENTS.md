Read NOW.md first for the current state and next steps; recent flow is the top of CHANGELOG.md.

# Working in PeerHub

Start with [README.md](README.md) for commands and the doc map, and
[CONTRIBUTING.md](CONTRIBUTING.md) for maintainer and release guidance.

- Update [NOW.md](NOW.md) in the same PR when the state or next steps change.
- Install development dependencies with `python -m pip install -e ".[dev]"`.
- Run deterministic tests with `python -m pytest -q`; run `pyright` for typing.
  Live tests spend provider quota and require explicit opt-in; see CONTRIBUTING.
- Full suite: `python -m pytest -q -n 8` (about 8 minutes when measured 2026-11-14; serial 15-17 min). The passed count grows with every release and depends on the installed extras: take it from the last line of your own run, not from a document.
  Missing-module errors for yaml or setuptools warrant checking the dev extras in a disposable venv. Other collection or setup failures require diagnosis.
- In the cx Windows sandbox, tests that use `tmp_path` cannot run (pytest creates owner-only temp directories, also with `--basetemp`; measured 2026-11-14 on Python 3.15): run the other targeted tests, report `tmp_path` tests as NOT RUN for the lead, and never add shims or temp folders to the repo.
- For shared stores, export a unique `PEERHUB_OWNER` per lead session, a readable `PEERHUB_LEAD`, and `PEERHUB_BATCH_ID`/`PEERHUB_BATCH_TASK` for lanes. See `peerhub explain caller-ownership`.
- Use LF for new and edited text; preserve the frozen CSV exceptions in
  `.gitattributes`.
- After any edit under `docs/m1_spec`, run
  `python docs/m1_spec/tools/seal_package.py` and include the regenerated seal.
- Register new M2/M3 extension modules in the appropriate module lists in
  `tests/continuity/test_m2_m3_exit_gates.py`. Register module classification and
  permitted imports in `tests/communication/architecture/test_arch_extension_graph.py`;
  keep both gates consistent with the extension boundaries.

## Lane rules (every AI lane reads these; prompts carry only the task)

- All peers: work only in your workspace (a git worktree on its own branch); do not commit, push or edit `NOW.md` (the lead merges and updates it). Every fix ships with a regression test. A durable behaviour adds a `docs/requirements.md` row with a unique slug ID (`R-<topic>`); do not rewrite a test pinned there unless the task changes that requirement, and list any pinned test you edited. Add one CHANGELOG line per change under `## [Unreleased]`. Report: files changed, tests run with results, pinned tests edited.
- cx: read-only shell commands (`rg`, `Get-Content`, `git diff/log/show/status`) and `python -m pytest <paths> -q -p no:cacheprovider` are fine; no git writes; no network in read-only asks.
- ag: read files with your file reader (no shell fallbacks); the only shell command is `python -m pytest <paths> -q -p no:cacheprovider`; every file you need is inside your `-w`; no git.
