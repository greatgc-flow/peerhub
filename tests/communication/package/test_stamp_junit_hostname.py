"""The JUnit files are attached to public releases: stamping them must not leave a host name behind."""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from tests.communication.harness.pkg_env import REPO
from tools import release_evidence as ev

pytestmark = [pytest.mark.package, pytest.mark.evidence]

SHA = "a" * 40
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
POLICY = REPO / ev.POLICY_REL


def test_stamp_junit_blanks_hostname_of_every_suite(tmp_path):
    j = tmp_path / "hosts.xml"
    j.write_text(
        '<testsuites>'
        '<testsuite name="a" tests="1" hostname="build-box-1"><testcase classname="c" name="t1"/></testsuite>'
        '<testsuite name="b" tests="1" hostname="build-box-2"><testcase classname="c" name="t2"/></testsuite>'
        '<testsuite name="c" tests="1"><testcase classname="c" name="t3"/></testsuite>'
        '</testsuites>',
        encoding="utf-8",
    )
    ev.stamp_junit(j, commit=SHA, gate="G0", repo_root=REPO, policy_path=POLICY, at=NOW)
    suites = list(ET.parse(j).getroot().iter("testsuite"))
    assert len(suites) == 3
    assert [s.get("hostname") for s in suites] == ["", "", ""]
    assert "build-box" not in j.read_text(encoding="utf-8")


def test_stamp_junit_blanks_hostname_of_a_single_testsuite_root(tmp_path):
    j = tmp_path / "one.xml"
    j.write_text('<testsuite name="a" tests="1" hostname="build-box-1"><testcase classname="c" name="t1"/></testsuite>',
                 encoding="utf-8")
    ev.stamp_junit(j, commit=SHA, gate="G0", repo_root=REPO, policy_path=POLICY, at=NOW)
    assert ET.parse(j).getroot().get("hostname") == ""
