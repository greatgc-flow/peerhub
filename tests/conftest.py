"""Default tests are hermetic; live provider access requires an explicit marker."""
from collections.abc import Callable
from pathlib import Path
from typing import Mapping

import pytest

from peerhub.core import load_limits


@pytest.fixture(autouse=True)
def _quiet_machine_resources(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if any(request.node.get_closest_marker(mark) for mark in ("real_resources", "live", "canary")):
        return
    sample = load_limits.ResourceSample(16 * 1024**3, 10.0, 8)
    monkeypatch.setattr(load_limits, "read_resources", lambda: sample)


@pytest.fixture(autouse=True)
def _isolate_ag_transport_cache(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Give each test its own cache directory on first access."""
    from peerhub.extensions.adapters import ag_transport as ag

    cache_path = ag._transport_cache_path
    root: Path | None = None

    def isolated_cache_path(identity: tuple[str, int, int, int, int, int], env: Mapping[str, str]) -> Path:
        nonlocal root
        if root is None:
            root = tmp_path_factory.mktemp("ag-transport")
        return root / cache_path(identity, env).name

    monkeypatch.setattr(ag, "_transport_cache_path", isolated_cache_path)


@pytest.fixture(autouse=True)
def _no_real_peer_binaries_by_default(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if any(request.node.get_closest_marker(m) for m in ("slow", "e2e", "live", "canary")):
        return
    monkeypatch.setattr("peerhub.extensions.quota_probes.common.shutil.which", lambda name: None)


@pytest.fixture(autouse=True)
def _isolate_env_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PEERHUB_DB", raising=False)


@pytest.fixture(autouse=True)
def _isolate_lead_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """No test reads a real Claude config folder: the lead reader sees one empty temporary folder (#376 test 18).
    The folder is made on first use, so tests that never read leads create no temporary folder."""
    made: list[str] = []

    def empty_lead_dirs(env: object) -> list[str]:
        if not made:
            made.append(str(tmp_path_factory.mktemp("lead-dirs")))
        return list(made)

    monkeypatch.setattr("peerhub.extensions.lead_sessions.lead_dirs", empty_lead_dirs)


@pytest.fixture(autouse=True)
def _record_catalog_ids(request: pytest.FixtureRequest, record_property: Callable[[str, object], None]) -> None:
    for marker in request.node.iter_markers("catalog_id"):
        record_property("catalog_id", marker.args[0])
