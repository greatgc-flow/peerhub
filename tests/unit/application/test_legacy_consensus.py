from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import LegacyActionCall, LegacyTranslator, TranslatedCommand


def _submission() -> SubmissionMetadata:
    return SubmissionMetadata("req", "corr", "client", "cx", {}, "idem", None, None, 1)


def test_consensus_legacy_actions_translate_with_wire_params() -> None:
    translator = LegacyTranslator()
    proposal = translator.translate(LegacyActionCall("consensus-propose", {"round_id": "r1", "title": "T", "question": "Q", "body": "B", "proposer_id": "cx", "required_participants": ["cx", "ag"], "eligible_participants": ["cx", "ag"], "risk": "normal", "source_hash": "sha256:x"}), _submission())
    assert isinstance(proposal, TranslatedCommand)
    assert proposal.command.method == "consensus.round.propose"
    assert proposal.command.encode_params()["round_id"] == "r1"
    vote = translator.translate(LegacyActionCall("consensus-vote", {"round_id": "r1", "actor_id": "ag", "choice": "agree"}), _submission())
    assert isinstance(vote, TranslatedCommand)
    assert vote.command.encode_params() == {"round_id": "r1", "actor_id": "ag", "choice": "agree"}
    check = translator.translate(LegacyActionCall("consensus-check", {"round_id": "r1"}), _submission())
    assert isinstance(check, TranslatedCommand)
    assert check.command.encode_params() == {"round_id": "r1"}
