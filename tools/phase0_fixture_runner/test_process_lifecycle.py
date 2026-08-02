from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from domain import DOMAIN_REGISTRY, IsolatedDomainContext
from domain import process_lifecycle
from runner import run_fixture


class DomainProcessLifecycleTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent / "fixtures"
    )
    POSITIVE_IDS = ("DT-01", "DT-06")
    NEGATIVE_IDS = ("DT-01-NEG-01", "DT-06-NEG-01")

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
            process_lifecycle.__file__
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_imports = {
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

        self.assertEqual(imported_roots & forbidden_imports, set())

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

    def test_expected_outcomes_are_exact(self) -> None:
        expected = {
            "DT-01": ("STARTED", "SUCCEEDED", 0),
            "DT-06": ("STARTED", "FAILED", 1),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id, values in expected.items():
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    output = self._verify(
                        fixture_id, root
                    ).domain_expected["output"]
                    self.assertEqual(
                        output["terminal_classification"],
                        "EXITED",
                    )
                    self.assertEqual(
                        (
                            output["effect_certainty"],
                            output["execution_outcome"],
                            output["cleanup_error_count"],
                        ),
                        values,
                    )

    def test_cleanup_error_never_overwrites_primary_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "DT-06", root
            ).domain_expected["output"]

        self.assertEqual(output["terminal_classification"], "EXITED")
        self.assertEqual(output["execution_outcome"], "FAILED")
        self.assertEqual(output["cleanup_error_count"], 1)

    def test_each_specific_fault_is_detected(self) -> None:
        expected = {
            "DT-01-NEG-01": (
                "effect_certainty",
                "MAY_HAVE_STARTED",
                "STARTED",
            ),
            "DT-06-NEG-01": (
                "execution_outcome",
                "SUCCEEDED",
                "FAILED",
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id, values in expected.items():
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(fixture_id, root)
                    actual = result.domain_actual["output"]
                    oracle = result.domain_expected["output"]

                    self.assertFalse(result.passed)
                    self.assertEqual(
                        result.domain_verification["status"],
                        "FAIL",
                    )
                    self.assertEqual(actual[values[0]], values[1])
                    self.assertEqual(oracle[values[0]], values[2])

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
