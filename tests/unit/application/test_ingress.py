"""Ingress contract (R4 section 2.1): exactly one source, every violation -> InputContractError."""
import pytest

from peerhub.application.ingress import InputContractError, resolve_prompt_input


def test_inline_text_is_returned_byte_for_byte():
    text = "  keep\tmy whitespace\r\nand trailing newline\n"
    assert resolve_prompt_input(text, None) == text


def test_query_file_text_is_returned_exactly_and_the_file_is_untouched(tmp_path):
    f = tmp_path / "q.txt"
    f.write_bytes("한글 질문\r\n둘째 줄\n".encode("utf-8"))
    before = f.read_bytes()
    assert resolve_prompt_input(None, str(f)) == "한글 질문\r\n둘째 줄\n"
    assert f.read_bytes() == before and f.exists()


@pytest.mark.parametrize("case,args,needle", [
    ("zero sources", (None, None), "missing input"),
    ("empty text", ("", None), "empty input"),
    ("whitespace only", ("  \n\t", None), "empty input"),
    ("stdin selector", (None, "-"), "unsupported selector"),
    ("stdin word", (None, "STDIN"), "unsupported selector"),
])
def test_violations_without_files(case, args, needle):
    with pytest.raises(InputContractError, match=needle):
        resolve_prompt_input(*args)


def test_multiple_sources_are_ambiguous(tmp_path):
    f = tmp_path / "q.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(InputContractError, match="ambiguous input"):
        resolve_prompt_input("inline", str(f))


def test_file_violations(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    with pytest.raises(InputContractError, match="empty input"):
        resolve_prompt_input(None, str(empty))
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(InputContractError, match="not valid UTF-8"):
        resolve_prompt_input(None, str(bad))
    with pytest.raises(InputContractError, match="directory"):
        resolve_prompt_input(None, str(tmp_path))
    with pytest.raises(InputContractError, match="cannot read"):
        resolve_prompt_input(None, str(tmp_path / "missing.txt"))


def test_no_size_rejection_at_ingress(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 5_000_000, encoding="utf-8")
    assert len(resolve_prompt_input(None, str(big))) == 5_000_000
