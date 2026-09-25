"""Authority fence for global state transactions."""

from typing import Protocol

from peerhub.core.errors import InvalidMutationError
from peerhub.governance.candidate import Candidate


class StaleAuthorityError(InvalidMutationError):
    """Raised when the authority fence version does not match the expected version."""
    pass


class AuthorityVersionStore(Protocol):
    def read_version(self) -> int: ...
    def increment(self) -> int: ...


class AuthorityFence:
    def __init__(self, store: AuthorityVersionStore) -> None:
        raise NotImplementedError("RED phase")

    def check(self, expected_version: int) -> None:
        raise NotImplementedError("RED phase")

    def bump_for(self, reason: str) -> None:
        raise NotImplementedError("RED phase")

    def claim(self, candidate: Candidate, effect_id: str, expected_version: int) -> str:
        raise NotImplementedError("RED phase")
