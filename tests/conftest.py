import pytest


@pytest.fixture(autouse=True)
def _no_real_peer_binaries_by_default(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the default (non-live) test run from shelling out to whatever
    real ag/claude/codex CLI happens to be installed on the machine running
    the suite.

    `_real_binary()` in `peerhub.telemetry.quota_polling` falls back to
    `shutil.which()` when a peer binary isn't found under the portable
    `_sys` layout a test set up. On a machine that *also* has the real
    CLIs on PATH -- which is every machine actually developing peerhub,
    since this suite runs under the portable venv Python living inside a
    real `D:\\...\\_sys` tree -- that fallback silently finds and invokes
    them, turning an intended-hermetic test into a slow, non-deterministic
    one that can burn real quota (concretely: it took an "isolated"
    11-test file from 83s to 125s until this was found and fixed; see the
    isolated_workspace fixture in tests/integration/telemetry/test_quota_wiring.py
    for that specific incident).

    Tests that actually want the real CLIs are marked `slow` or `e2e`
    (`tests/integration/telemetry/test_real_quota_polling.py`,
    `tests/integration/cli/test_cli.py::test_cli_ask_real_agy_end_to_end`,
    everything under `tests/e2e/`) and are excluded from the default run
    by `addopts` in pyproject.toml; this fixture leaves those alone so
    `pytest -m slow` / `pytest -m e2e` still exercise the real thing.
    `tests/e2e` also runs the CLI in a *subprocess*
    (`sys.executable -m peerhub.cli ...`), which this in-process monkeypatch
    cannot reach anyway.

    This only neutralizes the PATH-fallback half of the problem. A test
    whose `--workspace` has no `_sys` of its own can still resolve to the
    real portable `_sys` through `resolve_sys_dir()`'s separate
    sys.executable-parent-walk fallback (which finds a real, *portable-path*
    claude.cmd/codex.cmd directly, without ever calling `shutil.which`) --
    that class of leak has to be closed per-test by isolating `_sys` or
    mocking `_refresh_usage_projections`, as the other tests here already do.
    """
    if any(request.node.get_closest_marker(m) for m in ("slow", "e2e", "live")):  # live: M1 Wave 8 canary (opt-in)
        return
    monkeypatch.setattr(
        "peerhub.telemetry.quota_polling.shutil.which", lambda name: None, raising=False
    )
