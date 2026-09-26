"""Single input-ingress contract shared by ``ask`` and ``broadcast``.

Ratified in docs/design/GOVERNANCE-DISPATCH-R4-revision-ag-deepthink-2026-09-24.md section 2.1
("Ingress Semantics"): exactly one input source; every violation is an exit-2 input error before
anything is dispatched; the original query is preserved byte-for-byte (query fidelity); a borrowed
``--query-file`` is only ever READ -- never modified, moved or deleted by this layer; there is no
size rejection here (``max_inline_bytes`` only decides staging vs inline later).
"""

from __future__ import annotations

from pathlib import Path

UNSUPPORTED_SELECTORS = frozenset({"-", "stdin"})


class InputContractError(ValueError):
    """The prompt input violates the ingress contract (CLI exit 2)."""


def resolve_prompt_input(inline: str | None, query_file: str | None) -> str:
    """Return the canonical prompt text from exactly one valid source."""

    if inline is None and query_file is None:
        raise InputContractError("missing input: provide a prompt or --query-file")
    if inline is not None and query_file is not None:
        raise InputContractError("ambiguous input: provide either a prompt or --query-file, not both")

    if inline is not None:
        if not inline.strip():
            raise InputContractError("empty input: the prompt text is empty")
        return inline

    assert query_file is not None
    if query_file.strip().lower() in UNSUPPORTED_SELECTORS:
        raise InputContractError(
            f"unsupported selector {query_file!r}: only a file path is accepted by --query-file"
        )
    path = Path(query_file)
    if path.is_dir():
        raise InputContractError(f"invalid input: {query_file!r} is a directory")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise InputContractError(f"invalid input: cannot read {query_file!r}: {error}") from error
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InputContractError(f"invalid input: {query_file!r} is not valid UTF-8") from error
    if not text.strip():
        raise InputContractError(f"empty input: {query_file!r} is empty")
    return text
