from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.authority_json_custody as authority_json_custody
from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from runner import run_fixture


class DomainAuthorityJsonCustodyTests(
    unittest.TestCase
):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = tuple(
        f"AC-05-{index:02d}"
        for index in range(1, 9)
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    EXPECTED = {
        "AC-05-01": (
            "PROCEED",
            None,
            "INPUTS_STABLE",
            True,
            0,
            False,
            False,
            True,
        ),
        "AC-05-02": (
            "ABORT",
            "CUTOVER_INPUT_DRIFT",
            "INPUT_DRIFT",
            False,
            0,
            False,
            False,
            False,
        ),
        "AC-05-03": (
            "HOLD_FOR_RECONCILIATION",
            None,
            "INCOMPLETE_SAFE",
            False,
            0,
            True,
            True,
            False,
        ),
        "AC-05-04": (
            "ABORT",
            None,
            "WRITE_SCOPE_OMISSION",
            False,
            0,
            False,
            False,
            False,
        ),
        "AC-05-05": (
            "PROCEED",
            None,
            "POST_ABORT_WRITE_FENCED",
            True,
            0,
            False,
            False,
            True,
        ),
        "AC-05-06": (
            "ABORT",
            "WRITE_SCOPE_NOT_QUIESCED",
            "CUSTODY_UNPROVABLE",
            False,
            0,
            False,
            False,
            True,
        ),
        "AC-05-07": (
            "ABORT",
            "WRITE_SCOPE_NOT_QUIESCED",
            "CUSTODY_UNPROVABLE",
            False,
            0,
            False,
            False,
            True,
        ),
        "AC-05-08": (
            "ABORT",
            "WRITE_SCOPE_NOT_QUIESCED",
            "CUSTODY_UNPROVABLE",
            False,
            0,
            False,
            False,
            True,
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

    def test_module_has_no_os_or_filesystem_imports(
        self,
    ) -> None:
        source = Path(
            authority_json_custody.__file__
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {
            "ctypes",
            "os",
            "pathlib",
            "shutil",
            "socket",
            "sqlite3",
            "subprocess",
            "win32",
            "win32api",
            "win32con",
            "win32file",
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
                        result.domain_actual[
                            "output"
                        ],
                        result.domain_expected[
                            "output"
                        ],
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
                            output["error_code"],
                            output["disposition"],
                            output[
                                "marker_eligible"
                            ],
                            output[
                                "legacy_write_mutations"
                            ],
                            output[
                                "uncertain_effects"
                            ],
                            output[
                                "reconciliation_required"
                            ],
                            output[
                                "custody_verdict_consumed"
                            ],
                        ),
                        expected,
                    )
                    self.assertEqual(
                        output["marker_writes"],
                        0,
                    )
                    self.assertTrue(
                        output[
                            "zero_provider_calls"
                        ]
                    )

    def test_stable_comparison_is_path_keyed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-05-01",
                positive_root,
            )
            negative = self._verify(
                "AC-05-01-NEG-01",
                negative_root,
            )
            inputs = positive.domain_input[
                "inputs"
            ]

            self.assertNotEqual(
                [
                    row["path"]
                    for row in inputs[
                        "admission_snapshot"
                    ]
                ],
                [
                    row["path"]
                    for row in inputs[
                        "precommit_snapshot"
                    ]
                ],
            )
            self.assertEqual(
                positive.domain_expected[
                    "output"
                ]["decision"],
                "PROCEED",
            )
            self.assertEqual(
                negative.domain_actual[
                    "output"
                ]["error_code"],
                "CUTOVER_INPUT_DRIFT",
            )
            self.assertFalse(negative.passed)

    def test_changed_file_blocks_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "AC-05-02",
                root,
            ).domain_expected["output"]

        self.assertEqual(
            output["error_code"],
            "CUTOVER_INPUT_DRIFT",
        )
        self.assertFalse(
            output["marker_eligible"]
        )
        self.assertEqual(
            output["marker_writes"],
            0,
        )

    def test_unreceipted_crash_is_incomplete_safe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-05-03",
                positive_root,
            )
            negative = self._verify(
                "AC-05-03-NEG-01",
                negative_root,
            )
            expected = positive.domain_expected[
                "output"
            ]

            self.assertEqual(
                expected["disposition"],
                "INCOMPLETE_SAFE",
            )
            self.assertTrue(
                expected["uncertain_effects"]
            )
            self.assertTrue(
                expected[
                    "reconciliation_required"
                ]
            )
            self.assertFalse(
                expected["marker_eligible"]
            )
            self.assertEqual(
                negative.domain_actual[
                    "output"
                ]["decision"],
                "PROCEED",
            )
            self.assertFalse(negative.passed)

    def test_omitted_scope_entry_is_detected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-05-04",
                root,
            )
            inputs = result.domain_input[
                "inputs"
            ]

        declared = set(
            inputs["declared_write_scope"]
        )
        observed = {
            row["path"]
            for row in inputs[
                "observed_legacy_writes"
            ]
        }
        self.assertFalse(
            observed.issubset(declared)
        )
        self.assertEqual(
            result.domain_expected[
                "output"
            ]["disposition"],
            "WRITE_SCOPE_OMISSION",
        )
        self.assertFalse(
            result.domain_expected[
                "output"
            ]["marker_eligible"]
        )

    def test_post_abort_attempt_has_zero_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = self._verify(
                "AC-05-05",
                positive_root,
            )
            negative = self._verify(
                "AC-05-05-NEG-01",
                negative_root,
            )

            self.assertEqual(
                positive.domain_expected[
                    "output"
                ]["legacy_write_mutations"],
                0,
            )
            self.assertEqual(
                negative.domain_actual[
                    "output"
                ]["legacy_write_mutations"],
                1,
            )
            self.assertFalse(negative.passed)

    def test_custody_failures_consume_verdict(
        self,
    ) -> None:
        expected_facts = {
            "AC-05-06": (
                "ALREADY_OPEN_FILE_SHARE_DELETE"
            ),
            "AC-05-07": (
                "ABSENT_PATH_NAMESPACE_UNFENCED"
            ),
            "AC-05-08": (
                "EXCLUSIVE_CUSTODY_UNOBTAINABLE"
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, fact in (
                expected_facts.items()
            ):
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    positive_root = (
                        parent / fixture_id
                    )
                    negative_root = (
                        parent
                        / f"{fixture_id}-negative"
                    )
                    positive_root.mkdir()
                    negative_root.mkdir()

                    positive = self._verify(
                        fixture_id,
                        positive_root,
                    )
                    negative = self._verify(
                        f"{fixture_id}-NEG-01",
                        negative_root,
                    )
                    custody = (
                        positive.domain_input[
                            "inputs"
                        ][
                            "custody_observation"
                        ]
                    )
                    output = (
                        positive.domain_expected[
                            "output"
                        ]
                    )

                    self.assertEqual(
                        custody["failure_facts"],
                        [fact],
                    )
                    self.assertEqual(
                        output["error_code"],
                        "WRITE_SCOPE_NOT_QUIESCED",
                    )
                    self.assertTrue(
                        output[
                            "custody_verdict_consumed"
                        ]
                    )
                    self.assertFalse(
                        output[
                            "marker_eligible"
                        ]
                    )
                    self.assertEqual(
                        negative.domain_actual[
                            "output"
                        ]["decision"],
                        "PROCEED",
                    )
                    self.assertFalse(
                        negative.passed
                    )

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
                                (
                                    f"{key}_"
                                    "raw_sha256"
                                )
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
                "AC-05-06"
            )

            run_fixture(
                fixture,
                "AC-05-06",
                first_root,
            )
            run_fixture(
                fixture,
                "AC-05-06",
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