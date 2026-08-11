"""Pure expected-vs-observed comparison. No probing, no I/O to the world.

Keeping this side pure is what makes drift detection testable: a test can
hand it a synthetic observation and assert the produced status without
needing a peer CLI installed.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.peerhub_facts.collectors import (
    DecoderConformance,
    DependencySnapshot,
    PeerResolution,
    ProbeResult,
    expected_conformance_text,
    find_help_tokens,
    parse_version,
    table,
    text,
    text_tuple,
)
from tools.peerhub_facts.model import Fact, FactStatus, evidence_digest

DEFAULT_CONTRACTS_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "compatibility"
    / "peer-cli-contracts.toml"
)


@dataclass(frozen=True)
class PeerContract:
    """Semantic expectations for one peer. Never a raw output snapshot."""

    alias: str
    cli_name: str
    output_protocol: str
    required_output_fields: tuple[str, ...]
    verified_versions: tuple[str, ...]
    version_argv: tuple[str, ...]
    help_argv: tuple[str, ...]
    required_help_tokens: tuple[str, ...]


@dataclass(frozen=True)
class Contracts:
    schema_version: str
    peers: dict[str, PeerContract]
    last_cited: str
    last_cited_sha: str


def load_contracts(path: Path = DEFAULT_CONTRACTS_PATH) -> Contracts:
    """Read the human-maintained expectations. Never writes them back."""

    raw = table(tomllib.loads(path.read_text(encoding="utf-8")))
    peers: dict[str, PeerContract] = {}
    for alias, entry_value in table(raw.get("peers")).items():
        entry = table(entry_value)
        peers[alias] = PeerContract(
            alias=alias,
            cli_name=text(entry.get("cli_name"), alias),
            output_protocol=text(entry.get("output_protocol"), "unknown"),
            required_output_fields=text_tuple(
                entry.get("required_output_fields")
            ),
            verified_versions=text_tuple(entry.get("verified_versions")),
            version_argv=text_tuple(entry.get("version_argv"), ("--version",)),
            help_argv=text_tuple(entry.get("help_argv"), ("--help",)),
            required_help_tokens=text_tuple(entry.get("required_help_tokens")),
        )
    counts = table(raw.get("test_counts"))
    return Contracts(
        schema_version=text(raw.get("schema_version"), "unknown"),
        peers=peers,
        last_cited=text(counts.get("last_cited"), "unknown"),
        last_cited_sha=text(counts.get("last_cited_sha"), "unknown"),
    )


def compare_resolution(resolution: PeerResolution) -> Fact:
    """ABSENT is recorded and survived, never escalated (procedure step 2).

    A structural resolution failure is *not* ABSENT and is escalated to
    ERROR: "this contributor lacks the CLI" and "this adapter is broken"
    must not collapse into the same benign status.
    """

    if resolution.present:
        assert resolution.target is not None
        return Fact(
            fact_id=f"peer.{resolution.alias}.resolution",
            status=FactStatus.PASS,
            expected="resolvable via resolve_peer_target()",
            observed=str(resolution.target.executable_path),
            source_tag="empirical_probe",
            probe_command=f"resolve_peer_target({resolution.alias!r})",
            exit_code=0,
            evidence_digest=evidence_digest(
                resolution.alias, str(resolution.target.executable_path)
            ),
            recommended_action="none",
        )
    if resolution.failure_reason is not None:
        return Fact(
            fact_id=f"peer.{resolution.alias}.resolution",
            status=FactStatus.ERROR,
            expected="resolvable via resolve_peer_target()",
            observed=resolution.failure_reason,
            source_tag="empirical_probe",
            probe_command=f"resolve_peer_target({resolution.alias!r})",
            exit_code=None,
            evidence_digest=evidence_digest(
                resolution.alias, resolution.failure_reason
            ),
            recommended_action=(
                "resolution failed for a reason other than a missing "
                "install; the adapter or its profile set is broken and no "
                "other fact for this peer can be trusted until it is fixed"
            ),
        )
    return Fact(
        fact_id=f"peer.{resolution.alias}.resolution",
        status=FactStatus.NOT_RUN,
        expected="resolvable via resolve_peer_target()",
        observed=f"ABSENT ({resolution.absent_reason})",
        source_tag="empirical_probe",
        probe_command=f"resolve_peer_target({resolution.alias!r})",
        exit_code=None,
        evidence_digest=evidence_digest(
            resolution.alias, resolution.absent_reason
        ),
        recommended_action=(
            "none - a peer not installed locally is not drift. Install the "
            "CLI if you need this peer's version/help checked on this machine."
        ),
    )


def compare_version(
    contract: PeerContract, probe: ProbeResult | None
) -> Fact:
    fact_id = f"peer.{contract.alias}.version"
    expected = (
        ", ".join(contract.verified_versions)
        if contract.verified_versions
        else "(no verified_versions recorded)"
    )
    if probe is None:
        return _not_run(fact_id, expected, "peer ABSENT")
    if not probe.ok:
        return Fact(
            fact_id=fact_id,
            status=FactStatus.ERROR,
            expected=expected,
            observed=probe.error or f"exit {probe.exit_code}",
            source_tag="empirical_probe",
            probe_command=probe.command,
            exit_code=probe.exit_code,
            evidence_digest=evidence_digest(probe.command, probe.stdout_text),
            recommended_action=(
                "a mandatory probe failed to run; investigate the CLI install "
                "before trusting any other fact for this peer"
            ),
        )

    parsed = parse_version(contract.alias, probe.stdout_text)
    digest = evidence_digest(probe.command, probe.stdout_text)
    if parsed is None:
        return Fact(
            fact_id=fact_id,
            status=FactStatus.REVIEW_REQUIRED,
            expected=expected,
            observed=f"unparseable: {probe.stdout_text.strip()!r}",
            source_tag="empirical_probe",
            probe_command=probe.command,
            exit_code=probe.exit_code,
            evidence_digest=digest,
            recommended_action=(
                "the vendor changed its --version shape; update the "
                "vendor-specific parser, then re-verify"
            ),
        )
    if not contract.verified_versions:
        return Fact(
            fact_id=fact_id,
            status=FactStatus.REVIEW_REQUIRED,
            expected=expected,
            observed=parsed,
            source_tag="empirical_probe",
            probe_command=probe.command,
            exit_code=probe.exit_code,
            evidence_digest=digest,
            recommended_action=(
                "no expectation recorded yet; a human must add this version "
                "to verified_versions after verifying the adapter still works"
            ),
        )
    if parsed in contract.verified_versions:
        return Fact(
            fact_id=fact_id,
            status=FactStatus.PASS,
            expected=expected,
            observed=parsed,
            source_tag="empirical_probe",
            probe_command=probe.command,
            exit_code=probe.exit_code,
            evidence_digest=digest,
            recommended_action="none",
        )
    return Fact(
        fact_id=fact_id,
        status=FactStatus.DRIFT,
        expected=expected,
        observed=parsed,
        source_tag="empirical_probe",
        probe_command=probe.command,
        exit_code=probe.exit_code,
        evidence_digest=digest,
        recommended_action=(
            f"{contract.cli_name} moved to {parsed}; run --live conformance, "
            "then a human adds it to verified_versions if the adapter holds. "
            "This routine never adds it automatically."
        ),
    )


def compare_help(contract: PeerContract, probe: ProbeResult | None) -> Fact:
    fact_id = f"peer.{contract.alias}.help_tokens"
    expected = ", ".join(contract.required_help_tokens) or "(none required)"
    if probe is None:
        return _not_run(fact_id, expected, "peer ABSENT")
    if not probe.ok:
        return Fact(
            fact_id=fact_id,
            status=FactStatus.ERROR,
            expected=expected,
            observed=probe.error or f"exit {probe.exit_code}",
            source_tag="empirical_probe",
            probe_command=probe.command,
            exit_code=probe.exit_code,
            evidence_digest=evidence_digest(probe.command, probe.stdout_text),
            recommended_action="investigate the CLI install",
        )
    missing = find_help_tokens(probe.stdout_text, contract.required_help_tokens)
    digest = evidence_digest(probe.command, probe.stdout_text)
    if not missing:
        return Fact(
            fact_id=fact_id,
            status=FactStatus.PASS,
            expected=expected,
            observed="all required tokens present",
            source_tag="empirical_probe",
            probe_command=probe.command,
            exit_code=probe.exit_code,
            evidence_digest=digest,
            recommended_action="none",
        )
    return Fact(
        fact_id=fact_id,
        status=FactStatus.DRIFT,
        expected=expected,
        observed=f"missing: {', '.join(missing)}",
        source_tag="empirical_probe",
        probe_command=probe.command,
        exit_code=probe.exit_code,
        evidence_digest=digest,
        recommended_action=(
            "the adapter builds argv from these flags; a missing token means "
            "planned invocations may now be rejected by the CLI"
        ),
    )


def compare_decoder(
    contract: PeerContract, conformance: DecoderConformance
) -> Fact:
    fact_id = f"decoder.{contract.alias}.conformance"
    expected = (
        f"{contract.output_protocol} fields={contract.required_output_fields} "
        f"decodes to {expected_conformance_text()!r}"
    )
    digest = evidence_digest(
        contract.alias, conformance.fixture_digest_input
    )
    probe = f"{contract.alias} decoder.feed(fixture) -> finalize()"
    if conformance.error is not None:
        return Fact(
            fact_id=fact_id,
            status=FactStatus.ERROR,
            expected=expected,
            observed=conformance.error,
            source_tag="empirical_probe",
            probe_command=probe,
            exit_code=None,
            evidence_digest=digest,
            recommended_action="the decoder raised on its own protocol fixture",
        )
    missing_fields = tuple(
        field
        for field in contract.required_output_fields
        if field not in conformance.observed_output_fields
    )
    if (
        contract.output_protocol != conformance.output_protocol
        or missing_fields
    ):
        return Fact(
            fact_id=fact_id,
            status=FactStatus.DRIFT,
            expected=expected,
            observed=(
                f"protocol={conformance.output_protocol}; "
                f"fields={conformance.observed_output_fields}; "
                f"missing={missing_fields}"
            ),
            source_tag="empirical_probe",
            probe_command=probe,
            exit_code=None,
            evidence_digest=digest,
            recommended_action=(
                "the recorded output protocol or required fields no longer "
                "match the fixture exercised by this adapter; verify the "
                "vendor output before changing the human-maintained contract"
            ),
        )
    if conformance.canonical_text == expected_conformance_text():
        return Fact(
            fact_id=fact_id,
            status=FactStatus.PASS,
            expected=expected,
            observed=(
                f"protocol={conformance.output_protocol}; "
                f"fields={conformance.observed_output_fields}; "
                f"canonical_text matched; events={conformance.event_kinds}"
            ),
            source_tag="empirical_probe",
            probe_command=probe,
            exit_code=None,
            evidence_digest=digest,
            recommended_action="none",
        )
    return Fact(
        fact_id=fact_id,
        status=FactStatus.DRIFT,
        expected=expected,
        observed=f"canonical_text={conformance.canonical_text!r}",
        source_tag="empirical_probe",
        probe_command=probe,
        exit_code=None,
        evidence_digest=digest,
        recommended_action=(
            "the decoder no longer extracts the response from its own "
            "protocol shape; version/help success cannot substitute for this"
        ),
    )


def compare_dependencies(snapshot: DependencySnapshot) -> tuple[Fact, ...]:
    facts: list[Fact] = []
    for observation in snapshot.observations:
        # Scope the id by extra: the same distribution may legitimately be
        # declared both as a runtime dependency and inside an extra, and
        # two facts sharing an id would be indistinguishable in the report.
        fact_id = (
            f"dependency.{observation.distribution}"
            if observation.extra is None
            else f"dependency.{observation.extra}.{observation.distribution}"
        )
        digest = evidence_digest(
            observation.requirement,
            observation.installed_version,
            observation.extra,
        )
        if observation.installed_version is None:
            # A missing *runtime* dependency is real drift. A missing
            # optional extra usually just means the contributor never ran
            # `pip install -e .[dev]`, which is the dependency-side
            # equivalent of an ABSENT peer -- reported, not escalated.
            optional = observation.extra is not None
            facts.append(
                Fact(
                    fact_id=fact_id,
                    status=(
                        FactStatus.REVIEW_REQUIRED if optional
                        else FactStatus.DRIFT
                    ),
                    expected=observation.requirement,
                    observed=(
                        f"not installed (optional extra {observation.extra!r})"
                        if optional
                        else "not installed"
                    ),
                    source_tag="empirical_probe",
                    probe_command="importlib.metadata.version()",
                    exit_code=None,
                    evidence_digest=digest,
                    recommended_action=(
                        f"install the extra (`pip install -e .[{observation.extra}]`) "
                        "if you need this check to be meaningful here"
                        if optional
                        else "install it, or drop the declaration"
                    ),
                )
            )
            continue
        if observation.satisfied is None:
            facts.append(
                Fact(
                    fact_id=fact_id,
                    status=FactStatus.REVIEW_REQUIRED,
                    expected=observation.requirement,
                    observed=f"{observation.installed_version} ({observation.note})",
                    source_tag="empirical_probe",
                    probe_command="importlib.metadata.version()",
                    exit_code=None,
                    evidence_digest=digest,
                    recommended_action="install `packaging` to evaluate specifiers",
                )
            )
            continue
        facts.append(
            Fact(
                fact_id=fact_id,
                status=(
                    FactStatus.PASS if observation.satisfied else FactStatus.DRIFT
                ),
                expected=observation.requirement,
                observed=observation.installed_version,
                source_tag="empirical_probe",
                probe_command="importlib.metadata.version()",
                exit_code=None,
                evidence_digest=digest,
                recommended_action=(
                    "none"
                    if observation.satisfied
                    else "installed version violates the declared specifier"
                ),
            )
        )

    pip_check_status = (
        FactStatus.ERROR
        if snapshot.pip_check_exit is None
        else (
            FactStatus.PASS
            if snapshot.pip_check_exit == 0
            else FactStatus.DRIFT
        )
    )
    facts.append(
        Fact(
            fact_id="dependency.pip_check",
            status=pip_check_status,
            expected="pip check reports a coherent environment",
            observed=snapshot.pip_check_output or "(no output)",
            source_tag="empirical_probe",
            probe_command="python -m pip check",
            exit_code=snapshot.pip_check_exit,
            evidence_digest=evidence_digest(snapshot.pip_check_output),
            recommended_action=(
                "none"
                if snapshot.pip_check_exit == 0
                else (
                    "pip check could not run; repair the active environment "
                    "before trusting dependency facts"
                    if snapshot.pip_check_exit is None
                    else "resolve the reported incompatibility"
                )
            ),
        )
    )
    facts.append(
        Fact(
            fact_id="dependency.lockfile",
            status=FactStatus.PASS,
            expected="a lockfile is optional",
            observed=snapshot.lockfile or "UNLOCKED",
            source_tag="empirical_probe",
            probe_command="stat uv.lock / poetry.lock / requirements.lock",
            exit_code=None,
            evidence_digest=evidence_digest(snapshot.lockfile),
            recommended_action=(
                "none - absence of a lockfile is reported as UNLOCKED, not a "
                "failure; adopting a lock format is a separate packaging "
                "decision this routine does not make"
            ),
        )
    )
    return tuple(facts)


def compare_suite(
    counts: dict[str, Any], raw_output: str, exit_code: int | None
) -> Fact:
    digest = evidence_digest(raw_output)
    observed = (
        f"passed={counts.get('passed')} failed={counts.get('failed')} "
        f"deselected={counts.get('deselected')} "
        f"duration={counts.get('duration')}s"
    )
    if exit_code is None:
        return Fact(
            fact_id="suite.pytest",
            status=FactStatus.ERROR,
            expected="pytest -q runs and is green",
            observed=raw_output[:400],
            source_tag="empirical_probe",
            probe_command="python -m pytest -q",
            exit_code=None,
            evidence_digest=digest,
            recommended_action="the suite failed to run at all",
        )
    return Fact(
        fact_id="suite.pytest",
        status=FactStatus.PASS if exit_code == 0 else FactStatus.ERROR,
        expected="pytest -q runs and is green",
        observed=observed,
        source_tag="empirical_probe",
        probe_command="python -m pytest -q",
        exit_code=exit_code,
        evidence_digest=digest,
        recommended_action=(
            "none - a changed count on a green run is not drift"
            if exit_code == 0
            else "the suite is red; fix before trusting any other fact"
        ),
    )


def _not_run(fact_id: str, expected: str, reason: str) -> Fact:
    return Fact(
        fact_id=fact_id,
        status=FactStatus.NOT_RUN,
        expected=expected,
        observed=reason,
        source_tag="absent",
        probe_command="(skipped)",
        exit_code=None,
        evidence_digest=evidence_digest(fact_id, reason),
        recommended_action="none",
    )
