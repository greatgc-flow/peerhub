from __future__ import annotations

import copy
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
from runner import run_fixture


class DomainCommandAuthzTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "CJ-02",
        "CJ-05",
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    ORACLE_IDS = {
        "CJ-02": (
            "command_authz.cj02.valid_admission"
        ),
        "CJ-05": (
            "command_authz.cj05."
            "authorization_before_effects"
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

    def _read_record(
        self,
        path: Path,
    ) -> dict[str, Any]:
        return json.loads(
            path.read_text(encoding="utf-8")
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

    def _case(
        self,
        fixture_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        base_fixture_id = fixture_id.removesuffix(
            "-NEG-01"
        )
        return {
            "contract_version": 1,
            "fixture_id": fixture_id,
            "oracle_id": self.ORACLE_IDS[
                base_fixture_id
            ],
            "oracle_version": 1,
            "inputs": inputs,
        }

    def test_positive_oracle_adapter_pairs_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)

            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = (
                        temporary_path
                        / fixture_id
                    )
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
            temporary_path = Path(temporary)

            for fixture_id in self.NEGATIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = (
                        temporary_path
                        / fixture_id
                    )
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

    def test_cj02_preserves_identity_and_injected_id(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = DOMAIN_REGISTRY.verify(
                self._load_script("CJ-02")[
                    "domain_case"
                ],
                "CJ-02",
                self._context(root),
            )

            output = result.domain_expected[
                "output"
            ]
            self.assertEqual(
                output,
                {
                    "status": "ADMITTED",
                    "command_id": (
                        "command-CJ-02-001"
                    ),
                    "actor_identity": (
                        "actor-authorized"
                    ),
                    "client_request_key": (
                        "request-CJ-02"
                    ),
                    "workspace_scope": (
                        "workspace-CJ-02"
                    ),
                    "zero_provider_calls": True,
                    "zero_dispatch_calls": True,
                },
            )

    def test_cj05_unauthorized_rejection_has_no_effects(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = DOMAIN_REGISTRY.verify(
                self._load_script("CJ-05")[
                    "domain_case"
                ],
                "CJ-05",
                self._context(root),
            )

            self.assertTrue(result.passed)
            self.assertEqual(
                result.domain_expected["output"],
                {
                    "status": "REJECTED",
                    "reason": "ACTOR_UNAUTHORIZED",
                    "exit_code": 3,
                    "effect_certainty": "NOT_STARTED",
                    "retryable": False,
                    "command_id": None,
                    "zero_state_mutations": True,
                    "zero_receipt_writes": True,
                    "zero_outbox_writes": True,
                    "zero_provider_calls": True,
                    "zero_dispatch_calls": True,
                },
            )

    def test_authorized_revision_mismatch_is_distinct(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = copy.deepcopy(
                self._load_script("CJ-05")[
                    "domain_case"
                ]["inputs"]
            )
            inputs["authorization"][
                "actor_authorized"
            ] = True
            inputs["current_revisions"][
                "current_configuration_revision"
            ] = 18

            result = DOMAIN_REGISTRY.verify(
                self._case(
                    "CJ-05",
                    inputs,
                ),
                "CJ-05",
                self._context(root),
            )

            self.assertTrue(result.passed)
            output = result.domain_actual["output"]
            self.assertEqual(
                output["reason"],
                "ADMISSION_REVISION_MISMATCH",
            )
            self.assertIsNone(output["command_id"])
            self.assertEqual(
                output["effect_certainty"],
                "NOT_STARTED",
            )
            for key in (
                "zero_state_mutations",
                "zero_receipt_writes",
                "zero_outbox_writes",
                "zero_provider_calls",
                "zero_dispatch_calls",
            ):
                self.assertTrue(output[key], key)

    def test_cj05_late_authorization_fault_is_caught(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = self._load_script(
                "CJ-05-NEG-01"
            )
            result = DOMAIN_REGISTRY.verify(
                script["domain_case"],
                "CJ-05-NEG-01",
                self._context(root),
            )

            self.assertFalse(result.passed)
            actual = result.domain_actual["output"]
            expected = result.domain_expected[
                "output"
            ]

            self.assertEqual(
                actual["command_id"],
                "command-CJ-05-never-return",
            )
            self.assertFalse(
                actual["zero_state_mutations"]
            )
            self.assertFalse(
                actual["zero_receipt_writes"]
            )
            self.assertEqual(
                actual["effect_certainty"],
                "MAY_HAVE_STARTED",
            )
            self.assertIsNone(
                expected["command_id"]
            )
            self.assertTrue(
                expected["zero_state_mutations"]
            )
            self.assertTrue(
                expected["zero_receipt_writes"]
            )
            self.assertEqual(
                expected["effect_certainty"],
                "NOT_STARTED",
            )
            self.assertEqual(
                result.domain_verification[
                    "status"
                ],
                "FAIL",
            )

    def test_runner_integration_positive_and_negative(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)

            for fixture_id in (
                self.POSITIVE_IDS
                + self.NEGATIVE_IDS
            ):
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = (
                        temporary_path
                        / fixture_id
                    )
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
                        "PASS" if positive else "FAIL",
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
            temporary_path = Path(temporary)
            first_root = temporary_path / "first"
            second_root = temporary_path / "second"

            run_fixture(
                self._fixture_path("CJ-02"),
                "CJ-02",
                first_root,
            )
            run_fixture(
                self._fixture_path("CJ-02"),
                "CJ-02",
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
                    (
                        first_root / name
                    ).read_bytes(),
                    (
                        second_root / name
                    ).read_bytes(),
                    name,
                )


if __name__ == "__main__":
    unittest.main()