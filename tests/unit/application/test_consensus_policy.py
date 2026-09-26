from pathlib import Path

import pytest

from peerhub.application.consensus_policy import ConsensusPolicyProvider, required_votes_for
from peerhub.governance.policy_snapshot import ConfigurationError


def provider(tmp_path: Path, workspace: str | None = None, global_: str | None = None):
    ws, gl = tmp_path / "ws.toml", tmp_path / "gl.toml"
    if workspace is not None:
        ws.write_text(workspace, encoding="utf-8")
    if global_ is not None:
        gl.write_text(global_, encoding="utf-8")
    return ConsensusPolicyProvider(ws, gl)


def test_package_defaults_drive_the_round_config(tmp_path):
    cfg = provider(tmp_path).round_config("consensus.round.propose", "normal", 3, 3)
    assert cfg["final_call_rule"] == "tier_0_or_high_risk_or_dissent"
    assert cfg["formula"] == "all_required" and cfg["required_votes"] == 3
    assert cfg["deadlines"] == {"voting": 1800, "escalation": 1800}
    assert cfg["escalation_paths"] == ["human-tier-0"]


def test_workspace_layer_overrides_the_defaults(tmp_path):
    cfg = provider(
        tmp_path,
        workspace='[consensus]\nquorum_formula = "majority"\nfinal_call_rule = "always"\n'
                  'timeout_seconds = 60\nescalation_target = "ops"\n',
    ).round_config("consensus.round.propose", "normal", 5, 5)
    assert cfg["final_call_rule"] == "always"
    assert cfg["required_votes"] == 3  # majority of 5
    assert cfg["deadlines"]["voting"] == 60
    assert cfg["escalation_paths"] == ["ops"]


def test_proposals_are_always_strict_unanimous_of_the_electorate(tmp_path):
    cfg = provider(
        tmp_path, workspace='[consensus]\nquorum_formula = "majority"\n'
    ).round_config("governance.proposal.create", "high", 3, 4)
    assert cfg["formula"] == "unanimous" and cfg["required_votes"] == 4


def test_invalid_layer_fails_closed(tmp_path):
    with pytest.raises(ConfigurationError):
        provider(tmp_path, workspace='[consensus]\nquorum_formula = "nonsense"\n').round_config(
            "consensus.round.propose", "normal", 3, 3
        )
    with pytest.raises(ConfigurationError):
        provider(tmp_path, workspace="not = [valid toml").round_config(
            "consensus.round.propose", "normal", 3, 3
        )


@pytest.mark.parametrize(
    "formula,required,eligible,expected",
    [("majority", 4, 4, 3), ("supermajority", 3, 3, 2), ("supermajority", 6, 6, 4),
     ("all_required", 1, 1, 2), ("unanimous", 3, 5, 5)],
)
def test_formulas_respect_the_two_agreement_floor(formula, required, eligible, expected):
    assert required_votes_for(formula, required, eligible, strict=False) == expected
