"""#374 (design v4 F6): `ask_alive` fails closed; only an exact match is alive and only a missing pid is gone."""
import os

import pytest

from peerhub.extensions import caller, liveness

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
    import psutil
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
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    me = caller.detect_caller({})
    # the pid may be reused by the time we look; then the answer is unknown, never alive
    assert liveness.ask_alive(me["host"], me["ns"], proc.pid, 1.0) in (False, None)


def test_default_process_info_maps_missing_to_none(known_ns):
    import psutil
    pid = 2 ** 22 + 7
    while psutil.pid_exists(pid):
        pid += 1
    assert liveness.ask_alive(caller.host_id(), caller.pid_namespace(), pid, CREATED) is False
