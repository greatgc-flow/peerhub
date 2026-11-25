"""CLI contracts for narrowing stored diagnostic evidence."""

import json
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.core.load_limits import ResourceSample, load_snapshot
from peerhub.extensions.diag import ReadonlyDiag
from tests.communication.diag.test_diag_plan_339 import HOUR, _item, _report


MAX_AGE = 10
SELECTED = "diag-selected.db"
ALTERNATE = "diag-observations.db"


def _items(data):
    return [item for group in data["pools"] for item in group["items"]]


@pytest.fixture
def diag_cli(monkeypatch, capsys):
    def quota(peer, pool, age):
        return dict(_item(peer, pool, .5, span=HOUR, elapsed=HOUR / 2, age=age), source=pool)

    current = quota("cx", "X-5H", MAX_AGE)
    old = quota("cx", "X-7D", MAX_AGE + .25)
    future = quota("cc", "C-5H", -1)
    future.update(state="UNKNOWN", age_seconds=None)
    old_rate = quota("cc", "C-7D", MAX_AGE + 1)
    old_rate["kind"] = "rate_limit"
    credit = quota("cx", "credit", MAX_AGE + 1)
    credit["kind"] = "reset_credit"
    exact = quota("cx-lane", "XL-5H", MAX_AGE)
    alternate = quota("ag", "G-5H", MAX_AGE)
    primary_report = _report(current, old, future, old_rate, credit, exact)
    primary_report.sections["resource_pools"].data["pools"] = [
        {"resource_pool_id": old["resource_pool_ref"], "kind": "QUOTA"},
        {"resource_pool_id": "unused", "kind": "QUOTA", "provider": "fixture"},
    ]
    reports = {SELECTED: primary_report, ALTERNATE: _report(alternate)}
    reads = []
    load_reads = []
    load = load_snapshot({"cx": 1, "cc": 1}, ResourceSample(), {"X-5H": 1, "C-5H": 1})

    def read(reader, sections, **kwargs):
        db = Path(reader.db_path).name
        reads.append((db, kwargs.get("include_quota_samples", False)))
        assert sections == ["resource_pools", "observations"]
        return reports[db]

    def sample_load(db, **kwargs):
        load_reads.append(Path(db).name)
        return load

    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: p.name in reports or real_is_file(p))
    monkeypatch.setattr(ReadonlyDiag, "render", read)
    monkeypatch.setattr("peerhub.extensions.diag_running.sample_load", sample_load)
    monkeypatch.setenv("COLUMNS", "240")

    def run(*args, db=SELECTED, code=0):
        assert main(["--db", db, "diag", *args]) == code
        output = capsys.readouterr().out
        return json.loads(output) if "--json" in args else output

    return run, reads, load_reads


def test_quota_age_is_inclusive_and_counts_only_older_matching_observations(diag_cli):
    run, _, _ = diag_cli
    unfiltered = run("quota", "--json")
    assert "hidden_count" not in unfiltered
    age_filter = ("--max-age-seconds", str(MAX_AGE))
    filtered = run("quota", "--json", *age_filter)
    assert set(filtered) == {*unfiltered, "hidden_count"}
    assert filtered["filters"] == unfiltered["filters"]
    assert filtered["hidden_count"] == sum(
        item["age_seconds"] is not None and item["age_seconds"] > MAX_AGE
        for item in _items(unfiltered)
    )
    assert _items(filtered) == [
        item for item in _items(unfiltered)
        if item["subject_ref"] is None or item["age_seconds"] == MAX_AGE
    ]
    assert filtered["reset_credits"] == unfiltered["reset_credits"]
    peer_filtered = run("quota", "--json", "--peer", "cx", *age_filter)
    assert peer_filtered["hidden_count"] == 1
    header = next(line for line in run("quota", *age_filter).splitlines() if line.startswith("quota/rate-limit"))
    assert f"hidden: {filtered['hidden_count']} older than {MAX_AGE}s" in header
    assert "hidden:" not in run("quota")


@pytest.mark.parametrize("value", ["-1", "invalid"])
def test_quota_rejects_invalid_age(value, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--db", SELECTED, "diag", "quota", "--max-age-seconds", value])
    assert exc.value.code == 2
    assert "--max-age-seconds" in capsys.readouterr().err


def test_plan_peer_filters_rows_and_profiles_but_keeps_global_load(diag_cli):
    run, _, _ = diag_cli
    full = run("--plan", "--json")
    peers = ("cx", "cc")
    flags = tuple(arg for peer in peers for arg in ("--peer", peer))
    filtered = run("--plan", *flags, "--json")
    assert filtered["pools"] == [row for row in full["pools"] if row["peer"] in peers]
    assert {row["peer"] for row in filtered["pools"]} == set(peers)
    assert filtered["load"] == full["load"]

    text = run("--plan", "--peer", "cx")
    assert "cx-lane" not in text
    table = [line.split()[0] for line in text.splitlines() if line.startswith(("cx ", "cc "))]
    assert table and set(table) == {"cx"}
    footer = next(line for line in text.splitlines() if line.startswith("profiles:"))
    assert "X-5H" in footer and "C-5H" not in footer
    assert text.splitlines()[-1] == run("--plan").splitlines()[-1]
    assert run("--plan", "--peer", "c", "--json")["pools"] == []


def test_plan_observation_db_switches_observations_and_keeps_selected_load(diag_cli):
    run, reads, load_reads = diag_cli
    baseline = run("--plan", "--json")
    flags = ("--plan", "--observation-db", ALTERNATE)
    alternate = run(*flags, "--json")
    assert [row["peer"] for row in alternate["pools"]] == ["ag"]
    assert alternate["store_selection"] == baseline["store_selection"]
    assert alternate["load"] == baseline["load"]
    assert reads == [(SELECTED, True), (ALTERNATE, True)]
    assert load_reads == [SELECTED, SELECTED]
    assert "in flight C-5H 1, X-5H 1" in run(*flags).splitlines()[-1]
    assert run(*flags, "--peer", "cx", "--json")["pools"] == []
    assert run(*flags, "--json", db="missing-selected.db")["pools"] == alternate["pools"]
    assert load_reads[-1] == "missing-selected.db"


def test_plan_missing_observation_db_is_unavailable(diag_cli):
    run, reads, load_reads = diag_cli
    missing = Path("missing-observations.db").resolve()
    data = run("--plan", "--observation-db", str(missing), "--json", code=5)
    assert data["status"] == "UNAVAILABLE" and str(missing) in data["error"]
    assert not reads and not load_reads and not missing.exists()


@pytest.mark.parametrize("flag,value", [("--peer", "cx"), ("--observation-db", ALTERNATE)])
@pytest.mark.parametrize("action", [(), ("quota",)])
def test_plan_only_options_require_plan(flag, value, action, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--db", SELECTED, "diag", flag, value, *action])
    assert exc.value.code == 2
    assert "--plan" in capsys.readouterr().err
