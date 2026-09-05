"""Feature-independent transactional state-store ports."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

# TypeVar's `default=` parameter (PEP 696) is stdlib-only from Python 3.13;
# this project's `requires-python = ">=3.11"` needs the typing_extensions
# backport, which is safe to use unconditionally on 3.13+ too.
from typing_extensions import TypeVar


@runtime_checkable
class UnitOfWork(Protocol):
    """One explicit transaction over an authoritative state store."""

    def __enter__(self) -> Self:
        """Begin the transaction and return this unit of work."""

        ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back any transaction not explicitly committed."""

        ...

    def commit(self) -> None:
        """Atomically commit every mutation in this unit of work."""

        ...

    def rollback(self) -> None:
        """Roll back every mutation in this unit of work."""

        ...


UnitOfWorkT = TypeVar(
    "UnitOfWorkT",
    bound=UnitOfWork,
    covariant=True,
)


@runtime_checkable
class ReadUnitOfWork(Protocol):
    """One read-only view over an authoritative state store."""

    def __enter__(self) -> Self:
        """Begin the read-only view and return this unit of work."""

        ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the read-only view."""

        ...

    def close(self) -> None:
        """Explicitly close the read-only view."""

        ...


ReadUnitOfWorkT = TypeVar(
    "ReadUnitOfWorkT",
    bound=ReadUnitOfWork,
    covariant=True,
    default=ReadUnitOfWork,
)


@runtime_checkable
class ReadStateStore(Protocol[ReadUnitOfWorkT]):
    """A factory for isolated read-only units of work."""

    def read_unit_of_work(self) -> ReadUnitOfWorkT:
        """Return a new, not-yet-entered read-only unit of work."""

        ...


@runtime_checkable
class StateStore(Protocol[UnitOfWorkT, ReadUnitOfWorkT]):
    """A factory for isolated units of work."""

    def initialize(self) -> None:
        """Initialize and validate the backing store."""

        ...

    def unit_of_work(self) -> UnitOfWorkT:
        """Return a new, not-yet-entered unit of work."""

        ...

    def read_unit_of_work(self) -> ReadUnitOfWorkT:
        """Return a new, not-yet-entered read-only unit of work."""

        ...

    def close(self) -> None:
        """Release store-owned resources."""

        ...

