from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.dispatch_pipe_recovery as dispatch_pipe_recovery
from domain import DOMAIN_REGISTRY, IsolatedDomainContext
from runner import run_fixture


class DispatchPipeRecoveryDomainTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent / "fixtures"
    )
    POSITIVE_IDS = ("DP-06",)
    NEGATIVE_IDS = ("DP-06-NEG-01",)

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
            dispatch_pipe_recovery.__file__
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
                inputs = self._script(fixture_id)["domain_case"][
                    "inputs"
                ]
                self.assertEqual(
                    (
                        dispatch_pipe_recovery
                        .validate_dispatch_pipe_recovery_inputs(
                            fixture_id,
                            inputs,
                        )
                    ),
                    inputs,
                )

    def test_positive_oracle_adapter_pairs_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "DP-06",
                Path(temporary),
            )

        self.assertTrue(result.passed)
        self.assertEqual(
            result.domain_verification["status"],
            "PASS",
        )

    def test_dp06_positive_exact_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "DP-06",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "terminal_classification": "START_UNCERTAIN",
                "effect_certainty": "MAY_HAVE_STARTED",
                "execution_outcome": "UNKNOWN",
                "automatically_replayed": False,
            },
        )

    def test_dp06_negative_exact_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "DP-06-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_expected["output"],
            {
                "terminal_classification": "START_UNCERTAIN",
                "effect_certainty": "MAY_HAVE_STARTED",
                "execution_outcome": "UNKNOWN",
                "automatically_replayed": False,
            },
        )
        self.assertEqual(
            result.domain_actual["output"],
            {
                "terminal_classification": "NOT_STARTED",
                "effect_certainty": "NOT_STARTED",
                "execution_outcome": "NOT_STARTED",
                "automatically_replayed": False,
            },
        )

    def test_each_specific_fault_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "DP-06-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_verification["status"],
            "FAIL",
        )
        self.assertEqual(
            result.domain_actual["output"][
                "terminal_classification"
            ],
            "NOT_STARTED",
        )
        self.assertEqual(
            result.domain_expected["output"][
                "terminal_classification"
            ],
            "START_UNCERTAIN",
        )
        self.assertFalse(
            result.domain_actual["output"][
                "automatically_replayed"
            ]
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
