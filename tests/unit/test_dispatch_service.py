import pytest
from unittest.mock import MagicMock
from peerhub.dispatch.service import translate_outbox_to_journal
from peerhub.core.protocol import ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND

def test_translate_outbox_to_journal():
    event1 = MagicMock()
    event1.event_kind = "DISPATCH_INTENT"
    event2 = MagicMock()
    event2.event_kind = "RUNNING"
    event3 = MagicMock()
    event3.event_kind = ATTEMPT_TERMINAL_OBSERVED_EVENT_KIND
    
    events = [event1, event2, event3]
    journal = translate_outbox_to_journal(events)
    
    assert journal == ["INTENT_PERSISTED", "SPAWNED", "EXIT"]
