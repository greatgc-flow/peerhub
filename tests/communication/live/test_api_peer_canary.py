"""Local paid canaries of the API peers ca, oa (#378) and ga (#381). They spend API credits: opt-in only (PEERHUB_LIVE=1
and `-m canary`, for example `python -m tools.live_gate --yes --only canary --peer ca`), never part of the release gate.

One real call each through the public `ask` function on a store in tmp_path; no retries."""
from __future__ import annotations

import os
import shutil
import uuid

import pytest

from peerhub.extensions.ask import ask

pytestmark = [pytest.mark.canary, pytest.mark.provider, pytest.mark.timeout(600)]


@pytest.fixture
def ant():
    if shutil.which("ant") is None:
        pytest.skip("LIVE-PROVIDER-UNAVAILABLE[provider=ca]: the ant CLI is not on PATH")


def _delivered(result: dict) -> dict:
    assert result["status"] == "delivered" and result["certainty"] == "TERMINAL" and result["error"] is None, result
    assert result["usage"]["input_tokens"] > 0 and result["usage"]["output_tokens"] > 0, result
    return result


@pytest.mark.ca
def test_ca_standard_replies_ok(tmp_path, ant):
    result = _delivered(ask(tmp_path / "core.db", "ca", "Reply with OK only.", workspace=tmp_path, profile="ca.standard",
                            timeout_s=240))
    assert "ok" in result["response"].lower()


@pytest.mark.ca
def test_ca_sends_a_leading_at_path_as_literal_text(tmp_path, ant):
    """`ant` inlines a file for a string that starts with `@`; the fixed first line keeps the prompt literal."""
    marker = "marker_" + uuid.uuid4().hex
    probe = tmp_path / "probe.txt"
    probe.write_text(marker, encoding="utf-8")
    prompt = f"@{probe}\nRepeat the whole message you received, word for word, starting with its first line."
    result = _delivered(ask(tmp_path / "core.db", "ca", prompt, workspace=tmp_path, profile="ca.standard", timeout_s=240))
    assert f"@{probe}" in result["response"], result
    assert marker not in result["response"], result


@pytest.mark.oa
def test_oa_standard_replies_ok(tmp_path):
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("LIVE-OA-KEY-MISSING[env=OPENAI_API_KEY]: set OPENAI_API_KEY to run the oa canary (it spends API credits)")
    result = _delivered(ask(tmp_path / "core.db", "oa", "Reply with OK only.", workspace=tmp_path, profile="oa.standard",
                            timeout_s=240))
    assert "ok" in result["response"].lower()


@pytest.mark.ga
def test_ga_standard_replies_ok(tmp_path):
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("LIVE-GA-KEY-MISSING[env=GEMINI_API_KEY]: set GEMINI_API_KEY to run the ga canary (it spends API credits)")
    result = _delivered(ask(tmp_path / "core.db", "ga", "Reply with OK only.", workspace=tmp_path, profile="ga.standard",
                            timeout_s=240))
    assert "ok" in result["response"].lower()
