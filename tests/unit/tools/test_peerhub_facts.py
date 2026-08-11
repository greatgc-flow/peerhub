"""Tests for tools/peerhub_facts (docs/design/FACT-REFRESH-PROCEDURE-R1.md).

These deliberately do more than prove the routine runs. A drift detector
that only ever sees a healthy machine is indistinguishable from one that
returns PASS unconditionally, so every check the procedure specifies is
exercised twice: once against a healthy observation, and once against a
real drifted one, asserting that the drifted case is actually caught and
lands on the right status and exit code.

The complementary risk is a detector that cries wolf, so the false-drift
cases the procedure calls out by name -- a Codex sandbox warning prefix,
reworded vendor help text, a peer that simply isn't installed, a changed
test count on a green run -- are asserted to stay PASS.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from peerhub.adapters.registry import (
    ExecutableNotFoundError,
    ProfileNotFoundError,
    ResolvedPeerTarget,
    resolve_peer_adapter,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ``tests/unit/tools`` is a committed package, so pytest imports it as the
# top-level name ``tools`` and would otherwise hide the repository's real
# ``tools`` package. Extend that package search path explicitly instead of
# deleting the existing package marker as the interrupted implementation did.
import tools as tools_test_package

project_tools_path = str(PROJECT_ROOT / "tools")
if project_tools_path not in tools_test_package.__path__:
    tools_test_package.__path__.append(project_tools_path)

from tools.peerhub_facts import __main__ as facts_main
from tools.peerhub_facts import collectors
from tools.peerhub_facts.collectors import (
    DependencyObservation,
    DependencySnapshot,
    PeerResolution,
    ProbeResult,
    decoder_conformance,
    find_help_tokens,
    parse_pytest_summary,
    parse_version,
)
from tools.peerhub_facts.compare import (
    DEFAULT_CONTRACTS_PATH,
    PeerContract,
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
    evidence_digest,
)
# Aliased: pytest would otherwise try to collect `TestSuiteSnapshot` as a
# test class purely because of its leading "Test".
from tools.peerhub_facts.model import TestSuiteSnapshot as SuiteSnapshot


# --- helpers ---------------------------------------------------------------


def _contract(
    alias: str = "cc",
    *,
    verified_versions: tuple[str, ...] = ("2.1.222",),
    required_help_tokens: tuple[str, ...] = ("-p", "--output-format"),
    output_protocol: str | None = None,
    required_output_fields: tuple[str, ...] | None = None,
) -> PeerContract:
    protocols = {
        "ag": ("flat-json", ("response",)),
        "cc": ("claude-result-json", ("result", "is_error")),
        "cx": ("jsonl-events", ("type", "item")),
    }
    default_protocol, default_fields = protocols[alias]
    return PeerContract(
        alias=alias,
        cli_name=f"{alias}.cmd",
        output_protocol=output_protocol or default_protocol,
        required_output_fields=(
            required_output_fields
            if required_output_fields is not None
            else default_fields
        ),
        verified_versions=verified_versions,
        version_argv=("--version",),
        help_argv=("--help",),
        required_help_tokens=required_help_tokens,
    )


def _probe(stdout: str, *, exit_code: int = 0) -> ProbeResult:
    return ProbeResult(
        command="fake --version", exit_code=exit_code, stdout_text=stdout, error=None
    )


def _report(*facts: Fact) -> FactsReport:
    return FactsReport(
        generated_at="2026-08-12T00:00:00+00:00",
        head_sha="deadbee",
        live=False,
        facts=facts,
    )


def _fact(status: FactStatus) -> Fact:
    return Fact(
        fact_id="synthetic",
        status=status,
        expected="e",
        observed="o",
        source_tag="empirical_probe",
        probe_command="none",
        exit_code=0,
        evidence_digest=evidence_digest("x"),
        recommended_action="none",
    )


def _resolved(alias: str) -> ResolvedPeerTarget:
    """A target that does not require the CLI to exist on this machine."""

    adapter = resolve_peer_adapter(alias)
    return ResolvedPeerTarget(
        cli_name=alias,
        peer_kind=alias,
        adapter=adapter,
        profile=adapter.descriptor.profiles[0],
        executable_path=Path("C:/nowhere") / f"{alias}.cmd",
    )


# --- step 3: version drift -------------------------------------------------


def test_version_matching_a_verified_version_passes() -> None:
    fact = compare_version(_contract(), _probe("2.1.222 (Claude Code)\n"))
    assert fact.status is FactStatus.PASS


def test_version_drift_is_detected() -> None:
    """The load-bearing case: the CLI moved off every verified version."""

    fact = compare_version(_contract(), _probe("2.1.999 (Claude Code)\n"))
    assert fact.status is FactStatus.DRIFT
    assert fact.observed == "2.1.999"
    assert _report(fact).exit_code == 1


def test_version_drift_never_recommends_auto_updating_the_contract() -> None:
    """Blessing observed drift is the failure mode the routine exists to catch."""

    fact = compare_version(_contract(), _probe("9.9.9\n"))
    assert "never adds it automatically" in fact.recommended_action


def test_codex_version_survives_a_sandbox_warning_prefix() -> None:
    """A warning prefix is not drift -- and its paths are not the version.

    Regression for OBS-0002: the warning block here carries a semver-shaped
    path segment (``0.1.0``) that a naive "first number wins" scan locks
    onto, silently reporting drift on a healthy install.
    """

    raw = (
        "WARNING: sandbox is not enforced on this platform\n"
        "warning: reading config from C:\\tools\\codex\\0.1.0\\config.toml\n"
        "codex-cli 0.147.0\n"
    )
    assert parse_version("cx", raw) == "0.147.0"

    contract = _contract("cx", verified_versions=("0.147.0",))
    assert compare_version(contract, _probe(raw)).status is FactStatus.PASS


def test_claude_version_ignores_the_trailing_product_name() -> None:
    assert parse_version("cc", "2.1.222 (Claude Code)\n") == "2.1.222"


def test_bare_semver_banner_parses_for_a_vendor_without_a_marker() -> None:
    """agy prints a bare version with no product name of its own."""

    assert parse_version("ag", "1.1.12\n") == "1.1.12"


def test_unparseable_version_is_review_required_not_drift() -> None:
    """A changed --version *shape* needs a human, not a drift verdict."""

    fact = compare_version(_contract(), _probe("version: unreleased-build\n"))
    assert fact.status is FactStatus.REVIEW_REQUIRED
    assert _report(fact).exit_code == 1


def test_noise_only_version_never_guesses_from_a_path() -> None:
    raw = "warning: config path C:\\tools\\codex\\9.9.9\\config.toml\n"
    assert parse_version("cx", raw) is None
    fact = compare_version(_contract("cx"), _probe(raw))
    assert fact.status is FactStatus.REVIEW_REQUIRED


def test_version_with_no_recorded_expectation_is_review_required() -> None:
    fact = compare_version(
        _contract(verified_versions=()), _probe("2.1.222 (Claude Code)\n")
    )
    assert fact.status is FactStatus.REVIEW_REQUIRED


def test_failed_version_probe_is_an_error() -> None:
    fact = compare_version(_contract(), _probe("", exit_code=127))
    assert fact.status is FactStatus.ERROR
    assert _report(fact).exit_code == 2


def test_version_probe_is_wired_through_the_supervised_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 3 requires the supervised pipe mechanism, not a bare subprocess."""

    seen: dict[str, Any] = {}

    def fake_run_process(config: Any, supervisor: Any) -> Any:
        seen["argv"] = tuple(config.argv)
        seen["supervisor"] = supervisor
        raise RuntimeError("probe stopped after wiring was observed")

    monkeypatch.setattr(collectors, "run_process", fake_run_process)
    result = collectors.probe_cli(_resolved("cc"), ("--version",))

    assert seen["argv"][-1] == "--version"
    assert isinstance(seen["supervisor"], collectors.ProcessSupervisor)
    assert result.error is not None and result.exit_code is None


# --- step 4: help tokens ---------------------------------------------------


def test_help_tokens_present_pass() -> None:
    fact = compare_help(_contract(), _probe("  -p, --print\n  --output-format json\n"))
    assert fact.status is FactStatus.PASS


def test_help_token_drift_is_detected() -> None:
    """The adapter builds argv from these flags; a vanished flag breaks it."""

    fact = compare_help(_contract(), _probe("  -p, --print\n"))
    assert fact.status is FactStatus.DRIFT
    assert "--output-format" in fact.observed
    assert _report(fact).exit_code == 1


def test_reworded_help_text_is_not_drift() -> None:
    """Semantic token presence, not exact-text comparison (step 4)."""

    original = "Usage: claude [options]\n  -p, --print   Print response\n  --output-format <fmt>\n"
    reworded = (
        "USAGE\n\n    claude [OPTIONS]\n\nOPTIONS\n"
        "    -p, --print            print the response and exit\n"
        "    --output-format <FMT>  one of: text, json, stream-json\n"
    )
    assert find_help_tokens(original, ("-p", "--output-format")) == ()
    assert find_help_tokens(reworded, ("-p", "--output-format")) == ()
    assert compare_help(_contract(), _probe(reworded)).status is FactStatus.PASS


def test_help_token_substrings_do_not_false_pass() -> None:
    help_text = "  --permission MODE\n  --output-formatting STYLE\n"
    assert find_help_tokens(help_text, ("-p", "--output-format")) == (
        "-p",
        "--output-format",
    )


def test_cx_contract_probes_exec_help_not_top_level_help() -> None:
    """Regression for OBS-0005 -- the routine's first find about itself.

    ``--json`` is a flag of codex's ``exec`` subcommand and is absent from
    its top-level help, so probing ``--help`` reports a false DRIFT on a
    healthy install.
    """

    contracts = load_contracts(DEFAULT_CONTRACTS_PATH)
    assert contracts.peers["cx"].help_argv == ("exec", "--help")
    assert "--json" in contracts.peers["cx"].required_help_tokens


# --- step 5: decoder conformance ------------------------------------------


@pytest.mark.parametrize("peer_kind", ["ag", "cc", "cx"])
def test_decoder_conformance_passes_for_every_peer(peer_kind: str) -> None:
    fact = compare_decoder(
        _contract(peer_kind), decoder_conformance(peer_kind)
    )
    assert fact.status is FactStatus.PASS


def test_decoder_conformance_tolerates_claude_warning_prefix() -> None:
    """OBS-0003: claude.cmd can print a warning before its JSON object."""

    payload = (
        b"Warning: no stdin data received\n"
        b'{"is_error": false, "result": "peerhub-facts-conformance"}'
    )
    result = decoder_conformance("cc", payload=payload)
    assert result.canonical_text == collectors.expected_conformance_text()


@pytest.mark.parametrize(
    ("peer_kind", "drifted_payload"),
    [
        # agy renames its flat response key.
        ("ag", b'{"reply": "peerhub-facts-conformance"}'),
        # claude nests the answer instead of exposing a top-level result.
        ("cc", b'{"is_error": false, "output": {"text": "peerhub-facts-conformance"}}'),
        # codex renames the terminal event type.
        (
            "cx",
            b'{"type": "item.finished", "item": {"type": "agent_message", '
            b'"text": "peerhub-facts-conformance"}}\n',
        ),
    ],
)
def test_decoder_drift_is_detected_when_the_protocol_shape_changes(
    peer_kind: str, drifted_payload: bytes
) -> None:
    """Version and help success cannot substitute for this check.

    Each payload is a realistic vendor schema change pushed through the
    *real* decoder: the CLI is installed, its version is verified, its help
    still lists every flag -- and the response is silently no longer found.
    """

    result = decoder_conformance(peer_kind, payload=drifted_payload)
    assert result.canonical_text != collectors.expected_conformance_text()

    fact = compare_decoder(_contract(peer_kind), result)
    assert fact.status is FactStatus.DRIFT
    assert _report(fact).exit_code == 1


def test_decoder_raising_on_its_own_fixture_is_an_error() -> None:
    conformance = collectors.DecoderConformance(
        peer_kind="cc",
        output_protocol="claude-result-json",
        fixture_digest_input=b"{}",
        observed_output_fields=(),
        canonical_text=None,
        event_kinds=(),
        error="RuntimeError: boom",
    )
    fact = compare_decoder(_contract(), conformance)
    assert fact.status is FactStatus.ERROR
    assert _report(fact).exit_code == 2


def test_decoder_protocol_contract_drift_is_detected() -> None:
    conformance = decoder_conformance("ag")
    fact = compare_decoder(
        _contract("ag", output_protocol="jsonl-events"),
        conformance,
    )
    assert fact.status is FactStatus.DRIFT
    assert "protocol=flat-json" in fact.observed


def test_decoder_required_field_contract_drift_is_detected() -> None:
    conformance = decoder_conformance("cc")
    fact = compare_decoder(
        _contract(
            "cc",
            required_output_fields=("result", "is_error", "usage"),
        ),
        conformance,
    )
    assert fact.status is FactStatus.DRIFT
    assert "usage" in fact.observed


# --- step 2: graceful degradation -----------------------------------------


def test_absent_peer_is_recorded_but_is_not_drift() -> None:
    """A dev box without claude.cmd installed must still exit 0."""

    resolution = PeerResolution("cc", None, "executable 'claude.cmd' not found in PATH")
    resolution_fact = compare_resolution(resolution)
    version_fact = compare_version(_contract(), None)
    help_fact = compare_help(_contract(), None)

    assert resolution_fact.status is FactStatus.NOT_RUN
    assert "ABSENT" in resolution_fact.observed
    assert version_fact.status is FactStatus.NOT_RUN
    assert help_fact.status is FactStatus.NOT_RUN
    assert _report(resolution_fact, version_fact, help_fact).exit_code == 0


def test_missing_executable_is_classified_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_not_found(alias: str) -> ResolvedPeerTarget:
        raise ExecutableNotFoundError(f"executable {alias!r} not found in PATH")

    monkeypatch.setattr(collectors, "resolve_peer_target", raise_not_found)
    (resolution,) = collectors.resolve_peers(("cc",))

    assert resolution.absent_reason is not None
    assert resolution.failure_reason is None
    assert compare_resolution(resolution).status is FactStatus.NOT_RUN


def test_structural_resolution_failure_is_an_error_not_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken adapter must not be laundered into "not installed".

    ABSENT means the contributor lacks the CLI. A profile that no longer
    exists means the adapter is broken, and collapsing the two would hide
    exactly the drift this routine exists to catch.
    """

    def raise_profile_error(alias: str) -> ResolvedPeerTarget:
        raise ProfileNotFoundError(f"adapter {alias} has 2 profiles")

    monkeypatch.setattr(collectors, "resolve_peer_target", raise_profile_error)
    (resolution,) = collectors.resolve_peers(("cc",))

    assert resolution.absent_reason is None
    assert resolution.failure_reason is not None

    fact = compare_resolution(resolution)
    assert fact.status is FactStatus.ERROR
    assert _report(fact).exit_code == 2


def test_decoder_conformance_still_runs_for_an_absent_peer() -> None:
    """Protocol shape does not depend on a local install."""

    assert (
        decoder_conformance("cx").canonical_text
        == collectors.expected_conformance_text()
    )


# --- step 6: dependencies --------------------------------------------------


def _snapshot(*observations: DependencyObservation, **kwargs: Any) -> DependencySnapshot:
    return DependencySnapshot(
        observations=observations,
        pip_check_exit=kwargs.get("pip_check_exit", 0),
        pip_check_output=kwargs.get("pip_check_output", ""),
        lockfile=kwargs.get("lockfile"),
    )


def _fact_by_id(facts: tuple[Fact, ...], fact_id: str) -> Fact:
    return next(fact for fact in facts if fact.fact_id == fact_id)


def test_installed_version_violating_the_specifier_is_drift() -> None:
    """pyproject holds declared constraints; only the environment holds truth."""

    facts = compare_dependencies(
        _snapshot(
            DependencyObservation(
                "pydantic>=2.0", "pydantic", "1.10.2", False, "evaluated", None
            )
        )
    )
    fact = _fact_by_id(facts, "dependency.pydantic")
    assert fact.status is FactStatus.DRIFT
    assert FactsReport("t", "h", False, facts).exit_code == 1


def test_satisfied_dependency_passes() -> None:
    facts = compare_dependencies(
        _snapshot(
            DependencyObservation(
                "pydantic>=2.0", "pydantic", "2.9.0", True, "evaluated", None
            )
        )
    )
    assert _fact_by_id(facts, "dependency.pydantic").status is FactStatus.PASS


def test_missing_runtime_dependency_is_drift() -> None:
    facts = compare_dependencies(
        _snapshot(
            DependencyObservation("psutil>=5.9.0", "psutil", None, False, "x", None)
        )
    )
    assert _fact_by_id(facts, "dependency.psutil").status is FactStatus.DRIFT


def test_missing_optional_extra_is_review_required_not_drift() -> None:
    """A contributor who never ran `pip install -e .[dev]` is not drifting."""

    facts = compare_dependencies(
        _snapshot(
            DependencyObservation("pyright>=1.1.370", "pyright", None, False, "x", "dev")
        )
    )
    fact = _fact_by_id(facts, "dependency.dev.pyright")
    assert fact.status is FactStatus.REVIEW_REQUIRED
    assert ".[dev]" in fact.recommended_action


def test_a_distribution_in_both_runtime_and_an_extra_gets_distinct_facts() -> None:
    facts = compare_dependencies(
        _snapshot(
            DependencyObservation("pytest>=8.0", "pytest", "8.3.0", True, "x", None),
            DependencyObservation("pytest>=8.0", "pytest", "8.3.0", True, "x", "dev"),
        )
    )
    ids = {fact.fact_id for fact in facts}
    assert {"dependency.pytest", "dependency.dev.pytest"} <= ids


def test_absent_lockfile_is_unlocked_not_a_failure() -> None:
    """Adopting a lock format is a packaging decision the routine cannot make."""

    facts = compare_dependencies(_snapshot(lockfile=None))
    fact = _fact_by_id(facts, "dependency.lockfile")
    assert fact.status is FactStatus.PASS
    assert fact.observed == "UNLOCKED"


def test_pip_check_incoherence_is_drift() -> None:
    facts = compare_dependencies(
        _snapshot(pip_check_exit=1, pip_check_output="a 1.0 requires b<2, but you have b 2.0")
    )
    assert _fact_by_id(facts, "dependency.pip_check").status is FactStatus.DRIFT


def test_pip_check_that_cannot_run_is_an_error() -> None:
    facts = compare_dependencies(
        _snapshot(
            pip_check_exit=None,
            pip_check_output="FileNotFoundError: pip",
        )
    )
    fact = _fact_by_id(facts, "dependency.pip_check")
    assert fact.status is FactStatus.ERROR
    assert FactsReport("t", "h", False, facts).exit_code == 2


def test_collect_dependencies_reads_installed_versions_not_declarations(
    tmp_path: Path,
) -> None:
    """Step 6's correction: pyproject never says what is actually installed."""

    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "probe"\n'
        'dependencies = ["pytest>=1.0"]\n'
        "[project.optional-dependencies]\n"
        'dev = ["definitely-not-installed-xyz>=1.0"]\n',
        encoding="utf-8",
    )
    snapshot = collectors.collect_dependencies(tmp_path)
    by_name = {obs.distribution: obs for obs in snapshot.observations}

    installed = by_name["pytest"]
    assert installed.installed_version is not None
    assert installed.installed_version != "1.0"  # the declared floor, not the truth
    assert installed.satisfied is True
    assert installed.extra is None

    missing = by_name["definitely-not-installed-xyz"]
    assert missing.installed_version is None
    assert missing.extra == "dev"
    assert snapshot.lockfile is None


# --- step 7: the test suite ------------------------------------------------


def test_pytest_summary_parses_pytests_own_failures_first_ordering() -> None:
    """Regression: pytest prints failures before passes.

    A fixed-order pattern that assumed ``passed`` came first reported
    ``passed=None`` on exactly the red runs where the count matters.
    """

    parsed = parse_pytest_summary("1 failed, 527 passed, 3 deselected in 41.20s\n")
    assert parsed == {
        "passed": 527,
        "failed": 1,
        "deselected": 3,
        "duration": 41.20,
    }


def test_pytest_summary_parses_a_green_run() -> None:
    parsed = parse_pytest_summary("...\n528 passed, 3 deselected in 40.10s\n")
    assert parsed["passed"] == 528
    assert parsed["failed"] is None


def test_pytest_summary_folds_collection_errors_into_failures() -> None:
    parsed = parse_pytest_summary("1 failed, 2 errors, 10 passed in 3.00s\n")
    assert parsed["failed"] == 3


def test_green_suite_with_a_changed_count_is_not_drift() -> None:
    """"A changed count on a green run is not drift" -- verbatim from the spec."""

    fact = compare_suite(
        {"passed": 611, "failed": None, "deselected": 3, "duration": 44.0},
        "611 passed, 3 deselected in 44.00s",
        0,
    )
    assert fact.status is FactStatus.PASS
    assert _report(fact).exit_code == 0


def test_red_suite_blocks() -> None:
    fact = compare_suite(
        {"passed": 527, "failed": 1, "deselected": 3, "duration": 41.2},
        "1 failed, 527 passed, 3 deselected in 41.20s",
        1,
    )
    assert fact.status is FactStatus.ERROR
    assert _report(fact).exit_code == 2


def test_suite_that_could_not_run_at_all_is_an_error() -> None:
    fact = compare_suite({}, "FileNotFoundError: pytest", None)
    assert fact.status is FactStatus.ERROR
    assert _report(fact).exit_code == 2


def test_counts_block_renders_the_three_lines_the_procedure_specifies() -> None:
    snapshot = SuiteSnapshot(
        head_sha="abc1234",
        passed=530,
        failed=None,
        deselected=3,
        duration_seconds=40.0,
        raw_digest="sha256:x",
        exit_code=0,
    )
    assert snapshot.render_counts("528/528", "c77ebbb") == (
        "Current run:  530 passed at abc1234\n"
        "Last cited:   528/528 at c77ebbb\n"
        "Delta:        +2 tests (informational only)"
    )


# --- exit-code mapping -----------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected_exit"),
    [
        ((FactStatus.PASS,), 0),
        ((FactStatus.PASS, FactStatus.NOT_RUN), 0),
        ((FactStatus.PASS, FactStatus.DRIFT), 1),
        ((FactStatus.PASS, FactStatus.REVIEW_REQUIRED), 1),
        ((FactStatus.PASS, FactStatus.ERROR), 2),
        # An ERROR outranks drift: a probe that did not run makes the other
        # facts untrustworthy, so it must not be masked by an exit 1.
        ((FactStatus.DRIFT, FactStatus.ERROR), 2),
    ],
)
def test_exit_code_mapping(
    statuses: tuple[FactStatus, ...], expected_exit: int
) -> None:
    assert _report(*(_fact(status) for status in statuses)).exit_code == expected_exit


# --- the shipped contracts file -------------------------------------------


def test_shipped_contracts_cover_every_probed_alias() -> None:
    contracts = load_contracts(DEFAULT_CONTRACTS_PATH)
    assert set(contracts.peers) == set(collectors.PEER_ALIASES)
    for contract in contracts.peers.values():
        assert contract.verified_versions, contract.alias
        assert contract.required_help_tokens, contract.alias


def test_shipped_contracts_require_the_flags_each_adapter_actually_plans() -> None:
    """The contract is only meaningful if it tracks the real planned argv."""

    contracts = load_contracts(DEFAULT_CONTRACTS_PATH)
    planned = {"ag": ("-p", "--output-format"), "cc": ("-p", "--output-format"), "cx": ("exec", "--json")}
    for alias, flags in planned.items():
        assert set(flags) <= set(contracts.peers[alias].required_help_tokens)


def test_contracts_record_semantic_expectations_not_raw_snapshots() -> None:
    raw: dict[str, Any] = tomllib.loads(
        DEFAULT_CONTRACTS_PATH.read_text(encoding="utf-8")
    )
    for alias, entry in raw["peers"].items():
        for version in entry["verified_versions"]:
            # A raw --version snapshot would carry the vendor's banner text;
            # only the parsed semantic version belongs here.
            assert version == parse_version(alias, version), alias


def test_observations_log_exists_and_is_traceable() -> None:
    """Discoveries get a durable home instead of being rediscovered."""

    text = (
        PROJECT_ROOT / "docs" / "compatibility" / "peer-cli-observations.md"
    ).read_text(encoding="utf-8")
    for marker in ("OBS-0002", "OBS-0005", "PH-FACTS-OBS", "source tag"):
        assert marker in text


# --- end-to-end ------------------------------------------------------------


def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make an end-to-end run independent of this machine.

    Peers resolve as ABSENT (as they would on a bare CI box) and the
    dependency probe is stubbed, so what the run actually exercises is the
    argparse -> build_report -> write_reports -> exit-code path plus the
    real decoders.
    """

    def absent_everywhere(
        aliases: tuple[str, ...] = collectors.PEER_ALIASES,
    ) -> tuple[PeerResolution, ...]:
        return tuple(
            PeerResolution(alias, None, "not installed here") for alias in aliases
        )

    def no_dependencies(project_root: Path = PROJECT_ROOT) -> DependencySnapshot:
        return DependencySnapshot((), 0, "", None)

    def green_suite(
        project_root: Path = PROJECT_ROOT,
        *,
        extra_args: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], str, int | None]:
        del project_root, extra_args
        raw = "612 passed, 4 deselected in 42.50s\n"
        return (
            {
                "passed": 612,
                "failed": None,
                "deselected": 4,
                "duration": 42.5,
            },
            raw,
            0,
        )

    monkeypatch.setattr(facts_main, "resolve_peers", absent_everywhere)
    monkeypatch.setattr(facts_main, "collect_dependencies", no_dependencies)
    monkeypatch.setattr(facts_main, "run_test_suite", green_suite)


def test_cli_run_on_a_machine_without_any_peer_installed_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _hermetic(monkeypatch)
    exit_code = facts_main.main(["--project-root", str(tmp_path)])
    assert exit_code == 0

    output_dir = tmp_path / "build" / "peerhub-facts"
    report: dict[str, Any] = json.loads(
        (output_dir / "latest.json").read_text(encoding="utf-8")
    )
    statuses = {fact["fact_id"]: fact["status"] for fact in report["facts"]}
    assert statuses["peer.cc.resolution"] == "NOT_RUN"
    # Decoder conformance does not need the CLI, so it still really ran.
    assert statuses["decoder.cc.conformance"] == "PASS"
    assert report["suite"] == {
        "head_sha": report["head_sha"],
        "passed": 612,
        "failed": None,
        "deselected": 4,
        "duration_seconds": 42.5,
        "raw_digest": evidence_digest(
            "612 passed, 4 deselected in 42.50s\n"
        ),
        "exit_code": 0,
    }
    markdown = (output_dir / "latest.md").read_text(encoding="utf-8")
    assert markdown.startswith("# peerhub facts report")
    facts_header = next(
        line for line in markdown.splitlines() if line.startswith("| fact |")
    )
    for required_field in ("source", "probe", "evidence", "exit"):
        assert required_field in facts_header


def test_cli_run_detects_drift_from_the_contracts_file_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end drift: a real contract mismatch must not report PASS."""

    _hermetic(monkeypatch)
    contracts_path = tmp_path / "drifted-contracts.toml"
    contracts_path.write_text(
        DEFAULT_CONTRACTS_PATH.read_text(encoding="utf-8").replace(
            'output_protocol = "flat-json"',
            'output_protocol = "jsonl-events"',
            1,
        ),
        encoding="utf-8",
    )

    exit_code = facts_main.main(
        [
            "--contracts",
            str(contracts_path),
            "--project-root",
            str(tmp_path),
        ]
    )
    assert exit_code == 1

    output_dir = tmp_path / "build" / "peerhub-facts"
    report: dict[str, Any] = json.loads(
        (output_dir / "latest.json").read_text(encoding="utf-8")
    )
    drifted = [
        fact for fact in report["facts"] if fact["status"] == "DRIFT"
    ]
    assert {fact["fact_id"] for fact in drifted} == {
        "decoder.ag.conformance"
    }
    assert report["exit_code"] == 1


def test_cli_run_reports_a_peer_missing_from_the_contracts_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _hermetic(monkeypatch)
    contracts_path = tmp_path / "partial-contracts.toml"
    contracts_path.write_text(
        'schema_version = "1"\n[peers.ag]\ncli_name = "agy.exe"\n'
        'verified_versions = ["1.1.12"]\n',
        encoding="utf-8",
    )
    exit_code = facts_main.main(
        [
            "--contracts",
            str(contracts_path),
            "--project-root",
            str(tmp_path),
        ]
    )
    assert exit_code == 1

    report: dict[str, Any] = json.loads(
        (
            tmp_path / "build" / "peerhub-facts" / "latest.json"
        ).read_text(encoding="utf-8")
    )
    statuses = {fact["fact_id"]: fact["status"] for fact in report["facts"]}
    assert statuses["peer.cc.contract"] == "REVIEW_REQUIRED"
    assert statuses["peer.cx.contract"] == "REVIEW_REQUIRED"


def test_reports_are_written_only_under_the_requested_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Output contract: reports live under build/, which is gitignored."""

    _hermetic(monkeypatch)
    target = tmp_path / "build" / "peerhub-facts"
    facts_main.main(["--project-root", str(tmp_path)])
    assert sorted(path.name for path in target.iterdir()) == [
        "latest.json",
        "latest.md",
    ]
    assert facts_main.DEFAULT_OUTPUT_DIR.parent.name == "build"
    assert all(action.dest != "output_dir" for action in facts_main.build_parser()._actions)


def test_invalid_contracts_fail_with_exit_two_and_still_write_a_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _hermetic(monkeypatch)
    contracts_path = tmp_path / "invalid.toml"
    contracts_path.write_text("this is not toml", encoding="utf-8")

    assert facts_main.main(
        [
            "--contracts",
            str(contracts_path),
            "--project-root",
            str(tmp_path),
        ]
    ) == 2
    report: dict[str, Any] = json.loads(
        (
            tmp_path / "build" / "peerhub-facts" / "latest.json"
        ).read_text(encoding="utf-8")
    )
    assert report["facts"][0]["fact_id"] == "routine.setup"
    assert report["facts"][0]["status"] == "ERROR"


# --- real peers (deselected by default; spends no model quota) -------------


@pytest.mark.slow
def test_real_peer_versions_match_the_shipped_contracts() -> None:
    """Measured, not assumed (DIR-004). Skips wherever a peer is absent."""

    contracts = load_contracts(DEFAULT_CONTRACTS_PATH)
    checked = 0
    for resolution in collectors.resolve_peers():
        assert resolution.failure_reason is None, resolution.failure_reason
        if resolution.target is None:
            continue
        contract = contracts.peers[resolution.alias]
        version_fact = compare_version(
            contract, collectors.probe_cli(resolution.target, contract.version_argv)
        )
        help_fact = compare_help(
            contract, collectors.probe_cli(resolution.target, contract.help_argv)
        )
        assert version_fact.status is FactStatus.PASS, version_fact.observed
        assert help_fact.status is FactStatus.PASS, help_fact.observed
        checked += 1
    if checked == 0:
        pytest.skip("no peer CLI installed on this machine")
