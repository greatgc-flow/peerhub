# cx.pro final-review checklist (cx quota is limited: per-wave gates use ag.pro; cx reviews waves 0-7 ONCE at the end)

Classes of defect cx.pro found in earlier waves (probe all of them again, across waves):
1. Vacuous/masked tests: broad raises, placeholder digests masking the intended rejection, assertions computed by the implementation itself, no positive control, property tests that pass when the implementation rejects everything.
2. Scope/ownership binding: a valid token/claim/callback for scope A affecting scope B; delayed callbacks finalizing a newer delivery; mutation before validation.
3. Authentication against caller copies instead of the persisted Record (reconcile/control/boundary); triggers that check ordering but not authorization.
4. False success after partial failure (CAS exhaustion, close failure, direction/new stream leftovers).
5. Immutability bypass: INSERT OR REPLACE, recursive_triggers off, stale triggers retained on reopen.
6. Replay/crash safety: marker before invoke (TD-11), crash points leave complete old-or-new state, restore atomicity, generation consistency.
7. Spec contradictions and owner items: see DECISIONS.md items marked OWNER and the erratum/gap list in the final report.
