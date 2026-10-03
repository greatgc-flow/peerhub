from types import SimpleNamespace as NS

from peerhub.application.admission_reason import describe_admission_denial
from peerhub.core.protocol import ErrorCode


def _cand(cid, elig, why):
    return NS(candidate_id=cid, eligibility=NS(name=elig), exclusion_reason=why)


def test_denial_lists_candidate_reasons_and_code():
    route = NS(error_code=ErrorCode.ROUTE_EXHAUSTED,
               decision=NS(candidates=(_cand("cx.pro", "EXCLUDED", "health UNKNOWN"), _cand("ag.pro", "EXCLUDED", "telemetry stale"))))
    msg = describe_admission_denial(NS(route=route))
    assert "ROUTE_EXHAUSTED" in msg and "cx.pro" in msg and "health UNKNOWN" in msg and "telemetry stale" in msg
    assert "diag --fresh" in msg


def test_denial_without_route_or_candidates_is_still_informative():
    assert "no route" in describe_admission_denial(NS(route=None))
    msg = describe_admission_denial(NS(route=NS(error_code=ErrorCode.ROUTE_EXHAUSTED, decision=NS(candidates=()))))
    assert "no candidate" in msg and "ROUTE_EXHAUSTED" in msg


def test_denial_caps_candidate_list():
    cands = tuple(_cand(f"p{i}", "EXCLUDED", "x") for i in range(10))
    msg = describe_admission_denial(NS(route=NS(error_code=ErrorCode.ROUTE_EXHAUSTED, decision=NS(candidates=cands))))
    assert "+4 more" in msg
