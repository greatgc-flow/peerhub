"""#374 (design v4 F6): `ask_alive` fails closed; only an exact match is alive and only a missing pid is gone."""
import json
import os
import subprocess
import sys

import psutil
import pytest

from peerhub.extensions import caller, liveness, process_tree

pytestmark = pytest.mark.unit

HOST, NS, PID, CREATED = "box|linux", "pid:[4026531836]", 4242, 1_700_000_000.123456


def info(table):
    def lookup(pid):
        value = table[pid] if pid in table else None
        if isinstance(value, BaseException):
            raise value
        return value
    return lookup


def alive(host=HOST, ns=NS, pid=PID, created=CREATED, *, table=None, this_host=HOST, this_ns=NS):
    return liveness.ask_alive(host, ns, pid, created, this_host=this_host, this_ns=this_ns,
                              process_info=info({PID: CREATED} if table is None else table))


def test_alive_on_an_exact_match():
    assert alive() is True


def test_gone_when_no_process_has_the_pid():
    assert alive(table={}) is False


def test_reused_pid_within_a_millisecond_is_unknown():
    assert alive(table={PID: CREATED + 0.0005}) is None
    assert alive(table={PID: CREATED - 1e-6}) is None


def test_another_namespace_is_unknown():
    assert alive(ns="pid:[1]") is None
    assert alive(ns="pid:[1]", table={}) is None


def test_namespace_none_is_unknown(monkeypatch):
    # this_ns=None means "read this host's namespace"; pin it so a Linux host whose namespace equals NS cannot answer True
    monkeypatch.setattr(caller, "pid_namespace", lambda: None)
    assert alive(ns=None, this_ns=None) is None
    assert alive(ns=None, this_ns=None, table={}) is None
    assert alive(ns=NS, this_ns=None) is None
    assert alive(ns=NS, this_ns=None, table={}) is None


def test_another_host_is_unknown():
    assert alive(host="other|linux") is None
    assert alive(host="other|linux", table={}) is None


def test_access_denied_and_errors_are_unknown():
    assert alive(table={PID: psutil.AccessDenied(PID)}) is None
    assert alive(table={PID: RuntimeError("boom")}) is None
    assert alive(table={PID: "not a time"}) is None


@pytest.mark.parametrize("field", ["host", "ns", "pid", "created"])
def test_missing_values_are_unknown(field):
    kwargs = {field: None}
    assert alive(**kwargs) is None
    assert alive(table={}, **kwargs) is None


@pytest.mark.parametrize("pid,created", [(True, CREATED), (0, CREATED), (-1, CREATED), ("4242", CREATED),
                                         (PID, float("nan")), (PID, True), (PID, "1700000000")])
def test_malformed_values_are_unknown(pid, created):
    assert alive(pid=pid, created=created, table={pid: CREATED} if isinstance(pid, int) else {}) is None


@pytest.fixture
def known_ns(monkeypatch):
    """The default-path tests are about psutil, not about reading /proc: pin a readable namespace on every platform."""
    monkeypatch.setattr(caller, "pid_namespace", lambda: "pid:[test]")


def test_this_process_is_alive_by_default(known_ns):
    me = caller.detect_caller({})
    assert me["ask_created"] is not None and me["ns"] == "pid:[test]"
    assert liveness.ask_alive(me["host"], me["ns"], me["ask_pid"], me["ask_created"]) is True


def test_this_pid_with_another_creation_time_is_unknown_by_default(known_ns):
    me = caller.detect_caller({})
    assert liveness.ask_alive(me["host"], me["ns"], os.getpid(), me["ask_created"] + 0.0001) is None


def test_a_finished_process_is_gone_by_default(known_ns):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    me = caller.detect_caller({})
    # the pid may be reused by the time we look; then the answer is unknown, never alive
    assert liveness.ask_alive(me["host"], me["ns"], proc.pid, 1.0) in (False, None)


def test_default_process_info_maps_missing_to_none(known_ns):
    pid = 2 ** 22 + 7
    while psutil.pid_exists(pid):
        pid += 1
    assert liveness.ask_alive(caller.host_id(), caller.pid_namespace(), pid, CREATED) is False


def test_process_start_time_is_readable_until_the_process_exits():
    with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], stdin=subprocess.PIPE) as proc:
        assert process_tree.process_created(proc.pid) is not None
        proc.communicate(timeout=10)
        assert process_tree.process_created(proc.pid) is None


@pytest.mark.parametrize("table,host,descendant,expected", [
    ({PID: CREATED}, HOST, False, "alive"),
    ({PID + 1: CREATED - 1}, HOST, False, "alive"),
    ({}, HOST, False, liveness.PROCESSES_DEAD),
    ({PID: CREATED + 1, PID + 1: CREATED}, HOST, False, liveness.PROCESSES_DEAD),
    ({PID + 2: CREATED + 1}, HOST, False, liveness.ORPHAN_RUNNING),
    ({PID + 2: CREATED + 2}, HOST, False, liveness.PROCESSES_DEAD),
    ({}, HOST, True, liveness.ORPHAN_RUNNING),
    ({}, "remote|linux", False, None),
])
def test_ask_process_state_checks_both_identities_and_provider(table, host, descendant, expected, monkeypatch):
    probes = []

    def descendants(*args):
        assert host == HOST
        assert args == (PID, CREATED, PID + 2, CREATED + 1)
        return descendant

    def probe(pid):
        probes.append(pid)
        return table.get(pid)

    monkeypatch.setattr(process_tree, "provider_descendant", descendants)
    detail = json.dumps(dict(v=1, host=host, ns=NS, ask_pid=PID, ask_created=CREATED,
                             caller_pid=PID + 1, caller_created=CREATED - 1))
    provider = json.dumps(dict(provider_pid=PID + 2, provider_created=CREATED + 1))
    assert liveness.ask_process_state(detail, provider, this_host=HOST, this_ns=NS, process_info=probe) == expected
    if host != HOST:
        assert probes == []


def test_windows_orphan_parent_link_checks_when_the_child_was_born(monkeypatch):
    from unittest.mock import Mock

    child = Mock(spec=psutil.Process, pid=PID + 1)
    child.ppid.return_value = PID
    child.name.return_value = "node.exe"
    child.cmdline.return_value = ["node.exe", "npm/@anthropic-ai/claude-code/cli.js"]
    child.create_time.return_value = CREATED + 2
    child.status.return_value = psutil.STATUS_RUNNING
    child.is_running.return_value = True
    monkeypatch.setattr(process_tree.sys, "platform", "win32")
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([child]))
    monkeypatch.setattr(process_tree, "process_created", lambda pid: CREATED + 3)
    assert process_tree.provider_descendant(PID, CREATED, None, None)
    monkeypatch.setattr(process_tree, "process_created", lambda pid: CREATED + 1)
    assert not process_tree.provider_descendant(PID, CREATED, None, None)
