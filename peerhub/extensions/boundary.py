"""Errors shared by extensions that enforce filesystem trust boundaries (a neutral module: extensions never import each other)."""

from __future__ import annotations


class SecurityBoundaryError(Exception):
    """A path, link or source tree crossed a trust boundary (traversal, symlink, reparse point, escape)."""
