"""PeerHub guide (0.25.0 plan D5): decisions only, at most 1600 bytes of ASCII.

Rare hazards, the store and upgrade steps, batch and load limits, discovery commands and the API peers moved to
`peerhub explain <topic>` (peerhub/config_data/lessons.toml); the profile table is `peerhub profile list`.
The guide uses only packaged data and reads no store, profile file or provider.
"""
from __future__ import annotations

from pathlib import Path

GUIDE_MAX_BYTES = 1600  # enforced by tests/communication/e2e/test_cli_guide.py


def render_guide(workspace: Path | None = None) -> str:
    """The decisions a caller needs before its first ask. `workspace` is accepted for callers; it is not read."""
    del workspace
    from peerhub.cli.lessons import load_lessons
    from peerhub.extensions.batch import RECOMMENDED_MAX_PARALLEL
    from peerhub.core.peer_kinds import API_PEER_KINDS

    # The first pointer belongs to the Votes line. The rest appear under More.
    vote, *topics = sorted((lesson for lesson in load_lessons() if "guide_order" in lesson),
                           key=lambda lesson: lesson["guide_order"])
    more = ", ".join(topic["id"] for topic in topics)
    return "\n".join([
        "PeerHub guide (decisions only)",
        # CLI peers are grouped by what their sandboxes support.
        f"Peer: cc/cx for code and files (sandbox enforced); ag for drafts with content inline (always --writable, not enforced); {'/'.join(API_PEER_KINDS)} = API credits, text only.",
        "Tier: lowest that fits: standard = bulk, effort = code and routine review, deepthink = design and final check.",
        "Models: tops are opt-in and underused (cx.astra, ag.neon, ag.argon, cc.fable): use them for design, hard review, final checks; vary model and effort per task; give each vote a different model.",
        "Stream: one task = one --stream; reuse only for follow-ups and review rounds on ONE diff; new task = new stream.",
        f"Votes: routine check = ONE reviewer; consequential (release, schema, deletion, public API) = 3 independent peers, unanimity. All run FRESH, even if a stream name is given; facts inline. Reply VERDICT / BLOCKERS / NITS, <= 300 tokens (peerhub explain {vote['id']}).",
        f"Batch: peerhub batch run tasks.json --batch-id review-001 --max-parallel {RECOMMENDED_MAX_PARALLEL} --dry-run (validates only); execution spends quota, at most {RECOMMENDED_MAX_PARALLEL} at once.",
        "Timeout: --timeout-seconds 1800-3600 for implementer/review asks (default 600); ag up to 7200.",
        "Uncertain (timeout, empty reply, pending_earlier_record): stop that stream, check the working tree, never blind-retry; new work goes on a new stream.",
        f"More: peerhub profile list (models, tiers); peerhub explain <topic> for: {more}; all topics: peerhub explain lessons.",
    ])
