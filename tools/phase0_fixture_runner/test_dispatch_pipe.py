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
from domain import dispatch_pipe
from runner import run_fixture


class DomainDispatchPipeTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent / "fixtures"
    )
    POSITIVE_IDS = (
        "DP-01",
        "DP-02",
        "DP-03",
        "DP-04",
        "DP-05",
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )

    def _fixture_path(
        self,
        fixture_id: str,
    ) -> Path:
        return (
            self.FIXTURE_DIRECTORY
            / f"{fixture_id}.json"
        )

    def _script(
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
            ids=("run-test", "event-test"),
        )

    def _verify(
        self,
        fixture_id: str,
        root: Path,
    ):
        return DOMAIN_REGISTRY.verify(
            self._script(
                fixture_id
            )["domain_case"],
            fixture_id,
            self._context(root),
        )

    def test_module_has_no_real_os_access(
        self,
    ) -> None:
        source = Path(
            dispatch_pipe.__file__
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

    def test_fixture_inputs_satisfy_closed_vectors(
        self,
    ) -> None:
        for fixture_id in (
            self.POSITIVE_IDS
            + self.NEGATIVE_IDS
        ):
            with self.subTest(
                fixture_id=fixture_id
            ):
                raw_inputs = self._script(
                    fixture_id
                )["domain_case"]["inputs"]
                validated = (
                    dispatch_pipe
                    .validate_dispatch_pipe_inputs(
                        fixture_id,
                        raw_inputs,
                    )
                )
                self.assertEqual(
                    validated,
                    raw_inputs,
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

    def test_expected_classifications_are_exact(
        self,
    ) -> None:
        expected = {
            "DP-01": (
                "CANDIDATE",
                "EXITED",
                "TERMINAL_RESULT_DELIVERED",
                True,
            ),
            "DP-02": (
                "OBS",
                "PRE_SPAWN_REJECTED",
                "NOT_STARTED",
                False,
            ),
            "DP-03": (
                "OBS",
                "EXITED",
                "EXECUTION_UNCERTAIN",
                False,
            ),
            "DP-04": (
                "CANDIDATE",
                "OUTPUT_CAP_EXCEEDED",
                "OUTPUT_BOUNDED_STOP",
                False,
            ),
            "DP-05": (
                "OBS",
                "PROCESS_DEADLINE",
                "PROCESS_TIMEOUT",
                False,
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, values in expected.items():
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
                            output["rule_tier"],
                            output["terminal_category"],
                            output["execution_disposition"],
                            output["verified_task_success"],
                        ),
                        values,
                    )

    def test_dp01_records_three_distinct_layers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "DP-01",
                root,
            ).domain_expected["output"]

        self.assertTrue(
            output["layers_separately_recorded"]
        )
        self.assertTrue(
            output["process_evidence"]["recorded"]
        )
        self.assertEqual(
            output["process_evidence"][
                "process_creation_count"
            ],
            1,
        )
        self.assertTrue(
            output["protocol_evidence"]["recorded"]
        )
        self.assertEqual(
            output["protocol_evidence"]["chunk_count"],
            8,
        )
        self.assertEqual(
            output["protocol_evidence"]["byte_count"],
            8,
        )
        self.assertTrue(
            output["protocol_evidence"][
                "all_frames_complete"
            ]
        )
        self.assertTrue(
            output["completion_evidence"]["recorded"]
        )
        self.assertEqual(
            output["completion_evidence"]["exit_code"],
            0,
        )

    def test_dp02_is_unambiguously_not_started(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "DP-02",
                root,
            ).domain_expected["output"]

        self.assertEqual(
            output["effect_certainty"],
            "NOT_STARTED",
        )
        self.assertEqual(
            output["execution_disposition"],
            "NOT_STARTED",
        )
        self.assertFalse(
            output["process_evidence"]["spawn_observed"]
        )
        self.assertEqual(
            output["process_evidence"][
                "process_creation_count"
            ],
            0,
        )
        self.assertIsNone(
            output["process_evidence"]["identity_token"]
        )
        self.assertFalse(
            output["completion_evidence"][
                "exit_observed"
            ]
        )

    def test_dp03_preserves_nonzero_exit_without_success(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "DP-03",
                root,
            ).domain_expected["output"]

        self.assertEqual(
            output["effect_certainty"],
            "STARTED",
        )
        self.assertEqual(
            output["execution_disposition"],
            "EXECUTION_UNCERTAIN",
        )
        self.assertFalse(
            output["verified_task_success"]
        )
        self.assertTrue(
            output["completion_evidence"][
                "exit_observed"
            ]
        )
        self.assertEqual(
            output["completion_evidence"]["exit_code"],
            1,
        )

    def test_dp04_output_cap_is_distinct_terminal_category(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "DP-04",
                root,
            ).domain_expected["output"]

        protocol = output["protocol_evidence"]
        self.assertEqual(
            output["terminal_category"],
            "OUTPUT_CAP_EXCEEDED",
        )
        self.assertEqual(
            protocol["output_limit_bytes"],
            8,
        )
        self.assertEqual(
            protocol["byte_count"],
            10,
        )
        self.assertTrue(
            protocol["output_cap_exceeded"]
        )
        self.assertFalse(
            output["completion_evidence"][
                "deadline_reached"
            ]
        )
        self.assertTrue(
            output["process_evidence"]["stop_requested"]
        )
        self.assertTrue(
            output["process_evidence"][
                "tree_kill_observed"
            ]
        )

    def test_exit_cap_and_deadline_are_three_categories(
        self,
    ) -> None:
        categories: list[str] = []

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id in (
                "DP-01",
                "DP-04",
                "DP-05",
            ):
                root = parent / fixture_id
                root.mkdir()
                categories.append(
                    self._verify(
                        fixture_id,
                        root,
                    ).domain_expected[
                        "output"
                    ]["terminal_category"]
                )

        self.assertEqual(
            categories,
            [
                "EXITED",
                "OUTPUT_CAP_EXCEEDED",
                "PROCESS_DEADLINE",
            ],
        )
        self.assertEqual(
            len(categories),
            len(set(categories)),
        )

    def test_dp05_preserves_deadline_kill_and_exit_15(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "DP-05",
                root,
            ).domain_expected["output"]

        self.assertEqual(
            output["terminal_category"],
            "PROCESS_DEADLINE",
        )
        self.assertTrue(
            output["completion_evidence"][
                "deadline_reached"
            ]
        )
        self.assertTrue(
            output["process_evidence"]["stop_requested"]
        )
        self.assertTrue(
            output["process_evidence"][
                "tree_kill_observed"
            ]
        )
        self.assertTrue(
            output["completion_evidence"][
                "exit_observed"
            ]
        )
        self.assertEqual(
            output["completion_evidence"]["exit_code"],
            15,
        )

    def test_each_specific_fault_is_detected(
        self,
    ) -> None:
        expected = {
            "DP-01-NEG-01": (
                "layers_separately_recorded",
                False,
                True,
            ),
            "DP-02-NEG-01": (
                "effect_certainty",
                "MAY_HAVE_STARTED",
                "NOT_STARTED",
            ),
            "DP-03-NEG-01": (
                "verified_task_success",
                True,
                False,
            ),
            "DP-04-NEG-01": (
                "terminal_category",
                "PROCESS_DEADLINE",
                "OUTPUT_CAP_EXCEEDED",
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, values in expected.items():
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(
                        fixture_id,
                        root,
                    )
                    actual = result.domain_actual["output"]
                    oracle = result.domain_expected["output"]

                    self.assertFalse(result.passed)
                    self.assertEqual(
                        actual[values[0]],
                        values[1],
                    )
                    self.assertEqual(
                        oracle[values[0]],
                        values[2],
                    )

            fixture_id = "DP-05-NEG-01"
            root = parent / fixture_id
            root.mkdir()
            result = self._verify(fixture_id, root)
            self.assertFalse(result.passed)
            self.assertFalse(
                result.domain_actual["output"][
                    "completion_evidence"
                ]["exit_observed"]
            )
            self.assertTrue(
                result.domain_expected["output"][
                    "completion_evidence"
                ]["exit_observed"]
            )
            self.assertIsNone(
                result.domain_actual["output"][
                    "completion_evidence"
                ]["exit_code"]
            )
            self.assertEqual(
                result.domain_expected["output"][
                    "completion_evidence"
                ]["exit_code"],
                15,
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
                        self._fixture_path(fixture_id),
                        fixture_id,
                        root,
                    )
                    record = json.loads(
                        record_path.read_text(
                            encoding="utf-8"
                        )
                    )
                    positive = (
                        fixture_id in self.POSITIVE_IDS
                    )

                    self.assertEqual(
                        record["status"],
                        (
                            "V1_CAPTURE"
                            if positive
                            else "DOMAIN_ASSERTION_FAILED"
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


if __name__ == "__main__":
    unittest.main()
