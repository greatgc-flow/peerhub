# Working in PeerHub

Start with [README.md](README.md) for commands and the doc map, and
[CONTRIBUTING.md](CONTRIBUTING.md) for maintainer and release guidance.

- Install development dependencies with `python -m pip install -e ".[dev]"`.
- Run deterministic tests with `python -m pytest -q`; run `pyright` for typing.
  Live tests spend provider quota and require explicit opt-in; see CONTRIBUTING.
- Full suite: `python -m pytest -q -n 8` (4,453 passed in 475 s, measured 2026-11-14; serial 15-17 min).
  Missing-module errors for yaml or setuptools warrant checking the dev extras in a disposable venv. Other collection or setup failures require diagnosis.
- In the cx Windows sandbox, tests that use `tmp_path` cannot run (pytest creates owner-only temp directories, also with `--basetemp`; measured 2026-11-14 on Python 3.15): run the other targeted tests, report `tmp_path` tests as NOT RUN for the lead, and never add shims or temp folders to the repo.
- Use LF for new and edited text; preserve the frozen CSV exceptions in
  `.gitattributes`.
- After any edit under `docs/m1_spec`, run
  `python docs/m1_spec/tools/seal_package.py` and include the regenerated seal.
- Register new M2/M3 extension modules in the appropriate module lists in
  `tests/continuity/test_m2_m3_exit_gates.py`. Register module classification and
  permitted imports in `tests/communication/architecture/test_arch_extension_graph.py`;
  keep both gates consistent with the extension boundaries.
