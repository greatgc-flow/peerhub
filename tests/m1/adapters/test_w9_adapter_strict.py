"""W9: strict per-vendor completion parsing (incomplete / error-marked output is never TERMINAL) and exact prompt budget (no silent truncation)."""
from types import SimpleNamespace

import pytest

from peerhub.extensions.bridge import PrespawnError, RuntimeTargetError
from tests.m1.adapters.conftest import calls
from tests.m1.adapters.test_t1_adapters import bridge_cycle, drain, rec
from tests.m1.bridge_helpers import offset_row, responses

pytestmark = [pytest.mark.integration, pytest.mark.bridge, pytest.mark.security]
KINDS = ["cc", "cx", "ag"]


@pytest.mark.parametrize("kind", KINDS)
def test_complete_output_is_terminal_positive_control(tmp_path, mk, kind):
    h, r, res = bridge_cycle(tmp_path, mk(kind))
    assert res.status == "delivered" and res.certainty == "TERMINAL" and len(responses(h)) == 1 and offset_row(h)[0] == r.position


@pytest.mark.parametrize("mode", ["incomplete", "error_marked", "garbage"])
@pytest.mark.parametrize("kind", KINDS)
def test_incomplete_or_error_marked_output_is_uncertain_with_no_response_and_no_offset(tmp_path, mk, kind, mode):
    h, r, res = bridge_cycle(tmp_path, mk(kind, mode))
    assert res.status == "uncertain" and res.certainty != "TERMINAL"
    assert responses(h) == [] and offset_row(h)[0] == 0
    with pytest.raises(RuntimeTargetError, match="unparseable"):
        drain(mk(kind, mode).deliver("s", rec(), []))
    assert len(calls(mk.log)) >= 1  # a process really was spawned: this is post-spawn uncertainty, not a prespawn error


@pytest.mark.parametrize("kind", ["cc", "cx"])
def test_stdin_provider_delivers_full_25k_history_within_budget(mk, kind):
    hist = "".join(f"line{i:05d}-" + "h" * 20 + "\n" for i in range(900))  # ~25.6k chars, distinct tail
    assert len(hist) > 25_000
    drain(mk(kind).deliver("s", rec("now"), [SimpleNamespace(record_id="r0", body=hist, author_peer_id="a")]))
    got = calls(mk.log)[0]["prompt"]
    assert hist in got and "line00899-" in got and got.endswith("now")


def test_ag_25k_history_within_its_argv_limit_is_delivered_whole(mk):
    hist = "".join(f"line{i:05d}-" + "h" * 20 + "\n" for i in range(900))
    drain(mk("ag").deliver("s", rec("now"), [SimpleNamespace(record_id="r0", body=hist, author_peer_id="a")]))
    assert hist in calls(mk.log)[0]["prompt"]


@pytest.mark.parametrize("kind", KINDS)
def test_prompt_over_provider_limit_fails_before_spawn_precisely(tmp_path, mk, kind):
    huge = "q" * 1_100_000  # over every provider limit
    h, r, res = bridge_cycle(tmp_path, mk(kind))  # control: bridge cycle works for this adapter
    assert res.status == "delivered"
    n = len(calls(mk.log))
    with pytest.raises(PrespawnError, match=rf"exceeding the {kind} inline limit of \d+ bytes"):
        drain(mk(kind).deliver("s", rec("now"), [SimpleNamespace(record_id="r0", body=huge, author_peer_id="a")]))
    assert len(calls(mk.log)) == n  # nothing spawned
