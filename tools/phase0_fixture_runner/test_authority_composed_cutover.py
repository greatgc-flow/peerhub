from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.authority_composed_cutover as composed
from domain import DOMAIN_REGISTRY, IsolatedDomainContext
from runner import run_fixture


class DomainAuthorityComposedCutoverTests(
    unittest.TestCase
):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent / "fixtures"
    )
    POSITIVE_IDS = (
        "AC-COMPOSED-01",
        "AC-COMPOSED-02",
        "AC-COMPOSED-03",
        "AC-COMPOSED-04",
    )
    NEGATIVE_IDS = ("AC-COMPOSED-02-NEG-01",)

    def _fixture_path(self, fixture_id: str) -> Path:
        return self.FIXTURE_DIRECTORY / f"{fixture_id}.json"

    def _script(self, fixture_id: str) -> dict[str, Any]:
        return json.loads(
            self._fixture_path(fixture_id).read_text(
                encoding="utf-8"
            )
        )

    def _context(
        self,
        root: Path,
    ) -> IsolatedDomainContext:
        return IsolatedDomainContext(
            root=root,
            clock=(1,),
            ids=("run-test", "event-test"),
        )

    def _verify(
        self,
        fixture_id: str,
        root: Path,
    ):
        return DOMAIN_REGISTRY.verify(
            self._script(fixture_id)["domain_case"],
            fixture_id,
            self._context(root),
        )

    def test_composition_module_has_no_direct_os_access(
        self,
    ) -> None:
        source = Path(
            composed.__file__
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

        self.assertEqual(imported_roots & forbidden, set())

    def test_positive_pipeline_pairs_match(self) -> None:
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

    def test_faulty_gate_skip_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-COMPOSED-02-NEG-01",
                root,
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_verification["status"],
            "FAIL",
        )
        self.assertEqual(
            result.domain_expected["output"]["halt_stage"],
            "IDENTITY",
        )
        self.assertTrue(
            result.domain_actual["output"]["marker_attempted"]
        )
        self.assertEqual(
            result.domain_actual["output"]["marker_count"],
            1,
        )

    def test_real_sqlite_marker_only_exists_after_golden_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            golden_root = parent / "golden"
            golden_root.mkdir()
            golden_result = self._verify(
                "AC-COMPOSED-01",
                golden_root,
            )
            database_path = golden_root / "ac04-fence.sqlite"

            self.assertTrue(golden_result.passed)
            self.assertTrue(database_path.is_file())

            connection = sqlite3.connect(str(database_path))
            try:
                marker_rows = connection.execute(
                    """
                    SELECT authority_epoch, phase, contender_id
                    FROM authority_marker
                    ORDER BY authority_epoch
                    """
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(
                marker_rows,
                [
                    (
                        8,
                        "CUTOVER_DRAINING",
                        "compose-contender",
                    )
                ],
            )

            for fixture_id in (
                "AC-COMPOSED-02",
                "AC-COMPOSED-03",
                "AC-COMPOSED-04",
            ):
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    result = self._verify(fixture_id, root)
                    self.assertTrue(result.passed)
                    self.assertFalse(
                        (root / "ac04-fence.sqlite").exists()
                    )
                    self.assertFalse(
                        result.domain_actual["output"][
                            "marker_attempted"
                        ]
                    )
                    self.assertEqual(
                        result.domain_actual["output"][
                            "marker_count"
                        ],
                        0,
                    )

    def test_gate_order_and_halts_are_exact(self) -> None:
        expected = {
            "AC-COMPOSED-01": (
                "COMMITTED",
                None,
                [
                    "IDENTITY",
                    "DRAIN",
                    "CUSTODY",
                    "MARKER",
                ],
            ),
            "AC-COMPOSED-02": (
                "HALTED",
                "IDENTITY",
                ["IDENTITY"],
            ),
            "AC-COMPOSED-03": (
                "HALTED",
                "DRAIN",
                ["IDENTITY", "DRAIN"],
            ),
            "AC-COMPOSED-04": (
                "HALTED",
                "CUSTODY",
                ["IDENTITY", "DRAIN", "CUSTODY"],
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id, expected_output in expected.items():
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(fixture_id, root)
                    output = result.domain_expected["output"]
                    self.assertEqual(
                        (
                            output["pipeline_decision"],
                            output["halt_stage"],
                            output["executed_stages"],
                        ),
                        expected_output,
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
                        record_path.read_text(encoding="utf-8")
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

                    for key in (
                        "domain_input",
                        "domain_actual",
                        "domain_expected",
                        "domain_verification",
                    ):
                        artifact = (
                            root / record["artifact_paths"][key]
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