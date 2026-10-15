"""Runtime receipt facts and config provenance in grouped monitor."""
from __future__ import annotations

import json
import sqlite3

import pytest

from peerhub.cli.app import main
from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
from tests.communication.bridge_helpers import bseed
from tests.communication.harness.bridge import BridgeHarness


pytestmark = [pytest.mark.unit, pytest.mark.diag, pytest.mark.platform_sensitive]


def test_runtime_group_separates_saved_session_facts_from_current_profile(tmp_path, monkeypatch, capsys):
    class BoundRuntime(_ProviderTelemetryRuntime):
        def session_binding(self):
            return {"profile": "cc.effort", "model": "claude-sonnet-5-5", "effort": "high", "writable": False}

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    delivery = harness.delivery_cycle("b", "s", BoundRuntime())
    assert delivery.delivery_id
    with sqlite3.connect(harness.db_path) as conn:
        receipt_json, = conn.execute(
            "SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='session_telemetry'",
            (delivery.delivery_id,),
        ).fetchone()
    receipt = json.loads(receipt_json)
    assert receipt["schema_version"] == 3
    assert receipt["adapter_version"] == "2"
    assert receipt["provider_cli_version"] is None

    # Change the declarative profile after the session was created. Its current
    # winning layer cannot be used as the historical source of the saved binding.
    config = harness.workspace / ".peerhub" / "config" / "models.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        'schema_version = 1\n[profiles."cc.effort"]\nselection_mode = "pinned"\n'
        'model = "claude-opus-5-5"\nreasoning_effort = "xhigh"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "empty-global"))
    monkeypatch.chdir(tmp_path)  # `--db` still points to the workspace store.

    code = main(["--db", str(harness.db_path), "monitor", "--snapshot", "--group", "runtime", "--json"])
    frame = json.loads(capsys.readouterr().out)
    assert code == 0
    assert frame["selected_groups"] == ["runtime"]
    (session,) = frame["groups"]["runtime"]["data"]["sessions"]

    # Versioned v3 receipt fields are public facts. A missing provider CLI version
    # stays an explicit unknown rather than being inferred from the current host.
    assert session["adapter_version"] == "2"
    assert "provider_cli_version" in session and session["provider_cli_version"] is None
    assert session["configured_binding"] == {
        "profile": "cc.effort", "model": "claude-sonnet-5-5", "effort": "high"
    }
    assert session["resolved_model"] is None

    # Proposed stable shape: saved binding provenance states only what the receipt
    # proves; current_profile is a separate, explicitly present-time config view.
    assert session["configured_binding_provenance"] == {
        "basis": "session_snapshot",
        "field_sources": {"profile": None, "model": None, "effort": None},
    }
    assert session["current_profile"] == {
        "basis": "current_config",
        "profile": "cc.effort",
        "fields": {
            "selection_mode": {"value": "pinned", "source": "workspace"},
            "model": {"value": "claude-opus-5-5", "source": "workspace"},
            "effort": {"value": "xhigh", "source": "workspace"},
        },
    }
