"""Guard Python 3.11 syntax locally (#342): PEP 701 quote reuse inside an f-string replacement field parses on 3.12+ but is a
SyntaxError on 3.11 (CI). Tokenize every tracked *.py and fail on a nested string that reuses an enclosing f-string's quote."""
from __future__ import annotations

import io
import subprocess
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_HAS_FSTRING_TOKENS = hasattr(tokenize, "FSTRING_START")


def _quote(text: str) -> str:
    body = text.lstrip("rRbBuUfF")
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def reused_quotes(source: str) -> list[tuple[int, str]]:
    """(line, quote) for each string/f-string opened inside an f-string replacement field with an enclosing f-string's quote."""
    stack: list[str] = []
    depth: list[int] = []  # per open f-string: nesting depth of `{` replacement fields (0 = literal text)
    found: list[tuple[int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.FSTRING_START:  # pyright: ignore[reportAttributeAccessIssue]
            q = _quote(tok.string)
            if any(depth) and q in stack:
                found.append((tok.start[0], q))
            stack.append(q)
            depth.append(0)
        elif tok.type == tokenize.FSTRING_END:  # pyright: ignore[reportAttributeAccessIssue]
            stack.pop()
            depth.pop()
        elif tok.type == tokenize.OP and stack and tok.string == "{":
            depth[-1] += 1
        elif tok.type == tokenize.OP and stack and tok.string == "}":
            depth[-1] -= 1
        elif tok.type == tokenize.STRING and any(depth) and _quote(tok.string) in stack:
            found.append((tok.start[0], _quote(tok.string)))
    return found


needs_312 = pytest.mark.skipif(not _HAS_FSTRING_TOKENS, reason="tokenize has no FSTRING_START (3.11 itself rejects such files)")


@needs_312
@pytest.mark.parametrize("source", [
    "x = f\"{d['k']}\"\n",
    "x = f'{d[\"k\"]}'\n",
    "x = f\"{f'{a}'}\"\n",
    "x = f'{a}' + \"b\"\n",
    "x = f\"{a:>{w}}\" + 'c'\n",
    "x = f'''{d['k']}'''\n",
    "x = f\"{{}}\" + \"y\"\n",
])
def test_detector_accepts_distinct_quotes(source):
    assert reused_quotes(source) == []


@needs_312
@pytest.mark.parametrize("source", [
    "x = f\"{d.get('k', f'{a[\"b\"]}')}\"\n",
    "x = f\"{d[\"k\"]}\"\n",
    "x = f'{d['k']}'\n",
    "x = f\"{f\"{a}\"}\"\n",
])
def test_detector_flags_reused_quotes(source):
    assert reused_quotes(source) != []


@needs_312
def test_tracked_python_files_do_not_reuse_fstring_quotes():
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    bad = []
    for name in files:
        path = ROOT / name
        if path.is_file():
            bad += [f"{name}:{line} reuses {q}" for line, q in reused_quotes(path.read_text(encoding="utf-8"))]
    assert not bad, "Python 3.11 SyntaxError (quote reused inside an f-string field):\n" + "\n".join(bad)
