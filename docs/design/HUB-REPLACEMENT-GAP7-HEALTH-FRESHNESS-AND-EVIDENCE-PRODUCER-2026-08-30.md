# Gap 7 Design: Health Freshness and Evidence Producer (RATIFIED/BLOCKED)

Status: Mixed. Gap A (Centralized Read-Time Freshness) is READY FOR IMPLEMENTATION. Gap B (Health-Evidence Producer outside bootstrap) is RESEARCH-STAGE / BLOCKED.
Research by `cx.deepthink` (fresh session), independent critique by `cc.deepthink`, 2026-08-30.

This doc formalizes the two critical underlying gaps surfaced during the Gap 5 Health Cluster investigation (`docs/design/HUB-REPLACEMENT-GAP5-HEALTH-CLUSTER-2026-08-30.md`).

## Gap A -- Centralized read-time freshness (IMPLEMENTED (commit 75ff364))

**Status Update:** Fully implemented, tested, and committed to main (`75ff364`). Tests pass (1243 passed, only the pre-existing unrelated manifest test fails) with 0 new pyright errors. During implementation, 8 pre-existing tests initially regressed because their fixtures constructed `HealthProjectionSnapshot` directly with `readiness_observation_id=None` (valid under the old contract, but unrealistic now since production always links a real readiness observation). This was fixed by updating the test fixtures to seed real linked evidence, maintaining the new reducer's strictness.

Two real bugs expose peerhub to trusting unboundedly stale evidence:
1. **Direct-ask eligibility ignores `availability_state` entirely** (`peerhub/application/direct_ask.py:80-88` matches only on `instance_id`/`profile_id`/`entry.admission_state.value == "OPEN"`). A fix that only degrades `availability_state` to `STALE` would be completely inert for routing; staleness must also degrade effective *admission*.
2. **`HealthProjectionSnapshot.updated_at` is not a pure readiness clock**. It only advances when content actually changes (service.py:552-563). Furthermore, `_recompute_members` fires from 5 separate call sites, meaning its timestamp represents projection-recompute time, not evidence-observation time.

### Proposed Fix
Add `HealthService.read_health_projection(instance_id, profile_id, evaluated_at=None) -> HealthProjectionRead | None`, backed by a pure `evaluate_projection_at` reducer in `health/model.py`.

Rules for `evaluate_projection_at`:
- Preserve absent-projection -> `None` (fail-open).
- Evaluate freshness *fresh on every read* against the referenced readiness observation, not `projection.updated_at`. Never persist the derived `STALE` state.
- When stale, set effective availability = `STALE` **AND** fold it through `resolve_admission_state()` so effective admission becomes at least `RECOVERY_REQUIRED` (the critical fix for direct-ask routing).
- `freeze_admission_snapshot()` calls this at its captured timestamp.
- `role_assignment` and `leadership` consume the returned effective states instead of duplicating freshness math.
- Rename the raw getter to `_get_stored_health_projection()` (private).

**Critique Corrections (MUST be included in implementation):**
- **Signature Change:** We cannot just reuse `evaluate_readiness_evidence()`, because it discards its policy parameter (`del policy`, health/model.py:83) and doesn't accept an arbitrary read-time. It must be refactored to accept an `evaluated_at` parameter.
- **Monotonic Worst-Of Rule:** A read-time reducer re-deriving admission from readiness alone could accidentally lose circuit-derived admission severity (`COOLDOWN=3`, `QUARANTINED=4`). The rule must explicitly take the *worse of* (readiness-derived effective admission, existing circuit-derived admission), reusing the existing severity ladder.

*Inert Unit Inconsistency Note:* `bootstrap.py` sets `readiness_freshness_seconds=86400` but `valid_until = now + 86400000`. This looks like an off-by-1000 error, but it is currently a dead/vacuous branch (`now + 86_400_000 < now` is never true for positive offsets). The implementation should fix the constant, keep the `min(valid_until, observed_at + readiness_freshness_seconds)` guard, add an explanatory comment (defense in depth), and consolidate the three separate "one day" constants into one authoritative freshness source.

## Gap B -- Health-evidence producer outside bootstrap (RESEARCH-STAGE / BLOCKED)

Currently, peerhub can only produce `ReadinessObserved` evidence during bootstrap. Providing a re-validation mechanism outside bootstrap is required to unblock `health-update` (re-eval triggers) and operator `--recover` interventions.

**Confirmed Findings:**
- The current probe is just `--version` with a 5s timeout. Mapping this straight to full `HEALTHY`/`ADMITTED` conflates mere executable presence with actual readiness (auth/quota are untested). The probe should be demoted to produce a distinct "entrypoint verified" fact, not a full `HEALTHY` grant.
- **Critical Safety Issue:** Broadcast's producer catches ANY exception from the probe and substitutes fabricated `"fallback_ok"` evidence (bootstrap.py:82-85), meaning a peer whose binary cannot even spawn still yields `HEALTHY`. This must be fixed independently.
- `adapter_declares_probe_safe=True` is hardcoded (runtime.py:187), defeating the purpose of a safety declaration.
- **Service Placement:** A new application-layer `HealthRevalidationCoordinator` should compose siblings, while the extracted `ReadinessEvidenceProducer` itself should remain in `application/bootstrap.py` (which already owns it and has 3 callers).

### The Blocking Policy Decision
**There is a genuine manual-quarantine deadlock.** `authorize_recovery()` only accepts circuits where evaluated state is `RECOVERY_REQUIRED` (service.py:1063-1076). However, `reduce_evaluate_cooldown` returns `QUARANTINED` permanently for any non-AUTOMATIC authority class. Therefore, a manually-quarantined circuit can *never* reach the state its own recovery path requires.

**Open Policy Question (MUST BE RESOLVED BEFORE DESIGN RATIFICATION):** How does an operator authorize a re-validation probe against a MANUALLY quarantined circuit, given that the system's safety rules forbid a probe from directly granting `HEALTHY`, and the `authorize_recovery` gate structurally cannot accept a manually-quarantined circuit?

## Scope Note
Gap A and Gap B are independent and vastly different in size. **Gap A is a contained, implementation-ready fix.** Gap B involves a new coordinator and an unresolved policy question. Gap A should be scheduled and shipped independently first.
