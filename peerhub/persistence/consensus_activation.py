from __future__ import annotations

import sqlite3
from typing import Callable

class ActivationError(Exception):
    """Raised when consensus V2 activation is refused or invalid."""

def activate_consensus_v2(
    connection: sqlite3.Connection,
    *,
    now: int,
    fault_hook: Callable[[str], None] | None = None
) -> None:
    raise NotImplementedError("RED phase")

def read_activation_state(connection: sqlite3.Connection) -> str:
    raise NotImplementedError("RED phase")
