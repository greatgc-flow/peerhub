from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.authority_filesystem as authority_filesystem
from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from runner import run_fixture


class DomainAuthorityFilesystemTests(
    unittest.TestCase
):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = tuple(
        f"AC-01-{index:02d}"
        for index in range(1, 9)
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    EXPECTED = {
        "AC-01-01": (
            "ACCEPTED",
            None,
            "COMPLETE",
        ),
        "AC-01-02": (
            "REJECTED",
            "FILESYSTEM_UNSUPPORTED",
            "FILESYSTEM",
        ),
        "AC-01-03": (
            "REJECTED",
            "FILESYSTEM_UNSUPPORTED",
            "FILESYSTEM",
        ),
        "AC-01-04": (
            "REJECTED",
            "FILESYSTEM_UNSUPPORTED",
            "FILESYSTEM",
        ),
        "AC-01-05": (
            "REJECTED",
            "FILESYSTEM_UNSUPPORTED",
            "WAL_SHARED_MEMORY",
        ),
        "AC-01-06": (
            "REJECTED",
            "FILESYSTEM_UNSUPPORTED",
            "LOCK_RENAME_CUSTODY",
        ),
        "AC-01-07": (
            "REJECTED",
            "FILESYSTEM_UNSUPPORTED",
            "FILESYSTEM",
        ),
        "AC-01-08": (
            "ACCEPTED",
            None,
            "COMPLETE",
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
            authority_filesystem.__file__
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

    def test_expected_decisions_and_no_effects(
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
                            output["probe_stage"],
                        ),
                        expected,
                    )

                    for key in (
                        "zero_database_mutations",
                        "zero_marker_mutations",
                        "zero_legacy_mutations",
                        "zero_provider_calls",
                    ):
                        self.assertTrue(
                            output[key],
                            key,
                        )

    def test_ac0107_trusts_final_resolved_node(
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
                    "AC-01-07"
                )["domain_case"],
                "AC-01-07",
                self._context(positive_root),
            )
            negative = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-01-07-NEG-01"
                )["domain_case"],
                "AC-01-07-NEG-01",
                self._context(negative_root),
            )

            inputs = positive.domain_input[
                "inputs"
            ]
            nodes = {
                node["node_id"]: node
                for node in inputs[
                    "resolution_graph"
                ]["nodes"]
            }
            entry = inputs[
                "resolution_graph"
            ]["entries"][0]

            self.assertEqual(
                nodes[
                    entry["entry_node_id"]
                ]["reported_filesystem"],
                "NTFS",
            )

            physical = nodes["physical-07"]
            self.assertEqual(
                physical[
                    "resolved_filesystem"
                ],
                "EXFAT",
            )
            self.assertTrue(
                physical["redirected"]
            )
            self.assertTrue(
                physical["virtualized"]
            )
            self.assertEqual(
                positive.domain_expected[
                    "output"
                ]["decision"],
                "REJECTED",
            )
            self.assertEqual(
                negative.domain_actual[
                    "output"
                ]["decision"],
                "ACCEPTED",
            )
            self.assertFalse(negative.passed)

    def test_ac0108_keys_lock_by_physical_identity(
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
                    "AC-01-08"
                )["domain_case"],
                "AC-01-08",
                self._context(positive_root),
            )
            negative = DOMAIN_REGISTRY.verify(
                self._load_script(
                    "AC-01-08-NEG-01"
                )["domain_case"],
                "AC-01-08-NEG-01",
                self._context(negative_root),
            )

            expected = (
                positive.domain_expected[
                    "output"
                ]
            )
            self.assertEqual(
                len(
                    expected[
                        "workspace_identities"
                    ]
                ),
                1,
            )
            self.assertEqual(
                len(
                    expected[
                        "migration_lock_keys"
                    ]
                ),
                1,
            )
            self.assertEqual(
                expected[
                    "migration_lock_keys"
                ][0]["key_source"],
                "RESOLVED_IDENTITY",
            )
            self.assertEqual(
                [
                    row["disposition"]
                    for row in expected[
                        "lock_acquisitions"
                    ]
                ],
                [
                    "ACQUIRED",
                    "CONTENDED",
                ],
            )

            actual = negative.domain_actual[
                "output"
            ]
            self.assertEqual(
                len(
                    actual[
                        "workspace_identities"
                    ]
                ),
                1,
            )
            self.assertEqual(
                len(
                    actual[
                        "migration_lock_keys"
                    ]
                ),
                2,
            )
            self.assertEqual(
                {
                    row["key_source"]
                    for row in actual[
                        "migration_lock_keys"
                    ]
                },
                {"PRESENTED_PATH"},
            )
            self.assertEqual(
                [
                    row["disposition"]
                    for row in actual[
                        "lock_acquisitions"
                    ]
                ],
                [
                    "ACQUIRED",
                    "ACQUIRED",
                ],
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
                "AC-01-08"
            )

            run_fixture(
                fixture,
                "AC-01-08",
                first,
            )
            run_fixture(
                fixture,
                "AC-01-08",
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