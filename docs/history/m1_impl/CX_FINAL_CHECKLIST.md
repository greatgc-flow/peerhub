# cx.pro final-review checklist (cx quota is limited: per-wave gates use ag.pro; cx reviews waves 0-7 ONCE at the end)

Classes of defect cx.pro found in earlier waves (probe all of them again, across waves):
1. Vacuous/masked tests: broad raises, placeholder digests masking the intended rejection, assertions computed by the implementation itself, no positive control, property tests that pass when the implementation rejects everything.
2. Scope/ownership binding: a valid token/claim/callback for scope A affecting scope B; delayed callbacks finalizing a newer delivery; mutation before validation.
3. Authentication against caller copies instead of the persisted Record (reconcile/control/boundary); triggers that check ordering but not authorization.
4. False success after partial failure (CAS exhaustion, close failure, direction/new stream leftovers).
5. Immutability bypass: INSERT OR REPLACE, recursive_triggers off, stale triggers retained on reopen.
6. Replay/crash safety: marker before invoke (TD-11), crash points leave complete old-or-new state, restore atomicity, generation consistency.
7. Spec contradictions and owner items: see DECISIONS.md items marked OWNER and the erratum/gap list in the final report.

## Operating notes (2026-10-04)

Historical: the admission and sandbox restrictions below describe the 2026-10-04 runtime; the selector, importer, 109-command table and side-by-side CLI were retired, and current `ask --writable` selects the provider write mode.

- cx cannot run disk-backed tests: READ_ONLY denies writes (pytest basetemp, even inside the workspace) and WORKTREE_WRITE is not enforceable for cx (CapabilityLeaseViolation). Pass `--workspace <parent root>` so cx can READ sibling/sub paths; verify disk-backed behaviour with our own test runs and record evidence instead.
- cx.pro/cx.deepthink are refused (not admitted) while the cx pool pacing is critical; cx.effort/cx.standard still work. Large single review prompts end CANCELLED: split reviews into scoped parts.
- ag.pro refuses prompts worded as adversarial/exploit/injection hunting; phrase as correctness code review.
- Run `peerhub observation refresh` followed by `peerhub diag` before peer calls and watch pacing (CC/CX quotas were tight).
