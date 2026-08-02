"""Unit tests for FakePeerAdapter's Step 3 incremental decode path.

DT-02 (tests/contract/test_phase0_dp_dt_compatibility.py) is the binding
compatibility oracle; these cover edge cases DT-02 doesn't exercise.
"""
from __future__ import annotations

import pytest

from peerhub.adapters.contract import DecoderEventKind, OutputChannel
from peerhub.builtins.fake_adapter import FakePeerAdapter, _split_canonical_lines


def test_empty_stream_yields_empty_output():
    adapter = FakePeerAdapter()
    decoded = adapter.finalize_decoded_output()
    assert decoded.canonical_text == ""
    assert decoded.canonical_lines == ()


def test_single_chunk_no_split():
    adapter = FakePeerAdapter()
    events = adapter.interpret_chunk(b"hello world\n")
    assert len(events) == 1
    assert events[0].kind is DecoderEventKind.ASSISTANT_TEXT
    decoded = adapter.finalize_decoded_output()
    assert decoded.canonical_lines == ("hello world",)


def test_utf8_multibyte_split_across_three_chunks():
    # U+2713 CHECK MARK is 3 bytes (\xe2\x9c\x93); split one byte per chunk.
    adapter = FakePeerAdapter()
    adapter.interpret_chunk(b"\xe2")
    adapter.interpret_chunk(b"\x9c")
    adapter.interpret_chunk(b"\x93")
    decoded = adapter.finalize_decoded_output()
    assert decoded.canonical_text == "✓"


def test_empty_chunk_yields_no_event():
    adapter = FakePeerAdapter()
    events = adapter.interpret_chunk(b"")
    assert events == ()


def test_incomplete_utf8_sequence_at_true_end_raises():
    adapter = FakePeerAdapter()
    adapter.interpret_chunk(b"\xe2\x9c")  # truncated 3-byte sequence
    with pytest.raises(UnicodeDecodeError):
        adapter.finalize_decoded_output()


def test_finalize_is_not_reentrant():
    adapter = FakePeerAdapter()
    adapter.interpret_chunk(b"hi\n")
    adapter.finalize_decoded_output()
    with pytest.raises(RuntimeError):
        adapter.finalize_decoded_output()


def test_interpret_chunk_after_finalize_raises():
    adapter = FakePeerAdapter()
    adapter.finalize_decoded_output()
    with pytest.raises(RuntimeError):
        adapter.interpret_chunk(b"too late")


def test_two_adapter_instances_do_not_share_state():
    a = FakePeerAdapter()
    b = FakePeerAdapter()
    a.interpret_chunk(b"from a\n")
    b.interpret_chunk(b"from b\n")
    assert a.finalize_decoded_output().canonical_lines == ("from a",)
    assert b.finalize_decoded_output().canonical_lines == ("from b",)


def test_rejects_non_bytes_chunk():
    adapter = FakePeerAdapter()
    with pytest.raises(ValueError):
        adapter.interpret_chunk("not bytes")  # type: ignore[arg-type]


def test_rejects_invalid_channel():
    adapter = FakePeerAdapter()
    with pytest.raises(ValueError):
        adapter.interpret_chunk(b"hi", channel="STDOUT")  # type: ignore[arg-type]


def test_stderr_channel_recorded_in_event_payload():
    adapter = FakePeerAdapter()
    events = adapter.interpret_chunk(b"err\n", channel=OutputChannel.STDERR)
    assert events[0].payload["channel"] == "STDERR"


def test_decoded_output_accumulates_all_emitted_events():
    """Cross-review finding (cx, 2026-08-02): finalize_decoded_output used
    to discard every event interpret_chunk had already returned."""
    adapter = FakePeerAdapter()
    adapter.interpret_chunk(b"a\n")
    adapter.interpret_chunk(b"b\n")
    decoded = adapter.finalize_decoded_output()
    assert len(decoded.events) == 2
    assert all(e.kind is DecoderEventKind.ASSISTANT_TEXT for e in decoded.events)


class TestSplitCanonicalLines:
    """Cross-review finding (ag+cx, 2026-08-02): str.splitlines() is too
    broad (splits on \\v, \\f, U+2028, etc, not just CRLF/CR/LF)."""

    def test_matches_splitlines_for_dt02_case(self):
        text = "line 1\r\nline 2: ✓\n"
        assert _split_canonical_lines(text) == tuple(text.splitlines())
        assert _split_canonical_lines(text) == ("line 1", "line 2: ✓")

    def test_empty_text_yields_empty_tuple(self):
        assert _split_canonical_lines("") == ()

    def test_no_trailing_newline(self):
        assert _split_canonical_lines("only line") == ("only line",)

    def test_multiple_trailing_newlines_keep_one_empty_line(self):
        # Matches splitlines(): "a\n\n".splitlines() == ["a", ""]
        assert _split_canonical_lines("a\n\n") == ("a", "")

    def test_bare_cr_only_is_a_line_break(self):
        assert _split_canonical_lines("a\rb") == ("a", "b")

    def test_vertical_tab_is_not_a_line_break(self):
        # str.splitlines() WOULD split here; explicit CRLF/CR/LF must not.
        text = "a\vb"
        assert _split_canonical_lines(text) == ("a\vb",)
        assert tuple(text.splitlines()) == ("a", "b")  # proves the contrast

    def test_unicode_line_separator_is_not_a_line_break(self):
        # U+2028 LINE SEPARATOR -- str.splitlines() WOULD split here.
        text = "a b"
        assert _split_canonical_lines(text) == ("a b",)
        assert tuple(text.splitlines()) == ("a", "b")  # proves the contrast
