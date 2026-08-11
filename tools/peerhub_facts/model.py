"""Report data shapes for the fact-refresh routine.

Field set and status vocabulary are fixed by
``docs/design/FACT-REFRESH-PROCEDURE-R1.md`` ("Output contract"): per fact
a ``status``, ``expected``, ``observed``, ``source_tag``, the probe
command run, its exit code, an evidence digest, and a recommended action.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FactStatus(str, Enum):
    """The ratified status vocabulary. No values may be added casually."""

    PASS = "PASS"
    DRIFT = "DRIFT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ERROR = "ERROR"
    NOT_RUN = "NOT_RUN"


#: Statuses that make the process exit 1 (semantic drift / human review).
_EXIT_ONE = frozenset({FactStatus.DRIFT, FactStatus.REVIEW_REQUIRED})

#: Statuses that make the process exit 2 (a mandatory probe or the suite
#: itself failed to run). A peer merely being ABSENT is deliberately NOT
#: in here -- see the procedure's graceful-degradation requirement.
_EXIT_TWO = frozenset({FactStatus.ERROR})


def evidence_digest(*parts: object) -> str:
    """Return a stable sha256 over the given evidence parts."""

    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(repr(part).encode("utf-8"))
        hasher.update(b"\x1f")
    return f"sha256:{hasher.hexdigest()}"


@dataclass(frozen=True)
class Fact:
    """One checked fact and everything needed to audit the check."""

    fact_id: str
    status: FactStatus
    expected: str
    observed: str
    source_tag: str
    probe_command: str
    exit_code: int | None
    evidence_digest: str
    recommended_action: str

    def to_json(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "status": self.status.value,
            "expected": self.expected,
            "observed": self.observed,
            "source_tag": self.source_tag,
            "probe_command": self.probe_command,
            "exit_code": self.exit_code,
            "evidence_digest": self.evidence_digest,
            "recommended_action": self.recommended_action,
        }


@dataclass(frozen=True)
class TestSuiteSnapshot:
    """A pytest run pinned to the SHA it describes.

    The procedure forbids maintaining a living "current test count"; a
    count is only meaningful paired with its commit.
    """

    head_sha: str
    passed: int | None
    failed: int | None
    deselected: int | None
    duration_seconds: float | None
    raw_digest: str
    exit_code: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "head_sha": self.head_sha,
            "passed": self.passed,
            "failed": self.failed,
            "deselected": self.deselected,
            "duration_seconds": self.duration_seconds,
            "raw_digest": self.raw_digest,
            "exit_code": self.exit_code,
        }

    def render_counts(self, last_cited: str, last_cited_sha: str) -> str:
        """Render the exact three-line block the procedure specifies."""

        current = (
            f"{self.passed} passed" if self.passed is not None else "unknown"
        )
        delta = "unknown"
        cited_total = _leading_int(last_cited)
        if self.passed is not None and cited_total is not None:
            difference = self.passed - cited_total
            delta = f"{difference:+d} tests (informational only)"
        return (
            f"Current run:  {current} at {self.head_sha}\n"
            f"Last cited:   {last_cited} at {last_cited_sha}\n"
            f"Delta:        {delta}"
        )


def _leading_int(text: str) -> int | None:
    digits = ""
    for char in text.strip():
        if not char.isdigit():
            break
        digits += char
    return int(digits) if digits else None


@dataclass(frozen=True)
class FactsReport:
    """The full report plus the ratified exit-code mapping."""

    generated_at: str
    head_sha: str
    live: bool
    facts: tuple[Fact, ...] = field(default_factory=tuple)
    suite: TestSuiteSnapshot | None = None
    counts_block: str = ""

    @property
    def exit_code(self) -> int:
        """0 all mandatory checks pass; 1 drift/review; 2 probe or suite failure."""

        statuses = {fact.status for fact in self.facts}
        if statuses & _EXIT_TWO:
            return 2
        if statuses & _EXIT_ONE:
            return 1
        return 0

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {status.value: 0 for status in FactStatus}
        for fact in self.facts:
            counts[fact.status.value] += 1
        return counts

    def to_json(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "head_sha": self.head_sha,
            "live": self.live,
            "exit_code": self.exit_code,
            "status_counts": self.status_counts(),
            "counts_block": self.counts_block,
            "suite": self.suite.to_json() if self.suite is not None else None,
            "facts": [fact.to_json() for fact in self.facts],
        }

    def to_markdown(self) -> str:
        lines = [
            "# peerhub facts report",
            "",
            f"- generated_at: `{self.generated_at}`",
            f"- HEAD: `{self.head_sha}`",
            f"- mode: {'--live' if self.live else 'default'}",
            f"- exit code: **{self.exit_code}**",
            "",
            "## Test counts",
            "",
            "```",
            self.counts_block or "(suite not run)",
            "```",
            "",
            "## Facts",
            "",
            (
                "| fact | status | source | exit | expected | observed | "
                "probe | evidence | action |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for fact in self.facts:
            lines.append(
                f"| `{fact.fact_id}` | {fact.status.value} "
                f"| {_cell(fact.source_tag)} | {fact.exit_code} "
                f"| {_cell(fact.expected)} | {_cell(fact.observed)} "
                f"| {_cell(fact.probe_command)} "
                f"| `{_cell(fact.evidence_digest)}` "
                f"| {_cell(fact.recommended_action)} |"
            )
        lines.extend(
            [
                "",
                "Nothing above was auto-applied. Expected values live in "
                "`docs/compatibility/peer-cli-contracts.toml` and are only "
                "ever changed by a human.",
                "",
            ]
        )
        return "\n".join(lines)


def _cell(text: str) -> str:
    """Flatten a value for a markdown table cell."""

    flattened = " ".join(str(text).split())
    if len(flattened) > 120:
        flattened = flattened[:117] + "..."
    return flattened.replace("|", "\\|")
