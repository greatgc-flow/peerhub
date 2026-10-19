import os

import pytest

from tests.communication.live.live_support import opt_in_reason


@pytest.fixture(autouse=True)
def _live_opt_in():
    reason = opt_in_reason()
    if reason:
        pytest.skip(reason)


@pytest.fixture(autouse=True)
def _record_ag_model_override(record_property):
    """An exhausted default ag pool may be replaced for a gate run; the JUnit of every case must say so (#191)."""
    model = os.environ.get("PEERHUB_LIVE_AG_MODEL")
    if model:
        record_property("live_ag_model_override", f"{model}; effort=None; packaged ag.standard model NOT exercised")
