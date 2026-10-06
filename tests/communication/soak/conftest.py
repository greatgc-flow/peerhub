import pytest

from tests.communication.soak.soak_support import opt_in_reason


@pytest.fixture(autouse=True)
def _soak_opt_in(request):
    """Soak-marked (scaled) runs are opt-in; the smoke scale runs by default (tiny sanity pass of the same code path)."""
    if request.node.get_closest_marker("soak") is not None:
        reason = opt_in_reason()
        if reason:
            pytest.skip(reason)
