"""CLI provider specifications and executable lookup."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from peerhub.extensions.adapters import ag, cc, common, cx

_WIN_EXT = {"claude": ["claude.cmd", "claude.exe"], "codex": ["codex.cmd", "codex.exe"], "agy": ["agy.exe", "agy.cmd"]}

SPECS: dict[str, common.ProviderSpec] = {s.kind: s for s in (cc.CcSpec(), cx.CxSpec(), ag.AgSpec())}


def resolve_binary(spec: common.ProviderSpec) -> list[str] | None:
    names = _WIN_EXT[spec.binary] if sys.platform == "win32" else [spec.binary]
    for n in names:
        p = shutil.which(n)
        if p:
            if Path(p).suffix.lower() in (".cmd", ".bat"):
                from peerhub.extensions.binary_resolution import resolve_direct_binary
                direct = resolve_direct_binary(Path(p))
                if direct:
                    return direct
            return [p]
    return None
