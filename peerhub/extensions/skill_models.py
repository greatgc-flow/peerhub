"""Skill and capability catalog items and errors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast


class SkillCatalogError(Exception):
    """Base exception for Skill and Capability Catalog errors."""


class SkillNotFoundError(SkillCatalogError, LookupError):
    """Referenced skill_id does not exist in catalog."""


class CapabilityNotFoundError(SkillCatalogError, LookupError):
    """Referenced capability_id does not exist in catalog."""


class SkillRevisionConflictError(SkillCatalogError, RuntimeError):
    """Mutation expected_revision does not match current skill revision."""


class CapabilityConflictError(SkillCatalogError, RuntimeError):
    """Mutation expected_revision does not match current capability revision."""


class ForbiddenTransitionError(SkillCatalogError, RuntimeError):
    """Attempting an invalid state machine transition."""


class SkillManifestError(SkillCatalogError, ValueError):
    """SKILL.md frontmatter is missing, unparseable, or fails schema validation."""


class SkillTamperedError(SkillCatalogError, RuntimeError):
    """On-disk skill directory contents hash differs from indexed canonical digest."""


class UnauthorizedSkillError(SkillCatalogError, PermissionError):
    """Attempt to invoke or authorize an inactive, unvalidated, or suspended skill."""


class VolatileFactRejectedError(SkillCatalogError, ValueError):
    """Attempt to register volatile runtime measurement into curated capability catalog."""


@dataclass(frozen=True)
class SkillItem:
    """Represents a projected view of a skill derived from directory source and stream records."""

    skill_id: str
    stream_id: str
    name: str
    description: str
    version: str
    state: str
    revision: int
    tree_digest: str
    file_count: int
    path: str
    tags: list[str] = field(default_factory=lambda: cast(list[str], []))
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class CapabilityItem:
    """Represents a curated declared capability view."""

    capability_id: str
    stream_id: str
    spec: dict[str, Any]
    revision: int
    created_at: str = ""
    updated_at: str = ""
