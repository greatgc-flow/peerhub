"""Explicit provider quota collection; no invented measurements on an unavailable source."""
import pytest

from peerhub.core.store import CoreStore
from peerhub.extensions.quota_capture import refresh_quota
from peerhub.extensions.diag_quota import quota_report

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("provider", ["cx", "cc", "ag"])
def test_provider_quota_live_persists_measured_evidence(tmp_path, provider):
    db = tmp_path / "core.db"
    CoreStore(db)
    result = refresh_quota(db, [provider], deadline_sec=30)
    assert any(o["state"] == "MEASURED" for o in result["observations"]), result
    report = quota_report(db, peer=provider)
    assert report["status"] == "OK" and report["overall"] == "OK"
