"""24h reliability (B8): definitive attempts only, exact window, dedup, honest absence."""
import sqlite3

import pytest

from peerhub.persistence.sqlite import SqliteStateStore
from peerhub.telemetry.reliability import WINDOW_SECONDS, compute_reliability

AS_OF = 1_000_000


@pytest.fixture
def db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE dispatch_requests(command_id TEXT PRIMARY KEY, selected_peer_instance_id TEXT, selected_profile_id TEXT)")
    c.execute("CREATE TABLE dispatch_attempts(attempt_id TEXT, command_id TEXT, state TEXT, execution_certainty TEXT, updated_at INTEGER)")
    c.execute("INSERT INTO dispatch_requests VALUES('c1','cx','cx.standard'),('c2','cx','cx.effort'),('c3','ag','ag.standard')")
    return c


def add(db, aid, cmd, state, cert, at):
    db.execute("INSERT INTO dispatch_attempts VALUES(?,?,?,?,?)", (aid, cmd, state, cert, at))


def rel(db, inst="cx", prof="cx.standard", as_of=AS_OF):
    return compute_reliability(db, instance_id=inst, profile_id=prof, as_of=as_of)


def test_rate_is_failed_over_definitive_and_categories_are_counted(db):
    add(db, "a1", "c1", "SUCCEEDED_VERIFIED", "TERMINAL", AS_OF - 10)
    add(db, "a2", "c1", "SUCCEEDED_VERIFIED", "TERMINAL", AS_OF - 20)
    add(db, "a3", "c1", "FAILED", "TERMINAL", AS_OF - 30)
    add(db, "a4", "c1", "CANCELLED", "STARTED", AS_OF - 40)
    add(db, "a5", "c1", "FAILED_PRE_DISPATCH", "NOT_STARTED", AS_OF - 50)
    add(db, "a6", "c1", "FAILED", "NOT_STARTED", AS_OF - 60)
    add(db, "a7", "c1", "DELIVERED_UNVERIFIED", "TERMINAL", AS_OF - 70)
    add(db, "a8", "c1", "FAILED", "MAY_HAVE_STARTED", AS_OF - 80)
    add(db, "a9", "c1", "RUNNING", "STARTED", AS_OF - 5)  # still in flight: not counted
    r = rel(db)
    assert (r.succeeded, r.failed) == (2, 1)
    assert r.rate == pytest.approx(1 / 3)
    assert (r.excluded_cancelled, r.excluded_pre_admission, r.excluded_unknown) == (1, 2, 2)
    assert r.partial_coverage is True


def test_no_definitive_attempt_means_no_rate_never_zero_percent(db):
    add(db, "a1", "c1", "CANCELLED", "STARTED", AS_OF - 10)
    r = rel(db)
    assert r.definitive == 0 and r.rate is None


def test_window_is_open_closed_at_exactly_24h(db):
    add(db, "old", "c1", "FAILED", "TERMINAL", AS_OF - WINDOW_SECONDS)       # excluded (open lower bound)
    add(db, "edge", "c1", "FAILED", "TERMINAL", AS_OF - WINDOW_SECONDS + 1)  # included
    add(db, "now", "c1", "SUCCEEDED_VERIFIED", "TERMINAL", AS_OF)            # included (closed upper bound)
    add(db, "future", "c1", "FAILED", "TERMINAL", AS_OF + 1)                 # excluded
    r = rel(db)
    assert (r.succeeded, r.failed) == (1, 1)


def test_duplicate_attempt_rows_count_once_and_profiles_are_isolated(db):
    add(db, "dup", "c1", "FAILED", "TERMINAL", AS_OF - 1)
    add(db, "dup", "c1", "FAILED", "TERMINAL", AS_OF - 1)
    add(db, "other", "c2", "FAILED", "TERMINAL", AS_OF - 1)
    add(db, "agent", "c3", "FAILED", "TERMINAL", AS_OF - 1)
    assert rel(db).failed == 1
    assert rel(db, prof="cx.effort").failed == 1
    assert rel(db, inst="ag", prof="ag.standard").failed == 1


def test_the_real_schema_has_the_columns_this_query_uses(tmp_path):
    store = SqliteStateStore(tmp_path / "s.sqlite3", workspace_home_id="rel")
    store.initialize()
    c = sqlite3.connect(tmp_path / "s.sqlite3")
    attempts = {r[1] for r in c.execute("PRAGMA table_info(dispatch_attempts)")}
    requests = {r[1] for r in c.execute("PRAGMA table_info(dispatch_requests)")}
    assert {"attempt_id", "command_id", "state", "execution_certainty", "updated_at"} <= attempts
    assert {"command_id", "selected_peer_instance_id", "selected_profile_id"} <= requests
    compute_reliability(c, instance_id="cx", profile_id="cx.standard", as_of=1)  # runs on the real schema
