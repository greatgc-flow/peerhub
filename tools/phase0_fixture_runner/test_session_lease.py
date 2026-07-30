from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.session_lease as session_lease
from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from runner import run_fixture


class SessionLeaseDomainTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "SL-01",
        "SL-02",
        "SL-03",
        "SL-04",
        "SL-05",
        "SL-06",
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

    def test_module_has_no_real_os_access(
        self,
    ) -> None:
        source = Path(
            session_lease.__file__
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
                        session_lease
                        .validate_session_lease_inputs(
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

    def test_sl01_positive_exact_classification(
        self,
    ) -> None:
        inputs = self._inputs("SL-01")
        binding = inputs["binding"]

        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "SL-01",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "session": {
                    "session_id": "session-SL-01",
                    "binding": binding,
                    "binding_fingerprint": (
                        session_lease.fingerprint(
                            binding
                        )
                    ),
                    "lifecycle_state": "ACTIVE",
                    "revision": 1,
                },
                "lease": {
                    "lease_id": "lease-SL-01",
                    "session_id": "session-SL-01",
                    "lease_kind": (
                        "SESSION_MEMBERSHIP"
                    ),
                    "owner_peer_id": "ag",
                    "owner_principal_id": (
                        "principal-ag"
                    ),
                    "owner_instance_id": (
                        "instance-ag-01"
                    ),
                    "owner_process_birth_identity": (
                        "birth-ag-100"
                    ),
                    "fencing_token": 1,
                    "revision": 1,
                    "lifecycle_state": "ACTIVE",
                },
            },
        )

    def test_sl01_negative_exact_classification(
        self,
    ) -> None:
        inputs = self._inputs(
            "SL-01-NEG-01"
        )
        binding = inputs["binding"]

        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "SL-01-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "session": {
                    "session_id": "session-SL-01",
                    "binding": binding,
                    "binding_fingerprint": (
                        session_lease.fingerprint(
                            binding
                        )
                    ),
                    "lifecycle_state": "ACTIVE",
                    "revision": 1,
                },
                "lease": {
                    "lease_id": "session-SL-01",
                    "session_id": "session-SL-01",
                    "lease_kind": (
                        "SESSION_MEMBERSHIP"
                    ),
                    "owner_peer_id": "ag",
                    "owner_principal_id": (
                        "principal-ag"
                    ),
                    "owner_instance_id": (
                        "instance-ag-01"
                    ),
                    "owner_process_birth_identity": (
                        "birth-ag-100"
                    ),
                    "fencing_token": 1,
                    "revision": 1,
                    "lifecycle_state": "ACTIVE",
                },
            },
        )

    def test_sl02_positive_exact_classification(
        self,
    ) -> None:
        inputs = self._inputs("SL-02")
        stored_fingerprint = (
            session_lease.fingerprint(
                inputs["stored_binding"]
            )
        )
        resolved_fingerprint = (
            session_lease.fingerprint(
                inputs["resolved_binding"]
            )
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "SL-02",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "status": "COMPATIBLE_RESUME",
                "session_id": (
                    "session-SL-02-existing"
                ),
                "lease_id": (
                    "lease-SL-02-new-process"
                ),
                "owner_peer_id": "ag",
                "owner_principal_id": (
                    "principal-ag"
                ),
                "owner_instance_id": (
                    "instance-ag-02"
                ),
                "owner_process_birth_identity": (
                    "birth-ag-200"
                ),
                "fencing_token": 1,
                "lifecycle_state": "ACTIVE",
                "stored_fingerprint": (
                    stored_fingerprint
                ),
                "resolved_fingerprint": (
                    resolved_fingerprint
                ),
            },
        )
        self.assertEqual(
            stored_fingerprint,
            resolved_fingerprint,
        )

    def test_sl02_negative_exact_classification(
        self,
    ) -> None:
        inputs = self._inputs(
            "SL-02-NEG-01"
        )

        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "SL-02-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "status": "COMPATIBLE_RESUME",
                "session_id": (
                    "legacy-replacement-session-SL-02"
                ),
                "lease_id": (
                    "lease-SL-02-new-process"
                ),
                "owner_peer_id": "ag",
                "owner_principal_id": (
                    "principal-ag"
                ),
                "owner_instance_id": (
                    "instance-ag-02"
                ),
                "owner_process_birth_identity": (
                    "birth-ag-200"
                ),
                "fencing_token": 1,
                "lifecycle_state": "ACTIVE",
                "stored_fingerprint": (
                    session_lease.fingerprint(
                        inputs["stored_binding"]
                    )
                ),
                "resolved_fingerprint": (
                    session_lease.fingerprint(
                        inputs["resolved_binding"]
                    )
                ),
            },
        )

    def test_sl03_positive_exact_classification(
        self,
    ) -> None:
        inputs = self._inputs("SL-03")

        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "SL-03",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "status": "REJECTED",
                "code": (
                    "SESSION_BINDING_MISMATCH"
                ),
                "prior_session_id": (
                    "session-SL-03-existing"
                ),
                "mismatched_fields": [
                    "profile_revision"
                ],
                "stored_fingerprint": (
                    session_lease.fingerprint(
                        inputs["stored_binding"]
                    )
                ),
                "resolved_fingerprint": (
                    session_lease.fingerprint(
                        inputs["resolved_binding"]
                    )
                ),
                "disposition": "REJECTED",
                "zero_state_mutations": True,
            },
        )

    def test_sl03_negative_exact_classification(
        self,
    ) -> None:
        inputs = self._inputs(
            "SL-03-NEG-01"
        )

        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "SL-03-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "status": "REJECTED",
                "code": (
                    "SESSION_BINDING_MISMATCH"
                ),
                "prior_session_id": (
                    "session-SL-03-existing"
                ),
                "mismatched_fields": [
                    "profile_revision"
                ],
                "stored_fingerprint": (
                    session_lease.fingerprint(
                        inputs["stored_binding"]
                    )
                ),
                "resolved_fingerprint": (
                    session_lease.fingerprint(
                        inputs["resolved_binding"]
                    )
                ),
                "disposition": "REJECTED",
                "zero_state_mutations": False,
            },
        )

    def test_sl04_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "SL-04",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "renew_result": {
                    "renewed_lease": {
                        "lease_id": "lease-SL-04-A",
                        "owner_peer_id": "ag",
                        "owner_principal_id": (
                            "principal-ag"
                        ),
                        "owner_instance_id": (
                            "instance-ag-A"
                        ),
                        "owner_process_birth_identity": (
                            "birth-ag-A"
                        ),
                        "fencing_token": 11,
                        "revision": 5,
                        "lifecycle_state": "ACTIVE",
                    },
                    "unaffected_lease_b": {
                        "lease_id": "lease-SL-04-B",
                        "owner_peer_id": "ag",
                        "owner_principal_id": (
                            "principal-ag"
                        ),
                        "owner_instance_id": (
                            "instance-ag-B"
                        ),
                        "owner_process_birth_identity": (
                            "birth-ag-B"
                        ),
                        "fencing_token": 20,
                        "revision": 8,
                        "lifecycle_state": "ACTIVE",
                    },
                },
                "close_result": {
                    "closed_lease_b": {
                        "lease_id": "lease-SL-04-B",
                        "owner_peer_id": "ag",
                        "owner_principal_id": (
                            "principal-ag"
                        ),
                        "owner_instance_id": (
                            "instance-ag-B"
                        ),
                        "owner_process_birth_identity": (
                            "birth-ag-B"
                        ),
                        "fencing_token": 21,
                        "revision": 9,
                        "lifecycle_state": "CLOSED",
                    },
                    "unaffected_lease_a": {
                        "lease_id": "lease-SL-04-A",
                        "owner_peer_id": "ag",
                        "owner_principal_id": (
                            "principal-ag"
                        ),
                        "owner_instance_id": (
                            "instance-ag-A"
                        ),
                        "owner_process_birth_identity": (
                            "birth-ag-A"
                        ),
                        "fencing_token": 11,
                        "revision": 5,
                        "lifecycle_state": "ACTIVE",
                    },
                },
                "stale_cas_rejection": {
                    "status": "REJECTED",
                    "code": "LEASE_FENCE_VIOLATION",
                    "lease_id": "lease-SL-04-A",
                    "fencing_token": 11,
                    "revision": 5,
                    "zero_state_mutations": True,
                },
            },
        )

    def test_sl04_negative_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "SL-04-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        actual = result.domain_actual["output"]
        expected = result.domain_expected["output"]

        self.assertEqual(
            actual["renew_result"],
            expected["renew_result"],
        )
        self.assertEqual(
            actual["close_result"],
            expected["close_result"],
        )
        self.assertEqual(
            actual["stale_cas_rejection"],
            {
                "status": "ACCEPTED",
                "code": "LEASE_FENCE_VIOLATION",
                "lease_id": "lease-SL-04-A",
                "fencing_token": 12,
                "revision": 6,
                "zero_state_mutations": False,
            },
        )

    def test_sl05_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "SL-05",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "status": "REJECTED",
                "code": "LEASE_FENCE_VIOLATION",
                "lease_id": "lease-SL-05",
                "fencing_token": 31,
                "revision": 12,
                "zero_state_mutations": True,
            },
        )

    def test_sl05_negative_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "SL-05-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "status": "ACCEPTED",
                "code": "LEASE_FENCE_VIOLATION",
                "lease_id": "lease-SL-05",
                "fencing_token": 32,
                "revision": 13,
                "zero_state_mutations": False,
            },
        )

    def test_sl06_positive_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "SL-06",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output,
            {
                "receipt": {
                    "recovery_receipt_id": (
                        "receipt-SL-06"
                    ),
                    "session_id": "session-SL-06",
                    "lease_id": "lease-SL-06",
                    "detected_at": 100,
                    "recovery_actor_principal_id": (
                        "principal-recovery"
                    ),
                    "trigger": (
                        "PROCESS_BIRTH_MISMATCH"
                    ),
                    "mismatch_dimensions": [
                        "owner_process_birth_identity"
                    ],
                    "evidence_digest": (
                        "sha256:evidence-sl06-0001"
                    ),
                    "policy_id": (
                        "session-lease-recovery-v1"
                    ),
                    "policy_revision": 1,
                    "decision": "FENCE_AND_CLOSE",
                    "certainty_before_policy": (
                        "PRIOR_HOLDER_UNVERIFIED"
                    ),
                    "certainty_after_policy": (
                        "FENCED_FOR_FUTURE_WRITES"
                    ),
                    "external_effect_certainty": None,
                    "pre_lifecycle_state": "ACTIVE",
                    "pre_revision": 8,
                    "pre_fencing_token": 40,
                    "post_lifecycle_state": "CLOSED",
                    "post_revision": 9,
                    "post_fencing_token": 41,
                }
            },
        )

    def test_sl06_negative_exact_classification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "SL-06-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        self.assertEqual(
            result.domain_actual["output"],
            {
                "receipt": {
                    "recovery_receipt_id": (
                        "receipt-SL-06"
                    ),
                    "session_id": "session-SL-06",
                    "lease_id": "lease-SL-06",
                    "detected_at": 100,
                    "recovery_actor_principal_id": (
                        "principal-recovery"
                    ),
                    "trigger": (
                        "PROCESS_BIRTH_MISMATCH"
                    ),
                    "mismatch_dimensions": [
                        "owner_process_birth_identity"
                    ],
                    "evidence_digest": (
                        "sha256:evidence-sl06-0001"
                    ),
                    "policy_id": (
                        "session-lease-recovery-v1"
                    ),
                    "policy_revision": 1,
                    "decision": "FENCE_AND_CLOSE",
                    "certainty_before_policy": (
                        "PRIOR_HOLDER_UNVERIFIED"
                    ),
                    "certainty_after_policy": (
                        "FENCED_FOR_FUTURE_WRITES"
                    ),
                    "external_effect_certainty": None,
                    "pre_lifecycle_state": "ACTIVE",
                    "pre_revision": 8,
                    "pre_fencing_token": 40,
                    "post_lifecycle_state": "CLOSED",
                    "post_revision": 9,
                    "post_fencing_token": 40,
                }
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

                    if fixture_id == "SL-01-NEG-01":
                        self.assertEqual(
                            actual["lease"]["lease_id"],
                            actual["session"][
                                "session_id"
                            ],
                        )
                    elif fixture_id == "SL-02-NEG-01":
                        self.assertNotEqual(
                            actual["session_id"],
                            expected["session_id"],
                        )
                    elif fixture_id == "SL-03-NEG-01":
                        self.assertFalse(
                            actual[
                                "zero_state_mutations"
                            ]
                        )
                    elif fixture_id == "SL-04-NEG-01":
                        self.assertEqual(
                            actual[
                                "stale_cas_rejection"
                            ]["status"],
                            "ACCEPTED",
                        )
                        self.assertFalse(
                            actual[
                                "stale_cas_rejection"
                            ][
                                "zero_state_mutations"
                            ]
                        )
                    elif fixture_id == "SL-05-NEG-01":
                        self.assertEqual(
                            actual["status"],
                            "ACCEPTED",
                        )
                        self.assertFalse(
                            actual[
                                "zero_state_mutations"
                            ]
                        )
                    else:
                        self.assertEqual(
                            actual["receipt"][
                                "post_fencing_token"
                            ],
                            actual["receipt"][
                                "pre_fencing_token"
                            ],
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
                self._fixture_path("SL-01"),
                "SL-01",
                first_root,
            )
            run_fixture(
                self._fixture_path("SL-01"),
                "SL-01",
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
