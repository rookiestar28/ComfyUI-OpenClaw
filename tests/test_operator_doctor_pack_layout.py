"""Operator doctor contract-file checks on a source checkout and an installed archive."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from services import operator_doctor
from services.operator_doctor import DoctorReport, check_contract_files

ROOT = Path(__file__).resolve().parents[1]


def _results(pack_root: Path) -> dict[str, str]:
    report = DoctorReport()
    check_contract_files(report, pack_root)
    return {check.name: check.severity for check in report.checks}


class OperatorDoctorPackLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)

    def copy_tracked_docs(self, target: Path) -> None:
        listing = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "docs"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        for relative in filter(None, listing.split("\0")):
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)

    def test_pack_root_is_found_from_any_working_directory(self) -> None:
        original = os.getcwd()
        os.chdir(self.temp)
        self.addCleanup(os.chdir, original)

        self.assertEqual(operator_doctor._get_pack_root().resolve(), ROOT.resolve())

    def test_source_checkout_has_no_contract_file_failure(self) -> None:
        results = _results(ROOT)

        self.assertEqual(sorted(n for n, s in results.items() if s != "pass"), [])
        self.assertIn("contract_file:docs/RELEASE_CHECKLIST.md", results)
        self.assertIn("contract_file:docs/SECURITY.md", results)

    def test_installed_archive_reports_source_only_files_as_not_applicable(
        self,
    ) -> None:
        archive = self.temp / "archive"
        self.copy_tracked_docs(archive)

        results = _results(archive)

        self.assertEqual(sorted(n for n, s in results.items() if s == "fail"), [])
        self.assertEqual(results["contract_file:tests/TEST_SOP.md"], "skip")

    def test_source_checkout_missing_a_source_file_still_fails(self) -> None:
        checkout = self.temp / "checkout"
        self.copy_tracked_docs(checkout)
        (checkout / "tests").mkdir()

        results = _results(checkout)

        self.assertEqual(results["contract_file:tests/TEST_SOP.md"], "fail")

    def test_missing_user_facing_contract_document_fails_in_an_archive(self) -> None:
        archive = self.temp / "archive"
        self.copy_tracked_docs(archive)
        (archive / "docs" / "SECURITY.md").unlink()

        results = _results(archive)

        self.assertEqual(results["contract_file:docs/SECURITY.md"], "fail")


if __name__ == "__main__":
    unittest.main()
