"""Child-process entrypoints (spawn) for importer crash / concurrency tests."""
from __future__ import annotations

import os

CRASH_EXIT = 23


def import_crash_worker(source: str, target: str, crash_point: str, nth: int) -> None:
    """Apply the import and die hard (os._exit) the `nth` time `crash_point` fires; exit 0 if it never fires."""
    from peerhub.m1.legacy_import import LegacyImporter

    seen = {"n": 0}

    def hook(point: str) -> None:
        if point == crash_point:
            seen["n"] += 1
            if seen["n"] == nth:
                os._exit(CRASH_EXIT)

    LegacyImporter(source, target, fault_hook=hook).apply()
    os._exit(0)


def import_apply_worker(source: str, target: str, barrier, out_q) -> None:
    from peerhub.m1.legacy_import import LegacyImporter

    try:
        imp = LegacyImporter(source, target)
        barrier.wait(60)
        rep = imp.apply()
        out_q.put({"statuses": {u["unit"]: u["status"] for u in rep["units"]}, "totals": rep["totals"]})
    except BaseException as e:  # noqa: BLE001 - reported to the parent
        out_q.put({"fatal": f"{type(e).__name__}: {e}"})
