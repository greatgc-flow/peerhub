# Skill & General Capability Catalog Contract (M2.3)

## 1. Authority & Storage Layout
- **Authority Separation (Invariant 8 & Invariant 9):**
  - **Skill Source:** Physical directory containing `SKILL.md` (YAML frontmatter + Markdown body) and optional subdirectories (`scripts/`, `references/`, `resources/`). The sole authority for executable procedures and prompts.
  - **Directory Tree Digest:** Canonical, deterministic SHA-256 digest over sorted relative paths and file contents. Guarantees cryptographic tamper-resistance.
  - **Generated Skill Index:** Rebuildable read-model projection table `ext_skills` and `ext_skill_tags` in SQLite.
  - **General Capability Catalog:** Curated declared capability and fact registry (`ext_capabilities`) describing models, tools, and profiles. Volatile measured facts (quota, rate-limits, ping) are strictly prohibited and stay in M1 Observation/Diag (Invariant 9).
  - **Selection Policy:** Declarative rules mapping skills/capabilities to work tasks.
  - **Core Stream Records:** Home stream records authoritative for lifecycle events:
    - `m2.skill.registered`: Indexing a skill source with manifest digest.
    - `m2.skill.transitioned`: State transitions (`VALIDATED`, `ACTIVE`, `SUSPENDED`, `RETIRED`).
    - `m2.catalog.declared`: Declaring or updating a capability entry.

## 2. Port Protocols & Error Types
The Skill & Capability engine exposes a clean, typed public interface:
- `index_skill(skill_dir: Path) -> SkillItem`
  Errors: `SkillManifestError`, `SecurityBoundaryError`.
- `get_skill(skill_id: str) -> SkillItem`
  Errors: `SkillNotFoundError`.
- `list_skills(status: str | None = None, tag: str | None = None) -> list[SkillItem]`
- `transition_skill(skill_id: str, expected_revision: int, target_state: str, reason: str | None = None) -> SkillItem`
  Errors: `SkillNotFoundError`, `SkillRevisionConflictError`, `ForbiddenTransitionError`.
- `verify_skill_integrity(skill_id: str) -> bool`
  Errors: `SkillNotFoundError`, `SkillTamperedError`.
- `declare_capability(capability_id: str, spec: dict[str, Any], curated: bool = True) -> CapabilityItem`
  Errors: `VolatileFactRejectedError`, `CapabilityConflictError`.
- `get_capability(capability_id: str) -> CapabilityItem`
  Errors: `CapabilityNotFoundError`.
- `rebuild_index(records: Iterable[Record], skill_roots: Iterable[Path]) -> int`
  Wipes and re-evaluates skill index tables from disk sources and stream records.

## 3. Skill State Machine
```text
           ┌──────────────────────┐
           │      DISCOVERED      │
           └──────────┬───────────┘
                      │
                      ▼
           ┌──────────────────────┐
           │      VALIDATED       │
           └──────────┬───────────┘
                      │
                      ▼
           ┌──────────────────────┐
    ┌─────►│        ACTIVE        │◄─────┐
    │      └────┬────────────┬────┘      │
    │           │            │           │
    ▼           │            ▼           ▼
┌───────────┐   │       ┌─────────┐
│ SUSPENDED │◄──┘       │ RETIRED │ (Terminal)
└───────────┘           └─────────┘
```
- **Discovery & Validation:** `DISCOVERED` -> `VALIDATED`.
- **Activation:** `VALIDATED` -> `ACTIVE`.
- **Suspension / Quarantine:** `ACTIVE` <-> `SUSPENDED`.
- **Deprecation / Retirement:** `ACTIVE` -> `RETIRED`, `SUSPENDED` -> `RETIRED`.
- **Terminal State:** `RETIRED` cannot transition further; any transition attempt raises `ForbiddenTransitionError`.
- **Execution Boundary:** Discovered or Validated skills cannot be executed (`UnauthorizedSkillError`). Discovery != authorization.

## 4. Tamper Resistance & Security Boundaries
- Any mutation of files within a registered skill directory after indexing alters the canonical directory hash and raises `SkillTamperedError` upon integrity check or activation.
- Path traversal sequences (`..`), symlinks resolving outside the root, or reserved Windows device names (`NUL`, `CON`, `PRN`) raise `SecurityBoundaryError`.

## 5. Volatile Facts Quarantine (Invariant 9)
- Any attempt to declare dynamic measurements (such as `quota`, `rate_limit`, `latency_ms`, `tokens_remaining`) into the curated Capability Catalog raises `VolatileFactRejectedError`. Such measurements belong exclusively to M1 Observation and Diag modules.
