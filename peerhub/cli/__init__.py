"""Command-line interface for PeerHub: the `peerhub` entrypoint dispatcher.

This module is deliberately light. It validates PEERHUB_CLI (see `peerhub.cli.selector`) BEFORE importing anything else, then imports
either `peerhub.m1_cli` (selector `m1`) or the legacy implementation in `peerhub.cli._legacy` (selector `legacy`, the default).
Legacy names stay reachable as `peerhub.cli.<name>`: reads, writes and deletes of such attributes are forwarded to the legacy module
(imported on first use), so existing `from peerhub.cli import X` and `patch("peerhub.cli.X")` keep working.
"""

import os
import sys
import types
from typing import Any

_LEGACY = "peerhub.cli._legacy"


def _load_legacy() -> types.ModuleType:
    import importlib

    return importlib.import_module(_LEGACY)


class _CliModule(types.ModuleType):
    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name == "_LEGACY":
            raise AttributeError(name)
        return getattr(_load_legacy(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        own = (name in self.__dict__ or name.startswith("__") or name in _OWN or _LEGACY not in sys.modules
               or (isinstance(value, types.ModuleType) and value.__name__.startswith("peerhub.cli.")))  # submodule bindings
        if own:
            super().__setattr__(name, value)
        else:
            setattr(sys.modules[_LEGACY], name, value)  # e.g. patch("peerhub.cli.create_runtime") must reach the legacy globals

    def __delattr__(self, name: str) -> None:
        if name not in self.__dict__ and _LEGACY in sys.modules and hasattr(sys.modules[_LEGACY], name):
            delattr(sys.modules[_LEGACY], name)
        else:
            super().__delattr__(name)


_OWN = {"main", "legacy_main", "os", "sys", "types", "Any"}
sys.modules[__name__].__class__ = _CliModule


def legacy_main(args: list[str] | None = None) -> int:
    return _load_legacy().main(args)  # type: ignore[no-any-return]


def main(args: list[str] | None = None) -> int:
    """Console entrypoint: dispatch on PEERHUB_CLI (legacy default | m1). Invalid value: exit 2, one stderr line, no side effects."""
    from peerhub.cli.selector import ALLOWED_SELECTORS, REPORT_ENV, SELECTOR_ENV, InvalidSelectorError, resolve_selector

    try:
        selector, source = resolve_selector(os.environ)
    except InvalidSelectorError:
        sys.stderr.write(f"peerhub: invalid {SELECTOR_ENV}={os.environ.get(SELECTOR_ENV, '')!r}; allowed values: {', '.join(ALLOWED_SELECTORS)}\n")
        return 2
    if os.environ.get(REPORT_ENV) == "1":
        sys.stderr.write(f"peerhub: cli selector={selector} (source: {source})\n")
    if selector == "m1":
        from peerhub.m1_cli import main as m1_main

        return m1_main(args)
    return legacy_main(args)
