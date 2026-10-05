"""PeerHub M2: Durable Work Continuity Foundation.

Core invariant:
- Core (peerhub.m1) never imports peerhub.m2.
- M2 sits strictly on top of Core Record and Stream invariants.
"""

from __future__ import annotations

__version__ = "0.1.0"
