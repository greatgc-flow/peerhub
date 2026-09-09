"""Unit coverage for the generic layered-JSON-config mechanics (item 9,
dotdir consolidation) used by arbiter_review.py and proposals.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.application.config_layers import (
    LayeredConfigError,
    load_json_layer,
    merge_layers,
)


def test_load_json_layer_returns_none_for_missing_path(tmp_path: Path) -> None:
    assert (
        load_json_layer(
            tmp_path / "absent.json",
            label="x.json",
            layer="workspace",
            allowed_keys=frozenset({"a"}),
        )
        is None
    )


def test_load_json_layer_returns_none_for_none_path() -> None:
    assert (
        load_json_layer(
            None, label="x.json", layer="workspace", allowed_keys=frozenset({"a"})
        )
        is None
    )


def test_load_json_layer_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(LayeredConfigError, match="JSON object"):
        load_json_layer(path, label="x.json", layer="workspace", allowed_keys=frozenset())


def test_load_json_layer_rejects_missing_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")

    with pytest.raises(LayeredConfigError, match="schema_version=1"):
        load_json_layer(
            path, label="x.json", layer="global", allowed_keys=frozenset({"a"})
        )


def test_load_json_layer_rejects_wrong_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"schema_version": 2, "a": 1}), encoding="utf-8")

    with pytest.raises(LayeredConfigError, match="schema_version=1"):
        load_json_layer(
            path, label="x.json", layer="global", allowed_keys=frozenset({"a"})
        )


def test_load_json_layer_rejects_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(
        json.dumps({"schema_version": 1, "a": 1, "b": 2}), encoding="utf-8"
    )

    with pytest.raises(LayeredConfigError, match=r"\['b'\]"):
        load_json_layer(
            path, label="x.json", layer="workspace", allowed_keys=frozenset({"a"})
        )


def test_load_json_layer_accepts_valid_layer(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"schema_version": 1, "a": 1}), encoding="utf-8")

    result = load_json_layer(
        path, label="x.json", layer="workspace", allowed_keys=frozenset({"a"})
    )

    assert result == {"schema_version": 1, "a": 1}


def test_merge_layers_scalar_key_highest_layer_wins() -> None:
    merged, winners = merge_layers(
        [("global", {"a": 1}), ("workspace", {"a": 2})]
    )

    assert merged == {"a": 2}
    assert winners == {"a": "workspace"}


def test_merge_layers_skips_absent_layers() -> None:
    merged, winners = merge_layers([("global", None), ("workspace", {"a": 1})])

    assert merged == {"a": 1}
    assert winners == {"a": "workspace"}


def test_merge_layers_excludes_schema_version_from_merged_output() -> None:
    merged, winners = merge_layers(
        [("global", {"schema_version": 1, "a": 1})]
    )

    assert "schema_version" not in merged
    assert "schema_version" not in winners


def test_merge_layers_list_replaced_whole_not_unioned() -> None:
    merged, winners = merge_layers(
        [("global", {"items": [1, 2, 3]}), ("workspace", {"items": [9]})]
    )

    assert merged == {"items": [9]}
    assert winners == {"items": "workspace"}


def test_merge_layers_object_key_merges_field_by_field() -> None:
    merged, winners = merge_layers(
        [
            ("global", {"candidate": {"peer_name": "cc", "profile_id": "cc.deepthink"}}),
            ("workspace", {"candidate": {"profile_id": "cc.effort"}}),
        ],
        object_keys=frozenset({"candidate"}),
    )

    assert merged == {"candidate": {"peer_name": "cc", "profile_id": "cc.effort"}}
    assert winners == {
        "candidate": "workspace",
        "candidate.peer_name": "global",
        "candidate.profile_id": "workspace",
    }


def test_merge_layers_no_layers_present_returns_empty() -> None:
    merged, winners = merge_layers([("global", None), ("workspace", None)])

    assert merged == {}
    assert winners == {}
