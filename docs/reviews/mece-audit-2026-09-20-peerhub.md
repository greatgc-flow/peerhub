# MECE Audit — peerhub (2026-09-20)

**Reviewer:** ag.opus (single independent pass; ran out of quota — HTTP 429 RESOURCE_EXHAUSTED — before writing its own report or reaching the planned cross-review exchange with cx.effort/ag.effort. This file was reconstructed by the terminal from ag.opus's dispatch transcript so the finding isn't lost.)

## Findings

### Finding #1 (real, fixed) — `DIRECT_CLI_BYPASS` count was wrong in README.md/STATUS.md
Both docs claimed "18" bootstrap/session/config/health/routing/directive/node-level `DIRECT_CLI_BYPASS` mutating commands. The actual count in `docs/design/peerhub-production-call-map-R1.json` is **26**. Root cause: the terminal's own earlier verification script filtered `effect == "MUTATING"` exactly, missing 8 commands whose `effect` is `MUTATING_EXTERNAL` or `CONDITIONAL_MUTATION_EXTERNAL` (`status`, `backup workspace`, `diag`, `broadcast`, `health revalidate`, `health check`, `peer recover`, `ask`). Corrected in both README.md and docs/STATUS.md same day, with an inline note explaining the undercounting cause.

### Finding #2 (not a real discrepancy, verified) — apparent test-count mismatch
ag.opus's transcript flagged "1775 tests collected (1790 - 15 slow) vs STATUS.md's 1770 passed" as a possible inconsistency. On inspection this isn't one: 1770 passed + 5 skipped = 1775, matching ag.opus's own collected-count exactly. No action needed.

### Finding #3 (self-documented, not new) — 28 leaf CLI commands lack execution-level integration tests
The call map's own `TEST NEEDED` annotations already disclose this (per the call map's `known_gaps` field: "Several leaf paths have no execution-level integration test... marked TEST NEEDED rather than being credited with coverage"). Confirmed still accurate; not a hidden gap.

### Verified accurate (no issue)
- `GovernanceAuthorizer.authorize()` is genuinely called from `ApplicationAPI.submit()` (api.py) — the gateway claim is real, not aspirational.
- `_submit_via_gateway` → `Client.submit()` → `ApplicationAPI.submit()` chain confirmed for `error review resolve` specifically.
- `LessonService.approve()`'s `state["lifecycle"] = "APPROVED"` fix (this session) is present in the source as claimed.
- `docs/design/README.md`'s design-doc index (34 current files, tiered) is well-organized; no consolidation gap found in the pass completed.
- `peerhub/_version.py` matches the released version.
- `peerhub/application/legacy.py` is genuinely just re-exports now, consistent with STATUS.md's LegacyTranslator-retirement claim.

## Not reached (quota exhausted before this point)
- `duty close --close-session` bug-fix claim spot-check (was next on ag.opus's list).
- A systematic defined-but-never-called dead-code scan (started, not completed).
- The planned peer-to-peer cross-review exchange with cx.effort and ag.effort.

## Terminal's overall assessment
One real, cheap documentation bug found and fixed. Nothing found that undermines the v0.6.0 release's substantive claims (the gateway routing itself, the two documented bug fixes, the version/test-count claims). Given ag.opus's own quota is exhausted, cx.effort's (Engram) and ag.effort's (cross-repo) parallel reviews are the remaining source of further findings for this MECE pass.
