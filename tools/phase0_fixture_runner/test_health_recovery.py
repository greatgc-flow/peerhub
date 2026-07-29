from __future__ import annotations

import copy
import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.health_recovery as health_recovery
from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from runner import run_fixture


class HealthRecoveryDomainTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "HR-01",
        "HR-02",
        "HR-03",
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

    def _load_script(
        self,
        fixture_id: str,
    ) -> dict[str, Any]:
        return json.loads(
            self._fixture_path(
                fixture_id
            ).read_text(encoding="utf-8")
        )

    def _inputs(
        self,
        fixture_id: str,
    ) -> dict[str, Any]:
        return self._load_script(
            fixture_id
        )["domain_case"]["inputs"]

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

    def _expected_hr03_results(
        self,
    ) -> list[dict[str, Any]]:
        return [
            {
                "scenario_id": (
                    "executable-unavailable"
                ),
                "classification": (
                    "EXECUTABLE_UNAVAILABLE"
                ),
                "admission": "REJECTED",
                "attempted_trace": [
                    {
                        "stage": (
                            "resolve_executable"
                        ),
                        "outcome": "FAILED",
                    }
                ],
                "forbidden_downstream_stages": [
                    "validate_environment",
                    "authenticate",
                    "connect_network",
                    "call_provider",
                    "check_usage_admission",
                ],
                "forbidden_stages_present": [],
            },
            {
                "scenario_id": (
                    "environment-unavailable"
                ),
                "classification": (
                    "ENVIRONMENT_UNAVAILABLE"
                ),
                "admission": "REJECTED",
                "attempted_trace": [
                    {
                        "stage": (
                            "resolve_executable"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "validate_environment"
                        ),
                        "outcome": "FAILED",
                    },
                ],
                "forbidden_downstream_stages": [
                    "authenticate",
                    "connect_network",
                    "call_provider",
                    "check_usage_admission",
                ],
                "forbidden_stages_present": [],
            },
            {
                "scenario_id": "auth-unavailable",
                "classification": "AUTH_UNAVAILABLE",
                "admission": "REJECTED",
                "attempted_trace": [
                    {
                        "stage": (
                            "resolve_executable"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "validate_environment"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": "authenticate",
                        "outcome": "FAILED",
                    },
                ],
                "forbidden_downstream_stages": [
                    "connect_network",
                    "call_provider",
                    "check_usage_admission",
                ],
                "forbidden_stages_present": [],
            },
            {
                "scenario_id": (
                    "network-unavailable"
                ),
                "classification": (
                    "NETWORK_UNAVAILABLE"
                ),
                "admission": "REJECTED",
                "attempted_trace": [
                    {
                        "stage": (
                            "resolve_executable"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "validate_environment"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": "authenticate",
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "connect_network"
                        ),
                        "outcome": "FAILED",
                    },
                ],
                "forbidden_downstream_stages": [
                    "call_provider",
                    "check_usage_admission",
                ],
                "forbidden_stages_present": [],
            },
            {
                "scenario_id": (
                    "provider-unavailable"
                ),
                "classification": (
                    "PROVIDER_UNAVAILABLE"
                ),
                "admission": "REJECTED",
                "attempted_trace": [
                    {
                        "stage": (
                            "resolve_executable"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "validate_environment"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": "authenticate",
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "connect_network"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": "call_provider",
                        "outcome": "FAILED",
                    },
                ],
                "forbidden_downstream_stages": [
                    "check_usage_admission"
                ],
                "forbidden_stages_present": [],
            },
            {
                "scenario_id": "quota-exhausted",
                "classification": "QUOTA_EXHAUSTED",
                "admission": "REJECTED",
                "attempted_trace": [
                    {
                        "stage": (
                            "resolve_executable"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "validate_environment"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": "authenticate",
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "connect_network"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": "call_provider",
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "check_usage_admission"
                        ),
                        "outcome": "FAILED",
                    },
                ],
                "forbidden_downstream_stages": [],
                "forbidden_stages_present": [],
            },
            {
                "scenario_id": "rate-limited",
                "classification": "RATE_LIMITED",
                "admission": "REJECTED",
                "attempted_trace": [
                    {
                        "stage": (
                            "resolve_executable"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "validate_environment"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": "authenticate",
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "connect_network"
                        ),
                        "outcome": "OK",
                    },
                    {
                        "stage": "call_provider",
                        "outcome": "OK",
                    },
                    {
                        "stage": (
                            "check_usage_admission"
                        ),
                        "outcome": "FAILED",
                    },
                ],
                "forbidden_downstream_stages": [],
                "forbidden_stages_present": [],
            },
            {
                "scenario_id": (
                    "legacy-operational-timeout"
                ),
                "failure_class": (
                    "operational_error:timeout"
                ),
                "health": "RED",
                "gate": "closed",
                "admission": "rejected",
            },
        ]

    def test_module_has_no_real_os_access(
        self,
    ) -> None:
        source = Path(
            health_recovery.__file__
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
                raw_inputs = self._inputs(
                    fixture_id
                )
                self.assertEqual(
                    (
                        health_recovery
                        .validate_health_recovery_inputs(
                            fixture_id,
                            raw_inputs,
                        )
                    ),
                    raw_inputs,
                )

        self.assertEqual(
            self._inputs("HR-03"),
            self._inputs("HR-03-NEG-01"),
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

    def test_hr01_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "HR-01",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "readiness_state": "READY",
                "gate_state": "OPEN",
                "admission": {
                    "decision": "ADMITTED",
                    "provider_effect_permitted": True,
                },
            },
        )

    def test_hr01_negative_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "HR-01-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_expected["output"],
            {
                "readiness_state": (
                    "PROBE_INCONCLUSIVE"
                ),
                "gate_state": "CLOSED",
                "admission": {
                    "decision": "REJECTED",
                },
            },
        )
        self.assertEqual(
            result.domain_actual["output"],
            {
                "readiness_state": "READY",
                "gate_state": "OPEN",
                "admission": {
                    "decision": "ADMITTED",
                    "provider_effect_permitted": True,
                },
            },
        )

    def test_hr02_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "HR-02",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "readiness_state": (
                    "READINESS_STALE"
                ),
                "gate_state": "CLOSED",
                "admission": {
                    "decision": "REJECTED",
                    "reason_code": (
                        "READINESS_STALE"
                    ),
                },
                "revalidation_action": (
                    "REVALIDATION_REQUIRED"
                ),
                "zero_dispatch_calls": True,
            },
        )

    def test_hr02_negative_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "HR-02-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "readiness_state": "READY",
                "gate_state": "OPEN",
                "admission": {
                    "decision": "ADMITTED",
                    "provider_effect_permitted": True,
                },
            },
        )

    def test_hr03_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "HR-03",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "scenario_results": (
                    self._expected_hr03_results()
                )
            },
        )

    def test_hr03_negative_exact_classification(
        self,
    ) -> None:
        expected_actual = (
            self._expected_hr03_results()
        )
        expected_actual[0][
            "attempted_trace"
        ].append(
            {
                "stage": "connect_network",
                "outcome": "OK",
            }
        )
        expected_actual[0][
            "forbidden_stages_present"
        ] = ["connect_network"]

        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "HR-03-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_expected["output"],
            {
                "scenario_results": (
                    self._expected_hr03_results()
                )
            },
        )
        self.assertEqual(
            result.domain_actual["output"],
            {
                "scenario_results": expected_actual
            },
        )

    def test_each_specific_fault_is_detected(
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
                    actual = result.domain_actual[
                        "output"
                    ]
                    expected = result.domain_expected[
                        "output"
                    ]

                    self.assertFalse(result.passed)
                    self.assertEqual(
                        result.domain_verification[
                            "status"
                        ],
                        "FAIL",
                    )
                    self.assertNotEqual(
                        actual,
                        expected,
                    )

                    if fixture_id == "HR-01-NEG-01":
                        self.assertEqual(
                            actual["readiness_state"],
                            "READY",
                        )
                        self.assertEqual(
                            expected["readiness_state"],
                            "PROBE_INCONCLUSIVE",
                        )
                    elif fixture_id == "HR-02-NEG-01":
                        self.assertEqual(
                            actual["gate_state"],
                            "OPEN",
                        )
                        self.assertEqual(
                            expected["gate_state"],
                            "CLOSED",
                        )
                    else:
                        actual_rows = actual[
                            "scenario_results"
                        ]
                        expected_rows = expected[
                            "scenario_results"
                        ]
                        self.assertNotEqual(
                            actual_rows[0][
                                "attempted_trace"
                            ],
                            expected_rows[0][
                                "attempted_trace"
                            ],
                        )
                        self.assertEqual(
                            actual_rows[0][
                                "forbidden_stages_present"
                            ],
                            ["connect_network"],
                        )
                        self.assertEqual(
                            actual_rows[1:],
                            expected_rows[1:],
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
                            record["coverage_scope"],
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

            run_fixture(
                self._fixture_path("HR-03"),
                "HR-03",
                first_root,
            )
            run_fixture(
                self._fixture_path("HR-03"),
                "HR-03",
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
                    (first_root / name).read_bytes(),
                    (second_root / name).read_bytes(),
                    name,
                )


if __name__ == "__main__":
    unittest.main()
