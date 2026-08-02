from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from runner import run_fixture


class DomainBrokerTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "GB-01",
        "GB-03",
        "GB-04",
        "GB-05",
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    ORACLE_IDS = {
        "GB-01": "broker.gb01.atomic_cas_commit",
        "GB-03": "broker.gb03.idempotency_sequence",
        "GB-04": "broker.gb04.recovery_without_replay",
        "GB-05": (
            "broker.gb05."
            "immutable_terminal_receipt"
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
                fixture_id.removesuffix(
                    "-NEG-01"
                )
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

    def test_gb01_direct_sqlite_atomicity_probe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = self._load_script("GB-01")
            result = DOMAIN_REGISTRY.verify(
                script["domain_case"],
                "GB-01",
                self._context(root),
            )

            self.assertTrue(result.passed)
            database_path = root / "gb01-broker.sqlite"
            self.assertTrue(database_path.is_file())

            connection = sqlite3.connect(str(database_path))
            try:
                revision_rows = connection.execute(
                    """
                    SELECT target_revision, pending_receipt
                    FROM revision_state
                    """
                ).fetchall()
                outbox_rows = connection.execute(
                    "SELECT outbox_row FROM outbox"
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(
                revision_rows,
                [(42, "receipt-GB-01")],
            )
            self.assertEqual(
                outbox_rows,
                [("outbox-GB-01",)],
            )

    def test_gb01_before_commit_rolls_back_actual_sql(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = self._case(
                "GB-01",
                {
                    "target_revision": 99,
                    "pending_receipt": "receipt-rollback",
                    "outbox_row": "outbox-rollback",
                    "fault_point": "BEFORE_COMMIT",
                },
            )
            result = DOMAIN_REGISTRY.verify(
                case,
                "GB-01",
                self._context(root),
            )

            self.assertTrue(result.passed)
            self.assertEqual(
                result.domain_actual["output"],
                {
                    "revision_row_present": False,
                    "pending_receipt_present": False,
                    "outbox_row_present": False,
                    "both_or_neither": True,
                },
            )

            connection = sqlite3.connect(
                str(root / "gb01-broker.sqlite")
            )
            try:
                revision_count = connection.execute(
                    "SELECT COUNT(*) FROM revision_state"
                ).fetchone()[0]
                outbox_count = connection.execute(
                    "SELECT COUNT(*) FROM outbox"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(revision_count, 0)
            self.assertEqual(outbox_count, 0)

    def test_gb01_fault_commits_real_sql_before_fault(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = self._load_script(
                "GB-01-NEG-01"
            )
            result = DOMAIN_REGISTRY.verify(
                script["domain_case"],
                "GB-01-NEG-01",
                self._context(root),
            )

            self.assertFalse(result.passed)
            connection = sqlite3.connect(
                str(root / "gb01-broker.sqlite")
            )
            try:
                revision_count = connection.execute(
                    "SELECT COUNT(*) FROM revision_state"
                ).fetchone()[0]
                outbox_count = connection.execute(
                    "SELECT COUNT(*) FROM outbox"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(revision_count, 1)
            self.assertEqual(outbox_count, 1)

    def test_expected_outputs_cover_broker_invariants(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            outputs: dict[str, dict[str, Any]] = {}

            for fixture_id in (
                "GB-03",
                "GB-04",
                "GB-05",
            ):
                root = temporary_path / fixture_id
                root.mkdir()
                outputs[fixture_id] = (
                    DOMAIN_REGISTRY.verify(
                        self._load_script(
                            fixture_id
                        )["domain_case"],
                        fixture_id,
                        self._context(root),
                    ).domain_expected["output"]
                )

        gb03 = outputs["GB-03"]
        self.assertEqual(gb03["mutation_count"], 1)
        self.assertEqual(
            [
                row["disposition"]
                for row in gb03["submissions"]
            ],
            [
                "MUTATED",
                "IDEMPOTENCY_HIT",
                "IDEMPOTENCY_PAYLOAD_MISMATCH",
            ],
        )
        self.assertEqual(
            gb03["submissions"][0]["receipt"],
            gb03["submissions"][1]["receipt"],
        )
        self.assertIsNone(
            gb03["submissions"][2]["receipt"]
        )

        self.assertEqual(
            outputs["GB-04"],
            {
                "transition_applies": 1,
                "blind_replays": 0,
                "outbox_disposition": (
                    "PENDING_CONFIRMATION_REQUIRED"
                ),
            },
        )

        gb05 = outputs["GB-05"]
        self.assertEqual(
            gb05["stored_receipt"],
            {
                "request_id": "request-GB-05",
                "outbox_id": "outbox-GB-05",
                "attempt_id": "attempt-GB-05",
                "owner_id": "owner-first",
                "terminal_result": "EFFECT_SUCCEEDED",
            },
        )
        self.assertEqual(
            gb05["second_disposition"],
            "COMPETING_RECEIPT_REJECTED",
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
                                f"{key}_raw_sha256"
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
                self._fixture_path("GB-01"),
                "GB-01",
                first_root,
            )
            run_fixture(
                self._fixture_path("GB-01"),
                "GB-01",
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