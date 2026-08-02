from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.authority_identity as authority_identity
from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from runner import run_fixture


class DomainAuthorityIdentityTests(
    unittest.TestCase
):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = tuple(
        f"AC-02-{index:02d}"
        for index in range(1, 6)
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    EXPECTED = {
        "AC-02-01": (
            "ACCEPTED",
            None,
            "IDENTITY_CONFIRMED",
            False,
            True,
            True,
        ),
        "AC-02-02": (
            "REJECTED",
            "WORKSPACE_IDENTITY_MISMATCH",
            "IDENTITY_MISMATCH",
            True,
            True,
            True,
        ),
        "AC-02-03": (
            "REJECTED",
            "WORKSPACE_IDENTITY_MISMATCH",
            "EXPLICIT_RELOCATION_REQUIRED",
            True,
            True,
            True,
        ),
        "AC-02-04": (
            "ACCEPTED",
            None,
            "RELOCATION_IMPORTED",
            True,
            False,
            False,
        ),
        "AC-02-05": (
            "REJECTED",
            "WORKSPACE_IDENTITY_MISMATCH",
            "HOME_ID_COLLISION",
            True,
            True,
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

    def _read_record(
        self,
        path: Path,
    ) -> dict[str, Any]:
        return json.loads(
            path.read_text(encoding="utf-8")
        )

    def test_module_has_no_os_or_filesystem_imports(
        self,
    ) -> None:
        source = Path(
            authority_identity.__file__
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

                    result = DOMAIN_REGISTRY.verify(
                        self._load_script(
                            fixture_id
                        )["domain_case"],
                        fixture_id,
                        self._context(root),
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

                    result = DOMAIN_REGISTRY.verify(
                        self._load_script(
                            fixture_id
                        )["domain_case"],
                        fixture_id,
                        self._context(root),
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

    def test_expected_decisions_and_effects(
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

                    result = DOMAIN_REGISTRY.verify(
                        self._load_script(
                            fixture_id
                        )["domain_case"],
                        fixture_id,
                        self._context(root),
                    )
                    output = (
                        result.domain_expected[
                            "output"
                        ]
                    )

                    self.assertEqual(
                        (
                            output["decision"],
                            output["error_code"],
                            output["disposition"],
                            output[
                                "zero_operational_state_opens"
                            ],
                            output[
                                "zero_binding_mutations"
                            ],
                            output[
                                "zero_audit_writes"
                            ],
                        ),
                        expected,
                    )
                    self.assertTrue(
                        output[
                            "zero_legacy_mutations"
                        ]
                    )
                    self.assertTrue(
                        output[
                            "zero_provider_calls"
                        ]
                    )

    def test_matching_identity_ignores_path_alias(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-01"
                )["domain_case"],
                "AC-02-01",
                self._context(positive_root),
            )
            negative = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-01-NEG-01"
                )["domain_case"],
                "AC-02-01-NEG-01",
                self._context(negative_root),
            )

            inputs = positive.domain_input[
                "inputs"
            ]
            stored = inputs[
                "known_bindings"
            ][0]
            observed = inputs[
                "observed_home"
            ]

            self.assertNotEqual(
                stored[
                    "recorded_presented_path"
                ],
                observed["presented_path"],
            )
            self.assertEqual(
                stored["resolved_identity"],
                observed["resolved_identity"],
            )
            self.assertEqual(
                positive.domain_expected[
                    "output"
                ]["decision"],
                "ACCEPTED",
            )
            self.assertEqual(
                negative.domain_actual[
                    "output"
                ]["decision"],
                "REJECTED",
            )
            self.assertFalse(negative.passed)

    def test_same_path_cannot_mask_identity_mismatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-02"
                )["domain_case"],
                "AC-02-02",
                self._context(positive_root),
            )
            negative = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-02-NEG-01"
                )["domain_case"],
                "AC-02-02-NEG-01",
                self._context(negative_root),
            )

            inputs = positive.domain_input[
                "inputs"
            ]
            stored = inputs[
                "known_bindings"
            ][0]
            observed = inputs[
                "observed_home"
            ]

            self.assertEqual(
                stored[
                    "recorded_presented_path"
                ],
                observed["presented_path"],
            )
            self.assertNotEqual(
                stored["resolved_identity"],
                observed["resolved_identity"],
            )
            self.assertEqual(
                positive.domain_expected[
                    "output"
                ]["error_code"],
                "WORKSPACE_IDENTITY_MISMATCH",
            )
            self.assertTrue(
                positive.domain_expected[
                    "output"
                ][
                    "zero_operational_state_opens"
                ]
            )
            self.assertEqual(
                negative.domain_actual[
                    "output"
                ]["decision"],
                "ACCEPTED",
            )
            self.assertFalse(negative.passed)

    def test_copied_bytes_do_not_prove_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-03"
                )["domain_case"],
                "AC-02-03",
                self._context(positive_root),
            )
            negative = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-03-NEG-01"
                )["domain_case"],
                "AC-02-03-NEG-01",
                self._context(negative_root),
            )

            inputs = positive.domain_input[
                "inputs"
            ]
            stored = inputs[
                "known_bindings"
            ][0]
            observed = inputs[
                "observed_home"
            ]

            self.assertEqual(
                stored["home_content_digest"],
                observed["home_content_digest"],
            )
            self.assertNotEqual(
                stored["resolved_identity"],
                observed["resolved_identity"],
            )
            self.assertEqual(
                positive.domain_expected[
                    "output"
                ]["disposition"],
                "EXPLICIT_RELOCATION_REQUIRED",
            )
            self.assertEqual(
                negative.domain_actual[
                    "output"
                ]["decision"],
                "ACCEPTED",
            )
            self.assertFalse(negative.passed)

    def test_relocation_preserves_ids_and_writes_audit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-04"
                )["domain_case"],
                "AC-02-04",
                self._context(positive_root),
            )
            negative = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-04-NEG-01"
                )["domain_case"],
                "AC-02-04-NEG-01",
                self._context(negative_root),
            )

            inputs = positive.domain_input[
                "inputs"
            ]
            expected = positive.domain_expected[
                "output"
            ]
            active = expected["active_binding"]
            source = inputs[
                "known_bindings"
            ][0]
            observed = inputs[
                "observed_home"
            ]

            self.assertEqual(
                active["workspace_home_id"],
                source["workspace_home_id"],
            )
            self.assertEqual(
                active["database_identity"],
                source["database_identity"],
            )
            self.assertEqual(
                active["resolved_identity"],
                observed["resolved_identity"],
            )
            self.assertEqual(
                len(expected["audit_records"]),
                1,
            )
            self.assertEqual(
                expected["audit_records"][0][
                    "receipt_id"
                ],
                "relocation-receipt-04",
            )
            self.assertFalse(
                expected[
                    "zero_binding_mutations"
                ]
            )
            self.assertFalse(
                expected["zero_audit_writes"]
            )
            self.assertEqual(
                negative.domain_actual[
                    "output"
                ]["audit_records"],
                [],
            )
            self.assertTrue(
                negative.domain_actual[
                    "output"
                ]["zero_audit_writes"]
            )
            self.assertFalse(negative.passed)

    def test_home_id_collision_is_not_last_writer_wins(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            positive_root = parent / "positive"
            negative_root = parent / "negative"
            positive_root.mkdir()
            negative_root.mkdir()

            positive = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-05"
                )["domain_case"],
                "AC-02-05",
                self._context(positive_root),
            )
            negative = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-02-05-NEG-01"
                )["domain_case"],
                "AC-02-05-NEG-01",
                self._context(negative_root),
            )

            inputs = positive.domain_input[
                "inputs"
            ]
            bindings = inputs[
                "known_bindings"
            ]

            self.assertEqual(
                bindings[0]["workspace_home_id"],
                bindings[1]["workspace_home_id"],
            )
            self.assertNotEqual(
                bindings[0]["resolved_identity"],
                bindings[1]["resolved_identity"],
            )
            self.assertEqual(
                positive.domain_expected[
                    "output"
                ]["disposition"],
                "HOME_ID_COLLISION",
            )
            self.assertIsNone(
                positive.domain_expected[
                    "output"
                ]["active_binding"]
            )
            self.assertEqual(
                negative.domain_actual[
                    "output"
                ]["decision"],
                "ACCEPTED",
            )
            self.assertFalse(
                negative.domain_actual[
                    "output"
                ][
                    "zero_binding_mutations"
                ]
            )
            self.assertFalse(negative.passed)

    def test_runner_integration_positive_and_negative(
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
                    record = self._read_record(
                        run_fixture(
                            self._fixture_path(
                                fixture_id
                            ),
                            fixture_id,
                            root,
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
            first = parent / "first"
            second = parent / "second"
            fixture = self._fixture_path(
                "AC-02-04"
            )

            run_fixture(
                fixture,
                "AC-02-04",
                first,
            )
            run_fixture(
                fixture,
                "AC-02-04",
                second,
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
                        first / name
                    ).read_bytes(),
                    (
                        second / name
                    ).read_bytes(),
                    name,
                )


if __name__ == "__main__":
    unittest.main()