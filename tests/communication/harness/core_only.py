"""Core-only launcher (E2E-009): runs the real M1 CLI in a process where every `peerhub.extensions*` import is refused."""
from __future__ import annotations

import importlib.abc
import json
import sys


class ExtensionsDisabled(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "peerhub.extensions" or name.startswith("peerhub.extensions."):
            raise ImportError(f"first-party extension disabled: {name}")
        return None


if __name__ == "__main__":
    sys.meta_path.insert(0, ExtensionsDisabled())
    from peerhub.cli.app import main

    rc = main(sys.argv[1:])
    print("LOADED_EXTENSIONS:" + json.dumps([m for m in sys.modules if m.startswith("peerhub.extensions")]), file=sys.stderr)
    sys.exit(rc)
