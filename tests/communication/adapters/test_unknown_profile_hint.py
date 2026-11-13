"""An unknown profile names the command that lists the valid ones, for every peer (2026-11-13 profile restructure)."""
import pytest

from peerhub.extensions.adapters.profiles import resolve_profile


@pytest.mark.parametrize("peer", ["ag", "cc", "cx"])
def test_unknown_profile_error_points_to_profile_list(peer, tmp_path):
    with pytest.raises(ValueError) as excinfo:
        resolve_profile(peer, f"{peer}.terra-retired", tmp_path)
    message = str(excinfo.value)
    assert f"unknown model profile '{peer}.terra-retired'" in message
    assert "run 'peerhub profile list' to see valid profiles" in message
