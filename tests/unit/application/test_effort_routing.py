"""Declared-hint routing (R4 2.8): modes, pin, missing/unknown hints, no eligible target."""
import pytest

from peerhub.application.effort_routing import EffortRoutingError, decide_profile

MAP = {"deep": ["ag.deepthink", "cc.deepthink", "cx.deepthink", "cx.effort"], "fast": ["cx.standard"]}


def call(**kw):
    base = dict(peer_kind="cx", explicit_profile=None, hint="deep", mode="opt-in",
                preference_map=MAP, is_eligible=lambda b: True)
    base.update(kw)
    return decide_profile(**base)


def test_missing_hint_leaves_selection_unchanged_in_every_mode():
    for mode in ("off", "advisory", "opt-in"):
        assert call(hint=None, mode=mode).profile_id is None
        assert call(hint=None, mode=mode, explicit_profile="cx.effort").profile_id == "cx.effort"


def test_unknown_hint_is_a_validation_error_in_every_mode():
    for mode in ("off", "advisory", "opt-in"):
        with pytest.raises(EffortRoutingError, match="unknown effort hint"):
            call(hint="nonsense", mode=mode)


def test_opt_in_picks_the_first_eligible_binding_of_the_requested_peer_in_declared_order():
    d = call()
    assert (d.profile_id, d.applied) == ("cx.deepthink", True)
    d = call(is_eligible=lambda b: b != "cx.deepthink")
    assert d.profile_id == "cx.effort"


def test_opt_in_with_no_eligible_candidate_is_an_explicit_blocker():
    with pytest.raises(EffortRoutingError, match="no eligible profile"):
        call(is_eligible=lambda b: False)
    with pytest.raises(EffortRoutingError, match="no eligible profile"):
        call(peer_kind="cc", hint="fast")  # no cc candidate declared for this hint


@pytest.mark.parametrize("mode", ["off", "advisory"])
def test_off_and_advisory_never_change_the_selection(mode):
    d = call(mode=mode)
    assert d.profile_id is None and d.applied is False
    assert (d.note is not None) == (mode == "advisory")


def test_explicit_profile_is_a_pin_and_is_never_substituted():
    d = call(explicit_profile="cx.standard")
    assert (d.profile_id, d.applied) == ("cx.standard", False)
    assert "pins" in (d.note or "")


def test_invalid_mode_is_rejected():
    with pytest.raises(EffortRoutingError):
        call(mode="auto")
