"""Default tests are hermetic; live provider access requires an explicit marker."""
from collections.abc import Callable

import pytest


@pytest.fixture(autouse=True)
def _no_real_peer_binaries_by_default(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if any(request.node.get_closest_marker(m) for m in ("slow", "e2e", "live")):
        return
    monkeypatch.setattr("peerhub.extensions.quota_probes.shutil.which", lambda name: None)


@pytest.fixture(autouse=True)
def _record_catalog_ids(request: pytest.FixtureRequest, record_property: Callable[[str, object], None]) -> None:
    for marker in request.node.iter_markers("catalog_id"):
        record_property("catalog_id", marker.args[0])
