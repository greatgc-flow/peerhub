"""Authority fence for global state transactions."""

from typing import Protocol, Dict, Tuple

from peerhub.core.errors import InvalidMutationError
from peerhub.governance.candidate import Candidate


class StaleAuthorityError(InvalidMutationError):
    """Raised when the authority fence version does not match the expected version."""
    pass


class AuthorityVersionStore(Protocol):
    def read_version(self) -> int: ...
    def increment(self) -> int: ...


class AuthorityFence:
    ALLOWED_BUMP_REASONS = frozenset({
        "revocation",
        "rebinding",
        "health_transition"
    })

    def __init__(self, store: AuthorityVersionStore) -> None:
        self.store = store
        self._claims: Dict[Tuple[Candidate, str], int] = {}

    def check(self, expected_version: int) -> None:
        try:
            current_version = self.store.read_version()
        except Exception as e:
            raise StaleAuthorityError("Store error during check") from e
            
        if current_version != expected_version:
            raise StaleAuthorityError(f"Expected {expected_version}, got {current_version}")

    def bump_for(self, reason: str) -> None:
        if reason not in self.ALLOWED_BUMP_REASONS:
            raise ValueError(f"Unknown bump reason: {reason}")
        try:
            self.store.increment()
        except Exception as e:
            raise StaleAuthorityError("Store error during bump") from e

    def claim(self, candidate: Candidate, effect_id: str, expected_version: int) -> str:
        key = (candidate, effect_id)
        if key in self._claims:
            if self._claims[key] == expected_version:
                return "already_applied"
            else:
                raise StaleAuthorityError("Stale claim with mismatched version")

        self.check(expected_version)
        self._claims[key] = expected_version
        return "applied"
