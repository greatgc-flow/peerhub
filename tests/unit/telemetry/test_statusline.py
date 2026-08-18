"""Tests for peerhub's statusline formatter.

Verifies statusline output is genuinely computed from measured input
(or renders an honest default when input is absent), not from a
fabricated Engram-specific room-ID literal.
"""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.telemetry.statusline import format_statusline_ag


def test_format_statusline_ag_empty_input_has_no_room_id():
    """Empty stdin falls back to an honest default with no fabricated room ID."""
    result = format_statusline_ag("")
    assert "room-efde" not in result


def test_format_statusline_ag_invalid_json_has_no_room_id():
    """Unparseable stdin falls back to an honest default with no fabricated room ID."""
    result = format_statusline_ag("not json")
    assert "room-efde" not in result


def test_format_statusline_ag_real_input_has_no_room_id():
    """Real, valid statusline input never emits a fabricated room ID."""
    payload = {
        "model": {"display_name": "Gemini 3.1 Pro", "effort": "high"},
        "context_window": {"total_input_tokens": 50000, "context_window_size": 250000},
        "cwd": str(Path.cwd()),
        "quota": {},
    }
    result = format_statusline_ag(json.dumps(payload))
    assert "room-efde" not in result
    # The real, computed fields are still present.
    assert "ctx:50k/250k" in result


def test_format_statusline_ag_reflects_real_context_usage():
    """Two different context payloads produce two different context strings (not frozen)."""
    payload_a = {
        "context_window": {"total_input_tokens": 10000, "context_window_size": 250000},
    }
    payload_b = {
        "context_window": {"total_input_tokens": 200000, "context_window_size": 250000},
    }
    result_a = format_statusline_ag(json.dumps(payload_a))
    result_b = format_statusline_ag(json.dumps(payload_b))
    assert result_a != result_b
    assert "ctx:10k/250k" in result_a
    assert "ctx:200k/250k" in result_b
