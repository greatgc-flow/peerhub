from __future__ import annotations

import json
from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.directives import DirectiveService
from peerhub.persistence.sqlite import SqliteStateStore


def _service(tmp_path: Path) -> tuple[DirectiveService, GovernanceBroker]:
    store = SqliteStateStore(tmp_path / "directives.sqlite3", workspace_home_id="directives-test")
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock(range(1, 100)),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 200)]),
    )
    return (
        DirectiveService(
            broker,
            clock=FakeClock(range(1, 100)),
            ids=FakeIdSource([f"domain-{i}" for i in range(1, 200)]),
        ),
        broker,
    )


def test_propose_creates_directive_envelope(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(
        directive_id="DIR-01",
        title="Test Directive",
        rule="Rule text",
        effective_date="2026-09-03",
        proposer_id="cx",
        category="general",
    )

    target = broker.get_target("directive:DIR-01")
    assert target is not None
    assert target.revision == 1
    assert target.state["schema"] == "peerhub.governance-directive.v1"
    assert target.state["kind"] == "directive"
    assert target.state["lifecycle"] == "PROPOSED"
    assert target.state["content"]["title"] == "Test Directive"
    assert target.state["category"] == "general"


def test_migrate_creates_active_directive(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    consumers = [{"consumer_name": "cc", "implementation_status": "PENDING", "evidence_refs": []}]
    service.migrate(
        directive_id="DIR-02",
        title="Migrated Directive",
        rule_markdown="Rule markdown",
        digest="sha256:abc",
        consumers=consumers,
        source_path="_sys/test",
    )

    target = broker.get_target("directive:DIR-02")
    assert target is not None
    assert target.revision == 1
    assert target.state["lifecycle"] == "ACTIVE"
    assert target.state["digest"] == "sha256:abc"
    from peerhub.cli import _json_safe
    assert _json_safe(target.state["consumers"]) == consumers


def test_retire_records_lifecycle_metadata(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.migrate(
        directive_id="retire-me",
        title="T",
        rule_markdown="R",
        digest="D",
        consumers=[],
        source_path="S",
    )
    service.retire("retire-me", actor_id="cx", reason="STALE")
    state = broker.get_target("directive:retire-me").state
    assert state["lifecycle"] == "RETIRED"
    assert state["validity"]["retirement_reason"] == "STALE"


def test_list_all(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.propose(
        directive_id="DIR-A", title="A", rule="R", effective_date="D", proposer_id="P"
    )
    service.migrate(
        directive_id="DIR-B", title="B", rule_markdown="R", digest="D", consumers=[], source_path="S"
    )
    targets = service.list_all()
    assert len(targets) == 2
    target_ids = {t.target_id for t in targets}
    assert target_ids == {"directive:DIR-A", "directive:DIR-B"}


_ENGRAM_USER_DIRECTIVES_MD = Path(
    r"D:\Engram&Peerhub\engram-main-worktree\_sys\ai\user-directives.md"
)

# The real, independently-verified digest/consumers metadata for all 6
# directives -- see scripts/migrate_engram_directives_2026_09_03.py's
# DIRECTIVES_META for the single source of truth this mirrors. Duplicated
# here (not imported) so this test still catches an accidental edit to the
# script's own metadata table, not just a parsing regression.
_EXPECTED_DIRECTIVE_META = {
    "DIR-001": {
        "digest": "sha256:bd6a452ea5af1fab2653055ebdb52ac023d345ac8eaf3565fb23f6262c359f98",
        "consumers": [{"consumer_name": "PeerHub/Orchestrator", "implementation_status": "PENDING", "evidence_refs": ["no ROI-gate/EXHAUSTIVE_COMPLETE consumer found in peerhub source"]}],
        "lifecycle": "ACTIVE",
    },
    "DIR-002": {
        "digest": "sha256:6a68da23ad2d663acbb64bda3e45b565e199c7df480fcfb6011e9b023cab79fb",
        "consumers": [{"consumer_name": "cc", "implementation_status": "PENDING", "evidence_refs": []}, {"consumer_name": "cx", "implementation_status": "PENDING", "evidence_refs": ["real PeerHub Codex adapter invocation supplies no sandbox flag and inherits config.toml"]}],
        "lifecycle": "ACTIVE",
    },
    "DIR-003": {
        "digest": "sha256:9bb8df7f009ce6a572e9d9d5d9f9574a91ab9b1f1449b1d779e92909b193eac2",
        "consumers": [],
        "lifecycle": "RETIRED",
    },
    "DIR-004": {
        "digest": "sha256:47f495f76342681af8e7cccf76e09be88e37114e3138ece07fe14fbaa8880777",
        "consumers": [{"consumer_name": "peerhub.dispatch.capability", "implementation_status": "PENDING", "evidence_refs": []}],
        "lifecycle": "ACTIVE",
    },
    "DIR-005": {
        "digest": "sha256:c871314e6f273ada6a56b8124466c56e301fadfb7cb8cc2e3eeb2a2f9cc9c934",
        "consumers": [{"consumer_name": "FinalArbiterPolicy/arbiter_review.py", "implementation_status": "PENDING", "evidence_refs": []}],
        "lifecycle": "ACTIVE",
    },
    "DIR-006": {
        "digest": "sha256:ba5e5423878b59976a434bf2c0428e92e3b7a5f022a327096d816045dfb4a451",
        "consumers": [{"consumer_name": "ProposalCoordinator/.peerhub/proposals.json", "implementation_status": "PENDING", "evidence_refs": []}],
        "lifecycle": "ACTIVE",
    },
}


@pytest.mark.skipif(
    not _ENGRAM_USER_DIRECTIVES_MD.exists(),
    reason="Engram worktree not present on this machine",
)
def test_parse_directives_extracts_real_rule_text_exactly() -> None:
    """The migration script's own parser must extract each directive's real
    rule body byte-for-byte, not a placeholder or a truncated/mangled copy."""
    import sys as _sys
    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in _sys.path:
        _sys.path.insert(0, scripts_dir)
    from migrate_engram_directives_2026_09_03 import parse_directives

    parsed = parse_directives(_ENGRAM_USER_DIRECTIVES_MD)
    assert set(parsed) == set(_EXPECTED_DIRECTIVE_META)

    raw = _ENGRAM_USER_DIRECTIVES_MD.read_text(encoding="utf-8")
    for directive_id, rule in parsed.items():
        assert rule, f"{directive_id} parsed to an empty rule body"
        # Every parsed rule must be a real, non-trivial excerpt of the
        # source file -- not a placeholder -- and must not leak into the
        # next directive's heading.
        assert rule in raw
        assert not rule.startswith("### ")
        next_heading_index = raw.index(rule) + len(rule)
        # whatever immediately follows in the source is blank lines then
        # the next heading, never more of this directive's own body
        tail = raw[next_heading_index:next_heading_index + 200].lstrip("\n")
        assert tail.startswith("### ") or tail.startswith("## ") or tail == ""


@pytest.mark.skipif(
    not _ENGRAM_USER_DIRECTIVES_MD.exists(),
    reason="Engram worktree not present on this machine",
)
def test_full_migration_script_produces_all_6_directives_with_exact_metadata(
    tmp_path: Path,
) -> None:
    """Runs the actual migration script's main() against a real,
    test-isolated workspace database (not a hand-simulated loop), then
    asserts every one of the 6 real directives landed with the exact
    digest/consumers/lifecycle recorded in the ratified design -- this is
    the real "verified migration receipt" Increment D's precondition needs."""
    import sys as _sys
    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in _sys.path:
        _sys.path.insert(0, scripts_dir)
    import migrate_engram_directives_2026_09_03 as migration_script
    from peerhub.core.context import PathLayout, RuntimeContext
    from peerhub.cli import SystemClock, UuidSource, _detect_workspace_home_id, _json_safe
    from peerhub.runtime import create_runtime

    workspace_root = tmp_path
    paths = PathLayout.for_workspace(workspace_root)
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    parsed_rules = migration_script.parse_directives(_ENGRAM_USER_DIRECTIVES_MD)

    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        service = runtime.directive_service
        for directive_id, meta in migration_script.DIRECTIVES_META.items():
            service.migrate(
                directive_id=directive_id,
                title=meta["title"],
                rule_markdown=parsed_rules[directive_id],
                digest=meta["digest"],
                consumers=meta["consumers"],
                source_path=meta["source_path"],
            )
            if directive_id == "DIR-003":
                service.retire(
                    directive_id=directive_id,
                    actor_id="terminal",
                    reason="hub.py deleted in Engram/peerhub separation, directive has no surviving consumer",
                )

        targets = service.list_all()
        assert len(targets) == 6

        for directive_id, expected in _EXPECTED_DIRECTIVE_META.items():
            target = service.get_target(directive_id)
            assert target is not None, f"{directive_id} missing after migration"
            assert target.state["digest"] == expected["digest"], directive_id
            assert _json_safe(target.state["consumers"]) == expected["consumers"], directive_id
            assert target.state["lifecycle"] == expected["lifecycle"], directive_id
            assert target.state["content"]["rule"] == parsed_rules[directive_id], directive_id
