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

---

## Closure update (2026-10-08)

Written after the M2/M3 closure work (`docs/implementation/M2_M3_CLOSURE_2026-10-08_KO.md`) and checked against the code and tests. Where it conflicts with the text above, **this section wins**. The state-machine and exception JSON catalogs carry the same update.

Section 1: replace **Directory Tree Digest** and **Generated Skill Index**, and add the catalog update Record:

```markdown
  - **Directory Tree Digest:** Deterministic SHA-256 over files sorted by POSIX relative path, excluding files under `.git`. Each file contributes an 8-byte big-endian UTF-8 path-byte length, the path bytes, an 8-byte big-endian file size obtained from the open file, and streamed file contents. Length framing prevents file contents from impersonating path/content boundaries.
  - **Generated Skill Index:** Rebuildable SQLite table `ext_skills`; tags are stored in its JSON column. Capability declarations and updates are projected into `ext_capabilities`.
```

```markdown
    - `m2.catalog.updated`: Updates a declared capability with expected and new revisions.
```

Section 2: replace the mismatched signatures:

```markdown
- `index_skill(stream_id: str, skill_dir: Path | str, author_peer_id: str | None = None) -> SkillItem`
  Errors: `SkillManifestError`, `SecurityBoundaryError`, `SkillRevisionConflictError`.
- `list_skills() -> list[SkillItem]`
- `search_skills(query: str, tags: list[str] | None = None) -> list[SkillItem]`
- `declare_capability(stream_id: str, capability_id: str, spec: dict[str, Any], author_peer_id: str | None = None) -> CapabilityItem`
  Errors: `VolatileFactRejectedError`, `CapabilityConflictError`.
- `update_capability(capability_id: str, expected_revision: int, spec: dict[str, Any], author_peer_id: str | None = None) -> CapabilityItem`
  Errors: `CapabilityNotFoundError`, `CapabilityConflictError`, `VolatileFactRejectedError`.
- `rebuild_index(records: Iterable[Record]) -> int`
  Reconstructs both skill and capability projections from authoritative Records only, returning the number of applied Records. It accepts no `skill_roots`, performs no directory rescan, and preserves the recorded tree digest.
```

Section 3: add:

```markdown
- **Authoritative CAS:** Skill transitions and capability updates reconstruct current state from ordered authoritative Records and repair stale or missing projection rows before checking revisions.
- **Mutation Confirmation:** A transition or update is acknowledged only when the reducer accepted that mutation's own Record. Revision races and append idempotency conflicts raise `SkillRevisionConflictError` or `CapabilityConflictError`, respectively.
- **Creation-Crash Recovery:** Missing skill/capability rows are located and reconstructed from authoritative streams. Re-indexing an existing skill returns its authoritative item when stream, name, version, and description match; this path does not adopt a changed disk digest. Re-declaring a capability is idempotent only for an identical specification in the same stream at revision 1; otherwise it raises `CapabilityConflictError`.
- **Atomic Rebuild:** Both projections are folded before replacement and are deleted/repopulated together in one SQLite transaction. A record-source failure leaves existing rows intact; replacement-write failure rolls back the transaction.
- **Cached Reads:** Query interfaces read projections; they do not automatically perform authoritative CAS refresh.
```

Section 4: add:

```markdown
- `rebuild_index` does not authorize edited source files by adopting their current contents. `verify_skill_integrity` compares the current directory digest with the recorded digest and raises `SkillTamperedError` on drift.
- Digest computation rejects a file whose streamed byte count differs from the size obtained from its open file, raising `SecurityBoundaryError`. This detects growth or truncation during hashing; it does not establish detection of every same-size concurrent edit.
- Transitions into VALIDATED or ACTIVE verify source integrity before appending the transition Record.
- The standard-library frontmatter fallback supports inline lists, including quoted entries and empty lists, consistently with the tested PyYAML parsing cases.
```

**Wrong/obsolete:** `rebuild_index(..., skill_roots)` and disk-driven reconstruction; `ext_skill_tags`; declaring updates through `declare_capability`; the unused `curated` argument; and the status/tag filters on `list_skills`.

- **Materialization:** Skill FILES are the procedure source, Records are the lifecycle/provenance authority, and the SQLite catalog is a rebuildable projection (peerhub/extensions/skills.py). Per-CLI materialization/sync (claude/codex/agy) is DEFERRED until a concrete consumer needs it (design-only, not a defect).
