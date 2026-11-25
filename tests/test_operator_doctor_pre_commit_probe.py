"""
Operator doctor: how the pre-commit availability check finds the tool.

The check looks on PATH first and then asks whether the Python module can be found. It
must answer from the import machinery's spec lookup and never import the module itself:
importing runs a third-party package's import-time code just to learn that it exists.
"""

import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from services.operator_doctor import DoctorReport, Severity, check_pre_commit

OPERATOR_DOCTOR_SOURCE = (
    Path(__file__).resolve().parents[1] / "services" / "operator_doctor.py"
)
MODULE_IMPORT = re.compile(r"import_module\(\s*[\"']pre_commit[\"']")


def _run_check(*, on_path, spec=None, spec_error=None):
    """Run the check with PATH and the spec lookup faked; return the single result."""
    find_spec = MagicMock(return_value=spec, side_effect=spec_error)
    import_module = MagicMock()
    report = DoctorReport()
    with (
        patch(
            "services.operator_doctor.shutil.which",
            return_value="/tools/pre-commit" if on_path else None,
        ),
        patch("importlib.util.find_spec", find_spec),
        patch("importlib.import_module", import_module),
    ):
        check_pre_commit(report)
    return report, find_spec, import_module


class PreCommitProbeTests(unittest.TestCase):
    def only_result(self, report):
        self.assertEqual(len(report.checks), 1)
        result = report.checks[0]
        self.assertEqual(result.name, "pre_commit")
        return result

    def test_a_tool_on_path_passes_without_any_module_lookup(self):
        report, find_spec, import_module = _run_check(on_path=True)

        result = self.only_result(report)
        self.assertEqual(result.severity, Severity.PASS.value)
        self.assertEqual(result.message, "pre-commit found on PATH")
        find_spec.assert_not_called()
        import_module.assert_not_called()

    def test_a_findable_module_passes_and_is_never_imported(self):
        report, find_spec, import_module = _run_check(on_path=False, spec=object())

        result = self.only_result(report)
        self.assertEqual(result.severity, Severity.PASS.value)
        self.assertEqual(result.message, "pre-commit available as Python module")
        find_spec.assert_called_once_with("pre_commit")
        self.assertNotIn(call("pre_commit"), import_module.call_args_list)

    def test_a_missing_module_warns_with_the_install_hint(self):
        report, find_spec, import_module = _run_check(on_path=False, spec=None)

        result = self.only_result(report)
        self.assertEqual(result.severity, Severity.WARN.value)
        self.assertEqual(result.message, "pre-commit not found")
        self.assertEqual(
            result.remediation,
            "pip install pre-commit (required for SOP validation).",
        )
        find_spec.assert_called_once_with("pre_commit")
        self.assertNotIn(call("pre_commit"), import_module.call_args_list)

    def test_a_lookup_that_cannot_resolve_the_name_counts_as_missing(self):
        for error in (ValueError("pre_commit.__spec__ is None"), ImportError("broken")):
            with self.subTest(error=type(error).__name__):
                report, _, import_module = _run_check(on_path=False, spec_error=error)

                result = self.only_result(report)
                self.assertEqual(result.severity, Severity.WARN.value)
                self.assertEqual(result.message, "pre-commit not found")
                self.assertNotIn(call("pre_commit"), import_module.call_args_list)

    def test_the_doctor_source_does_not_import_the_module_to_probe_it(self):
        text = OPERATOR_DOCTOR_SOURCE.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            self.assertIsNone(
                MODULE_IMPORT.search(line),
                f"operator_doctor.py line {number} imports pre_commit: {line.strip()}",
            )


if __name__ == "__main__":
    unittest.main()
