"""Wave 9: the scheduled soak job is wired non-blocking (G6), uploads the evidence JSON and never gates publishing."""
import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WF = REPO / ".github" / "workflows"


def load(name):
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def test_soak_workflow_is_scheduled_non_blocking_and_uploads_evidence():
    wf = load("soak.yml")
    triggers = wf.get("on", wf.get(True))
    assert triggers["schedule"][0]["cron"].split() and len(triggers["schedule"][0]["cron"].split()) == 5
    assert "push" not in triggers and "pull_request" not in triggers
    job = wf["jobs"]["soak"]
    assert job["continue-on-error"] is True
    assert job["env"]["PEERHUB_SOAK"] == "1" and "PEERHUB_SOAK_EVIDENCE_DIR" in job["env"]
    runs = " ".join(s.get("run", "") for s in job["steps"])
    assert "-m soak" in runs and "tests/communication/soak" in runs
    up = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert len(up) == 1 and up[0]["if"] == "always()" and up[0]["with"]["path"].endswith("*.json")


def test_soak_is_g6_non_blocking_and_publish_does_not_depend_on_it():
    gates = {g["id"]: g for g in json.loads((REPO / "docs/m1_spec/08_LIFECYCLE/release-gates.json").read_text(encoding="utf-8"))["gates"]}
    assert gates["G6"]["blocking"] is False and gates["G6"]["on_fail"] == "OBSERVE" and gates["G6"]["phase"] == "soak"
    # positive control: a blocking gate is recognised as blocking by the same check
    assert gates["G2"]["blocking"] is True
    assert "soak" not in (WF / "publish.yml").read_text(encoding="utf-8").lower()
    for name in ("ci.yml", "publish.yml"):
        for job in load(name)["jobs"].values():
            assert "soak" not in str(job.get("needs", ""))
