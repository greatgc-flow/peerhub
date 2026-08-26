from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import LegacyActionCall, LegacyTranslator, TranslatedCommand


def _submission() -> SubmissionMetadata:
    return SubmissionMetadata("req", "corr", "client", "cx", {}, "idem", None, None, 1)


def test_duty_legacy_actions_translate_to_fenced_wire_commands() -> None:
    translator = LegacyTranslator()
    cases = [
        ("leader-claim", {"room_id": "room", "instance_id": "i", "profile_id": "cx", "owner_principal_id": "p", "authority_epoch": 2}, "routing.leadership.claim"),
        ("leader-yield", {"lease_id": "lease", "room_id": "room", "instance_id": "i", "profile_id": "cx", "term": 1, "authority_epoch": 2}, "routing.leadership.yield"),
        ("terminal-handoff", {"current_lease_id": "old", "room_id": "room", "current_instance_id": "i", "current_profile_id": "cx", "term": 1, "authority_epoch": 2, "new_instance_id": "j", "new_profile_id": "ag", "new_owner_principal_id": "p2", "new_authority_epoch": 3}, "coordination.terminal.handoff"),
        ("terminal-heartbeat", {"lease_id": "lease", "room_id": "room", "instance_id": "i", "profile_id": "cx", "term": 1, "authority_epoch": 2}, "coordination.terminal.heartbeat"),
    ]
    for action, arguments, method in cases:
        outcome = translator.translate(LegacyActionCall(action, arguments), _submission())
        assert isinstance(outcome, TranslatedCommand)
        assert outcome.command.method == method
        assert outcome.command.encode_params()
