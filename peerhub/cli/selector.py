"""Entrypoint selector for the `peerhub` console script (cutover rollback switch).

`PEERHUB_CLI` chooses which CLI the `peerhub` command runs: `legacy` (the v0 hierarchy, the shipped default) or `m1`
(`peerhub.m1_cli`). The switch is environment-only, pure (no file or database access) and never touches M1 data.
"""
from __future__ import annotations

from collections.abc import Mapping

SELECTOR_ENV = "PEERHUB_CLI"
REPORT_ENV = "PEERHUB_CLI_REPORT"
ALLOWED_SELECTORS = ("legacy", "m1")
DEFAULT_CLI_SELECTOR = "m1"


class InvalidSelectorError(ValueError):
    """PEERHUB_CLI holds a value that is not one of ALLOWED_SELECTORS."""


def resolve_selector(environ: Mapping[str, str]) -> tuple[str, str]:
    """Return (selector, source) where source is `env` or `default`; unset or empty means the default."""
    raw = environ.get(SELECTOR_ENV, "")
    if raw == "":
        return DEFAULT_CLI_SELECTOR, "default"
    if raw not in ALLOWED_SELECTORS:
        raise InvalidSelectorError(f"{SELECTOR_ENV}={raw!r} is not valid; allowed values: {', '.join(ALLOWED_SELECTORS)}")
    return raw, "env"
