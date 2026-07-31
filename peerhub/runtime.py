"""Production dependency composition for PeerHub."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from .core.context import RuntimeContext
from .governance.broker import GovernanceBroker
from .persistence.sqlite import SqliteStateStore


@dataclass
class Runtime:
    """A composed PeerHub runtime and its owned infrastructure."""

    context: RuntimeContext
    state_store: SqliteStateStore
    governance_broker: GovernanceBroker

    def close(self) -> None:
        """Release resources owned by this runtime."""

        self.state_store.close()

    def __enter__(self) -> Runtime:
        """Return this runtime for use as a context manager."""

        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release runtime resources when leaving a context."""

        del exception_type, exception, traceback
        self.close()


def create_runtime(context: RuntimeContext) -> Runtime:
    """Create the Slice 1 runtime from immutable injected dependencies."""

    state_store = SqliteStateStore(
        context.paths.database_path,
        workspace_home_id=context.workspace_home_id,
    )
    state_store.initialize()

    governance_broker = GovernanceBroker(
        state_store,
        clock=context.clock,
        ids=context.ids,
    )
    return Runtime(
        context=context,
        state_store=state_store,
        governance_broker=governance_broker,
    )
