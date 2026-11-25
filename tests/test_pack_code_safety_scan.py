"""
Pack code-safety scan: which dynamic-execution calls a pack's Python members may hold.

The scan is a plain byte search over every ``.py`` member. These tests pin what it
rejects (every spelling of the two calls, including whitespace before the parenthesis,
and every superset input a plain search would catch), what it leaves alone, and that the
scan's own source does not spell the calls out, because a source scanner would read the
deny-list as the code it denies.
"""

import io
import os
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from services.packs.pack_archive import PackArchive, PackError
from services.registry_quarantine import QuarantineState, RegistryQuarantineStore

PACK_ARCHIVE_SOURCE = (
    Path(__file__).resolve().parents[1] / "services" / "packs" / "pack_archive.py"
)
CALL_SYNTAX = re.compile(r"\b(?:exec|eval)\s*\(")


def _archive(members):
    """An in-memory zip whose members are stored in the given (insertion) order."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    buffer.seek(0)
    return zipfile.ZipFile(buffer, "r")


def _scan(members):
    with _archive(members) as zf:
        PackArchive._check_code_safety(zf)


class PackCodeSafetyScanTests(unittest.TestCase):
    def assert_rejected(self, members, filename, call):
        with self.assertRaises(PackError) as caught:
            _scan(members)
        self.assertEqual(
            str(caught.exception),
            f"Code safety violation in {filename}: Dynamic execution ({call})",
        )

    def test_unspaced_calls_are_rejected_with_their_own_message(self):
        for source, call in ((b"exec('x')", "exec"), (b"eval('x')", "eval")):
            with self.subTest(source=source):
                self.assert_rejected({"module.py": source}, "module.py", call)

    def test_whitespace_before_the_parenthesis_is_rejected(self):
        variants = (
            (b"exec ('x')", "exec"),
            (b"exec  ('x')", "exec"),
            (b"exec\t('x')", "exec"),
            (b"eval ('x')", "eval"),
            (b"eval\t('x')", "eval"),
            (b"eval \t ('x')", "eval"),
        )
        for source, call in variants:
            with self.subTest(source=source):
                self.assert_rejected({"module.py": source}, "module.py", call)

    def test_everything_a_plain_search_rejects_stays_rejected(self):
        # No word boundary on purpose: a longer name, an attribute call, and the text
        # inside a string or a comment all contain the call and were rejected before.
        supersets = (
            (b"myexec('x')", "exec"),
            (b"subexec(1)", "exec"),
            (b"model.eval()", "eval"),
            (b"self.eval(batch)", "eval"),
            (b"note = \"never exec('x') here\"", "exec"),
            (b"# eval('x') is not allowed", "eval"),
        )
        for source, call in supersets:
            with self.subTest(source=source):
                self.assert_rejected({"module.py": source}, "module.py", call)

    def test_exec_is_reported_before_eval_whichever_comes_first_in_the_file(self):
        for source in (b"eval('a')\nexec('b')", b"exec('a')\neval('b')"):
            with self.subTest(source=source):
                self.assert_rejected({"module.py": source}, "module.py", "exec")

    def test_the_first_offending_member_in_archive_order_is_reported(self):
        members = {"first.py": b"eval('a')", "second.py": b"exec('b')"}
        self.assert_rejected(members, "first.py", "eval")

    def test_an_offending_member_after_clean_ones_is_still_found(self):
        members = {"clean.py": b"print(1)", "later.py": b"exec ('x')"}
        self.assert_rejected(members, "later.py", "exec")

    def test_members_that_are_not_python_files_are_not_scanned(self):
        _scan({"notes.txt": b"exec('x')", "config.json": b"eval ('x')"})

    def test_code_without_the_calls_passes(self):
        source = b"\n".join(
            (
                b"import os",
                b"import subprocess",
                b"from os import path",
                b"value = __import__('json')",
                b"label = 'exec'",
                b"other = 'eval'",
                b"execute(1)",
                b"evaluate (2)",
                b"executor (3)",
            )
        )
        _scan({"module.py": source})

    def test_the_scan_source_does_not_spell_the_calls_out(self):
        text = PACK_ARCHIVE_SOURCE.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            self.assertIsNone(
                CALL_SYNTAX.search(line),
                f"pack_archive.py line {number} spells out a call: {line.strip()}",
            )


class RegistryStrictPolicyInheritsTheScanTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir)
        self.store_dir = os.path.join(self.test_dir, "store")
        os.makedirs(self.store_dir, exist_ok=True)
        environment = patch.dict(
            os.environ,
            {
                "OPENCLAW_ENABLE_REGISTRY_SYNC": "1",
                "OPENCLAW_REGISTRY_POLICY": "strict",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_strict_policy_quarantines_a_pack_with_a_spaced_call(self):
        path = os.path.join(self.test_dir, "spaced.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("spaced.py", b"exec ('bad')")

        store = RegistryQuarantineStore(self.store_dir)
        store.register_fetch("spaced-pack", "v1", "http://u", "hash", signature="sig")
        accepted = store.verify_integrity("spaced-pack", "v1", "hash", file_path=path)

        self.assertFalse(accepted)
        entry = store.get_entry("spaced-pack", "v1")
        self.assertEqual(entry.state, QuarantineState.QUARANTINED.value)
        failures = [a for a in entry.audit_trail if a["action"] == "preflight_failed"]
        self.assertEqual(len(failures), 1)
        self.assertIn("Dynamic execution (exec)", failures[0]["detail"])


if __name__ == "__main__":
    unittest.main()
