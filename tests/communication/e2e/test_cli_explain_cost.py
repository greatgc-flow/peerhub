"""An AI learns usage and lessons from `peerhub guide` + `peerhub explain` at minimum token cost.

One match = the full text; several matches = one `id: title` line each plus how to open one (not every full text).
Words an AI asks with (model, ram, load, diag, ...) find a topic.
"""
from __future__ import annotations

import json

import pytest

from peerhub.cli.app import main
from peerhub.cli.lessons import lookup_lessons

pytestmark = [pytest.mark.unit]


def _explain(capsys, *words: str, as_json: bool = False) -> str:
    assert main(["explain", *words, *(["--json"] if as_json else [])]) == 0
    return capsys.readouterr().out


def test_several_matches_are_listed_not_printed_in_full(capsys):
    matches = lookup_lessons("vote")
    assert len(matches) > 1
    out = _explain(capsys, "vote")
    assert [line.split(":")[0] for line in out.splitlines()[:-1]] == [m["id"] for m in matches]
    assert "peerhub explain <id>" in out.splitlines()[-1]
    assert "Symptom:" not in out and len(out) < 700


def test_one_match_is_the_full_text(capsys):
    out = _explain(capsys, "parallel-quota")
    assert "Symptom:" in out and "Fix:" in out


def test_json_keeps_every_match_in_full(capsys):
    data = json.loads(_explain(capsys, "vote", as_json=True))
    assert len(data["lessons"]) > 1 and all("fix" in lesson for lesson in data["lessons"])


@pytest.mark.parametrize("words,topic", [
    (("free", "ram"), "parallel-quota"), (("memory",), "parallel-quota"), (("slots",), "parallel-quota"),
    (("diag", "plan"), "parallel-quota"), (("pace",), "parallel-quota"),
    (("model",), "discovery-commands"), (("models",), "discovery-commands"),
])
def test_everyday_words_find_a_topic(words, topic):
    assert topic in [lesson["id"] for lesson in lookup_lessons(" ".join(words))]
