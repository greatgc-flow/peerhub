"""Test-only fakes (FIXTURES_FAKES_HARNESS.md): FakeRuntimeTarget, CrashInjector. No real CLI/provider is ever started."""
from tests.m1.fakes.crash import CrashInjected, CrashInjector
from tests.m1.fakes.runtime import FakeRuntimeTarget

__all__ = ["CrashInjected", "CrashInjector", "FakeRuntimeTarget"]
