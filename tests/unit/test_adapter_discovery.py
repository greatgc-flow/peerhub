"""Tests for Gate 2 Lane 1: Built-in adapter discovery."""

import json
from pathlib import Path
from unittest.mock import patch
import dataclasses

import pytest
from peerhub.adapters.discovery import (
    discover_builtin_adapters,
    AdapterFoundAndReady,
    AdapterNotReady,
    AdapterNotFound,
)
from peerhub.adapters import registry
from peerhub.cli import main as cli_main

@pytest.fixture
def mock_executables(tmp_path: Path):
    """Mock executable resolution to return valid files."""
    def _mock_resolve(name):
        p = tmp_path / Path(name).name
        p.touch()
        # write some data so size > 0
        p.write_text("dummy")
        return p
        
    with patch("peerhub.adapters.discovery.resolve_executable_path", side_effect=_mock_resolve):
        yield tmp_path
        
def test_discover_builtin_adapters_found(mock_executables):
    results = discover_builtin_adapters()
    assert len(results) == 3
    for r in results:
        assert isinstance(r, AdapterFoundAndReady)
        assert r.peer_kind in ["ag", "cc", "cx"]
        assert len(r.profiles) >= 1
        
def test_discover_builtin_adapters_not_found():
    def _mock_resolve(name):
        raise registry.ExecutableNotFoundError("not found")
        
    with patch("peerhub.adapters.discovery.resolve_executable_path", side_effect=_mock_resolve):
        results = discover_builtin_adapters()
        assert len(results) == 3
        for r in results:
            assert isinstance(r, AdapterNotFound)

def test_discover_builtin_adapters_empty_file(tmp_path: Path):
    def _mock_resolve(name):
        p = tmp_path / name
        p.touch() # size 0
        return p
        
    with patch("peerhub.adapters.discovery.resolve_executable_path", side_effect=_mock_resolve):
        results = discover_builtin_adapters()
        for r in results:
            assert isinstance(r, AdapterNotReady)
            assert r.reason == "executable size is 0"

def test_discover_builtin_adapters_profile_count_fix(mock_executables):
    # Construct a mock adapter with 2+ profiles
    from peerhub.builtins.fake_adapter import FakePeerAdapter
    from peerhub.adapters.contract import ProfileDescriptor
    
    class MultiProfileAdapter(FakePeerAdapter):
        @property
        def descriptor(self):
            desc = super().descriptor
            return dataclasses.replace(desc, profiles=(
                    ProfileDescriptor(profile_id="fake.1", profile_class=desc.profiles[0].profile_class, supports_reasoning_effort=False),
                    ProfileDescriptor(profile_id="fake.2", profile_class=desc.profiles[0].profile_class, supports_reasoning_effort=False),
            ))
            
    def _mock_resolve_adapter(kind):
        if kind == "ag":
            return MultiProfileAdapter()
        return registry.resolve_peer_adapter(kind)
        
    with patch("peerhub.adapters.discovery.resolve_peer_adapter", side_effect=_mock_resolve_adapter):
        results = discover_builtin_adapters()
        
    ag_result = next(r for r in results if r.peer_kind == "ag")
    assert isinstance(ag_result, AdapterFoundAndReady)
    assert "fake.1" in ag_result.profiles
    assert "fake.2" in ag_result.profiles

def test_cli_adapter_discover_json(mock_executables, capsys):
    ret = cli_main(["adapter", "discover", "--json"])
    assert ret == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)
    
    assert "ag" in data
    assert data["ag"]["state"] == "MEASURED"
    assert "executable_path" in data["ag"]
    
def test_cli_adapter_discover_not_found_json(capsys):
    def _mock_resolve(name):
        raise registry.ExecutableNotFoundError("not found")
        
    with patch("peerhub.adapters.discovery.resolve_executable_path", side_effect=_mock_resolve):
        ret = cli_main(["adapter", "discover", "--json"])
        
    assert ret == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)
    
    assert data["ag"]["state"] == "ABSENT"
