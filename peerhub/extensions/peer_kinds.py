"""Provider-kind aliases shared by the Session Bridge entry (`ask`) and Observation collection.

A neutral, dependency-free module: extensions must not import each other (the composition root is `peerhub/cli`), so a
vocabulary both need lives here.
"""

from __future__ import annotations

ALIASES = {"codex": "cx", "cx": "cx", "claude": "cc", "cc": "cc", "agy": "ag", "ag": "ag"}
