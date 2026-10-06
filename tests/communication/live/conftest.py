import pytest

from tests.communication.live.live_support import opt_in_reason


@pytest.fixture(autouse=True)
def _live_opt_in():
    reason = opt_in_reason()
    if reason:
        pytest.skip(reason)
