"""PeerHub guide (0.25.0 plan D5): decisions only, at most 1600 bytes of ASCII.

Rare hazards, the store and upgrade steps, batch resources, discovery commands and the API peers moved to
`peerhub explain <topic>` (peerhub/config_data/lessons.toml); the profile table is `peerhub profile list`.
The guide uses only packaged data and reads no store, profile file or provider.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from peerhub.cli.output import emit

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


GUIDE_MAX_BYTES = 1600  # enforced by tests/communication/e2e/test_cli_guide.py


def render_guide(workspace: Path | None = None) -> str:
    """The decisions a caller needs before its first ask. `workspace` is accepted for callers; it is not read."""
    del workspace
    from peerhub.cli.lessons import load_lessons
    from peerhub.core.load_limits import CPU_BUSY_WARN_PCT, MIN_FREE_RAM_GB
    from peerhub.core.peer_kinds import API_PEER_KINDS

    # The first pointer belongs to the Votes line. The rest appear under More.
    vote, *topics = sorted((lesson for lesson in load_lessons() if "guide_order" in lesson),
                           key=lambda lesson: lesson["guide_order"])
    more = ", ".join(topic["id"] for topic in topics)
    return "\n".join([
        "PeerHub guide (decisions only)",
        # CLI peers are grouped by what their sandboxes support.
        f"Peer: cc/cx for code and files (sandbox enforced); ag for drafts with content inline (always --writable, not enforced); {'/'.join(API_PEER_KINDS)} = API credits, text only.",
        "Tier: lowest that fits: standard=bulk, effort=code and routine review, deepthink=design and final check.",
        "Models: underused tops (cx.astra, ag.neon, ag.argon, cc.fable) for design, review, final checks; vary model/effort; different model per vote.",
        "Quota (AI): peerhub diag --plan = pools, load, RAM/CPU (stale: observation refresh); monitor for humans.",
        "Stream: one --stream per task; reuse only for follow-ups/reviews on ONE diff; new task = new stream.",
        f"Votes: routine check = ONE reviewer; consequential (release, schema, deletion, public API) = 3 independent peers, unanimity. All run FRESH, even if a stream name is given; facts inline. Reply VERDICT / BLOCKERS / NITS, <= 300 tokens (peerhub explain {vote['id']}).",
        f"Batch: peerhub batch run tasks.json --batch-id ID --max-parallel n --dry-run (validates only); execution spends quota. "
        f"n at once. Read load (free RAM >= {MIN_FREE_RAM_GB} GB, CPU below {CPU_BUSY_WARN_PCT}%). {CPU_BUSY_WARN_PCT}%+ CPU: hold. "
        "Other sessions' asks count. No cap.",
        "Timeout: --timeout-seconds 1800-3600 for implementer/review (default 600); ag up to 7200.",
        "Uncertain (timeout, empty reply, pending_earlier_record): stop the stream, check diag running and working tree, never blind-retry.",
        f"More: peerhub profile list; peerhub explain <topic> for: {more}; peerhub explain lessons.",
    ])


def register_guide_parser(subparsers: Subparsers) -> None:
    guide_parser = subparsers.add_parser("guide", help="Print the offline quick reference")
    guide_parser.description = "Print the offline quick reference. Use peerhub profile list for model profiles."


def run_guide(args: argparse.Namespace) -> int:
    emit({"guide": render_guide()}, True) if args.json else print(render_guide())
    return 0
