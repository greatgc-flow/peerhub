import json
from pathlib import Path

import peerhub.cli as cli_module
from peerhub.cli import main
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.runtime import create_runtime
from tests.integration.conftest import FakeClock, FakeIdSource


def test_diag_domains_and_default_output(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    # This test is about the GOVERNED DOMAINS section, not quota telemetry.
    # Without this, `--workspace tmp_path` has no `_sys` of its own, so
    # `_refresh_usage_projections` resolves to the real portable `_sys`
    # (via resolve_sys_dir()'s sys.executable-parent-walk fallback) and
    # actually polls the real claude/codex CLIs on this machine -- ~11s/call
    # instead of near-instant. See tests/conftest.py for the general version
    # of this problem; that fixture doesn't cover this specific path because
    # the real claude.cmd/codex.cmd are found by a direct portable-path
    # `.exists()` check, not by the `shutil.which` fallback it neutralizes.
    monkeypatch.setattr(cli_module, "_refresh_usage_projections", lambda *a, **k: [])

    base = ["--workspace", str(tmp_path), "--no-color"]

    # Populate governance data BEFORE any `diag` call: `_run_diag` always
    # touches the same workspace DB (via `_refresh_usage_projections`,
    # even without `--domains`), auto-detecting whatever workspace_home_id
    # is already persisted there. Creating the DB here first, then letting
    # every later call auto-detect it, avoids a real
    # WorkspaceIdentityMismatchError that a manually-forced identity would
    # hit against a DB `diag` already initialized with its own detected one.
    paths = PathLayout.for_workspace(tmp_path)
    with create_runtime(RuntimeContext("diag-home", paths, FakeClock(), FakeIdSource())) as runtime:
        runtime.consensus_service.propose(round_id="diag-round", title="t", question="q", body="b", proposer_id="p", required_participants=("p", "q"), eligible_participants=("p", "q"), risk="normal", source_hash="h")
        runtime.task_service.create(task_id="diag-task", summary="s", spec="x", creator_id="p")
        runtime.lesson_service.propose(lesson_id="diag-lesson", title="t", rule="r", category="c", severity="low", proposer_id="p", affected_peers=())
        runtime.lesson_service.approve("diag-lesson", approved_by_actor_id="p")
        runtime.lesson_service.record_enforcement_result(
            "diag-lesson", artifact_id="test-artifact", artifact_uri="test://fixture",
            passed=True, actor_id="p",
        )
        runtime.lesson_service.activate("diag-lesson", actor_id="p")

    assert main(["diag", *base]) == 0
    default = capsys.readouterr().out
    assert "GOVERNED DOMAINS" not in default

    assert main(["diag", *base, "--domains", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {row["target_id"] for row in payload["domains"]["consensus"]} == {"diag-round"}
    assert {row["target_id"] for row in payload["domains"]["tasks"]} == {"diag-task"}
    assert {row["target_id"] for row in payload["domains"]["lessons"]} == {"lesson:diag-lesson"}


def test_diag_domains_degrades_when_governance_collection_fails(tmp_path: Path, capsys, monkeypatch) -> None:
    # See the comment in test_diag_domains_and_default_output above: without
    # this, diag polls the real claude/codex CLIs on this machine.
    monkeypatch.setattr(cli_module, "_refresh_usage_projections", lambda *a, **k: [])

    paths = PathLayout.for_workspace(tmp_path)
    with create_runtime(
        RuntimeContext("diag-home", paths, FakeClock(), FakeIdSource())
    ):
        pass

    def fail(*args, **kwargs):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(cli_module, "create_read_runtime", fail)
    assert main(["diag", "--workspace", str(tmp_path), "--domains"]) == 0
    output = capsys.readouterr().out
    assert "governance state unavailable" in output
