# Batch 08h — test_stage2_boundary.py Feedback, Diagnostics & Alerts Preservation Record (FINAL)

> LegacyTranslator retirement, sub-batch 8h of 8 (overall batch 8 of 9).
> Baseline commit: `f343467` (sub-batch 8g).
> Target: partial rewrite of `tests/integration/test_stage2_boundary.py` (final 6 feedback/diagnostics_and_alerts test nodes off `LegacyTranslator`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-08h/report.md`).

## Summary

Rewrote the 6 feedback, error reporting, and alert test nodes in
`tests/integration/test_stage2_boundary.py` off `LegacyTranslator`,
constructing typed `Command` objects directly and wrapping each in
`SimpleNamespace(command=...)` where the original assertion text needed
to survive unchanged.

The 6 rewritten test functions are:
1. `test_legacy_feedback_add_and_list_translate_and_execute` -> constructs `FeedbackAddCommand` and follow-up `FeedbackListCommand` directly
2. `test_legacy_feedback_add_applies_legacy_defaults` -> constructs `FeedbackAddCommand` directly with default fields
3. `test_legacy_feedback_resolve_translates_and_executes` -> constructs `FeedbackResolveCommand` directly
4. `test_legacy_report_error_aliases_translate_and_execute` -> constructs `ReportErrorCommand` directly with resolved aliases
5. `test_legacy_report_error_applies_defaults` -> constructs `ReportErrorCommand` directly with default fields
6. `test_legacy_alert_raise_translates_and_executes_end_to_end` -> constructs `AlertRaiseCommand` directly

All other 55 test functions (75 parameterized invocations) in
`test_stage2_boundary.py`, including the 39 already-rewritten 8a-8g
nodes, were **completely untouched** and verified with zero assertion
delta.

## Per-Node Assertion Table (Touched Nodes)

| Test Node ID | Before | After | Delta | Notes |
|---|:---:|:---:|:---:|---|
| `test_legacy_feedback_add_and_list_translate_and_execute` | 11 | 7 | -4 | Waived 4 translator-shape checks (`TranslatedCommand` x2, `FeedbackAddCommand`, `FeedbackListCommand`) |
| `test_legacy_feedback_add_applies_legacy_defaults` | 8 | 6 | -2 (net) | Waived 2 translator-shape wrapper checks + 5 now-tautological field checks (see correction below); added 5 real post-submit stored-state assertions |
| `test_legacy_feedback_resolve_translates_and_executes` | 8 | 6 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `FeedbackResolveCommand`) |
| `test_legacy_report_error_aliases_translate_and_execute` | 9 | 7 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `ReportErrorCommand`) |
| `test_legacy_report_error_applies_defaults` | 10 | 9 | -1 (net) | Waived 2 translator-shape wrapper checks + 5 now-tautological field checks (see correction below); added 6 real post-submit stored-state assertions |
| `test_legacy_alert_raise_translates_and_executes_end_to_end` | 15 | 13 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `AlertRaiseCommand`) |
| **Touched Total** | **61** | **48** | **-13 (net)** | 24 translator-shape/now-tautological assertions retired (all waived); 11 new real post-submit assertions added; every pre-existing real outcome/state assertion preserved byte-identical |

## Real Assertions Preserved (Not Retired)

- `test_legacy_feedback_add_and_list_translate_and_execute`: `translated.command.source_peer == "cc"`, `translated.command.title == "CLI flag parse error"`, `outcome` `CommandSuccess`, `target_id == "feedback:GAP-19700101-001"`, `listed` `CommandSuccess`, feedback ID matching, and `status == "open"`.
- `test_legacy_feedback_add_applies_legacy_defaults`: `client.submit(translated.command)` `CommandSuccess`, and (after a terminal correction, see below) real post-submit checks on the stored feedback record's `source_peer`/`category`/`severity`/`title`/`detail`.
- `test_legacy_feedback_resolve_translates_and_executes`: `translated.command.owner == "cx"`, `outcome` `CommandSuccess`, `target_id == "feedback:GAP-19700101-001"`, resolved state `status == "dismissed"`, `owner == "cx"`, and `resolved_at == 1000`.
- `test_legacy_report_error_aliases_translate_and_execute`: `peer_key == "cx"`, `pattern == "sandbox violation"`, `outcome` `CommandSuccess`, target ID match, series target existence, `count == 1`, and `detail == "write denied"`.
- `test_legacy_report_error_applies_defaults`: `outcome` `CommandSuccess`, and (after a terminal correction, see below) real post-submit checks on the stored operational-error-series record's `threshold`/`count`/`reports[0].severity`/`reports[0].detail`.
- `test_legacy_alert_raise_translates_and_executes_end_to_end`: `room_id == "room-legacy-alert"`, `raiser_instance_id == "raiser"`, `raiser_profile_id == "raiser"`, `severity == "P1"`, `message == "command bus alert"`, `outcome` `CommandSuccess`, `alert_target_id` match, `recipient_profile_ids == ("recipient",)`, target existence, target severity/message, recipient inbox count == 1, and inbox priority == "CRITICAL".

## Correction Made During This Batch: Two Tests Had Become Tautological

`test_legacy_feedback_add_applies_legacy_defaults` and
`test_legacy_report_error_applies_defaults` originally verified that
`LegacyTranslator` fills in specific default values (`"unknown"`,
`"other"`, `"medium"`, `"warn"`, an empty title/detail, `threshold=3`)
when the legacy CLI action is invoked with no arguments at all. The
initial rewrite of both tests preserved the field-equality assertions'
exact text (`translated.command.source_peer == "unknown"` etc.) but
changed what `translated.command` *is* -- from a value the translator
computed from empty input, to a value hand-supplied as a literal
constructor argument in the same line. This made the assertions check a
literal against itself: always true, and no longer verifying anything
about default-value resolution, since there is no resolution step left
to verify once `LegacyTranslator` is gone.

This was caught before commit (not by the gate, which cannot tell a
tautological assertion from a meaningful one -- both have valid syntax
and both passed at runtime) via direct review of the rewrite's semantics.
Fixed by retiring the tautological field checks (all structurally valid
translator-shape waivers verified against the baseline's real `translate()`
result) and replacing them with real post-submission verification against
the actually-persisted state:
- `test_legacy_feedback_add_applies_legacy_defaults`: looks up the
  created record via `runtime.feedback_service.get_feedback(
  "GAP-19700101-001")` and asserts its stored `source_peer`/`category`/
  `severity`/`title`/`detail` fields -- verifying the values actually
  round-trip through the real application boundary, not just that
  Python stored a constructor argument.
- `test_legacy_report_error_applies_defaults`: looks up the created
  series via `runtime.governance_broker.get_target(target_id)` (same
  pattern as the sibling `test_legacy_report_error_aliases_translate_
  and_execute`) and asserts `threshold`, `count`, and the first report's
  `severity`/`detail`.

Both replacements are real, non-tautological assertions on persisted
state reached only by actually submitting the command through the real
API -- a strictly stronger check than what they replaced was capable of
providing post-retirement, and (unlike batch 7's incident) no coverage
or behavioral verification was lost in the process.

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08h.json`
for the full waiver list (24 entries total). 14 are translator-shape
wrapper checks (`isinstance(translated, TranslatedCommand)` or
`isinstance(translated.command, CommandType)`); 10 more (5 each in
`test_legacy_feedback_add_applies_legacy_defaults` and
`test_legacy_report_error_applies_defaults`) are the now-tautological
`translated.command.FIELD == literal` default-value checks discussed in
the correction section above -- structurally valid translator-shape
waivers against the baseline's real `translate()` result, replaced with
real post-submit assertions.

## Untouched Nodes Confirmation

All 55 untouched test functions (75 parameterized invocations, including
the 39 sub-batch 8a-8g nodes) in `tests/integration/test_stage2_boundary.py`
were verified by the gate to have **exactly 0 assertion delta**, confirmed
via the per-node table in `tools/legacy_retirement/runs/batch-08h/report.md`.

## Gate Extensions

No modifications to `tools/legacy_retirement/gate.py` were required for
this sub-batch; all retired assertion shapes matched existing verified
`translator_only()` patterns.

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched
(confirmed by the gate's coverage comparison; the gate reported PASS).

## Completion of Batch 8 (test_stage2_boundary.py)

With Sub-batch 8h complete:
- All 45 `LegacyTranslator`-using test nodes in `tests/integration/test_stage2_boundary.py` have been rewritten off `LegacyTranslator`.
- `grep -c LegacyTranslator tests/integration/test_stage2_boundary.py` counts exactly 1 (the module-level import statement in the header).
- Zero test bodies in `tests/integration/test_stage2_boundary.py` invoke `LegacyTranslator()`.
- This officially concludes **Batch 8 of 9** in the ratified LegacyTranslator retirement roadmap.

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-08h \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08h.json \
    tests/integration/test_stage2_boundary.py
```
