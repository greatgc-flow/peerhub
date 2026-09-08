"""Focused negative controls for the preservation comparator (stdlib unittest)."""
from copy import deepcopy
import unittest

import gate


NODE = "tests/test_sample.py::test_sample[explicit-parameter-id]"
FUNCTION = '''def test_sample():
    from peerhub.application.legacy import LegacyTranslator
    translator = LegacyTranslator()
    outcome = translator.translate(call, submission=submission)
    assert outcome.command.target_peer_id == "cc"
'''


def assertion(expression):
    import ast
    return {"expression": expression, "normalized_ast": ast.dump(ast.parse(expression, mode="eval").body), "message_ast": None}


def evidence():
    return {
        "exit_code": 0, "collection_errors": [], "import_leaks": {},
        "selected": [NODE], "deselected": [],
        "collected": {NODE: {"markers": [], "function_source": FUNCTION,
                              "assertions": [assertion("outcome.command.target_peer_id == 'cc'")]}},
        "reports": {NODE: [{"phase": phase, "outcome": "passed", "wasxfail": None, "longrepr": None}
                            for phase in ("setup", "call", "teardown")]},
        "production": {"peerhub/application/legacy.py": {
            "nonexcluded_source_sha256": "same-source", "line_possible": [1, 2, 3], "line_executed": [1, 2],
            "branch_possible": [[2, 3], [2, -1]], "branch_executed": [[2, 3]],
        }},
    }


class ComparatorControls(unittest.TestCase):
    def compare(self, before, after, waivers=None):
        return gate.compare(before, after, waivers or [])

    def test_unchanged_positive_control(self):
        before = evidence()
        self.assertTrue(self.compare(before, deepcopy(before))["passed"])

    def test_equal_count_weakening_rejected(self):
        before = evidence()
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = [assertion("outcome.command.target_peer_id")]
        self.assertFalse(self.compare(before, after)["passed"])

    def test_only_structurally_verified_removal_can_be_waived(self):
        before = evidence()
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE, "expression": "outcome.command.target_peer_id == 'cc'",
                  "reason": "Retires the translator argument mapping check."}
        result = self.compare(before, after, [waiver])
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["accepted_translator_only_removals"]), 1)

    def test_unverified_import_scope_rejected(self):
        """A waiver must not pass just because a variable is *named* like a
        translator result. If `translator_only()` cannot verify LegacyTranslator
        was actually imported/constructed within the function's own AST (e.g.
        the import is only at module scope, outside what `function_source`
        captures), it must fail closed and reject the waiver -- never assume
        the alias regardless of evidence. This guards against silently turning
        the whole preservation gate into a rubber stamp."""
        before = evidence()
        no_local_import_function = '''def test_sample():
    translator = LegacyTranslator()
    outcome = translator.translate(call, submission=submission)
    assert outcome.command.target_peer_id == "cc"
'''
        before["collected"][NODE]["function_source"] = no_local_import_function
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE, "expression": "outcome.command.target_peer_id == 'cc'",
                  "reason": "Retires the translator argument mapping check."}
        self.assertFalse(self.compare(before, after, [waiver])["passed"])

    def test_verified_module_level_import_accepted(self):
        """A module-scoped `from peerhub.application.legacy import LegacyTranslator`
        (as opposed to batch 1's function-local style) must still let a
        genuinely translator-only assertion be waived -- but only via the
        explicit, pre-verified module_aliases channel, never via a guess."""
        before = evidence()
        module_scope_function = '''def test_sample():
    translator = LegacyTranslator()
    outcome = translator.translate(call, submission=submission)
    assert outcome.command.target_peer_id == "cc"
'''
        before["collected"][NODE]["function_source"] = module_scope_function
        before["collected"][NODE]["module_aliases"] = ["LegacyTranslator"]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE, "expression": "outcome.command.target_peer_id == 'cc'",
                  "reason": "Retires the translator argument mapping check."}
        result = self.compare(before, after, [waiver])
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["accepted_translator_only_removals"]), 1)

    def test_chained_translator_call_accepted(self):
        """`LegacyTranslator().translate(...)` (no intermediate `translator`
        variable) must be recognized as a translation result exactly like
        the two-step `translator = LegacyTranslator(); translator.translate(...)`
        form batch 1/2 used -- found necessary for batch 3's files."""
        before = evidence()
        chained_function = '''def test_sample():
    from peerhub.application.legacy import LegacyTranslator
    outcome = LegacyTranslator().translate(call, submission=submission)
    assert outcome.command.target_peer_id == "cc"
'''
        before["collected"][NODE]["function_source"] = chained_function
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE, "expression": "outcome.command.target_peer_id == 'cc'",
                  "reason": "Retires the translator argument mapping check."}
        result = self.compare(before, after, [waiver])
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["accepted_translator_only_removals"]), 1)

    def test_unverified_chained_call_rejected(self):
        """A chained call on some OTHER, unrelated class must not be treated
        as a translator result just because it superficially matches the
        `X().translate(...)` shape -- fails closed exactly like the
        unverified-import case."""
        before = evidence()
        chained_function = '''def test_sample():
    outcome = SomeUnrelatedThing().translate(call, submission=submission)
    assert outcome.command.target_peer_id == "cc"
'''
        before["collected"][NODE]["function_source"] = chained_function
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE, "expression": "outcome.command.target_peer_id == 'cc'",
                  "reason": "Retires the translator argument mapping check."}
        self.assertFalse(self.compare(before, after, [waiver])["passed"])

    def test_invalid_legacy_arguments_comparison_accepted(self):
        """`outcome == InvalidLegacyArguments(...)` is the translator's own
        documented rejection shape and must be waivable, verified via the
        same import-alias discipline as everything else."""
        before = evidence()
        function = '''def test_sample():
    from peerhub.application.legacy import LegacyTranslator, InvalidLegacyArguments
    translator = LegacyTranslator()
    outcome = translator.translate(call, submission=submission)
'''
        before["collected"][NODE]["function_source"] = function
        before["collected"][NODE]["assertions"] = [
            assertion('outcome == InvalidLegacyArguments(action="thread-new", reason="thread-new requires --topic")')]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE,
                  "expression": 'outcome == InvalidLegacyArguments(action="thread-new", reason="thread-new requires --topic")',
                  "reason": "Retires the legacy invalid-arguments rejection-shape check."}
        result = self.compare(before, after, [waiver])
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["accepted_translator_only_removals"]), 1)

    def test_invalid_legacy_arguments_inline_call_comparison_accepted(self):
        """`translator.translate(...) == InvalidLegacyArguments(...)` made
        directly inline in the comparison (no intermediate `outcome =`
        variable) must be waivable exactly like the variable-bound form --
        found necessary for batch 8c's file, which asserts the rejection
        shape straight off the call."""
        before = evidence()
        function = '''def test_sample():
    from peerhub.application.legacy import LegacyTranslator, InvalidLegacyArguments
    translator = LegacyTranslator()
'''
        before["collected"][NODE]["function_source"] = function
        before["collected"][NODE]["assertions"] = [
            assertion('translator.translate(call, submission=submission) == '
                      'InvalidLegacyArguments(action="thread-new", reason="thread-new requires --topic")')]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE,
                  "expression": 'translator.translate(call, submission=submission) == '
                                'InvalidLegacyArguments(action="thread-new", reason="thread-new requires --topic")',
                  "reason": "Retires the inline legacy invalid-arguments rejection-shape check."}
        result = self.compare(before, after, [waiver])
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["accepted_translator_only_removals"]), 1)

    def test_invalid_legacy_arguments_inline_call_on_unverified_alias_rejected(self):
        """The inline-call form must still fail closed when the call is on
        some OTHER, unrelated object that merely has a `.translate()`
        method and is not a verified LegacyTranslator -- exactly like
        `test_unverified_chained_call_rejected`, but for the
        InvalidLegacyArguments comparison shape specifically."""
        before = evidence()
        function = '''def test_sample():
    from peerhub.application.legacy import InvalidLegacyArguments
'''
        before["collected"][NODE]["function_source"] = function
        before["collected"][NODE]["assertions"] = [
            assertion('SomeUnrelatedThing().translate(call, submission=submission) == '
                      'InvalidLegacyArguments(action="thread-new", reason="thread-new requires --topic")')]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE,
                  "expression": 'SomeUnrelatedThing().translate(call, submission=submission) == '
                                'InvalidLegacyArguments(action="thread-new", reason="thread-new requires --topic")',
                  "reason": "Claimed translator-only, but not actually LegacyTranslator."}
        self.assertFalse(self.compare(before, after, [waiver])["passed"])

    def test_direct_result_field_comparison_accepted(self):
        """`translated.FIELD == literal` (a field read directly off the
        translation result, not through `.command`) must be waivable --
        needed for the InvalidLegacyArguments rejection shape, whose
        `action`/`reason` fields live on the result itself with no
        `.command` attribute at all. Found necessary for batch 8d's file
        (`translated.reason == "action must be ADD or REMOVE"`)."""
        before = evidence()
        function = '''def test_sample():
    from peerhub.application.legacy import LegacyTranslator
    translator = LegacyTranslator()
    translated = translator.translate(call, submission=submission)
    assert translated.reason == "action must be ADD or REMOVE"
'''
        before["collected"][NODE]["function_source"] = function
        before["collected"][NODE]["assertions"] = [
            assertion('translated.reason == "action must be ADD or REMOVE"')]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE, "expression": 'translated.reason == "action must be ADD or REMOVE"',
                  "reason": "Retires the translator's own rejection-reason field check."}
        result = self.compare(before, after, [waiver])
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["accepted_translator_only_removals"]), 1)

    def test_direct_result_field_on_unverified_name_rejected(self):
        """The direct-field form must still fail closed when the name is
        not actually a verified translation result -- some other object
        that merely happens to have a same-shaped `.reason` attribute."""
        before = evidence()
        function = '''def test_sample():
    translated = SomeUnrelatedThing()
    assert translated.reason == "action must be ADD or REMOVE"
'''
        before["collected"][NODE]["function_source"] = function
        before["collected"][NODE]["assertions"] = [
            assertion('translated.reason == "action must be ADD or REMOVE"')]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE, "expression": 'translated.reason == "action must be ADD or REMOVE"',
                  "reason": "Claimed translator-only, but not actually a translation result."}
        self.assertFalse(self.compare(before, after, [waiver])["passed"])

    def test_reused_result_name_across_multiple_translate_calls_accepted(self):
        """A test function that reuses the same variable name for SEVERAL
        sequential `translate()` calls (a common pattern when verifying
        multiple scenarios in one function) must still be recognized --
        found necessary for batch 6's file."""
        before = evidence()
        reused_name_function = '''def test_sample():
    from peerhub.application.legacy import LegacyTranslator
    translator = LegacyTranslator()
    translated = translator.translate(call, submission=submission)
    assert isinstance(translated, TranslatedCommand)
    result = client.submit(translated.command)
    translated = translator.translate(other_call, submission=submission)
    assert isinstance(translated, TranslatedCommand)
'''
        before["collected"][NODE]["function_source"] = reused_name_function
        before["collected"][NODE]["assertions"] = [
            assertion("isinstance(translated, TranslatedCommand)"),
            assertion("isinstance(translated, TranslatedCommand)"),
        ]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waivers = [
            {"nodeid": NODE, "expression": "isinstance(translated, TranslatedCommand)",
             "reason": "Retires the first translation wrapper check."},
            {"nodeid": NODE, "expression": "isinstance(translated, TranslatedCommand)",
             "reason": "Retires the second translation wrapper check."},
        ]
        result = self.compare(before, after, waivers)
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["accepted_translator_only_removals"]), 2)

    def test_result_name_reassigned_to_non_translation_value_rejected(self):
        """If even ONE assignment to a reused name is NOT a verified
        translate() call, that name must not qualify as a translation
        result at all -- fails closed rather than accepting some
        assignments and not others."""
        before = evidence()
        mixed_function = '''def test_sample():
    from peerhub.application.legacy import LegacyTranslator
    translator = LegacyTranslator()
    translated = translator.translate(call, submission=submission)
    assert isinstance(translated, TranslatedCommand)
    translated = None
    assert translated is None
'''
        before["collected"][NODE]["function_source"] = mixed_function
        before["collected"][NODE]["assertions"] = [assertion("isinstance(translated, TranslatedCommand)")]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waiver = {"nodeid": NODE, "expression": "isinstance(translated, TranslatedCommand)",
                  "reason": "Retires the translation wrapper check."}
        self.assertFalse(self.compare(before, after, [waiver])["passed"])

    def test_overprovisioned_duplicate_waiver_still_rejected(self):
        """Even with the duplicate-expression fix, an extra unused waiver
        (3 waivers for only 2 real removed occurrences) must still fail --
        the ambiguity check moved from per-occurrence candidate counting to
        the final used-vs-total-waivers count, it wasn't just deleted."""
        before = evidence()
        reused_name_function = '''def test_sample():
    from peerhub.application.legacy import LegacyTranslator
    translator = LegacyTranslator()
    translated = translator.translate(call, submission=submission)
    assert isinstance(translated, TranslatedCommand)
    translated = translator.translate(other_call, submission=submission)
    assert isinstance(translated, TranslatedCommand)
'''
        before["collected"][NODE]["function_source"] = reused_name_function
        before["collected"][NODE]["assertions"] = [
            assertion("isinstance(translated, TranslatedCommand)"),
            assertion("isinstance(translated, TranslatedCommand)"),
        ]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        waivers = [
            {"nodeid": NODE, "expression": "isinstance(translated, TranslatedCommand)", "reason": "First."},
            {"nodeid": NODE, "expression": "isinstance(translated, TranslatedCommand)", "reason": "Second."},
            {"nodeid": NODE, "expression": "isinstance(translated, TranslatedCommand)", "reason": "Extra, unused."},
        ]
        self.assertFalse(self.compare(before, after, waivers)["passed"])

    def test_wire_contract_cannot_be_waived(self):
        before = evidence()
        expr = "outcome.command.encode_params() == {'target_peer_id': 'cc'}"
        before["collected"][NODE]["assertions"] = [assertion(expr)]
        after = deepcopy(before)
        after["collected"][NODE]["assertions"] = []
        self.assertFalse(self.compare(before, after, [{"nodeid": NODE, "expression": expr, "reason": "claimed translator-only"}])["passed"])

    def test_removed_parameter_node_rejected(self):
        before = evidence()
        after = deepcopy(before)
        after["collected"] = {}
        after["selected"] = []
        self.assertFalse(self.compare(before, after)["passed"])

    def test_skip_reason_change_rejected(self):
        before = evidence()
        before["collected"][NODE]["markers"] = [{"name": "skip", "args": [], "kwargs": {"reason": "original"}}]
        after = deepcopy(before)
        after["collected"][NODE]["markers"][0]["kwargs"]["reason"] = "different"
        self.assertFalse(self.compare(before, after)["passed"])

    def test_teardown_regression_rejected(self):
        before = evidence()
        after = deepcopy(before)
        after["reports"][NODE][-1]["outcome"] = "failed"
        self.assertFalse(self.compare(before, after)["passed"])

    def test_missing_actual_execution_rejected(self):
        before = evidence()
        before["reports"] = {}
        self.assertFalse(self.compare(before, deepcopy(before))["passed"])

    def test_equal_percentage_changed_executed_set_rejected(self):
        before = evidence()
        after = deepcopy(before)
        after["production"]["peerhub/application/legacy.py"]["line_executed"] = [1, 3]
        after["production"]["peerhub/application/legacy.py"]["branch_executed"] = [[2, -1]]
        result = self.compare(before, after)
        self.assertFalse(result["passed"])
        self.assertEqual(result["coverage_delta"]["peerhub/application/legacy.py"]["line"]["lost"], [2])

    def test_denominator_change_rejected(self):
        before = evidence()
        after = deepcopy(before)
        after["production"]["peerhub/application/legacy.py"]["line_possible"].remove(3)
        self.assertFalse(self.compare(before, after)["passed"])


if __name__ == "__main__":
    unittest.main()
