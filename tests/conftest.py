"""Default tests are hermetic; live provider access requires an explicit marker."""
from collections.abc import Callable

import pytest


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
