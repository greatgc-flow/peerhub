"""enable_wal waits out the concurrent first-creator race (database is locked on the WAL conversion), nothing else."""
import sqlite3

import pytest

from peerhub.core.migrations import enable_wal


class FakeConn:
    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    def execute(self, sql):
        self.calls += 1
        assert sql == "PRAGMA journal_mode = WAL;"
        if self.errors:
            raise self.errors.pop(0)


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def _locked():
    return sqlite3.OperationalError("database is locked")


def test_positive_control_no_error_calls_once_and_never_sleeps():
    c, clk = FakeConn([]), Clock()
    enable_wal(c, _sleep=clk.sleep, _clock=clk)
    assert c.calls == 1 and clk.sleeps == []


def test_locked_is_retried_until_it_succeeds_with_bounded_backoff():
    c, clk = FakeConn([_locked(), _locked(), _locked()]), Clock()
    enable_wal(c, _sleep=clk.sleep, _clock=clk)
    assert c.calls == 4 and clk.sleeps == [0.02, 0.04, 0.08]


def test_backoff_is_capped():
    c, clk = FakeConn([_locked()] * 9), Clock()
    enable_wal(c, _sleep=clk.sleep, _clock=clk)
    assert max(clk.sleeps) == 0.25 and c.calls == 10


def test_deadline_exhaustion_raises_the_original_locked_error():
    c, clk = FakeConn([_locked()] * 1000), Clock()
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        enable_wal(c, wait_seconds=1.0, _sleep=clk.sleep, _clock=clk)
    assert clk.now >= 1.0 and c.calls < 1000


@pytest.mark.parametrize("msg", ["disk I/O error", "attempt to write a readonly database", "database or disk is full",
                                 "file is not a database"])
def test_other_operational_errors_propagate_immediately(msg):
    c, clk = FakeConn([sqlite3.OperationalError(msg)]), Clock()
    with pytest.raises(sqlite3.OperationalError, match=msg):
        enable_wal(c, _sleep=clk.sleep, _clock=clk)
    assert c.calls == 1 and clk.sleeps == []


def test_non_operational_errors_are_not_swallowed():
    c, clk = FakeConn([sqlite3.DatabaseError("malformed")]), Clock()
    with pytest.raises(sqlite3.DatabaseError):
        enable_wal(c, _sleep=clk.sleep, _clock=clk)
    assert c.calls == 1


def test_real_connection_switches_a_fresh_database_to_wal(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db")
    try:
        enable_wal(conn)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()
