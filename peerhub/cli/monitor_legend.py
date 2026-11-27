"""Labels and legends for the human monitor views."""
from __future__ import annotations

from peerhub.core.quota_policy import LOW_QUOTA_FRACTION, PLAN_REST_PACE_PT, PLAN_SPEND_PACE_PT

POOLS_HEADING = "POOLS"
QUOTA_NA = "n/a"
RESET_PENDING_NOTE = "reset · refresh pending"
CONTEXT_ESTIMATE_LABEL = "est"
SUPERSEDED_MARK = "(superseded)"
POOL_MARGIN_COLUMN = "MARGIN"
POOL_MARGIN_LEGEND = f"{POOL_MARGIN_COLUMN}=EXH-RESET, - = early"
POOL_LEGEND = (
    "PACE(pp)=EVN-LEFT, 0=even burn (|), "
    f"<=-{PLAN_SPEND_PACE_PT:g} spend, >+{PLAN_REST_PACE_PT:g} rest! | EXH/RESET ! = early"
    f" | {POOL_MARGIN_LEGEND} | ? = too little data | EVN=even-burn left"
)

POOL_STATE_LEGEND = (
    f"ok enough | low >0-{LOW_QUOTA_FRACTION:.0%} | fast >{LOW_QUOTA_FRACTION:.0%} + threat (2+ samples)"
    " | out 0% | aged stale | reset passed | n/a plan no longer reports it (diag quota may keep an older value)"
    " | ? unknown"
)

MONITOR_LEGEND = (
    POOL_STATE_LEGEND,
    "TIME: the header time is the snapshot time (when the store was read), not the time quota was measured",
    "LEADS: label | state (?busy = alive not confirmed, - = no session) | age of the last activity"
    " | ctx = context of the model's maximum input (! 80% or more; 106k = window unknown; ? unknown) | running asks | model | title",
    "CTX est: estimated context of a cc session at the end of its last ask (percent of the model's maximum input, with"
    " its age); not a live measurement",
)


# The top-to-bottom order below the title (#430). Compact ends with LEADS.
# Detail section keys are monitor group IDs.
FRAME_SECTIONS = {
    "pools": POOLS_HEADING,
    "suggest": "suggest:",
    "alerts": "ALERTS",
    "running": "RUNNING",
    "recent": "RECENT",
    "leads": "LEADS",
    "capacity": "QUOTA",
    "peers": "PEERS",
    "execution": "EXECUTION",
    "sessions": "SESSIONS",
    "usage": "USAGE",
    "context": "CONTEXT",
}
_FRAME_SECTION_KEYS = tuple(FRAME_SECTIONS)
FRAME_SECTION_ORDER = _FRAME_SECTION_KEYS[:_FRAME_SECTION_KEYS.index("leads") + 1]
FRAME_SECTION_HEADS = {key: FRAME_SECTIONS[key] for key in FRAME_SECTION_ORDER}
FRAME_HEADS = tuple(FRAME_SECTION_HEADS.values())
FRAME_DETAIL_ORDER = _FRAME_SECTION_KEYS[len(FRAME_SECTION_ORDER):]
