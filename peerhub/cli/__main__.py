"""Preserve ``python -m peerhub.cli`` during the CLI package migration."""

import sys

from peerhub.cli import main


sys.exit(main())
