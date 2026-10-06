"""Test-only fakes (FIXTURES_FAKES_HARNESS.md): FakeRuntimeTarget, CrashInjector. No real CLI/provider is ever started."""
from tests.communication.fakes.crash import CrashInjected, CrashInjector
from tests.communication.fakes.runtime import FakeRuntimeTarget

__all__ = ["CrashInjected", "CrashInjector", "FakeRuntimeTarget"]
