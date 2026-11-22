"""The offline exit-code lesson reflects the CLI's exit statuses and output streams."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from peerhub.cli.app import main
from peerhub.cli.lessons import lesson_by_id
from peerhub.cli.recovery import REGISTRY, RESULT_STATUS_REGISTRY
from peerhub.extensions import ask, batch, diag_quota
from peerhub.extensions.diag import DiagnosticReport, ReadonlyDiag

pytestmark = [pytest.mark.unit]
TOPIC = "exit-codes"
MAX_TOPIC_BYTES = 1800


def _topic_text() -> str:
    (lesson,) = lesson_by_id(TOPIC)
    return lesson["fix"]


def test_exit_codes_topic_is_searchable_and_fits_the_output_budget(capsys):
    assert main(["explain", TOPIC]) == 0
    output = capsys.readouterr()
    assert output.out.startswith(f"{TOPIC}:") and not output.err
    assert len(output.out.encode("utf-8")) < MAX_TOPIC_BYTES
    assert main(["explain", "exit", "code", "error", "stderr", "json", "--json"]) == 0
    (lesson,) = json.loads(capsys.readouterr().out)["lessons"]
    assert lesson["id"] == TOPIC
    assert "guide_order" not in lesson
    assert main(["guide"]) == 0
    assert TOPIC not in capsys.readouterr().out


def test_exit_codes_topic_is_available_with_extensions_disabled():
    result = subprocess.run(
        [sys.executable, "-m", "tests.communication.harness.core_only", "explain", TOPIC],
        capture_output=True, text=True, timeout=30, check=True,
    )
    assert result.stdout.startswith(f"{TOPIC}:")
    assert "LOADED_EXTENSIONS:[]" in result.stderr


def test_exit_codes_topic_derives_the_diag_status_mapping():
    meanings = {
        "OK": "OK",
        "FAILED": "a storage/read fault",
        "UNAVAILABLE": "an unavailable store",
        "SCHEMA_VERSION": "an unsupported schema version",
    }
    diag_text = _topic_text().split("Diag ", 1)[1].split(". ", 1)[0]
    assert "health, quota, readiness, running, --plan and integrity" in diag_text
    assert meanings.keys() == diag_quota.EXIT_BY_STATUS.keys()
    for status, code in diag_quota.EXIT_BY_STATUS.items():
        assert f"{code} for {meanings[status]}" in diag_text


def test_exit_codes_topic_distinguishes_missing_alternate_observation_store(monkeypatch, capsys):
    # Use an existing path for the healthy main store and stub its read-only snapshot.
    existing = Path(__file__).resolve()
    missing = existing.with_suffix(".missing.db")
    monkeypatch.setattr(ReadonlyDiag, "render", lambda *a, **kw: DiagnosticReport("OK", 0, {}, {}))

    def run(action):
        code = main(["--db", str(existing), "diag", action, "--observation-db", str(missing), "--json"])
        return code, json.loads(capsys.readouterr().out)["status"]

    health_code, health_status = run("health")
    quota_code, quota_status = run("quota")
    assert health_code != quota_code
    assert f"health {health_code} ({health_status}) and quota {quota_code} ({quota_status})" in _topic_text()


def test_exit_codes_topic_distinguishes_empty_terminal_reply(monkeypatch, capsys):
    monkeypatch.setattr(ask, "ask", lambda *a, **kw: {
        "status": "delivered", "certainty": "TERMINAL", "response": "",
    })
    ask_code = main(["--db", __file__, "ask", "cx", "hello", "--json"])
    status, reason = batch.classify_worker(ask_code, capsys.readouterr().out)
    batch_code = batch.exit_code({"tasks": [{"status": status}]})
    assert reason == "empty_response" and ask_code != batch_code
    assert f"exits {ask_code} in ask and {batch_code} in batch ({status})" in _topic_text()


def test_exit_codes_topic_covers_ask_and_batch_errors(capsys):
    text = _topic_text()
    assert f"{RESULT_STATUS_REGISTRY['uncertain'].exit_code} for uncertainty" in text
    assert f"{REGISTRY['ask_idempotency_conflict'].exit_code} for syntax, invalid selectors or idempotency conflict" in text
    assert f"{REGISTRY['ask_cas_conflict'].exit_code} for a compare-and-swap conflict" in text
    assert f"{REGISTRY['invalid_manifest'].exit_code} for an invalid manifest or unsuccessful tasks" in text

    invalid_ask = ["ask", "cx", "hello", "--timeout-seconds", "bad"]
    invalid_batch = ["batch", "run", __file__, "--batch-id", "bad-manifest", "--max-parallel", "1"]
    for argv, code in (
        (invalid_ask, RESULT_STATUS_REGISTRY["session_unavailable"].exit_code),
        (invalid_batch, REGISTRY["invalid_manifest"].exit_code),
    ):
        assert main(["--db", __file__, *argv, "--json"]) == code
        output = capsys.readouterr()
        error = json.loads(output.out)["error"]
        assert error["message"] and output.err.startswith(f"[{error['code']}] ")
        assert "; next: " in output.err
    assert "stderr as [code] message; next: ..." in text
    assert "code and message on stdout" in text
