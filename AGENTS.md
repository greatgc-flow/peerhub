# Working in PeerHub

Start with [README.md](README.md) for commands and the doc map, and
[CONTRIBUTING.md](CONTRIBUTING.md) for maintainer and release guidance.

- Install development dependencies with `python -m pip install -e ".[dev]"`.
- Run deterministic tests with `python -m pytest -q`; run `pyright` for typing.
  Live tests spend provider quota and require explicit opt-in; see CONTRIBUTING.
- Use LF for new and edited text; preserve the frozen CSV exceptions in
  `.gitattributes`.
- After any edit under `docs/m1_spec`, run
  `python docs/m1_spec/tools/seal_package.py` and include the regenerated seal.
- Register new M2/M3 extension modules in the appropriate module lists in
  `tests/continuity/test_m2_m3_exit_gates.py`. Register module classification and
  permitted imports in `tests/communication/architecture/test_arch_extension_graph.py`;
  keep both gates consistent with the extension boundaries.

## Owner's standing collaboration rules

- Always involve cc, cx and ag; check `peerhub diag quota` / `peerhub monitor` and balance the 7-day pace so `cc <= ag == cx`.
- Delegate even small edits with `peerhub ask <peer> --writable`; verify peer drafts against the code and run pytest and Pyright yourself.
- Settle ambiguity through independent consultation of ag and cx, followed by cross-critique; decide on evidence.
- Write AI-to-AI prompts in English and user-facing text in the user's language.
- Keep source, comments, docs, commits and PRs in English; keep human-facing terminal output short.
- Peers may leave scratch files: run `git status` before `git add` and stage explicit paths.
- In autonomous mode, the permission classifier requires an explicit user yes for `gh pr merge` and release creation even after a general grant; ask once with a clear question.

See [CONTRIBUTING.md](CONTRIBUTING.md#working-with-ai-peers) for peer sandbox limits and tooling pitfalls.
