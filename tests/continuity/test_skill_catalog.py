"""M2.3 Skill and General Capability Catalog Test Suite (SKL-001..014).

Verifies:
- SKL-001: Skill directory indexing computes canonical deterministic SHA-256 tree digest.
- SKL-002: Skill lifecycle transitions DISCOVERED -> VALIDATED -> ACTIVE.
- SKL-003: Suspending an active skill moves it to SUSPENDED.
- SKL-004: Resuming a suspended skill restores ACTIVE state.
- SKL-005: Retiring a skill moves it to terminal RETIRED state.
- SKL-006: Forbidden direct transition DISCOVERED to ACTIVE is rejected.
- SKL-007: Querying non-existent skill raises SkillNotFoundError.
- SKL-008: Malformed SKILL.md frontmatter raises SkillManifestError.
- SKL-009: Post-index file modification detected as tampered (SkillTamperedError).
- SKL-010: Attempt to invoke inactive skill raises UnauthorizedSkillError.
- SKL-011: Capability catalog records declared capabilities with CAS revision gating.
- SKL-012: Conflicting capability declaration is rejected (CapabilityConflictError).
- SKL-013: Volatile runtime measurements in capability catalog are strictly rejected (VolatileFactRejectedError).
- SKL-014: Wiping SQLite skill index and replaying stream records + directory scan reconstructs full state.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import pytest

from peerhub.core.models import Peer, Stream, StreamState
from peerhub.core.store import CoreStore
from peerhub.extensions.skills import (
    SkillCatalogEngine,
    SkillItem,
    CapabilityItem,
    SkillNotFoundError,
    SkillManifestError,
    SkillTamperedError,
    UnauthorizedSkillError,
    CapabilityConflictError,
    CapabilityNotFoundError,
    VolatileFactRejectedError,
    ForbiddenTransitionError,
    SkillRevisionConflictError,
)


@pytest.fixture
def store(tmp_path: Path) -> CoreStore:
    s = CoreStore(tmp_path / "core.db")
    s.register_peer(Peer(peer_id="p1", display_name="Peer 1"))
    s.create_stream(Stream(stream_id="s1", members=["p1"], state=StreamState.OPEN))
    return s


@pytest.fixture
def catalog_engine(tmp_path: Path, store: CoreStore) -> SkillCatalogEngine:
    db_path = tmp_path / "skill_catalog.db"
    return SkillCatalogEngine(db_path, store=store)


@pytest.fixture
def valid_skill_dir(tmp_path: Path) -> Path:
    sdir = tmp_path / "skills" / "code_review"
    sdir.mkdir(parents=True, exist_ok=True)
    manifest = sdir / "SKILL.md"
    manifest.write_text(
        "---\n"
        "name: code_review\n"
        "description: Automated peer code review assistant\n"
        "version: 1.0.0\n"
        "tags:\n"
        "  - review\n"
        "  - quality\n"
        "---\n\n"
        "# Code Review Instructions\n"
        "Check code quality thoroughly.\n",
        encoding="utf-8",
    )
    scripts_dir = sdir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "check.py").write_text("print('checking')", encoding="utf-8")
    return sdir


def test_skl_001_directory_indexing_computes_tree_digest(
    catalog_engine: SkillCatalogEngine, valid_skill_dir: Path
):
    """SKL-001: Skill directory indexing computes canonical deterministic SHA-256 tree digest."""
    skill = catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)

    assert skill.skill_id == "code_review"
    assert skill.name == "code_review"
    assert skill.version == "1.0.0"
    assert skill.state == "DISCOVERED"
    assert skill.revision == 1
    assert "review" in skill.tags
    assert len(skill.tree_digest) == 64  # SHA-256 hex length
    assert skill.file_count == 2  # SKILL.md and scripts/check.py


def test_skl_002_lifecycle_transitions(
    catalog_engine: SkillCatalogEngine, valid_skill_dir: Path
):
    """SKL-002: Skill lifecycle transitions DISCOVERED -> VALIDATED -> ACTIVE."""
    catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)

    # DISCOVERED -> VALIDATED
    s1 = catalog_engine.transition_skill("code_review", expected_revision=1, target_state="VALIDATED")
    assert s1.state == "VALIDATED"
    assert s1.revision == 2

    # VALIDATED -> ACTIVE
    s2 = catalog_engine.transition_skill("code_review", expected_revision=2, target_state="ACTIVE")
    assert s2.state == "ACTIVE"
    assert s2.revision == 3


def test_skl_003_suspend_active_skill(
    catalog_engine: SkillCatalogEngine, valid_skill_dir: Path
):
    """SKL-003: Suspending an active skill moves it to SUSPENDED."""
    catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)
    catalog_engine.transition_skill("code_review", expected_revision=1, target_state="VALIDATED")
    catalog_engine.transition_skill("code_review", expected_revision=2, target_state="ACTIVE")

    suspended = catalog_engine.transition_skill(
        "code_review", expected_revision=3, target_state="SUSPENDED", reason="audit check"
    )
    assert suspended.state == "SUSPENDED"
    assert suspended.revision == 4


def test_skl_004_resume_suspended_skill(
    catalog_engine: SkillCatalogEngine, valid_skill_dir: Path
):
    """SKL-004: Resuming a suspended skill restores ACTIVE state."""
    catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)
    catalog_engine.transition_skill("code_review", expected_revision=1, target_state="VALIDATED")
    catalog_engine.transition_skill("code_review", expected_revision=2, target_state="ACTIVE")
    catalog_engine.transition_skill("code_review", expected_revision=3, target_state="SUSPENDED")

    resumed = catalog_engine.transition_skill("code_review", expected_revision=4, target_state="ACTIVE")
    assert resumed.state == "ACTIVE"
    assert resumed.revision == 5


def test_skl_005_retire_terminal_skill(
    catalog_engine: SkillCatalogEngine, valid_skill_dir: Path
):
    """SKL-005: Retiring a skill moves it to terminal RETIRED state; cannot transition further."""
    catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)
    catalog_engine.transition_skill("code_review", expected_revision=1, target_state="VALIDATED")
    catalog_engine.transition_skill("code_review", expected_revision=2, target_state="ACTIVE")

    retired = catalog_engine.transition_skill("code_review", expected_revision=3, target_state="RETIRED")
    assert retired.state == "RETIRED"
    assert retired.revision == 4

    # Terminal state rejects any further transition
    with pytest.raises(ForbiddenTransitionError):
        catalog_engine.transition_skill("code_review", expected_revision=4, target_state="ACTIVE")


def test_skl_006_forbidden_direct_transition(
    catalog_engine: SkillCatalogEngine, valid_skill_dir: Path
):
    """SKL-006: Forbidden direct transition DISCOVERED to ACTIVE is rejected."""
    catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)

    with pytest.raises(ForbiddenTransitionError):
        catalog_engine.transition_skill("code_review", expected_revision=1, target_state="ACTIVE")
    skill = catalog_engine.get_skill("code_review")
    assert (skill.state, skill.revision) == ("DISCOVERED", 1)  # rejection left state unchanged


def test_skl_007_query_non_existent_skill(catalog_engine: SkillCatalogEngine):
    """SKL-007: Querying non-existent skill raises SkillNotFoundError."""
    with pytest.raises(SkillNotFoundError):
        catalog_engine.get_skill("non_existent_skill")


def test_skl_008_malformed_skill_manifest(
    catalog_engine: SkillCatalogEngine, tmp_path: Path
):
    """SKL-008: Malformed SKILL.md frontmatter raises SkillManifestError."""
    bad_dir = tmp_path / "skills" / "bad_skill"
    bad_dir.mkdir(parents=True, exist_ok=True)
    manifest = bad_dir / "SKILL.md"
    manifest.write_text("--- broken yaml: [unclosed\n---\n# Bad Skill", encoding="utf-8")

    assert catalog_engine.list_skills() == []
    with pytest.raises(SkillManifestError):
        catalog_engine.index_skill(stream_id="s1", skill_dir=bad_dir)
    assert catalog_engine.list_skills() == []  # nothing partially indexed


def test_skl_009_tampered_file_detected(
    catalog_engine: SkillCatalogEngine, valid_skill_dir: Path
):
    """SKL-009: Post-index file modification detected as tampered."""
    catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)

    # Tamper with the script file on disk
    script_file = valid_skill_dir / "scripts" / "check.py"
    script_file.write_text("print('tampered script!')", encoding="utf-8")

    with pytest.raises(SkillTamperedError):
        catalog_engine.verify_skill_integrity("code_review")
    skill = catalog_engine.get_skill("code_review")
    assert (skill.state, skill.revision) == ("DISCOVERED", 1)  # detection did not mutate the record
    with pytest.raises(SkillTamperedError):  # and it keeps detecting
        catalog_engine.verify_skill_integrity("code_review")


def test_skl_010_unauthorized_skill_invocation_blocked(
    catalog_engine: SkillCatalogEngine, valid_skill_dir: Path
):
    """SKL-010: Attempt to invoke inactive skill raises UnauthorizedSkillError."""
    catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)

    # In DISCOVERED state, execution authorization must be rejected
    with pytest.raises(UnauthorizedSkillError):
        catalog_engine.authorize_skill_execution("code_review")

    # In VALIDATED state, execution authorization must also be rejected
    catalog_engine.transition_skill("code_review", expected_revision=1, target_state="VALIDATED")
    with pytest.raises(UnauthorizedSkillError):
        catalog_engine.authorize_skill_execution("code_review")

    # In ACTIVE state, execution authorization succeeds
    catalog_engine.transition_skill("code_review", expected_revision=2, target_state="ACTIVE")
    authorized = catalog_engine.authorize_skill_execution("code_review")
    assert authorized.skill_id == "code_review"


def test_skl_011_capability_catalog_cas(catalog_engine: SkillCatalogEngine):
    """SKL-011: Capability catalog records declared capabilities with CAS revision gating."""
    cap = catalog_engine.declare_capability(
        stream_id="s1",
        capability_id="cap-python-exec",
        spec={"tools": ["python_interpreter"], "sandbox": True},
    )

    assert cap.capability_id == "cap-python-exec"
    assert cap.revision == 1
    assert cap.spec["sandbox"] is True

    # Mutate capability with CAS expected_revision
    updated = catalog_engine.update_capability(
        capability_id="cap-python-exec",
        expected_revision=1,
        spec={"tools": ["python_interpreter", "bash"], "sandbox": True},
    )
    assert updated.revision == 2
    assert "bash" in updated.spec["tools"]


def test_skl_012_conflicting_capability_rejected(catalog_engine: SkillCatalogEngine):
    """SKL-012: Conflicting capability declaration is rejected (CapabilityConflictError)."""
    catalog_engine.declare_capability(
        stream_id="s1",
        capability_id="cap-llm",
        spec={"models": ["gpt-4o", "gemini-pro"]},
    )

    with pytest.raises(CapabilityConflictError):
        catalog_engine.update_capability(
            capability_id="cap-llm",
            expected_revision=999,  # Mismatched expected revision
            spec={"models": ["claude-3-opus"]},
        )
    cap = catalog_engine.get_capability("cap-llm")
    assert cap.revision == 1 and cap.spec == {"models": ["gpt-4o", "gemini-pro"]}  # unchanged after the conflict


def test_skl_013_volatile_runtime_facts_rejected(catalog_engine: SkillCatalogEngine):
    """SKL-013: Volatile runtime measurements in capability catalog are strictly rejected (Invariant 9)."""
    # Reject quota
    with pytest.raises(VolatileFactRejectedError):
        catalog_engine.declare_capability(
            stream_id="s1",
            capability_id="cap-bad-1",
            spec={"quota": 1000},
        )

    # Reject rate limit
    with pytest.raises(VolatileFactRejectedError):
        catalog_engine.declare_capability(
            stream_id="s1",
            capability_id="cap-bad-2",
            spec={"rate_limit": "60rpm"},
        )

    # Reject live ping / latency
    with pytest.raises(VolatileFactRejectedError):
        catalog_engine.declare_capability(
            stream_id="s1",
            capability_id="cap-bad-3",
            spec={"latency_ms": 42},
        )


def test_skl_014_wipe_sqlite_table_rebuild_full_state(
    catalog_engine: SkillCatalogEngine,
    valid_skill_dir: Path,
    store: CoreStore,
    tmp_path: Path,
):
    """SKL-014: Wiping SQLite skill index and replaying stream records + directory scan reconstructs full state."""
    # Index and activate skill
    catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)
    catalog_engine.transition_skill("code_review", expected_revision=1, target_state="VALIDATED")
    catalog_engine.transition_skill("code_review", expected_revision=2, target_state="ACTIVE")

    # Declare capability
    catalog_engine.declare_capability(
        stream_id="s1",
        capability_id="cap-docker",
        spec={"image": "python:3.12"},
    )

    before_skill = catalog_engine.get_skill("code_review")
    before_cap = catalog_engine.get_capability("cap-docker")

    # WIPE the database tables completely
    with sqlite3.connect(catalog_engine.db_path) as conn:
        conn.execute("DROP TABLE ext_skills;")
        conn.execute("DROP TABLE ext_capabilities;")

    # Rebuild from Core Stream records and skill directory root
    records = store.read_records(stream_id="s1")
    rebuilt_count = catalog_engine.rebuild_index(records=records)

    assert rebuilt_count >= 3

    after_skill = catalog_engine.get_skill("code_review")
    after_cap = catalog_engine.get_capability("cap-docker")

    # Verify structural and logical equivalence
    assert after_skill.skill_id == before_skill.skill_id
    assert after_skill.state == before_skill.state == "ACTIVE"
    assert after_skill.revision == before_skill.revision == 3
    assert after_skill.tree_digest == before_skill.tree_digest

    assert after_cap.capability_id == before_cap.capability_id
    assert after_cap.revision == before_cap.revision == 1
    assert after_cap.spec == before_cap.spec


def test_skl_rebuild_never_adopts_disk_content_and_drift_is_detected(
    catalog_engine: SkillCatalogEngine, valid_skill_dir: Path, store: CoreStore
):
    """Rebuild replays Records only: edited files neither change the recorded digest nor pass integrity verification."""
    from peerhub.extensions.skills import SkillTamperedError

    catalog_engine.index_skill(stream_id="s1", skill_dir=valid_skill_dir)
    recorded = catalog_engine.get_skill("code_review").tree_digest
    (valid_skill_dir / "extra.txt").write_text("edited after registration", encoding="utf-8")
    catalog_engine.rebuild_index(records=store.read_records(stream_id="s1"))
    assert catalog_engine.get_skill("code_review").tree_digest == recorded  # the Record, not the disk, is the authority
    with pytest.raises(SkillTamperedError):
        catalog_engine.verify_skill_integrity("code_review")
