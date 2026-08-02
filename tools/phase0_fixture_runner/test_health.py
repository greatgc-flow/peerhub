from __future__ import annotations

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


class DomainHealthTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "HR-04",
        "HR-05",
        "HR-06",
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    ORACLE_IDS = {
        "HR-04": (
            "health.hr04.authority_clearance"
        ),
        "HR-05": (
            "health.hr05.one_probe_grant"
        ),
        "HR-06": (
            "health.hr06.cas_probe_transition"
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
        return {
            "contract_version": 1,
            "fixture_id": fixture_id,
            "oracle_id": self.ORACLE_IDS[
                fixture_id
            ],
            "oracle_version": 1,
            "inputs": inputs,
        }

    def test_positive_oracle_adapter_pairs_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(
                Path(temporary)
            )

            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    script = self._load_script(
                        fixture_id
                    )
                    result = (
                        DOMAIN_REGISTRY.verify(
                            script["domain_case"],
                            fixture_id,
                            context,
                        )
                    )

                    self.assertTrue(
                        result.passed
                    )
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
            context = self._context(
                Path(temporary)
            )

            for fixture_id in self.NEGATIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    script = self._load_script(
                        fixture_id
                    )
                    result = (
                        DOMAIN_REGISTRY.verify(
                            script["domain_case"],
                            fixture_id,
                            context,
                        )
                    )

                    self.assertFalse(
                        result.passed
                    )
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

    def test_expected_outputs_cover_health_invariants(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(
                Path(temporary)
            )
            outputs = {
                fixture_id: (
                    DOMAIN_REGISTRY.verify(
                        self._load_script(
                            fixture_id
                        )["domain_case"],
                        fixture_id,
                        context,
                    ).domain_expected["output"]
                )
                for fixture_id
                in self.POSITIVE_IDS
            }

        self.assertEqual(
            outputs["HR-04"][
                "circuit_state"
            ],
            "CIRCUIT_OPEN",
        )
        self.assertEqual(
            outputs["HR-04"]["reason"],
            (
                "QUARANTINE_AUTHORITY_"
                "INSUFFICIENT"
            ),
        )

        self.assertEqual(
            [
                row["disposition"]
                for row
                in outputs["HR-05"][
                    "probe_decisions"
                ]
            ],
            [
                "EXECUTED",
                "REJECTED",
            ],
        )
        self.assertEqual(
            outputs["HR-05"][
                "probe_decisions"
            ][1]["reason"],
            "PROBE_GRANT_EXHAUSTED",
        )
        self.assertTrue(
            outputs["HR-05"][
                "state_unchanged"
            ]
        )
        self.assertEqual(
            outputs["HR-05"][
                "health_value_before"
            ],
            outputs["HR-05"][
                "health_value_after"
            ],
        )
        self.assertEqual(
            outputs["HR-05"][
                "gate_state_before"
            ],
            outputs["HR-05"][
                "gate_state_after"
            ],
        )

        self.assertEqual(
            outputs["HR-06"]["transition"],
            "FAILURE_BACKOFF_INCREMENTED",
        )
        self.assertEqual(
            outputs["HR-06"][
                "backoff_count_after"
            ],
            (
                outputs["HR-06"][
                    "backoff_count_before"
                ]
                + 1
            ),
        )
        self.assertEqual(
            outputs["HR-06"][
                "circuit_state_after"
            ],
            "CIRCUIT_OPEN",
        )

    def test_hr04_requires_match_and_automatic_authority(
        self,
    ) -> None:
        current = {
            "incident": "incident-current",
            "gate_generation": 7,
            "timestamp": 700,
            "fingerprint": (
                "fingerprint-current"
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(
                Path(temporary)
            )

            automatic = (
                DOMAIN_REGISTRY.verify(
                    self._case(
                        "HR-04",
                        {
                            "clearance_receipt": (
                                dict(current)
                            ),
                            "current": (
                                dict(current)
                            ),
                            (
                                "quarantine_"
                                "authority_class"
                            ): "AUTOMATIC",
                        },
                    ),
                    "HR-04",
                    context,
                ).domain_expected["output"]
            )

            stale_receipt = dict(current)
            stale_receipt["timestamp"] = 699
            stale = DOMAIN_REGISTRY.verify(
                self._case(
                    "HR-04",
                    {
                        "clearance_receipt": (
                            stale_receipt
                        ),
                        "current": dict(current),
                        (
                            "quarantine_"
                            "authority_class"
                        ): "AUTOMATIC",
                    },
                ),
                "HR-04",
                context,
            ).domain_expected["output"]

            manual = DOMAIN_REGISTRY.verify(
                self._case(
                    "HR-04",
                    {
                        "clearance_receipt": (
                            dict(current)
                        ),
                        "current": dict(current),
                        (
                            "quarantine_"
                            "authority_class"
                        ): "MANUAL",
                    },
                ),
                "HR-04",
                context,
            ).domain_expected["output"]

        self.assertEqual(
            automatic["circuit_state"],
            "CIRCUIT_CLOSED",
        )
        self.assertTrue(
            automatic["clearance_applied"]
        )
        self.assertEqual(
            stale["reason"],
            (
                "AUTOMATIC_CLEARANCE_"
                "RECEIPT_MISMATCH"
            ),
        )
        self.assertEqual(
            manual["reason"],
            (
                "QUARANTINE_AUTHORITY_"
                "INSUFFICIENT"
            ),
        )

    def test_hr06_success_is_cas_gated_and_stale_is_no_op(
        self,
    ) -> None:
        identity = {
            "revision": 12,
            "incident": "incident-current",
            "gate_generation": 7,
            "timestamp": 720,
            "fingerprint": (
                "fingerprint-current"
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(
                Path(temporary)
            )

            current_match = {
                **identity,
                "backoff_count": 2,
                "circuit_state": (
                    "CIRCUIT_OPEN"
                ),
            }
            matched = DOMAIN_REGISTRY.verify(
                self._case(
                    "HR-06",
                    {
                        "probe_result": "SUCCESS",
                        "reported": (
                            dict(identity)
                        ),
                        "current": current_match,
                    },
                ),
                "HR-06",
                context,
            ).domain_expected["output"]

            stale_report = dict(identity)
            stale_report["fingerprint"] = (
                "fingerprint-stale"
            )
            current_closed = {
                **identity,
                "backoff_count": 4,
                "circuit_state": (
                    "CIRCUIT_CLOSED"
                ),
            }
            stale = DOMAIN_REGISTRY.verify(
                self._case(
                    "HR-06",
                    {
                        "probe_result": "SUCCESS",
                        "reported": stale_report,
                        "current": current_closed,
                    },
                ),
                "HR-06",
                context,
            ).domain_expected["output"]

        self.assertEqual(
            matched["transition"],
            "SUCCESS_CIRCUIT_CLOSED",
        )
        self.assertEqual(
            matched["circuit_state_after"],
            "CIRCUIT_CLOSED",
        )
        self.assertEqual(
            stale["transition"],
            "STALE_PROBE_NO_OP",
        )
        self.assertEqual(
            stale["circuit_state_after"],
            stale["circuit_state_before"],
        )
        self.assertEqual(
            stale["backoff_count_after"],
            stale["backoff_count_before"],
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

                    expected_status = (
                        "V1_CAPTURE"
                        if fixture_id
                        in self.POSITIVE_IDS
                        else (
                            "DOMAIN_"
                            "ASSERTION_FAILED"
                        )
                    )
                    expected_domain_status = (
                        "PASS"
                        if fixture_id
                        in self.POSITIVE_IDS
                        else "FAIL"
                    )

                    self.assertEqual(
                        record["status"],
                        expected_status,
                    )
                    self.assertEqual(
                        record[
                            "domain_verification"
                        ]["status"],
                        expected_domain_status,
                    )

                    if (
                        fixture_id
                        in self.POSITIVE_IDS
                    ):
                        self.assertEqual(
                            record[
                                "coverage_scope"
                            ],
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
            first_root = (
                temporary_path / "first"
            )
            second_root = (
                temporary_path / "second"
            )

            run_fixture(
                self._fixture_path("HR-05"),
                "HR-05",
                first_root,
            )
            run_fixture(
                self._fixture_path("HR-05"),
                "HR-05",
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