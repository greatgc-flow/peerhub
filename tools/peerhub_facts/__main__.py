"""CLI entry point: ``python -m tools.peerhub_facts [--live]``.

Orchestration only -- the probing lives in ``collectors``, the judging in
``compare``, and the shapes in ``model``. This module runs the procedure's
checks in the order the spec lists them, writes the two reports, and
returns the ratified exit code.

Exit codes (``docs/design/FACT-REFRESH-PROCEDURE-R1.md``, "Output contract"):

* ``0`` -- all mandatory checks pass. A peer that simply isn't installed
  locally lands here: ABSENT is recorded, not escalated.
* ``1`` -- semantic drift, or a fact a human has to look at.
* ``2`` -- a mandatory probe, or the test suite itself, failed. A red suite
  is also reported here: the routine's other facts are not trustworthy while
  the suite is red, so it is treated as a run-blocking failure rather than
  as ordinary drift.

Nothing is auto-applied except generating the reports. Expected versions,
required flags, decoder schemas and dependency constraints are only ever
changed by a human editing ``docs/compatibility/peer-cli-contracts.toml``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.peerhub_facts.collectors import (
    PEER_ALIASES,
    PROJECT_ROOT,
    collect_dependencies,
    decoder_conformance,
    head_sha,
    probe_cli,
    resolve_peers,
    run_test_suite,
)
from tools.peerhub_facts.compare import (
    DEFAULT_CONTRACTS_PATH,
    Contracts,
    compare_decoder,
    compare_dependencies,
    compare_help,
    compare_resolution,
    compare_suite,
    compare_version,
    load_contracts,
)
from tools.peerhub_facts.model import (
    Fact,
    FactStatus,
    FactsReport,
    TestSuiteSnapshot,
    evidence_digest,
)

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "build" / "peerhub-facts"

#: The slow, actually-shells-out adapter conformance tests. They carry
#: ``@pytest.mark.slow`` and are deselected by the default addopts, so
#: ``-m slow`` (last ``-m`` wins) is what brings them back.
_LIVE_TEST_ARGS: tuple[str, ...] = (
    "-m",
    "slow",
    "tests/integration/adapters",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.peerhub_facts",
        description=(
            "Compare observed peer-CLI / dependency / test-suite facts "
            "against the recorded semantic expectations. Reports only; "
            "never updates the expectations."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "additionally run the real shells-out adapter conformance tests "
            "for all 3 peers. Spends real peer usage -- not for every run."
        ),
    )
    parser.add_argument(
        "--contracts",
        type=Path,
        default=DEFAULT_CONTRACTS_PATH,
        help="path to peer-cli-contracts.toml",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root probed for dependencies, HEAD, and the suite",
    )
    return parser


def collect_peer_facts(contracts: Contracts) -> list[Fact]:
    """Steps 1-5: resolution, version, help tokens, decoder conformance."""

    facts: list[Fact] = []
    for resolution in resolve_peers(PEER_ALIASES):
        facts.append(compare_resolution(resolution))
        contract = contracts.peers.get(resolution.alias)
        if contract is None:
            facts.append(
                Fact(
                    fact_id=f"peer.{resolution.alias}.contract",
                    status=FactStatus.REVIEW_REQUIRED,
                    expected=f"a [peers.{resolution.alias}] entry",
                    observed="missing from peer-cli-contracts.toml",
                    source_tag="declared, unverified",
                    probe_command="load_contracts()",
                    exit_code=None,
                    evidence_digest=evidence_digest(resolution.alias),
                    recommended_action=(
                        "record this peer's semantic expectations, or drop "
                        "it from PEER_ALIASES"
                    ),
                )
            )
            continue

        version_probe = None
        help_probe = None
        if resolution.target is not None:
            version_probe = probe_cli(resolution.target, contract.version_argv)
            help_probe = probe_cli(resolution.target, contract.help_argv)

        facts.append(compare_version(contract, version_probe))
        facts.append(compare_help(contract, help_probe))
        # Decoder conformance checks a protocol shape, not an install, so
        # it still runs for an ABSENT peer -- that is the whole point of
        # keeping it separate from the version/help probes.
        facts.append(
            compare_decoder(contract, decoder_conformance(resolution.alias))
        )
    return facts


def run_live_conformance(project_root: Path) -> Fact:
    """``--live``: the real, slow, shells-out adapter conformance tests.

    Version and help success cannot prove an output schema still matches
    what the decoders expect; only a real round trip can.
    """

    command = "python -m pytest -q -m slow tests/integration/adapters"
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *_LIVE_TEST_ARGS],
            capture_output=True,
            text=True,
            timeout=3600,
            cwd=project_root,
        )
    except Exception as error:  # noqa: BLE001 - reportable, never fatal
        return Fact(
            fact_id="live.adapter_conformance",
            status=FactStatus.ERROR,
            expected="the slow adapter conformance tests run and pass",
            observed=f"{type(error).__name__}: {error}",
            source_tag="empirical_probe",
            probe_command=command,
            exit_code=None,
            evidence_digest=evidence_digest(command, str(error)),
            recommended_action="the live conformance run could not start",
        )
    raw = completed.stdout + completed.stderr
    return Fact(
        fact_id="live.adapter_conformance",
        status=(
            FactStatus.PASS if completed.returncode == 0 else FactStatus.DRIFT
        ),
        expected="the slow adapter conformance tests run and pass",
        observed=raw.strip().splitlines()[-1] if raw.strip() else "(no output)",
        source_tag="empirical_probe",
        probe_command=command,
        exit_code=completed.returncode,
        evidence_digest=evidence_digest(raw),
        recommended_action=(
            "none"
            if completed.returncode == 0
            else "a real peer round trip no longer matches what the decoder "
            "expects; fix the adapter before recording any new version"
        ),
    )


def build_report(
    *,
    contracts: Contracts,
    live: bool,
    project_root: Path,
) -> FactsReport:
    facts: list[Fact] = collect_peer_facts(contracts)
    facts.extend(compare_dependencies(collect_dependencies(project_root)))

    suite: TestSuiteSnapshot | None = None
    counts_block = ""
    sha = head_sha(project_root)

    counts, raw, exit_code = run_test_suite(project_root)
    facts.append(compare_suite(counts, raw, exit_code))
    suite = TestSuiteSnapshot(
        head_sha=sha,
        passed=counts.get("passed"),
        failed=counts.get("failed"),
        deselected=counts.get("deselected"),
        duration_seconds=counts.get("duration"),
        raw_digest=evidence_digest(raw),
        exit_code=exit_code,
    )
    counts_block = suite.render_counts(
        contracts.last_cited, contracts.last_cited_sha
    )

    if live:
        facts.append(run_live_conformance(project_root))

    return FactsReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        head_sha=sha,
        live=live,
        facts=tuple(facts),
        suite=suite,
        counts_block=counts_block,
    )


def write_reports(
    report: FactsReport, project_root: Path
) -> tuple[Path, Path]:
    """Write both reports. Only ever under ``build/`` (already gitignored)."""

    output_dir = project_root / "build" / "peerhub-facts"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "latest.json"
    md_path = output_dir / "latest.md"
    json_path.write_text(
        json.dumps(report.to_json(), indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        contracts = load_contracts(args.contracts)
        report = build_report(
            contracts=contracts,
            live=bool(args.live),
            project_root=args.project_root,
        )
    except Exception as error:  # noqa: BLE001 - convert fatal setup to exit 2
        report = FactsReport(
            generated_at=datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            head_sha=head_sha(args.project_root),
            live=bool(args.live),
            facts=(
                Fact(
                    fact_id="routine.setup",
                    status=FactStatus.ERROR,
                    expected="contracts and mandatory collectors initialize",
                    observed=f"{type(error).__name__}: {error}",
                    source_tag="empirical_probe",
                    probe_command=(
                        f"load_contracts({str(args.contracts)!r}); "
                        "build_report()"
                    ),
                    exit_code=None,
                    evidence_digest=evidence_digest(
                        str(args.contracts), type(error).__name__, str(error)
                    ),
                    recommended_action=(
                        "repair the contracts file or collector failure; "
                        "no expected facts were auto-updated"
                    ),
                ),
            ),
        )
    json_path, md_path = write_reports(report, args.project_root)

    if report.counts_block:
        print(report.counts_block)
        print()
    for fact in report.facts:
        if fact.status is not FactStatus.PASS:
            print(f"{fact.status.value:<16} {fact.fact_id}: {fact.observed}")
    counts = report.status_counts()
    print(
        "  ".join(f"{name}={value}" for name, value in counts.items() if value)
    )
    print(f"reports: {json_path}")
    print(f"         {md_path}")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
