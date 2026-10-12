# Gap 8 Design: Lesson Inject (DESIGN REVISED, MOSTLY RATIFIED)

Status: IMPLEMENTED.
Research by `ag.deepthink`, independent critique and correction by `cx.deepthink`, 2026-08-30.
Implementation done, full suite 1254 passed (only pre-existing unrelated manifest test fails), 0 new pyright errors. Knowledge asset finding: real persisted governance state is `mappingproxy`, not `dict` -- any future code reading `TargetState.state` sub-objects must use `isinstance(x, collections.abc.Mapping)`, never `isinstance(x, dict)`.

This doc formalizes the design investigation for `lesson-inject`, a legacy operation for compiling active lessons into a prompt injection block.

## 1. Legacy Behavior Corrections

An independent critique of the original research found several material corrections regarding real legacy semantics:

- **Output Format:** The parity ledger's output format claim (`[PEER LESSONS (peer=...)]` with titles) is inaccurate. Real hub.py emits exactly `[PEER LESSONS]` followed by `- SEVERITY ID: compact_rule` with no peer name or title (hub.py:9824, 9834-9839).
- **Ordering:** The real sort key is strictly `(sticky-critical priority, severity rank)`. There is no ID tie-break; Python's stable sort merely preserves incoming file/load order for ties (hub.py:9805-9813).
- **Severity Values:** Severity is not strictly locked to 4 fixed values. Four lowercase values are recognized for ranking (`critical`, `high`, `medium`, `low`), but arbitrary or differently-cased input is accepted and silently ranked as `medium` (hub.py:9760-9763, 9774-9777, 10101-10111).
- **Peer Applicability:** A nonempty `applies_to.peer_ids` list must explicitly contain the requested peer for a lesson to apply (hub.py:9767-9772).
- **Context Matching (OS/Shell/Task-type):** Filtering only applies when *both* the lesson constraint and the workspace-profile value are truthy. A missing workspace context fails open and admits constrained lessons (hub.py:9749-9758, 9779-9790).
- **Delivery Toggle:** `delivery.enabled=False` disables rendering entirely (hub.py:9798-9803, 9815-9829).
- **Budget/Sticky Overrides:** `sticky` priority applies *only* to sticky-AND-critical lessons; a sticky-but-noncritical lesson gets no priority boost. The character budget counts only the entry text, not the header, newlines, or omission notices (hub.py:9820-9839).

## 2. Peerhub Substrate Corrections (The Material Error)

- `LessonService` genuinely has no `sticky`, `os`, `shell`, or `task_types` fields, and no `list_active_lessons()` method on itself.
- **Material Error:** The original research proposed duplicating broker-filtering logic via raw `GovernanceBroker.list_targets()` access. This is incorrect. A public standalone helper `peerhub.governance.activity.list_active_lessons()` *already exists* (activity.py:41-54) and is exposed via the `governance.lesson.list` application command. The corrected design **MUST** reuse this helper.

## 3. Ratified 3-Layer Design

Rendering does not belong inside `LessonService`. The correct decomposition is:
1. **Selection:** The existing `peerhub.governance.activity.list_active_lessons()` helper selects authoritative active lessons.
2. **Coordination:** An application coordinator (e.g., `peerhub/application/lesson_inject.py`) wraps the helper and applies workspace and peer applicability rules.
3. **Rendering:** A pure, testable renderer sorts and truncates the normalized lessons according to configured policy limits.

### Command Surface (READ_ONLY)
The standalone `host.lesson.inject` compatibility command maps to `Mutability.READ_ONLY` and `IdempotencyPolicy.READ_ONLY`. It has no `_submit()` and writes no governance receipt, matching the `governance.lesson.list` precedent. (A future integrated-dispatch design may separately record delivery evidence once a block is actually consumed, but this standalone CLI command does not.)

### Schema Placement (Creation-Time Metadata)
Legacy's `lessons-propose` hardcodes OS/shell/task-type metadata to `None`, but peerhub must support these fields. They will be added as creation-time metadata (updated via replacement/supersede):
- **Applicability:** Add a new top-level `applicability` object (adjacent to `affected_peers`): `applicability: {peer_ids, os, shell, task_types}`. `scope` remains `{kind, workspace_id}` exclusively.
- **Sticky:** Add `sticky: bool` as a new top-level field (it affects both rendering and sweep retirement).
- **Severity Normalization:** The renderer must case-normalize severity at read/render time to support legacy's lowercase expectations against peerhub's uppercase fixtures.

### Policy / Config Placement
Configuration limits (`enabled`, `min_severity`, `max_chars`, `max_items`, `critical_always_include`) will live in a new, immutable `LessonInjectionPolicy` injected at the application level, matching `LeadershipPolicy`. It is not promoted into a versioned governance policy like `HealthPolicy` because it controls presentation logic rather than state mutation.

## 4. Required Implementation Prerequisites (RESOLVED)

1. **Global-plus-workspace Selection:** `lesson_inject.py` calls `list_active_lessons(broker, None)` exactly ONCE (unscoped), then filters in the coordinator, retaining a lesson iff its scope is either `{"kind": "global", "workspace_id": None}` OR `{"kind": "workspace", "workspace_id": <the requested workspace_id>}`. This correctly reuses the helper's lifecycle filtering unchanged.
2. **Empty `affected_peers` Semantics:** Absent or empty `affected_peers` means UNIVERSAL applicability (applies to all peers). A nonempty list restricts to only the listed peers. `affected_peers` is the SOLE peer-applicability field -- do not add a separate `applicability.peer_ids` field.
3. **Applicability Context Source:** `RuntimeContext` lacks OS/shell/task-type fields. An explicit, immutable `LessonInjectionContext` parameter is provided by the caller.
4. **Expiry/Recency Filtering Gap:** Legacy filters on both. Peerhub currently only checks `lifecycle == ACTIVE`, and `propose()` hardcodes `validity.expires_at=None`. This gap is explicitly acknowledged.
5. **Duplicate-ID Resolution:** Native peerhub lesson IDs are GLOBALLY unique by construction (every scope maps to the same broker key `lesson:<lesson_id>`). Thus, `lesson_inject.py` needs NO duplicate-resolution logic. (If a future legacy-JSONL importer is built, it should resolve using documented legacy intent—workspace overrides global—as a parity correction, not replicate legacy's loader-order bug).
6. **Tie Ordering:** Legacy preserves file/load order for severity ties. Peerhub's broker returns ascending target IDs. This represents a deliberate parity deviation (tie-break by ID rather than load order).

## 5. Boundary-Condition Test List
The eventual implementation round must include these boundary tests:
- Missing profile values fail open.
- Unknown severity defaults to rank `medium`.
- Exactly-equal character budget is successfully admitted.
- Header and newline bytes are not counted against the character budget.
- Sticky-but-noncritical lessons receive no priority boost.
- Critical bypass allows inclusion exceeding both char and item caps without limit.
