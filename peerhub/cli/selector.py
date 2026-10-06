"""Pure selection validation; archived v0 is restored through Git, never loaded here."""
from __future__ import annotations

from collections.abc import Mapping

SELECTOR_ENV = "PEERHUB_CLI"
REPORT_ENV = "PEERHUB_CLI_REPORT"
ALLOWED_SELECTORS = ("core",)
DEFAULT_CLI_SELECTOR = "core"


class InvalidSelectorError(ValueError):
    pass


def resolve_selector(environ: Mapping[str, str]) -> tuple[str, str]:
    raw = environ.get(SELECTOR_ENV, "")
    if not raw:
        return DEFAULT_CLI_SELECTOR, "default"
    if raw == "legacy":
        raise InvalidSelectorError("the v0 CLI is retired; restore legacy/v0-main-final to use it. Current data is not changed.")
    if raw not in ALLOWED_SELECTORS:
        raise InvalidSelectorError(f"{SELECTOR_ENV}={raw!r} is invalid; allowed values: {', '.join(ALLOWED_SELECTORS)}")
    return raw, "env"
