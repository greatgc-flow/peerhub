from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from domain import authority_drain
from runner import run_fixture


class DomainAuthorityDrainTests(
    unittest.TestCase
):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = tuple(
        f"AC-08-{index:02d}"
        for index in range(1, 8)
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    EXPECTED = {
        "AC-08-01": (
            "PROCEED_TO_PRECOMMIT",
            "DRAIN_COMPLETE",
            True,
            False,
            False,
            None,
        ),
        "AC-08-02": (
            "WAITING_FOR_DRAIN",
            "COOPERATIVE_CANCELLATION",
            False,
            False,
            False,
            90,
        ),
        "AC-08-03": (
            "PROCEED_TO_PRECOMMIT",
            "CUTOFF_SAFE_ABORT",
            True,
            False,
            True,
            None,
        ),
        "AC-08-04": (
            "BLOCKED_INCOMPLETE_SAFE",
            "INCOMPLETE_SAFE",
            False,
            True,
            True,
            None,
        ),
        "AC-08-05": (
            "BLOCKED_INCOMPLETE_SAFE",
            "INCOMPLETE_SAFE",
            False,
            True,
            True,
            None,
        ),
        "AC-08-06": (
            "BLOCKED_INCOMPLETE_SAFE",
            "INCOMPLETE_SAFE",
            False,
            True,
            True,
            None,
        ),
        "AC-08-07": (
            "BLOCKED_INCOMPLETE_SAFE",
            "INCOMPLETE_SAFE",
            False,
            True,
            True,
            None,
        ),
    }

    def _fixture_path(
        self,
        fixture_id: str,
    ) -> Path:
        return (
            self.FIXTURE_DIRECTORY
            / f"{fixture_id}.json"
        )

    def _load_script(
        self,
        fixture_id: str,
    ) -> dict[str, Any]:
        return json.loads(
            self._fixture_path(
                fixture_id
            ).read_text(encoding="utf-8")
        )

    def _context(
        self,
        root: Path,
    ) -> IsolatedDomainContext:
        return IsolatedDomainContext(
            root=root,
            clock=(1,),
            ids=(
                "run-test",
                "event-test",
            ),
        )

    def _verify(
        self,
        fixture_id: str,
        root: Path,
    ):
        return DOMAIN_REGISTRY.verify(
            self._load_script(
                fixture_id
            )["domain_case"],
            fixture_id,
            self._context(root),
        )

    def test_module_has_no_os_process_or_filesystem_imports(
        self,
    ) -> None:
        source = Path(
            authority_drain.__file__
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {
            "ctypes",
            "multiprocessing",
            "os",
            "pathlib",
            "psutil",
            "shutil",
            "signal",
            "socket",
            "sqlite3",
            "subprocess",
            "win32",
            "win32api",
            "win32con",
            "win32file",
            "win32process",
        }
        imported_roots: set[str] = set()

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

        self.assertEqual(
            imported_roots & forbidden,
            set(),
        )

    def test_positive_oracle_adapter_pairs_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(
                        fixture_id,
                        root,
                    )
                    self.assertTrue(result.passed)
                    self.assertEqual(
                        result.domain_verification[
                            "status"
                        ],
                        "PASS",
                    )

    def test_fault_injected_pairs_are_detected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id in self.NEGATIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(
                        fixture_id,
                        root,
                    )
                    self.assertFalse(result.passed)
                    self.assertEqual(
                        result.domain_verification[
                            "status"
                        ],
                        "FAIL",
                    )
                    self.assertNotEqual(
                        result.domain_actual["output"],
                        result.domain_expected["output"],
                    )

    def test_expected_decisions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, expected in (
                self.EXPECTED.items()
            ):
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = parent / fixture_id
                    root.mkdir()
                    output = self._verify(
                        fixture_id,
                        root,
                    ).domain_expected["output"]

                    self.assertEqual(
                        (
                            output["decision"],
                            output["disposition"],
                            output["marker_eligible"],
                            output[
                                "reconciliation_required"
                            ],
                            output["cutoff_applied"],
                            output[
                                "cancellation_sent_at_seconds"
                            ],
                        ),
                        expected,
                    )
                    self.assertTrue(
                        output["admission_closed"]
                    )
                    self.assertEqual(
                        output["new_leases_admitted"],
                        0,
                    )
                    self.assertTrue(
                        output["zero_provider_calls"]
                    )

    def test_normal_completion_keeps_admission_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-08-01",
                positive_root,
            )
            negative = self._verify(
                "AC-08-01-NEG-01",
                negative_root,
            )

        self.assertEqual(
            positive.domain_input["inputs"][
                "new_lease_attempts"
            ],
            1,
        )
        self.assertEqual(
            positive.domain_expected["output"][
                "new_leases_admitted"
            ],
            0,
        )
        self.assertEqual(
            negative.domain_actual["output"][
                "new_leases_admitted"
            ],
            1,
        )
        self.assertFalse(negative.passed)

    def test_ninety_seconds_only_requests_cancellation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-08-02",
                positive_root,
            )
            negative = self._verify(
                "AC-08-02-NEG-01",
                negative_root,
            )

        expected = positive.domain_expected["output"]
        actual_fault = negative.domain_actual["output"]
        self.assertEqual(
            expected["lease_results"][0][
                "disposition"
            ],
            "CANCEL_REQUESTED",
        )
        self.assertFalse(expected["cutoff_applied"])
        self.assertEqual(
            actual_fault["lease_results"][0][
                "disposition"
            ],
            "FORCE_ABORTED_SAFE",
        )
        self.assertTrue(
            actual_fault["cutoff_applied"]
        )
        self.assertFalse(negative.passed)

    def test_cutoff_requires_complete_conjunction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            safe_root = parent / "safe"
            birth_root = parent / "birth"
            safety_root = parent / "safety"
            safe_root.mkdir()
            birth_root.mkdir()
            safety_root.mkdir()

            safe = self._verify(
                "AC-08-03",
                safe_root,
            ).domain_expected["output"]
            birth = self._verify(
                "AC-08-05",
                birth_root,
            ).domain_expected["output"]
            safety = self._verify(
                "AC-08-07",
                safety_root,
            ).domain_expected["output"]

        safe_row = safe["lease_results"][0]
        self.assertTrue(
            safe_row["static_classification_accepted"]
        )
        self.assertTrue(
            safe_row["process_birth_match_accepted"]
        )
        self.assertTrue(
            safe_row["admission_hashes_accepted"]
        )
        self.assertTrue(
            safe_row["identity_and_lock_accepted"]
        )
        self.assertTrue(
            safe_row["terminal_receipt_accepted"]
        )
        self.assertEqual(
            safe_row["disposition"],
            "FORCE_ABORTED_SAFE",
        )

        birth_row = birth["lease_results"][0]
        self.assertTrue(
            birth_row["static_classification_accepted"]
        )
        self.assertFalse(
            birth_row["process_birth_match_accepted"]
        )
        self.assertEqual(
            birth_row["disposition"],
            "INCOMPLETE_SAFE",
        )

        safety_rows = {
            row["lease_id"]: row
            for row in safety["lease_results"]
        }
        self.assertFalse(
            safety_rows[
                "lease-identity-unknown"
            ]["identity_and_lock_accepted"]
        )
        self.assertFalse(
            safety_rows[
                "lease-lock-owner-unknown"
            ]["identity_and_lock_accepted"]
        )
        self.assertFalse(
            safety_rows[
                "lease-admission-hash-changed"
            ]["admission_hashes_accepted"]
        )
        self.assertTrue(
            all(
                row["disposition"]
                == "INCOMPLETE_SAFE"
                for row in safety_rows.values()
            )
        )

    def test_cutoff_is_inclusive_at_120_seconds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-08-03",
                positive_root,
            )
            negative = self._verify(
                "AC-08-03-NEG-01",
                negative_root,
            )

        self.assertEqual(
            positive.domain_input["inputs"][
                "elapsed_seconds"
            ],
            120,
        )
        self.assertTrue(
            positive.domain_expected["output"][
                "cutoff_applied"
            ]
        )
        self.assertFalse(
            negative.domain_actual["output"][
                "cutoff_applied"
            ]
        )
        self.assertFalse(negative.passed)

    def test_static_classification_cannot_be_self_reported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-08-04",
                positive_root,
            )
            negative = self._verify(
                "AC-08-04-NEG-01",
                negative_root,
            )

        inputs = positive.domain_input["inputs"]
        self.assertTrue(
            all(
                lease["classification"][
                    "self_declared_effect_class"
                ]
                == "CONFIG_ONLY"
                and lease["classification"][
                    "self_declared_effect_phase"
                ]
                == "PRE_EFFECT"
                for lease in inputs["leases"]
            )
        )
        self.assertTrue(
            all(
                not row[
                    "static_classification_accepted"
                ]
                for row in positive.domain_expected[
                    "output"
                ]["lease_results"]
            )
        )
        self.assertEqual(
            positive.domain_expected["output"][
                "decision"
            ],
            "BLOCKED_INCOMPLETE_SAFE",
        )
        self.assertEqual(
            negative.domain_actual["output"][
                "decision"
            ],
            "PROCEED_TO_PRECOMMIT",
        )
        self.assertFalse(negative.passed)

    def test_process_exit_does_not_replace_birth_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-08-05",
                positive_root,
            )
            negative = self._verify(
                "AC-08-05-NEG-01",
                negative_root,
            )

        lease = positive.domain_input[
            "inputs"
        ]["leases"][0]
        self.assertEqual(
            lease["status"],
            "TERMINAL",
        )
        self.assertFalse(
            lease["terminal_receipt"]
        )
        self.assertFalse(
            lease["process_birth_match"]
        )
        self.assertEqual(
            positive.domain_expected["output"][
                "disposition"
            ],
            "INCOMPLETE_SAFE",
        )
        self.assertEqual(
            negative.domain_actual["output"][
                "disposition"
            ],
            "CUTOFF_SAFE_ABORT",
        )
        self.assertFalse(negative.passed)

    def test_external_effect_requires_durable_receipt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-08-06",
                positive_root,
            )
            negative = self._verify(
                "AC-08-06-NEG-01",
                negative_root,
            )

        lease = positive.domain_input[
            "inputs"
        ]["leases"][0]
        expected_row = positive.domain_expected[
            "output"
        ]["lease_results"][0]

        self.assertTrue(
            lease[
                "operation_may_have_external_effect"
            ]
        )
        self.assertFalse(
            lease["terminal_receipt"]
        )
        self.assertFalse(
            lease["provider_effect_visible"]
        )
        self.assertFalse(
            expected_row["terminal_receipt_accepted"]
        )
        self.assertEqual(
            expected_row["disposition"],
            "INCOMPLETE_SAFE",
        )
        self.assertEqual(
            negative.domain_actual["output"][
                "decision"
            ],
            "PROCEED_TO_PRECOMMIT",
        )
        self.assertFalse(negative.passed)

    def test_unknown_identity_lock_and_hash_each_block(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-08-07",
                positive_root,
            )
            negative = self._verify(
                "AC-08-07-NEG-01",
                negative_root,
            )

        expected = positive.domain_expected["output"]
        self.assertEqual(
            expected["decision"],
            "BLOCKED_INCOMPLETE_SAFE",
        )
        self.assertTrue(
            expected["reconciliation_required"]
        )
        self.assertFalse(
            expected["marker_eligible"]
        )
        self.assertEqual(
            negative.domain_actual["output"][
                "decision"
            ],
            "PROCEED_TO_PRECOMMIT",
        )
        self.assertFalse(negative.passed)

    def test_runner_integration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id in (
                self.POSITIVE_IDS
                + self.NEGATIVE_IDS
            ):
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = parent / fixture_id
                    record_path = run_fixture(
                        self._fixture_path(
                            fixture_id
                        ),
                        fixture_id,
                        root,
                    )
                    record = json.loads(
                        record_path.read_text(
                            encoding="utf-8"
                        )
                    )
                    positive = (
                        fixture_id
                        in self.POSITIVE_IDS
                    )

                    self.assertEqual(
                        record["status"],
                        (
                            "V1_CAPTURE"
                            if positive
                            else (
                                "DOMAIN_"
                                "ASSERTION_FAILED"
                            )
                        ),
                    )
                    self.assertEqual(
                        record[
                            "domain_verification"
                        ]["status"],
                        (
                            "PASS"
                            if positive
                            else "FAIL"
                        ),
                    )

                    if positive:
                        self.assertEqual(
                            record[
                                "coverage_scope"
                            ],
                            "SPEC_FAITHFUL",
                        )
                    else:
                        self.assertNotIn(
                            "coverage_scope",
                            record,
                        )

                    for key in (
                        "domain_input",
                        "domain_actual",
                        "domain_expected",
                        "domain_verification",
                    ):
                        artifact = (
                            root
                            / record[
                                "artifact_paths"
                            ][key]
                        )
                        self.assertTrue(
                            artifact.is_file()
                        )
                        self.assertEqual(
                            hashlib.sha256(
                                artifact.read_bytes()
                            ).hexdigest(),
                            record["digests"][
                                f"{key}_raw_sha256"
                            ],
                        )

    def test_domain_outputs_are_deterministic(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            first_root = parent / "first"
            second_root = parent / "second"
            fixture = self._fixture_path(
                "AC-08-04"
            )

            run_fixture(
                fixture,
                "AC-08-04",
                first_root,
            )
            run_fixture(
                fixture,
                "AC-08-04",
                second_root,
            )

            for name in (
                "domain-input.json",
                "domain-actual.json",
                "domain-expected.json",
                "domain-verification.json",
                "fixture-record.json",
            ):
                self.assertEqual(
                    (
                        first_root / name
                    ).read_bytes(),
                    (
                        second_root / name
                    ).read_bytes(),
                    name,
                )


if __name__ == "__main__":
    unittest.main()