"""Single-key input for a human monitor on a terminal."""
from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
import math
import os
import sys
from time import monotonic, sleep

# The CLI accepts every finite positive float for --interval-seconds.
MIN_INTERVAL = math.nextafter(0.0, math.inf)
MAX_INTERVAL = sys.float_info.max
WATCH_KEYS = {"r": "refresh", "\r": "refresh", "\n": "refresh", "q": "quit",
              "+": "longer", "-": "shorter", "i": "inspect"}
WATCH_HINT = "r/Enter refresh | q quit | +/- interval {interval:g}s | i details & exit"
ReadKey = Callable[[float], str | None]


def adjusted_interval(seconds: float, action: str) -> float:
    return min(MAX_INTERVAL, seconds * 2) if action == "longer" else max(MIN_INTERVAL, seconds / 2)


if sys.platform == "win32":
    import msvcrt

    def _windows_key(timeout: float) -> str | None:
        deadline = monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\x00", "\xe0"):  # Function and arrow keys have a second scan code.
                    msvcrt.getwch()
                    continue
                if key == "\x03":
                    raise KeyboardInterrupt
                return key
            remaining = deadline - monotonic()
            if remaining <= 0:
                return None
            sleep(min(.05, remaining))


@contextmanager
def watch_input(enabled: bool) -> Generator[ReadKey | None, None, None]:
    if not enabled or sys.stdin is None or not sys.stdin.isatty():
        yield None
        return
    if sys.platform == "win32":
        yield _windows_key
        return

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)

    def read_key(timeout: float) -> str | None:
        if select.select([fd], [], [], timeout)[0]:
            key = os.read(fd, 1)
            if not key:
                raise KeyboardInterrupt  # The controlling terminal closed.
            return key.decode("ascii", "ignore")
        return None

    try:
        tty.setcbreak(fd, termios.TCSANOW)
        yield read_key
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original)
