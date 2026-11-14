"""#373 item 1: `_json_lines` splits on LF only; Unicode line separators inside a JSON string never cut an event."""
import json

import pytest

from peerhub.extensions.adapters import base

pytestmark = pytest.mark.unit

# Escapes, never raw characters: editors and tools may silently turn raw separators into line ends or spaces.
SEPARATORS = ["\u0085", "\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}"]


def test_the_separators_are_the_three_unicode_line_breaks():
    assert [ord(s) for s in SEPARATORS] == [0x85, 0x2028, 0x2029]
    assert all(len(("a" + s + "b").splitlines()) == 2 for s in SEPARATORS)


def _line(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("sep", SEPARATORS)
@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_cc_result_text_with_a_raw_separator_stays_whole(sep, eol):
    text = f"first{sep}second"
    stdout = (_line({"type": "system", "subtype": "init", "session_id": "sess-1"}) + eol
              + _line({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": "sess-1",
                       "usage": {"input_tokens": 10, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 30,
                                 "output_tokens": 5}}) + eol)
    assert sep in stdout and len(stdout.splitlines()) > 2  # str.splitlines would cut the result line
    spec = base.CcSpec()
    assert spec.parse(stdout) == text
    assert spec.parse_session_id(stdout) == "sess-1"
    assert spec.parse_usage(stdout) == {"input_tokens": 60, "cached_input_tokens": 20, "cache_write_tokens": 30,
                                        "output_tokens": 5}


@pytest.mark.parametrize("sep", SEPARATORS)
@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_cx_agent_message_with_a_raw_separator_stays_whole(sep, eol):
    text = f"alpha{sep}beta"
    stdout = (_line({"type": "thread.started", "thread_id": "thr-1"}) + eol
              + _line({"type": "item.completed", "item": {"type": "agent_message", "text": text}}) + eol
              + _line({"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}}) + eol)
    spec = base.CxSpec()
    assert spec.parse(stdout) == text
    assert spec.parse_session_id(stdout) == "thr-1"
    assert spec.parse_usage(stdout) == {"input_tokens": 7, "output_tokens": 3}


@pytest.mark.parametrize("sep", SEPARATORS)
def test_ag_flat_object_with_a_raw_separator_keeps_denied_names(sep):
    stdout = _line({"response": f"x{sep}y", "conversation_id": "conv-1",
                    "denied_actions": [{"action": "command", "display_name": f"Ba sh {sep}"}, {"action": "edit"}]})
    assert base.denied_action_names(stdout) == ["command", "edit"]


@pytest.mark.parametrize("sep", SEPARATORS)
def test_json_lines_keeps_the_object_and_strips_a_trailing_cr(sep):
    obj = {"k": f"a{sep}b"}
    assert base._json_lines(_line(obj) + "\r\n" + _line(obj) + "\r") == [obj, obj]  # pyright: ignore[reportPrivateUsage]
