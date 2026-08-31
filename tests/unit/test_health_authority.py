"""Unit tests for explicit quarantine-authority precedence."""

from __future__ import annotations

from itertools import product

import pytest

from peerhub.health.contract import QuarantineAuthorityClass
from peerhub.health.model import dominates


_LADDER = (
    QuarantineAuthorityClass.AUTOMATIC,
    QuarantineAuthorityClass.MANUAL,
    QuarantineAuthorityClass.POLICY,
    QuarantineAuthorityClass.SECURITY,
)


@pytest.mark.parametrize(
    ("authority", "required"),
    tuple(product(_LADDER, repeat=2)),
)
def test_dominates_encodes_explicit_authority_ladder(
    authority: QuarantineAuthorityClass,
    required: QuarantineAuthorityClass,
) -> None:
    assert dominates(authority, required) is (
        _LADDER.index(authority) >= _LADDER.index(required)
    )


@pytest.mark.parametrize(
    ("authority", "required"),
    (
        ("SECURITY", QuarantineAuthorityClass.MANUAL),
        (QuarantineAuthorityClass.SECURITY, "MANUAL"),
    ),
)
def test_dominates_rejects_non_enum_inputs(
    authority: object,
    required: object,
) -> None:
    with pytest.raises(TypeError):
        dominates(authority, required)  # type: ignore[arg-type]
