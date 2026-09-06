"""PeerHub coordination engine."""

from importlib.metadata import PackageNotFoundError, version as _distribution_version

try:
    __version__ = _distribution_version("peerhub")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = ("__version__",)
