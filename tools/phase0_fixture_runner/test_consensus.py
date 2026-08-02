from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.consensus as consensus
from domain import DOMAIN_REGISTRY, IsolatedDomainContext
from runner import run_fixture


class ConsensusDomainTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent / "fixtures"
    )
    POSITIVE_IDS = tuple(
        f"CS-{index:02d}"
        for index in range(1, 7)
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )

    def _fixture_path(self, fixture_id: str) -> Path:
        return self.FIXTURE_DIRECTORY / f"{fixture_id}.json"

    def _script(self, fixture_id: str) -> dict[str, Any]:
        return json.loads(
            self._fixture_path(fixture_id).read_text(
                encoding="utf-8"
            )
        )

    def _context(self, root: Path) -> IsolatedDomainContext:
        return IsolatedDomainContext(
            root=root,
            clock=(1,),
            ids=("run-test", "event-test"),
        )

    def _verify(self, fixture_id: str, root: Path):
        return DOMAIN_REGISTRY.verify(
            self._script(fixture_id)["domain_case"],
            fixture_id,
            self._context(root),
        )

    def test_module_has_no_real_os_access(self) -> None:
        source = Path(
            consensus.__file__
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_imports = {
            "ctypes",
            "http",
            "multiprocessing",
            "os",
            "pathlib",
            "psutil",
            "requests",
            "shutil",
            "signal",
            "socket",
            "sqlite3",
            "subprocess",
            "urllib",
            "win32",
            "win32api",
            "win32con",
            "win32file",
            "win32process",
        }
        forbidden_calls = {
            "__import__",
            "compile",
            "eval",
            "exec",
            "open",
        }
        imported_roots: set[str] = set()
        called_names: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
            ):
                imported_roots.add(
                    node.module.split(".", 1)[0]
                )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
            ):
                called_names.add(node.func.id)

        self.assertEqual(
            imported_roots & forbidden_imports,
            set(),
        )
        self.assertEqual(
            called_names & forbidden_calls,
            set(),
        )

    def test_fixture_inputs_satisfy_closed_vectors(self) -> None:
        for fixture_id in self.POSITIVE_IDS + self.NEGATIVE_IDS:
            with self.subTest(fixture_id=fixture_id):
                raw_inputs = self._script(fixture_id)[
                    "domain_case"
                ]["inputs"]
                self.assertEqual(
                    consensus.validate_consensus_inputs(
                        fixture_id,
                        raw_inputs,
                    ),
                    raw_inputs,
                )

    def test_positive_oracle_adapter_pairs_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(fixture_id, root)
                    self.assertTrue(result.passed)
                    self.assertEqual(
                        result.domain_verification["status"],
                        "PASS",
                    )

    def test_expected_tiers_and_decisions_are_exact(self) -> None:
        expected = {
            "CS-01": ("OBS", "ROUND_CONTRACT_FROZEN"),
            "CS-02": ("OBS", "VOTE_IDEMPOTENT_NOOP"),
            "CS-03": ("OBS", "VOTE_REJECTED"),
            "CS-04": ("OBS", "ROUND_ESCALATED"),
            "CS-05": ("OBS", "ROUND_FINALIZED"),
            "CS-06": ("CANDIDATE", "OUTCOMES_DERIVED"),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, values in expected.items():
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    output = self._verify(
                        fixture_id,
                        root,
                    ).domain_expected["output"]
                    self.assertEqual(
                        (
                            output["rule_tier"],
                            output["decision"],
                        ),
                        values,
                    )

    def test_cs01_freezes_electorate_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            details = self._verify(
                "CS-01",
                Path(temporary),
            ).domain_expected["output"]["details"]

        self.assertTrue(details["round_contract_frozen"])
        self.assertEqual(
            details["policy_revision"],
            "consensus-policy-v1",
        )
        self.assertEqual(
            details["voters"],
            ["cc", "ag", "cx"],
        )
        self.assertEqual(
            details["required_voters"],
            ["cc", "ag"],
        )
        self.assertEqual(
            details["excluded_voters"],
            [
                {
                    "voter_id": "cx",
                    "reason": "health_red",
                }
            ],
        )

    def test_cs02_identical_repeat_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            details = self._verify(
                "CS-02",
                Path(temporary),
            ).domain_expected["output"]["details"]

        self.assertEqual(details["vote_record_count"], 1)
        self.assertTrue(details["repeat_noop"])
        self.assertFalse(details["attempted_vote_applied"])
        self.assertTrue(details["original_vote_immutable"])
        self.assertIsNone(details["error_code"])
        self.assertEqual(
            details["recorded_votes"][0]["vote_value"],
            "AGREE",
        )

    def test_cs03_conflict_rejected_original_immutable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            details = self._verify(
                "CS-03",
                Path(temporary),
            ).domain_expected["output"]["details"]

        self.assertEqual(
            details["error_code"],
            "VOTE_ALREADY_CAST",
        )
        self.assertFalse(details["attempted_vote_applied"])
        self.assertTrue(details["original_vote_immutable"])
        self.assertEqual(details["vote_record_count"], 1)
        self.assertEqual(
            details["recorded_votes"][0]["vote_value"],
            "AGREE",
        )

    def test_cs04_timeout_escalates_and_retains_votes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            details = self._verify(
                "CS-04",
                Path(temporary),
            ).domain_expected["output"]["details"]

        self.assertEqual(details["status"], "ESCALATED")
        self.assertEqual(
            details["effective_outcome"],
            "TIMEOUT_UNRESOLVED",
        )
        self.assertEqual(
            details["missing_electorate"],
            ["cc", "cx"],
        )
        self.assertEqual(
            details["retained_votes"],
            [
                {
                    "voter_id": "ag",
                    "vote_value": "AGREE",
                    "reason": "retained-before-timeout",
                }
            ],
        )
        self.assertEqual(details["decision_event_count"], 0)

    def test_cs05_unanimity_emits_one_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            details = self._verify(
                "CS-05",
                Path(temporary),
            ).domain_expected["output"]["details"]

        self.assertEqual(details["status"], "FINALIZED")
        self.assertEqual(
            details["effective_outcome"],
            "UNANIMOUS",
        )
        self.assertEqual(
            details["required_voters"],
            ["cc", "ag"],
        )
        self.assertEqual(details["decision_event_count"], 1)

    def test_cs06_candidate_formula_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "CS-06",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(output["rule_tier"], "CANDIDATE")
        details = output["details"]
        self.assertEqual(
            details["derivation_formula"],
            (
                "NO_DISSENT=>VOTE;"
                "DISSENT_WITHOUT_ARBITER=>REJECTED;"
                "DISSENT_WITH_ARBITER=>ARBITER_OPINION"
            ),
        )

        rows = {
            row["case_id"]: row
            for row in details["derivation_rows"]
        }

        self.assertFalse(
            rows["no-dissent"]["dissent_present"]
        )
        self.assertEqual(
            rows["no-dissent"]["vote_value"],
            "APPROVE",
        )
        self.assertEqual(
            rows["no-dissent"]["effective_outcome"],
            "APPROVED",
        )

        self.assertTrue(
            rows["dissent-no-opinion"]["dissent_present"]
        )
        self.assertFalse(
            rows["dissent-no-opinion"][
                "arbiter_opinion_recorded"
            ]
        )
        self.assertEqual(
            rows["dissent-no-opinion"][
                "effective_outcome"
            ],
            "REJECTED",
        )

        overridden = rows["dissent-with-opinion"]
        self.assertTrue(overridden["dissent_present"])
        self.assertEqual(
            overridden["dissent_voters"],
            ["ag"],
        )
        self.assertTrue(
            overridden["arbiter_opinion_recorded"]
        )
        self.assertEqual(
            overridden["arbiter_opinion_value"],
            "APPROVE",
        )
        self.assertEqual(
            overridden["effective_outcome"],
            "APPROVED",
        )
        self.assertEqual(
            overridden["derivation_basis"],
            "ARBITER_OPINION",
        )

    def test_each_specific_fault_is_detected(self) -> None:
        expected = {
            "CS-01-NEG-01": (
                "required_voters",
                ["cc", "ag", "cx"],
                ["cc", "ag"],
            ),
            "CS-02-NEG-01": (
                "vote_record_count",
                2,
                1,
            ),
            "CS-03-NEG-01": (
                "attempted_vote_applied",
                True,
                False,
            ),
            "CS-04-NEG-01": (
                "status",
                "FINALIZED",
                "ESCALATED",
            ),
            "CS-05-NEG-01": (
                "decision_event_count",
                2,
                1,
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, values in expected.items():
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(fixture_id, root)
                    actual = result.domain_actual[
                        "output"
                    ]["details"]
                    oracle = result.domain_expected[
                        "output"
                    ]["details"]

                    self.assertFalse(result.passed)
                    self.assertEqual(
                        actual[values[0]],
                        values[1],
                    )
                    self.assertEqual(
                        oracle[values[0]],
                        values[2],
                    )

            fixture_id = "CS-06-NEG-01"
            root = parent / fixture_id
            root.mkdir()
            result = self._verify(fixture_id, root)
            self.assertFalse(result.passed)

            actual_rows = {
                row["case_id"]: row
                for row in result.domain_actual[
                    "output"
                ]["details"]["derivation_rows"]
            }
            expected_rows = {
                row["case_id"]: row
                for row in result.domain_expected[
                    "output"
                ]["details"]["derivation_rows"]
            }
            self.assertEqual(
                actual_rows["dissent-with-opinion"][
                    "effective_outcome"
                ],
                "REJECTED",
            )
            self.assertEqual(
                expected_rows["dissent-with-opinion"][
                    "effective_outcome"
                ],
                "APPROVED",
            )

    def test_runner_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id in (
                self.POSITIVE_IDS + self.NEGATIVE_IDS
            ):
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    record_path = run_fixture(
                        self._fixture_path(fixture_id),
                        fixture_id,
                        root,
                    )
                    record = json.loads(
                        record_path.read_text(
                            encoding="utf-8"
                        )
                    )
                    positive = fixture_id in self.POSITIVE_IDS

                    self.assertEqual(
                        record["status"],
                        (
                            "V1_CAPTURE"
                            if positive
                            else "DOMAIN_ASSERTION_FAILED"
                        ),
                    )
                    self.assertEqual(
                        record["domain_verification"]["status"],
                        "PASS" if positive else "FAIL",
                    )

                    if positive:
                        self.assertEqual(
                            record["coverage_scope"],
                            "SPEC_FAITHFUL",
                        )

                    for key in (
                        "domain_input",
                        "domain_actual",
                        "domain_expected",
                        "domain_verification",
                    ):
                        artifact = (
                            root
                            / record["artifact_paths"][key]
                        )
                        self.assertTrue(artifact.is_file())
                        self.assertEqual(
                            hashlib.sha256(
                                artifact.read_bytes()
                            ).hexdigest(),
                            record["digests"][
                                f"{key}_raw_sha256"
                            ],
                        )


if __name__ == "__main__":
    unittest.main()
