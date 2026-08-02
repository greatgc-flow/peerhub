from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.authority_shadow as shadow
from domain import DOMAIN_REGISTRY, IsolatedDomainContext
from runner import run_fixture


class DomainAuthorityShadowTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent / "fixtures"
    )
    POSITIVE_IDS = (
        "AC-03-01",
        "AC-03-02",
        "AC-03-03",
        "AC-03-04",
    )
    NEGATIVE_IDS = (
        "AC-03-01-NEG-01",
        "AC-03-02-NEG-01",
        "AC-03-03-NEG-01",
        "AC-03-04-NEG-01",
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

    def test_shadow_module_has_no_direct_os_access(self) -> None:
        source = Path(shadow.__file__).read_text(encoding="utf-8")
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

    def test_positive_pairs_match(self) -> None:
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

    def test_streak_semantics_are_exact(self) -> None:
        expected = {
            "AC-03-01": (
                4,
                [
                    "SAME_REVISION_EQUIVALENT",
                    "SAME_REVISION_EQUIVALENT",
                ],
            ),
            "AC-03-02": (
                0,
                ["SAME_REVISION_VALUE_DRIFT"],
            ),
            "AC-03-03": (
                1,
                ["REVISION_CHANGED_RESET"],
            ),
            "AC-03-04": (
                0,
                [
                    "SAME_REVISION_EQUIVALENT",
                    "SAME_REVISION_VALUE_DRIFT",
                ],
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id, expected_values in expected.items():
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    output = self._verify(
                        fixture_id,
                        root,
                    ).domain_expected["output"]
                    self.assertEqual(
                        output[
                            "final_consecutive_equivalence_streak"
                        ],
                        expected_values[0],
                    )
                    self.assertEqual(
                        [
                            row["disposition"]
                            for row in output["comparison_rows"]
                        ],
                        expected_values[1],
                    )

    def test_revision_reset_is_not_value_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "AC-03-03",
                root,
            ).domain_expected["output"]

        row = output["comparison_rows"][0]
        self.assertTrue(row["values_equivalent"])
        self.assertTrue(row["revision_changed"])
        self.assertEqual(row["disposition"], "REVISION_CHANGED_RESET")
        self.assertEqual(
            row["consecutive_equivalence_streak"],
            1,
        )

    def test_every_positive_fixture_proves_no_effect(self) -> None:
        counters = (
            "legacy_writes",
            "peerhub_operational_state_mutations",
            "provider_calls",
        )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(fixture_id, root)
                    for output in (
                        result.domain_actual["output"],
                        result.domain_expected["output"],
                    ):
                        for counter in counters:
                            self.assertEqual(output[counter], 0)

    def test_each_specific_fault_is_detected(self) -> None:
        expectations = {
            "AC-03-01-NEG-01": (
                "final_consecutive_equivalence_streak",
                1,
                4,
            ),
            "AC-03-02-NEG-01": (
                "final_consecutive_equivalence_streak",
                4,
                0,
            ),
            "AC-03-03-NEG-01": (
                "final_consecutive_equivalence_streak",
                4,
                1,
            ),
            "AC-03-04-NEG-01": (
                "peerhub_operational_state_mutations",
                1,
                0,
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id, expected in expectations.items():
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
                    self.assertEqual(actual[expected[0]], expected[1])
                    self.assertEqual(oracle[expected[0]], expected[2])

    def test_runner_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id in self.POSITIVE_IDS + self.NEGATIVE_IDS:
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
                        artifact = root / record["artifact_paths"][key]
                        self.assertTrue(artifact.is_file())
                        self.assertEqual(
                            hashlib.sha256(
                                artifact.read_bytes()
                            ).hexdigest(),
                            record["digests"][f"{key}_raw_sha256"],
                        )


if __name__ == "__main__":
    unittest.main()
