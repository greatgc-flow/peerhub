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
