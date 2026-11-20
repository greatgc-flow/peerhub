"""PeerHub guide (0.25.0 plan D5): decisions only, at most 1600 bytes of ASCII.

Rare hazards, the store and upgrade steps, batch and load limits, discovery commands and the API peers moved to
`peerhub explain <topic>` (peerhub/config_data/lessons.toml); the profile table is `peerhub profile list`. Static text:
it reads no store, no profile file and no provider, so nothing configured can change it.
"""
from __future__ import annotations

from pathlib import Path

GUIDE_MAX_BYTES = 1600  # enforced by tests/communication/e2e/test_cli_guide.py


def render_guide(workspace: Path | None = None) -> str:
    """The decisions a caller needs before its first ask. `workspace` is accepted for callers; it is not read."""
    del workspace
    from peerhub.extensions.batch import RECOMMENDED_MAX_PARALLEL

    return "\n".join([
        "PeerHub guide (decisions only)",
        "Peer: cc/cx for code and files (sandbox enforced); ag for drafts with content inline (always --writable, not enforced); ca/oa/ga = API credits, text only.",
        "Tier: lowest that fits: standard = bulk, effort = code and routine review, deepthink = design and final check.",
        "Stream: one task = one --stream; reuse only for follow-ups and review rounds on ONE diff; new task = new stream.",
        "Votes: routine check = ONE reviewer; consequential (release, schema, deletion, public API) = 3 independent peers, unanimity. All run FRESH, even if a stream name is given; facts inline. Reply VERDICT / BLOCKERS / NITS, <= 300 tokens (peerhub explain independent-peer-votes).",
        f"Batch: peerhub batch run tasks.json --batch-id review-001 --max-parallel {RECOMMENDED_MAX_PARALLEL} --dry-run (spends quota; at most {RECOMMENDED_MAX_PARALLEL} at once).",
        "Timeout: --timeout-seconds 1800-3600 for implementer/review asks (default 600); ag up to 7200.",
        "Uncertain (timeout, empty reply, pending_earlier_record): stop that stream, check the working tree, never blind-retry; new work goes on a new stream.",
        "More: peerhub profile list (models, tiers); peerhub explain <topic> for: ask-basics, writes-and-uncertain, store-and-upgrade, batch-and-load, discovery-commands, report-issues, ag-recipe, api-peers; all topics: peerhub explain lessons.",
    ])
