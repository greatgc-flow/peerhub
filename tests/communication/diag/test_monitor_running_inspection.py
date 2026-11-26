"""RUNNING rows identify the exact prompt record in both layouts."""
from contextlib import closing
import json
import sqlite3

import pytest

from peerhub.cli.app import main
from peerhub.cli.monitor_view import render_grouped_frame
from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions import monitor_read_model
from tests.communication.diag.test_monitor_activity import NOW, claim, db  # noqa: F401
from tests.communication.diag.test_monitor_activity_view import ask
from tests.communication.diag.test_monitor_plain_golden import _payload


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("view", ["rich", "plain"])
@pytest.mark.parametrize("width", [52, 118])
def test_running_rows_share_one_open_command(expand, view, width):
    ids = ["123456789abc", "123456789abd"]
    rows = [{**ask(), "_ask_id": "rec-" + value} for value in ids]
    output = render_grouped_frame(_payload(rows, []), expand=expand, view=view, width=width)
    assert all(output.count(value) == 1 for value in ids)
    assert output.splitlines().count("peerhub monitor open ID") == 1


@pytest.mark.parametrize("expand", [False, True])
def test_open_running_ask_from_frame(db, capsys, expand):
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="user"))
    stream = "reviewer's  stream"
    store.create_stream(Stream(stream_id=stream))
    records = [store.append_record(stream_id=stream, author_peer_id="user", kind="prompt", body=body,
                                   created_at="2026-11-27T00:00:00Z", idempotency_key=body)
               for body in ("prior", "inspect this", "later")]
    record = records[1]
    claim(db, stream)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO bridge_deliveries(delivery_id,seq,stream_id,peer_id,record_id,record_position,"
                     "attempt,certainty,claim_generation) VALUES ('ask',1,?,'cx',?,?,1,'NOT_STARTED',1)",
                     (stream, record.record_id, record.position))
        conn.commit()
    payload = monitor_read_model.render_monitor_frame(str(db), ["activity"], read_at=NOW, human=True)
    payload["store_selection"] = {"path": str(db), "source": "explicit"}
    output = render_grouped_frame(payload, expand=expand, width=100)
    short_id = record.record_id.removeprefix("rec-")
    assert short_id in output and output.count("monitor open ID") == 1
    assert "peerhub --db " in output
    assert main(["--db", str(db), "monitor", "open", short_id]) == 0
    assert json.loads(capsys.readouterr().out) == "inspect this"


@pytest.mark.parametrize("expand", [False, True])
def test_plain_running_footer_uses_path_placeholder_when_plain_conversion_would_alter_db_path(expand):
    payload = _payload([{**ask(), "_ask_id": "rec-123456789abc"}], [])
    payload["store_selection"] = {"path": "D:/café/core.db", "source": "explicit"}
    output = render_grouped_frame(payload, expand=expand, view="plain", unicode=False, width=100)
    assert "peerhub --db PATH monitor open ID" in output.splitlines()
    assert not any(line.startswith("peerhub --db D:/caf") for line in output.splitlines())


def test_open_accepts_json_after_id_and_reports_future_schema(db, capsys):
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="user"))
    store.create_stream(Stream(stream_id="s"))
    record = store.append_record(stream_id="s", author_peer_id="user", kind="prompt", body="inspect",
                                 created_at="2026-11-27T00:00:00Z", idempotency_key="k")
    short_id = record.record_id.removeprefix("rec-")
    assert main(["--db", str(db), "monitor", "open", short_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"id": short_id, "prompt": "inspect"}
    assert main(["--db", str(db), "--json", "monitor", "open", short_id]) == 0
    assert json.loads(capsys.readouterr().out)["prompt"] == "inspect"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA user_version=999")
        conn.commit()
    assert main(["--db", str(db), "monitor", "open", short_id]) == 6
    assert "[schema_version_error]" in capsys.readouterr().err
