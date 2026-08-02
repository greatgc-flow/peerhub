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
from domain import authority_quota
from runner import run_fixture


class DomainAuthorityQuotaTests(
    unittest.TestCase
):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "AC-09-01",
        "AC-09-02",
        "AC-09-03",
        "AC-09-04",
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
            authority_quota.__file__
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
                validated = (
                    authority_quota
                    .validate_authority_quota_inputs(
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

    def test_expected_dispositions_are_exact(
        self,
    ) -> None:
        expected = {
            "AC-09-01": "QUOTA_EVIDENCE_UNAVAILABLE",
            "AC-09-02": "QUOTA_EVIDENCE_STALE",
            "AC-09-03": (
                "WORKSPACES_EVALUATED_INDEPENDENTLY"
            ),
            "AC-09-04": (
                "ROUTING_ADVICE_NOT_AUTHORIZATION"
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, disposition in expected.items():
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
                        output["disposition"],
                        disposition,
                    )

    def test_missing_evidence_does_not_invent_quota(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "AC-09-01",
                root,
            ).domain_expected["output"]

        row = output["workspace_evaluations"][0]
        self.assertEqual(
            row["evidence_state"],
            "MISSING",
        )
        self.assertFalse(
            row["evidence_authoritative"]
        )
        self.assertFalse(
            row["adapter_evidence_consumed"]
        )
        self.assertIsNone(
            row["observed_available_units"]
        )
        self.assertIsNone(
            row["usable_available_units"]
        )
        self.assertIsNone(
            row["quota_sufficient"]
        )
        self.assertTrue(
            output[
                "provider_quota_evidence_external_only"
            ]
        )

    def test_stale_boundary_is_explicit_and_not_authoritative(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-09-02",
                root,
            )

        inputs = result.domain_input["inputs"]
        row = result.domain_expected[
            "output"
        ]["workspace_evaluations"][0]

        self.assertEqual(
            row["evidence_age_epochs"],
            6,
        )
        self.assertGreater(
            row["evidence_age_epochs"],
            inputs["maximum_evidence_age_epochs"],
        )
        self.assertEqual(
            row["evidence_state"],
            "STALE",
        )
        self.assertFalse(
            row["evidence_authoritative"]
        )
        self.assertEqual(
            row["observed_available_units"],
            11,
        )
        self.assertIsNone(
            row["usable_available_units"]
        )
        self.assertIsNone(
            row["quota_sufficient"]
        )

    def test_workspaces_are_evaluated_independently(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-09-03",
                root,
            )

        rows = result.domain_expected[
            "output"
        ]["workspace_evaluations"]
        by_workspace = {
            row["workspace_id"]: row
            for row in rows
        }
        output = result.domain_expected["output"]

        self.assertEqual(
            by_workspace["workspace-alpha"][
                "usable_available_units"
            ],
            7,
        )
        self.assertTrue(
            by_workspace["workspace-alpha"][
                "quota_sufficient"
            ]
        )
        self.assertEqual(
            by_workspace["workspace-beta"][
                "usable_available_units"
            ],
            4,
        )
        self.assertTrue(
            by_workspace["workspace-beta"][
                "quota_sufficient"
            ]
        )
        self.assertEqual(
            by_workspace["workspace-alpha"][
                "provider_account_id"
            ],
            by_workspace["workspace-beta"][
                "provider_account_id"
            ],
        )
        self.assertFalse(
            output[
                "cross_workspace_coordination_performed"
            ]
        )
        self.assertEqual(
            output["provider_quota_reservation_count"],
            0,
        )
        self.assertEqual(
            output["provider_quota_allocation_count"],
            0,
        )

    def test_routing_advice_is_not_dispatch_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._verify(
                "AC-09-04",
                root,
            ).domain_expected["output"]

        self.assertTrue(
            output["routing_recommendation_present"]
        )
        self.assertTrue(
            output[
                "routing_recommendation_advisory_only"
            ]
        )
        self.assertFalse(
            output[
                "manual_dispatch_authorization_present"
            ]
        )
        self.assertFalse(
            output["dispatch_authorized"]
        )
        self.assertEqual(
            output["dispatch_authority_source"],
            "NONE",
        )

    def test_positive_fixtures_never_own_or_allocate_quota(
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
                    output = self._verify(
                        fixture_id,
                        root,
                    ).domain_expected["output"]

                    self.assertTrue(
                        output[
                            "provider_quota_evidence_external_only"
                        ]
                    )
                    self.assertFalse(
                        output[
                            "cross_workspace_coordination_performed"
                        ]
                    )
                    self.assertEqual(
                        output[
                            "provider_quota_reservation_count"
                        ],
                        0,
                    )
                    self.assertEqual(
                        output[
                            "provider_quota_allocation_count"
                        ],
                        0,
                    )
                    self.assertEqual(
                        output[
                            "workspace_database_quota_write_count"
                        ],
                        0,
                    )

    def test_each_specific_fault_is_detected(
        self,
    ) -> None:
        expected = {
            "AC-09-01-NEG-01": (
                "disposition",
                "ASSUMED_QUOTA_AVAILABLE",
                "QUOTA_EVIDENCE_UNAVAILABLE",
            ),
            "AC-09-02-NEG-01": (
                "disposition",
                "STALE_QUOTA_ACCEPTED",
                "QUOTA_EVIDENCE_STALE",
            ),
            "AC-09-03-NEG-01": (
                "cross_workspace_coordination_performed",
                True,
                False,
            ),
            "AC-09-04-NEG-01": (
                "dispatch_authorized",
                True,
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
                    result = self._verify(
                        fixture_id,
                        root,
                    )
                    actual = result.domain_actual["output"]
                    oracle = result.domain_expected["output"]

                    self.assertFalse(result.passed)
                    self.assertEqual(
                        result.domain_verification[
                            "status"
                        ],
                        "FAIL",
                    )
                    self.assertEqual(
                        actual[values[0]],
                        values[1],
                    )
                    self.assertEqual(
                        oracle[values[0]],
                        values[2],
                    )

    def test_shared_account_fault_changes_only_second_view(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-09-03-NEG-01",
                root,
            )

        actual = {
            row["workspace_id"]: row
            for row in result.domain_actual[
                "output"
            ]["workspace_evaluations"]
        }
        expected = {
            row["workspace_id"]: row
            for row in result.domain_expected[
                "output"
            ]["workspace_evaluations"]
        }

        self.assertEqual(
            actual["workspace-alpha"],
            expected["workspace-alpha"],
        )
        self.assertEqual(
            actual["workspace-beta"][
                "observed_available_units"
            ],
            4,
        )
        self.assertEqual(
            actual["workspace-beta"][
                "usable_available_units"
            ],
            2,
        )
        self.assertFalse(
            actual["workspace-beta"]["quota_sufficient"]
        )
        self.assertEqual(
            expected["workspace-beta"][
                "usable_available_units"
            ],
            4,
        )
        self.assertTrue(
            expected["workspace-beta"][
                "quota_sufficient"
            ]
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
            fixture = self._fixture_path(
                "AC-09-03"
            )

            run_fixture(
                fixture,
                "AC-09-03",
                first_root,
            )
            run_fixture(
                fixture,
                "AC-09-03",
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
