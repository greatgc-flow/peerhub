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
