"""W9 defects: importer ownership needs an exact non-null marker; classification recomputes digests from persisted fields."""
import sqlite3
from contextlib import closing

import pytest

from peerhub.core.legacy_import import LegacyImporter
from tests.communication.harness.legacy_fixture import make_legacy, normalized_state

pytestmark = [pytest.mark.migration, pytest.mark.cutover]


@pytest.fixture
def imported(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    legacy = make_legacy(src / "legacy.db")
    target = tmp_path / "core.db"
    LegacyImporter(legacy, target).apply()
    return legacy, target


def _sql(db, sql, *a):
    with closing(sqlite3.connect(db)) as c:
        c.execute(sql, a)
        c.commit()


@pytest.mark.parametrize("marker", ["null", "{}", '{"source": "consumer_offsets", "x": 1}', '{"source": "other"}', '"legacy"'])
def test_foreign_consumer_peer_with_non_exact_marker_is_conflict_and_untouched(tmp_path, imported, marker):
    legacy, target = imported
    with closing(sqlite3.connect(legacy)) as c:
        eid = c.execute("SELECT event_id FROM event_log WHERE outbox_position = 3").fetchone()[0]
        c.execute("INSERT INTO consumer_offsets (consumer_id, outbox_position, event_id, revision) VALUES ('late',3,?,1)", (eid,))
        c.commit()
    _sql(target, "INSERT INTO peers (peer_id, display_name, adapter_ref, metadata_json, created_at) VALUES ('legacy:consumer:late', 'mine', NULL, ?, 'x')",
         '{"legacy_import": %s}' % marker)
    before = normalized_state(target)
    dry = LegacyImporter(legacy, target).dry_run()
    assert dry["totals"]["offset_conflicts"] == 2 and dry["totals"]["would_write_offsets"] == 0
    assert all(c["kind"] == "foreign_peer" for u in dry["units"] for c in u["offsets"]["conflicts"])
    assert normalized_state(target) == before  # dry-run unchanged
    LegacyImporter(legacy, target).apply()
    assert normalized_state(target) == before  # no membership/offset writes for the foreign peer


def test_positive_control_exact_marker_peer_is_owned_and_gets_offsets(tmp_path, imported):
    legacy, target = imported
    with closing(sqlite3.connect(legacy)) as c:
        eid = c.execute("SELECT event_id FROM event_log WHERE outbox_position = 3").fetchone()[0]
        c.execute("INSERT INTO consumer_offsets (consumer_id, outbox_position, event_id, revision) VALUES ('late',3,?,1)", (eid,))
        c.commit()
    _sql(target, "INSERT INTO peers (peer_id, display_name, adapter_ref, metadata_json, created_at) VALUES ('legacy:consumer:late', NULL, NULL, ?, 'x')",
         '{"legacy_import": {"source": "consumer_offsets"}}')
    assert LegacyImporter(legacy, target).apply()["totals"]["offsets_written"] == 2


def test_record_with_changed_kind_or_body_but_expected_stored_digest_is_conflict_not_already_imported(tmp_path, imported):
    legacy, target = imported
    assert {u["status"] for u in LegacyImporter(legacy, target).dry_run()["units"]} == {"already_imported"}  # positive control
    _sql(target, "DROP TRIGGER records_immutable_update")  # simulate out-of-band tampering with the stored row
    _sql(target, "UPDATE records SET kind = 'evil.kind', body_json = '{\"tampered\": true}' WHERE stream_id = 'legacy:c1' AND position = 1")
    before = normalized_state(target)
    dry = LegacyImporter(legacy, target).dry_run()
    st = {u["unit"]: u["status"] for u in dry["units"]}
    assert st == {"legacy:c1": "conflict", "legacy:c2": "already_imported"}
    assert "differ" in next(u for u in dry["units"] if u["unit"] == "legacy:c1")["reason"]
    assert normalized_state(target) == before
    rep = LegacyImporter(legacy, target).apply()
    assert {u["unit"]: u["status"] for u in rep["units"]}["legacy:c1"] == "conflict"
    assert normalized_state(target) == before
