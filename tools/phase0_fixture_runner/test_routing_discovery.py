from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.routing_discovery as routing_discovery
from domain import DOMAIN_REGISTRY, IsolatedDomainContext
from runner import run_fixture


class RoutingDiscoveryDomainTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent / "fixtures"
    )
    POSITIVE_IDS = ("RT-01", "RT-02", "RT-03")
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
            routing_discovery.__file__
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
                    routing_discovery.validate_routing_discovery_inputs(
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
            "RT-01": ("CANDIDATE", "CANDIDATE_SELECTED"),
            "RT-02": ("CANDIDATE", "CANDIDATE_EXCLUDED"),
            "RT-03": ("OBS", "USAGE_ABSENT"),
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
                        (output["rule_tier"], output["decision"]),
                        values,
                    )

    def test_candidate_audit_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            details = self._verify(
                "RT-01",
                Path(temporary),
            ).domain_expected["output"]["details"]

        self.assertEqual(details["candidate_id"], "cx")
        self.assertEqual(
            details["audit_record"],
            {
                "candidate_id": "cx",
                "score": 12,
                "status": "GREEN",
                "tier": "mid",
                "capability_match": True,
            },
        )

    def test_exclusion_is_reasoned_and_usage_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            exclusion_root = parent / "exclusion"
            exclusion_root.mkdir()
            exclusion = self._verify(
                "RT-02",
                exclusion_root,
            ).domain_expected["output"]["details"]

            usage_root = parent / "usage"
            usage_root.mkdir()
            usage = self._verify(
                "RT-03",
                usage_root,
            ).domain_expected["output"]["details"]

        self.assertFalse(exclusion["capability_match"])
        self.assertEqual(
            exclusion["exclusion_reason"],
            "CAPABILITY_UNSUPPORTED",
        )
        self.assertEqual(usage["peer_id"], "ag")
        self.assertEqual(
            usage["usage_disposition"],
            "ABSENT",
        )
        self.assertIsNone(usage["usage_value"])

    def test_each_specific_fault_is_detected(self) -> None:
        expected = {
            "RT-01-NEG-01": (
                "audit_record",
                None,
                {
                    "candidate_id": "cx",
                    "score": 12,
                    "status": "GREEN",
                    "tier": "mid",
                    "capability_match": True,
                },
            ),
            "RT-02-NEG-01": (
                "exclusion_reason",
                None,
                "CAPABILITY_UNSUPPORTED",
            ),
            "RT-03-NEG-01": (
                "usage_value",
                0,
                None,
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
                        actual["details"][values[0]],
                        values[1],
                    )
                    self.assertEqual(
                        oracle["details"][values[0]],
                        values[2],
                    )

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
