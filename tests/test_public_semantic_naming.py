"""Contract for the public semantic naming ratchet.

The repository check at the end of this module is what actually enforces the
ratchet: the acceptance gate runs the backend unit suite, so importing and
executing the verifier here puts it on every gate run. The fixture tests around
it prove the individual rules, including the ones that must *not* fire.
"""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_public_semantic_naming import (
    PolicyError,
    build_reference_graph,
    compare_report,
    find_code_named_tests,
    read_policy,
    scan_comment_codes,
    validate_report_privacy,
    verify,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "tests" / "public_semantic_naming_policy.json"

BASE_POLICY = {
    "scanned_suffixes": [".py", ".js"],
    "excluded_prefixes": ["reference/", ".planning/"],
    "allowed_public_identifiers": [],
    "code_named_tests": [],
    "comment_counts": {},
}


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestCommentScanning(unittest.TestCase):
    def test_counts_comment_lines_not_code_and_not_prose(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write(
                root,
                "services/sample.py",
                "\n".join(
                    [
                        "# R82: expanded event type coverage",
                        "value = 1  # trailing comments are not scanned",
                        'TEXT = "R82 inside a string literal is not a comment"',
                        "# S31/F43 two codes on one line",
                        "# a plain behavioral comment",
                        "// R99 in a js-style comment",
                    ]
                ),
            )
            counts, occurrences = scan_comment_codes(
                root, BASE_POLICY, ["services/sample.py"]
            )

        self.assertEqual(counts["services/sample.py"], 3)
        self.assertEqual(len(occurrences), 3)
        self.assertEqual(occurrences[1]["tokens"], ["S31", "F43"])

    def test_excluded_prefixes_and_suffixes_are_never_scanned(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for rel in (
                "reference/docs/notes.py",
                ".planning/scratch.py",
                "docs/guide.md",
            ):
                _write(root, rel, "# R1 should never be counted")
            counts, occurrences = scan_comment_codes(
                root,
                BASE_POLICY,
                ["reference/docs/notes.py", ".planning/scratch.py", "docs/guide.md"],
            )

        self.assertEqual(counts, {})
        self.assertEqual(occurrences, [])

    def test_an_allowed_identifier_suppresses_exactly_one_path_and_token(self):
        policy = dict(
            BASE_POLICY,
            allowed_public_identifiers=[
                {
                    "path": "services/storage.py",
                    "token": "S3",
                    "reason": "S3 is the object storage service name, not an item id.",
                }
            ],
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write(root, "services/storage.py", "# upload to S3 with server-side keys")
            _write(root, "services/other.py", "# upload to S3 with server-side keys")
            _write(root, "services/mixed.py", "# S3 bucket, see S31 handling")
            counts, _ = scan_comment_codes(
                root,
                policy,
                ["services/storage.py", "services/other.py", "services/mixed.py"],
            )

        self.assertNotIn("services/storage.py", counts)
        self.assertEqual(counts["services/other.py"], 1)
        # The exception covers the allowlisted token only; the other code on
        # the same line still counts.
        self.assertEqual(counts["services/mixed.py"], 1)

    def test_an_allowed_identifier_requires_a_public_safe_reason(self):
        for bad_reason in ("", "   ", "see .planning/roadmap.md", "reference/docs"):
            policy = dict(
                BASE_POLICY,
                allowed_public_identifiers=[
                    {"path": "a.py", "token": "S3", "reason": bad_reason}
                ],
            )
            with self.subTest(reason=bad_reason), self.assertRaises(PolicyError):
                scan_comment_codes(Path("."), policy, [])


class TestFilenameAndComparison(unittest.TestCase):
    def test_code_named_test_files_are_detected_by_shape(self):
        found = find_code_named_tests(
            [
                "tests/test_r236_advanced_3d_result.py",
                "tests/security/test_s78_redaction.py",
                "tests/test_F44_kakao_bundle.py",
                "tests/test_jobs_endpoint_contract.py",
                "tests/test_route_bootstrap.py",
                "tests/test_r236_notes.md",
                "services/test_helper.py",
            ]
        )

        self.assertEqual(
            found,
            [
                "tests/security/test_s78_redaction.py",
                "tests/test_F44_kakao_bundle.py",
                "tests/test_r236_advanced_3d_result.py",
            ],
        )

    def test_new_debt_fails_and_grandfathered_debt_does_not(self):
        policy = dict(
            BASE_POLICY,
            code_named_tests=["tests/test_r1_legacy.py"],
            comment_counts={"services/legacy.py": 4},
        )
        unchanged = {
            "code_named_tests": ["tests/test_r1_legacy.py"],
            "comment_counts": {"services/legacy.py": 4},
        }
        self.assertEqual(compare_report(policy, unchanged), [])

        new_comment = {
            "code_named_tests": ["tests/test_r1_legacy.py"],
            "comment_counts": {"services/legacy.py": 5},
        }
        failures = compare_report(policy, new_comment)
        self.assertEqual(len(failures), 1)
        self.assertIn("new item-code comment", failures[0])
        self.assertIn("expected 4, found 5", failures[0])

        new_file = {
            "code_named_tests": ["tests/test_r1_legacy.py", "tests/test_r2_new.py"],
            "comment_counts": {"services/legacy.py": 4},
        }
        failures = compare_report(policy, new_file)
        self.assertEqual(len(failures), 1)
        self.assertIn("new item-code test filename", failures[0])
        self.assertIn("tests/test_r2_new.py", failures[0])

    def test_removing_debt_fails_until_the_baseline_is_updated(self):
        policy = dict(
            BASE_POLICY,
            code_named_tests=["tests/test_r1_legacy.py"],
            comment_counts={"services/legacy.py": 4},
        )
        reduced = {
            "code_named_tests": [],
            "comment_counts": {"services/legacy.py": 1},
        }
        failures = compare_report(policy, reduced)

        self.assertEqual(len(failures), 2)
        self.assertTrue(any("stale baseline test filename" in f for f in failures))
        self.assertTrue(any("stale baseline comment count" in f for f in failures))

    def test_a_brand_new_file_with_coded_comments_fails(self):
        policy = dict(BASE_POLICY, comment_counts={})
        failures = compare_report(
            policy,
            {"code_named_tests": [], "comment_counts": {"services/fresh.py": 1}},
        )

        self.assertEqual(len(failures), 1)
        self.assertIn("expected 0, found 1", failures[0])


class TestReferenceGraph(unittest.TestCase):
    def test_reference_graph_finds_path_module_and_stem_forms(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write(
                root,
                "tests/policy.json",
                json.dumps({"owner": "tests/test_r180_boundary.py"}),
            )
            _write(root, "tests/skip.json", json.dumps(["tests.test_r180_boundary"]))
            _write(root, "scripts/gate.ps1", "run test_r180_boundary now")
            _write(root, "docs/unrelated.md", "nothing to see")
            _write(root, "reference/mirror.py", "tests/test_r180_boundary.py")
            _write(root, "tests/test_r180_boundary.py", "# self reference")

            found = build_reference_graph(
                root,
                [
                    "tests/policy.json",
                    "tests/skip.json",
                    "scripts/gate.ps1",
                    "docs/unrelated.md",
                    "reference/mirror.py",
                    "tests/test_r180_boundary.py",
                ],
                "tests/test_r180_boundary.py",
            )

        self.assertEqual(
            found, ["scripts/gate.ps1", "tests/policy.json", "tests/skip.json"]
        )


class TestReportPrivacy(unittest.TestCase):
    def test_internal_path_fragments_in_a_report_are_rejected(self):
        for leaked in (
            {"comment_counts": {".planning/roadmap.md": 1}},
            {"comment_counts": {"reference/ComfyUI/x.py": 1}},
            {"comment_counts": {".tmp/scratch.py": 1}},
            {"comment_counts": {"C:\\Users\\someone\\repo\\a.py": 1}},
        ):
            with self.subTest(report=leaked):
                self.assertTrue(validate_report_privacy(leaked))

        self.assertEqual(
            validate_report_privacy({"comment_counts": {"services/history.py": 3}}), []
        )


class TestPolicyDocumentValidation(unittest.TestCase):
    def test_malformed_policy_documents_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, payload in (
                ("list.json", []),
                ("no_suffixes.json", {"excluded_prefixes": [], "code_named_tests": []}),
                (
                    "bad_counts.json",
                    {
                        "scanned_suffixes": [],
                        "excluded_prefixes": [],
                        "code_named_tests": [],
                        "comment_counts": [],
                    },
                ),
            ):
                path = root / name
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(document=name), self.assertRaises(PolicyError):
                    read_policy(path)


class TestRepositoryRatchet(unittest.TestCase):
    """The enforcement point. A failure here means new naming debt was added."""

    def test_repository_matches_its_pinned_naming_baseline(self):
        failures, report = verify(REPO_ROOT, POLICY_PATH)

        self.assertEqual(failures, [], "\n".join(failures))
        self.assertGreater(report["scanned_files"], 500)

    def test_the_repository_report_exposes_only_public_tracked_paths(self):
        _, report = verify(REPO_ROOT, POLICY_PATH)

        self.assertEqual(validate_report_privacy(report), [])
        for path in report["code_named_tests"]:
            self.assertFalse(path.startswith((".", "/")))
            self.assertTrue((REPO_ROOT / path).is_file())

    def test_the_policy_document_declares_its_own_verifier(self):
        policy = read_policy(POLICY_PATH)

        self.assertEqual(policy["verifier"], "scripts/verify_public_semantic_naming.py")
        self.assertTrue((REPO_ROOT / policy["verifier"]).is_file())


if __name__ == "__main__":
    unittest.main()
