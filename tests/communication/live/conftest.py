import pytest

from tests.communication.live.live_support import opt_in_reason


@pytest.fixture(autouse=True)
def _live_opt_in():
    reason = opt_in_reason()
    if reason:
        pytest.skip(reason)


@pytest.fixture(autouse=True)
def _record_ag_model_override(record_property, monkeypatch):
    """Record an override only for a case that applies it to ag selection."""
    from tests.communication.live import live_support

    resolve = live_support.ag_model_override

    def recorded(kind):
        model = resolve(kind)
        if model:
            record_property("live_ag_model_override", f"{model}; effort=None; packaged ag.standard model NOT exercised")
        return model

    monkeypatch.setattr(live_support, "ag_model_override", recorded)
