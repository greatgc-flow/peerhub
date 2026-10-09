"""Errors shared by extensions that enforce filesystem trust boundaries.

Extension imports are limited to the allowlist enforced by
tests/communication/architecture/test_arch_extension_graph.py."""

from __future__ import annotations


class SecurityBoundaryError(Exception):
    """A path, link or source tree crossed a trust boundary (traversal, symlink, reparse point, escape)."""
