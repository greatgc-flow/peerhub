from __future__ import annotations

import ast
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.cli_envelope as cli_envelope
from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from runner import run_fixture


class CliEnvelopeDomainTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "CJ-01",
        "CJ-03",
        "CJ-04",
        "CJ-06",
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

    def test_module_has_no_real_os_access(
        self,
    ) -> None:
        source = Path(
            cli_envelope.__file__
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
                raw_inputs = self._load_script(
                    fixture_id
                )["domain_case"]["inputs"]
                self.assertEqual(
                    (
                        cli_envelope
                        .validate_cli_envelope_inputs(
                            fixture_id,
                            raw_inputs,
                        )
                    ),
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

    def test_cj01_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "CJ-01",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "status": "OK",
                "actor_identity": "actor-CJ-01",
                "workspace_scope": "workspace-CJ-01",
                "result": {
                    "count": 1,
                    "peers": [
                        {
                            "peer_id": "ag",
                            "status": "GREEN",
                        }
                    ],
                },
                "zero_state_mutations": True,
                "zero_receipt_writes": True,
            },
        )
        domain_inputs = self._load_script(
            "CJ-01"
        )["domain_case"]["inputs"]
        self.assertNotIn(
            "client_request_key",
            domain_inputs["envelope"],
        )
        self.assertNotIn(
            "idempotency_key",
            domain_inputs["envelope"],
        )

    def test_cj01_negative_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "CJ-01-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "status": "OK",
                "actor_identity": "actor-CJ-01",
                "workspace_scope": "workspace-CJ-01",
                "result": {
                    "count": 1,
                    "peers": [
                        {
                            "peer_id": "ag",
                            "status": "GREEN",
                        }
                    ],
                },
                "zero_state_mutations": True,
                "zero_receipt_writes": False,
            },
        )

    def test_cj03_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "CJ-03",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "status": "REJECTED",
                "error_code": "MALFORMED_ENVELOPE",
                "exit_code": 2,
                "effect_certainty": "NOT_STARTED",
                "zero_state_mutations": True,
                "zero_receipt_writes": True,
                "zero_outbox_writes": True,
                "zero_dispatch_calls": True,
            },
        )

    def test_cj03_negative_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "CJ-03-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "status": "REJECTED",
                "error_code": "MALFORMED_ENVELOPE",
                "exit_code": 2,
                "effect_certainty": "NOT_STARTED",
                "zero_state_mutations": False,
                "zero_receipt_writes": True,
                "zero_outbox_writes": True,
                "zero_dispatch_calls": True,
            },
        )

    def test_cj04_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "CJ-04",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "status": "REJECTED",
                "error_code": (
                    "PROTOCOL_VERSION_MISMATCH"
                ),
                "exit_code": 2,
                "effect_certainty": "NOT_STARTED",
                "supported_protocol_majors": [1],
                "supported_schema_names": [
                    "peerhub.request.v1"
                ],
                "zero_state_mutations": True,
                "zero_dispatch_calls": True,
            },
        )

    def test_cj04_negative_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "CJ-04-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "status": "REJECTED",
                "error_code": (
                    "PROTOCOL_VERSION_MISMATCH"
                ),
                "exit_code": 2,
                "effect_certainty": "NOT_STARTED",
                "supported_protocol_majors": [],
                "supported_schema_names": [],
                "zero_state_mutations": True,
                "zero_dispatch_calls": True,
            },
        )

    def test_cj04_schema_only_mismatch_uses_frozen_code(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            domain_case = copy.deepcopy(
                self._load_script("CJ-04")[
                    "domain_case"
                ]
            )
            domain_case["inputs"][
                "protocol_major"
            ] = 1
            domain_case["inputs"][
                "schema_name"
            ] = "peerhub.request.v2"

            result = DOMAIN_REGISTRY.verify(
                domain_case,
                "CJ-04",
                self._context(Path(temporary)),
            )

        self.assertTrue(result.passed)
        self.assertEqual(
            result.domain_expected["output"][
                "error_code"
            ],
            "SCHEMA_VERSION_UNSUPPORTED",
        )
        self.assertEqual(
            result.domain_expected["output"][
                "exit_code"
            ],
            2,
        )

    def test_cj06_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "CJ-06",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            cli_envelope._EXIT_CODE_BY_ERROR_CODE,
            {
                "MALFORMED_ENVELOPE": 2,
                "PROTOCOL_VERSION_MISMATCH": 2,
                "SCHEMA_VERSION_UNSUPPORTED": 2,
                "CONFIGURATION_STALE": 4,
                "POLICY_STALE": 4,
                "ACTOR_UNAUTHORIZED": 3,
            },
        )
        self.assertEqual(
            output,
            {
                "status": "REJECTED",
                "error_code": (
                    "CONFIGURATION_STALE"
                ),
                "exit_code": 4,
                "message": (
                    "request rejected: "
                    "CONFIGURATION_STALE"
                ),
                "zero_state_mutations": True,
                "zero_receipt_writes": True,
                "zero_outbox_writes": True,
                "zero_dispatch_calls": True,
            },
        )
        self.assertNotIn(
            "workspace-secret-CJ-06",
            output["message"],
        )

    def test_cj06_negative_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "CJ-06-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "status": "REJECTED",
                "error_code": (
                    "CONFIGURATION_STALE"
                ),
                "exit_code": 4,
                "message": (
                    "request rejected: "
                    "CONFIGURATION_STALE: "
                    "workspace-secret-CJ-06"
                ),
                "zero_state_mutations": True,
                "zero_receipt_writes": True,
                "zero_outbox_writes": True,
                "zero_dispatch_calls": True,
            },
        )

    def test_each_specific_fault_is_detected(
        self,
    ) -> None:
        expected_changed_fields = {
            "CJ-01-NEG-01": "zero_receipt_writes",
            "CJ-03-NEG-01": "zero_state_mutations",
            "CJ-04-NEG-01": (
                "supported_protocol_majors"
            ),
            "CJ-06-NEG-01": "message",
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, field in (
                expected_changed_fields.items()
            ):
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
                        actual[field],
                        expected[field],
                    )

                    if fixture_id == "CJ-04-NEG-01":
                        self.assertNotEqual(
                            actual[
                                "supported_schema_names"
                            ],
                            expected[
                                "supported_schema_names"
                            ],
                        )
                    if fixture_id == "CJ-06-NEG-01":
                        self.assertIn(
                            "workspace-secret-CJ-06",
                            actual["message"],
                        )
                        self.assertNotIn(
                            "workspace-secret-CJ-06",
                            expected["message"],
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


if __name__ == "__main__":
    unittest.main()
