"""Exact metadata predicates on lexical search: equal by type and value, applied before the limit."""
from __future__ import annotations

import pytest

from peerhub.extensions.search import SearchIndex


@pytest.fixture
def index(tmp_path):
    idx = SearchIndex(tmp_path / "s.db")
    for i, meta in enumerate([
        {"name": "a", "owner": "eval-team", "rank": 1, "flag": True},
        {"name": "b", "owner": "eval-team", "rank": 2, "flag": False},
        {"name": "c", "owner": "other", "rank": 1, "flag": True},
    ]):
        idx.index_artifact(digest=f"{i:064x}", metadata=meta, snippet="shared benchmark text")
    return idx


def names(results):
    return sorted(r.metadata["name"] for r in results)


def test_exact_predicates_filter_hits(index):
    assert names(index.search("benchmark")) == ["a", "b", "c"]
    assert names(index.search("benchmark", metadata={"owner": "eval-team"})) == ["a", "b"]
    assert names(index.search("benchmark", metadata={"owner": "eval-team", "rank": 2})) == ["b"]
    assert index.search("benchmark", metadata={"owner": "nobody"}) == []
    assert index.search("benchmark", metadata={"missing": "x"}) == []


def test_match_is_exact_by_type(index):
    assert names(index.search("benchmark", metadata={"flag": True})) == ["a", "c"]
    assert index.search("benchmark", metadata={"rank": True}) == []  # True is not 1
    assert index.search("benchmark", metadata={"rank": "1"}) == []  # "1" is not 1


def test_limit_applies_after_the_metadata_filter(index):
    assert len(index.search("benchmark", limit=1, metadata={"owner": "other"})) == 1
    assert len(index.search("benchmark", limit=1, metadata={"owner": "eval-team"})) == 1


def test_invalid_metadata_rejected(index):
    for bad in ({"k": ["list"]}, {"k": {"n": 1}}, {1: "x"}):
        with pytest.raises(ValueError):
            index.search("benchmark", metadata=bad)  # type: ignore[arg-type]
