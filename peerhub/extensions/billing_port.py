"""Lazy access to the optional billing consumer."""
from __future__ import annotations


def billing_consumer():
    try:
        import peerhub.extensions.billing as billing
    except ImportError as exc:
        if exc.name not in (None, "peerhub.extensions.billing"):
            raise
        return None
    return billing
