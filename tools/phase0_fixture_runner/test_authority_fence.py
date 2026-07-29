from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.authority_fence as authority_fence
from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from runner import run_fixture


class DomainAuthorityFenceTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent / "fixtures"
    )
    POSITIVE_IDS = tuple(
        f"AC-04-{index:02d}"
        for index in range(1, 7)
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    ORACLE_IDS = {
        "AC-04-01": (
            "authority_fence.ac0401."
            "pre_marker_write_unchanged_epoch"
        ),
        "AC-04-02": (
            "authority_fence.ac0402."
            "stale_lease_epoch_fenced"
        ),
        "AC-04-03": (
            "authority_fence.ac0403."
            "mandatory_final_epoch_recheck"
        ),
        "AC-04-04": (
            "authority_fence.ac0404."
            "same_epoch_marker_cas_contention"
        ),
        "AC-04-05": (
            "authority_fence.ac0405."
            "stale_admission_marker_cas"
        ),
        "AC-04-06": (
            "authority_fence.ac0406."
            "migration_lock_loss_before_marker"
        ),
    }

    def _fixture_path(self, fixture_id: str) -> Path:
        return self.FIXTURE_DIRECTORY / f"{fixture_id}.json"

    def _load_script(
        self,
        fixture_id: str,
    ) -> dict[str, Any]:
        return json.loads(
            self._fixture_path(fixture_id).read_text(
                encoding="utf-8"
            )
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

    def _read_record(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_module_allows_only_sqlite_filesystem_access(
        self,
    ) -> None:
        source = Path(
            authority_fence.__file__
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {
            "ctypes",
            "os",
            "pathlib",
            "shutil",
            "socket",
            "subprocess",
            "win32",
            "win32api",
            "win32con",
            "win32file",
        }
        imported_roots: set[str] = set()

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

        self.assertEqual(
            imported_roots & forbidden,
            set(),
        )
        self.assertIn("sqlite3", imported_roots)

    def test_positive_oracle_adapter_pairs_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
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
                        result.domain_verification["status"],
                        "PASS",
                    )

    def test_fault_injected_pairs_are_detected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id in self.NEGATIVE_IDS:
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
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
                        result.domain_verification["status"],
                        "FAIL",
                    )
                    self.assertNotEqual(
                        result.domain_actual["output"],
                        result.domain_expected["output"],
                    )

    def test_ac0401_is_not_over_fenced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = DOMAIN_REGISTRY.verify(
                self._load_script("AC-04-01")[
                    "domain_case"
                ],
                "AC-04-01",
                self._context(root),
            )
            output = result.domain_expected["output"]

        self.assertEqual(output["decision"], "ACCEPTED")
        self.assertEqual(
            output["disposition"],
            "WRITE_COMMITTED",
        )
        self.assertEqual(
            output["legacy_write_mutations"],
            1,
        )
        self.assertTrue(
            output["final_recheck_performed"]
        )

    def test_ac0402_and_ac0403_have_zero_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id in ("AC-04-02", "AC-04-03"):
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    result = DOMAIN_REGISTRY.verify(
                        self._load_script(
                            fixture_id
                        )["domain_case"],
                        fixture_id,
                        self._context(root),
                    )
                    output = result.domain_expected["output"]
                    self.assertEqual(
                        output["disposition"],
                        "FENCED_STALE_EPOCH",
                    )
                    self.assertEqual(
                        output[
                            "legacy_write_mutations"
                        ],
                        0,
                    )
                    self.assertTrue(
                        output[
                            "final_recheck_performed"
                        ]
                    )

    def test_ac0404_direct_sqlite_probe_has_one_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = DOMAIN_REGISTRY.verify(
                self._load_script("AC-04-04")[
                    "domain_case"
                ],
                "AC-04-04",
                self._context(root),
            )
            self.assertTrue(result.passed)

            database_path = root / "ac04-fence.sqlite"
            self.assertTrue(database_path.is_file())
            connection = sqlite3.connect(str(database_path))
            try:
                marker_rows = connection.execute(
                    """
                    SELECT authority_epoch, phase, contender_id
                    FROM authority_marker
                    ORDER BY authority_epoch
                    """
                ).fetchall()
                state = connection.execute(
                    """
                    SELECT authority_epoch, phase
                    FROM authority_state
                    WHERE singleton = 1
                    """
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(
            marker_rows,
            [(8, "CUTOVER_DRAINING", "contender-A")],
        )
        self.assertEqual(
            state,
            (8, "CUTOVER_DRAINING"),
        )
        self.assertEqual(
            result.domain_expected["output"]["contenders"],
            [
                {
                    "contender_id": "contender-A",
                    "disposition": "MARKER_COMMITTED",
                },
                {
                    "contender_id": "contender-B",
                    "disposition": (
                        "CUTOVER_EPOCH_CONTENDED"
                    ),
                },
            ],
        )

    def test_ac0405_stale_snapshot_writes_no_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = DOMAIN_REGISTRY.verify(
                self._load_script("AC-04-05")[
                    "domain_case"
                ],
                "AC-04-05",
                self._context(root),
            )
            self.assertTrue(result.passed)
            connection = sqlite3.connect(
                str(root / "ac04-fence.sqlite")
            )
            try:
                marker_count = connection.execute(
                    "SELECT COUNT(*) FROM authority_marker"
                ).fetchone()[0]
                epoch = connection.execute(
                    """
                    SELECT authority_epoch
                    FROM authority_state
                    WHERE singleton = 1
                    """
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(marker_count, 0)
        self.assertEqual(epoch, 8)
        self.assertEqual(
            result.domain_expected["output"]["error_code"],
            "CUTOVER_EPOCH_CONTENDED",
        )

    def test_ac0406_does_not_retry_same_admission(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = DOMAIN_REGISTRY.verify(
                self._load_script("AC-04-06")[
                    "domain_case"
                ],
                "AC-04-06",
                self._context(root),
            )
            output = result.domain_expected["output"]

        self.assertEqual(
            output["error_code"],
            "MIGRATION_LOCK_LOST",
        )
        self.assertEqual(output["retry_count"], 0)
        self.assertEqual(output["marker_count"], 0)
        self.assertEqual(output["committed_epoch"], 7)

    def test_runner_integration_positive_and_negative(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id in (
                self.POSITIVE_IDS + self.NEGATIVE_IDS
            ):
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    record = self._read_record(
                        run_fixture(
                            self._fixture_path(fixture_id),
                            fixture_id,
                            root,
                        )
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
                            / record["artifact_paths"][key]
                        )
                        self.assertTrue(artifact.is_file())
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
                self._fixture_path("AC-04-04"),
                "AC-04-04",
                first_root,
            )
            run_fixture(
                self._fixture_path("AC-04-04"),
                "AC-04-04",
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