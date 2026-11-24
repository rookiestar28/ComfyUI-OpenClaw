"""What the real-host lane prints when one of its own subprocesses fails.

The lane runs git, pip and helpers through ``_run``. Before this contract a failure
printed only the command and its exit status, so a failed dependency install left
no cause in the (public) CI log. These tests drive the real ``_run`` and ``main``
with synthetic children started from ``sys.executable``: no network, no real pip,
no host. Every secret-shaped value below is invented for the test.

A child receives the text it must print through its environment, never through its
command line. The failure message used to echo the command, so a cause passed as an
argument would have appeared in it for the wrong reason.
"""

import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from collections.abc import Iterator
from pathlib import Path

from scripts import real_host_smoke
from scripts.real_host_smoke import AUTHORIZATION_ENV, SmokeError, _run, main

PREFIX = "REAL-HOST-SMOKE-FAIL: "
CAUSE = "synthetic-cause: the index refused the request"
CONTEXT = "context-marker"

CHILD_PRINTING_ITS_ENVIRONMENT = (
    "import os, sys, time\n"
    "sys.stdout.write(os.environ.get('SYNTHETIC_STDOUT', ''))\n"
    "sys.stderr.write(os.environ.get('SYNTHETIC_STDERR', ''))\n"
    "sys.stdout.flush()\n"
    "sys.stderr.flush()\n"
    "if os.environ.get('SYNTHETIC_HANG') == '1':\n"
    "    time.sleep(120)\n"
    "sys.exit(int(os.environ.get('SYNTHETIC_EXIT', '1')))\n"
)
CHILD_WRITING_NUMBERED_LINES = (
    "import sys\n"
    "for number in range(1, int(sys.argv[1]) + 1):\n"
    "    sys.stderr.write('L%03d\\n' % number)\n"
    "sys.exit(1)\n"
)
CHILD_WRITING_ONE_LONG_LINE = (
    "import sys\n" "sys.stderr.write('Z' * 9000 + 'END-MARKER\\n')\n" "sys.exit(1)\n"
)
CHILD_WRITING_LONG_LINES_TO_BOTH_STREAMS = (
    "import sys\n"
    "sys.stdout.write('Z' * 9000 + 'OUT-END\\n')\n"
    "sys.stderr.write('Y' * 9000 + 'ERR-END\\n')\n"
    "sys.exit(1)\n"
)
CHILD_WRITING_HOSTILE_INPUT = (
    "import sys\n"
    "sys.stderr.write('a' * 400000 + ' ' + 'http://' * 20000 + ' ' + 'token' * 20000 + '\\n')\n"
    "sys.exit(1)\n"
)
CHILD_SUCCEEDING_WITH_OUTPUT = (
    "import sys\n"
    "sys.stdout.write('stdout-text\\n')\n"
    "sys.stderr.write('stderr-text\\n')\n"
)


def _child(source: str, *arguments: str) -> list[str]:
    return [sys.executable, "-c", source, *arguments]


@contextlib.contextmanager
def _child_output(
    *, stderr: str = "", stdout: str = "", exit_code: int = 1, hang: bool = False
) -> Iterator[None]:
    values = {
        "SYNTHETIC_STDERR": stderr,
        "SYNTHETIC_STDOUT": stdout,
        "SYNTHETIC_EXIT": str(exit_code),
        "SYNTHETIC_HANG": "1" if hang else "0",
    }
    with unittest.mock.patch.dict(os.environ, values):
        yield


def _printed_failure(command: list[str], *, timeout: float = 60.0) -> str:
    """The exact line ``main`` prints when ``_run`` raises for this command."""
    try:
        _run(command, timeout=timeout)
    except (SmokeError, subprocess.SubprocessError) as exc:
        return f"{PREFIX}{exc}"
    raise AssertionError("the command was expected to fail")


def _printed_failure_of_child(
    *,
    stderr: str = "",
    stdout: str = "",
    exit_code: int = 1,
    hang: bool = False,
    timeout: float = 60.0,
) -> str:
    with _child_output(stderr=stderr, stdout=stdout, exit_code=exit_code, hang=hang):
        return _printed_failure(_child(CHILD_PRINTING_ITS_ENVIRONMENT), timeout=timeout)


def _main_printing(side_effect: object) -> tuple[int, str]:
    """Drive ``main`` through the real authorization and workspace checks."""
    output = io.StringIO()
    with (
        tempfile.TemporaryDirectory() as workspace,
        unittest.mock.patch.dict(os.environ, {AUTHORIZATION_ENV: "1"}),
        unittest.mock.patch.object(
            real_host_smoke, "run_lane", side_effect=side_effect
        ),
        contextlib.redirect_stdout(output),
    ):
        code = main(["--subject", "bundled", "--workspace", workspace])
    return code, output.getvalue()


class TestFailureCauseReachesThePrintedLine(unittest.TestCase):
    def test_the_stderr_cause_of_a_failed_command_is_printed(self):
        printed = _printed_failure_of_child(stderr=CAUSE)
        self.assertIn(CAUSE, printed)

    def test_the_stdout_cause_of_a_failed_command_is_printed(self):
        printed = _printed_failure_of_child(stdout=CAUSE)
        self.assertIn(CAUSE, printed)

    def test_main_prints_the_cause_keeps_the_prefix_and_exits_with_one(self):
        with _child_output(stderr=CAUSE):
            code, output = _main_printing(
                lambda *_a, **_k: _run(
                    _child(CHILD_PRINTING_ITS_ENVIRONMENT), timeout=60
                )
            )
        self.assertEqual(code, 1)
        self.assertTrue(output.startswith(PREFIX), output)
        self.assertEqual(output.count(PREFIX), 1)
        self.assertIn(CAUSE, output)

    def test_the_exit_status_is_still_named(self):
        printed = _printed_failure_of_child(stderr=CAUSE, exit_code=7)
        self.assertIn("exit status 7", printed)

    def test_streams_are_labelled_and_stderr_comes_before_stdout(self):
        printed = _printed_failure_of_child(
            stdout="from-stdout-marker", stderr="from-stderr-marker"
        )
        self.assertIn("stderr", printed)
        self.assertIn("stdout", printed)
        self.assertLess(printed.index("stderr"), printed.index("stdout"))
        self.assertLess(
            printed.index("from-stderr-marker"), printed.index("from-stdout-marker")
        )

    def test_a_command_with_no_output_says_so(self):
        printed = _printed_failure_of_child(exit_code=3)
        self.assertIn("exit status 3", printed)
        self.assertIn("no output", printed)
        self.assertNotIn("stderr", printed)
        self.assertNotIn("stdout", printed)

    def test_the_wrapped_error_keeps_the_original_failure_as_its_cause(self):
        with _child_output(stderr=CAUSE, exit_code=5):
            with self.assertRaises(SmokeError) as raised:
                _run(_child(CHILD_PRINTING_ITS_ENVIRONMENT), timeout=60)
        cause = raised.exception.__cause__
        self.assertIsInstance(cause, subprocess.CalledProcessError)
        self.assertEqual(cause.returncode, 5)


class TestTheTailIsBounded(unittest.TestCase):
    def test_only_the_last_forty_lines_of_a_stream_are_kept(self):
        printed = _printed_failure(_child(CHILD_WRITING_NUMBERED_LINES, "100"))
        self.assertIn("L100", printed)
        self.assertIn("L061", printed)
        self.assertNotIn("L060", printed)
        self.assertNotIn("L001", printed)
        self.assertEqual(len(re.findall(r"L\d{3}", printed)), 40)

    def test_a_short_stream_is_kept_whole(self):
        printed = _printed_failure(_child(CHILD_WRITING_NUMBERED_LINES, "5"))
        for number in range(1, 6):
            self.assertIn("L%03d" % number, printed)

    def test_trailing_blank_lines_do_not_use_up_the_window(self):
        printed = _printed_failure_of_child(stderr="first-line\n" + "\n" * 50)
        self.assertIn("first-line", printed)

    def test_one_very_long_line_keeps_only_its_last_four_thousand_ninety_six_characters(
        self,
    ):
        printed = _printed_failure(_child(CHILD_WRITING_ONE_LONG_LINE))
        self.assertIn("END-MARKER", printed)
        self.assertLessEqual(printed.count("Z"), 4096)
        self.assertGreater(printed.count("Z"), 4000)
        self.assertLess(len(printed), 4096 + 600)

    def test_each_stream_has_its_own_bound(self):
        printed = _printed_failure(_child(CHILD_WRITING_LONG_LINES_TO_BOTH_STREAMS))
        self.assertIn("OUT-END", printed)
        self.assertIn("ERR-END", printed)
        self.assertLessEqual(printed.count("Z"), 4096)
        self.assertLessEqual(printed.count("Y"), 4096)
        self.assertGreater(printed.count("Z"), 4000)
        self.assertGreater(printed.count("Y"), 4000)
        self.assertLess(len(printed), 2 * 4096 + 800)

    def test_a_cut_stream_says_that_earlier_output_was_omitted(self):
        cut = _printed_failure(_child(CHILD_WRITING_NUMBERED_LINES, "100"))
        whole = _printed_failure(_child(CHILD_WRITING_NUMBERED_LINES, "5"))
        self.assertIn("omitted", cut)
        self.assertNotIn("omitted", whole)

    def test_tail_lines_are_indented_so_none_can_start_a_workflow_command(self):
        printed = _printed_failure_of_child(stderr="::add-mask::synthetic-value")
        self.assertIn("::add-mask::", printed)
        self.assertFalse(
            [line for line in printed.splitlines() if line.startswith("::")], printed
        )


class TestTimeouts(unittest.TestCase):
    def test_a_timed_out_command_prints_its_cause_and_the_budget(self):
        printed = _printed_failure_of_child(stderr=CAUSE, hang=True, timeout=5)
        self.assertIn("timed out after 5", printed)
        self.assertIn(CAUSE, printed)

    def test_a_timeout_through_main_keeps_the_prefix_and_exit_code(self):
        expired = subprocess.TimeoutExpired(
            ["tool", "subcommand"], 5, output=b"", stderr=CAUSE.encode()
        )
        with unittest.mock.patch.object(
            real_host_smoke.subprocess, "run", side_effect=expired
        ):
            code, output = _main_printing(
                lambda *_a, **_k: _run(["tool", "subcommand"], timeout=5)
            )
        self.assertEqual(code, 1)
        self.assertTrue(output.startswith(PREFIX), output)
        self.assertIn(CAUSE, output)


class TestStreamTypes(unittest.TestCase):
    """``TimeoutExpired`` carries bytes for captured output even in text mode."""

    command = ["tool", "subcommand"]

    def _failure_from(self, error: BaseException) -> str:
        with unittest.mock.patch.object(
            real_host_smoke.subprocess, "run", side_effect=error
        ):
            try:
                _run(self.command, timeout=9)
            except SmokeError as exc:
                return f"{PREFIX}{exc}"
        raise AssertionError("the patched run was expected to fail")

    def test_timeout_with_bytes_streams(self):
        printed = self._failure_from(
            subprocess.TimeoutExpired(
                self.command,
                9,
                output=b"bytes-out-marker\n",
                stderr=b"bytes-err-marker\n",
            )
        )
        self.assertIn("bytes-out-marker", printed)
        self.assertIn("bytes-err-marker", printed)
        self.assertNotIn("b'", printed)

    def test_timeout_with_text_streams(self):
        printed = self._failure_from(
            subprocess.TimeoutExpired(
                self.command, 9, output="text-out-marker\n", stderr="text-err-marker\n"
            )
        )
        self.assertIn("text-out-marker", printed)
        self.assertIn("text-err-marker", printed)

    def test_timeout_with_no_captured_output(self):
        printed = self._failure_from(subprocess.TimeoutExpired(self.command, 9))
        self.assertIn("timed out after 9", printed)
        self.assertIn("no output", printed)

    def test_failure_with_bytes_streams_and_invalid_utf8(self):
        printed = self._failure_from(
            subprocess.CalledProcessError(
                4, self.command, output=b"ok-bytes\n", stderr=b"bad-\xff\xfe-bytes\n"
            )
        )
        self.assertIn("exit status 4", printed)
        self.assertIn("ok-bytes", printed)
        self.assertIn("bad-", printed)
        self.assertIn("-bytes", printed)

    def test_failure_with_a_missing_stream(self):
        printed = self._failure_from(
            subprocess.CalledProcessError(
                2, self.command, output=None, stderr="only-err\n"
            )
        )
        self.assertIn("only-err", printed)
        self.assertNotIn("None", printed)


class TestTheCommandIsNamedWithoutItsArguments(unittest.TestCase):
    def _message_for(self, command: list[str]) -> str:
        failure = subprocess.CalledProcessError(
            1, command, output="", stderr="diagnostic\n"
        )
        with unittest.mock.patch.object(
            real_host_smoke.subprocess, "run", side_effect=failure
        ):
            with self.assertRaises(SmokeError) as raised:
                _run(command, timeout=9)
        return str(raised.exception)

    def test_program_and_subcommand_only(self):
        message = self._message_for(
            [
                "git",
                "-C",
                "/work/synthetic-workspace/ComfyUI",
                "fetch",
                "--depth",
                "1",
                "origin",
                "0123abcd",
            ]
        )
        self.assertIn("git fetch", message)
        self.assertNotIn("synthetic-workspace", message)
        self.assertNotIn("0123abcd", message)
        self.assertNotIn("--depth", message)

    def test_a_credential_in_an_argument_never_reaches_the_message(self):
        message = self._message_for(
            [
                "git",
                "remote",
                "add",
                "origin",
                "https://synthetic-user:synthetic-pw-1@example.invalid/r.git",
            ]
        )
        self.assertIn("git remote add", message)
        self.assertNotIn("synthetic-pw-1", message)
        self.assertNotIn("synthetic-user", message)
        self.assertNotIn("example.invalid", message)

    def test_a_module_run_is_named_without_the_interpreter_or_requirements_path(self):
        message = self._message_for(
            [
                "C:\\Users\\synthetic-person\\venv\\Scripts\\python.exe",
                "-m",
                "pip",
                "install",
                "-r",
                "C:\\Users\\synthetic-person\\requirements.txt",
            ]
        )
        self.assertIn("python -m pip install", message)
        self.assertNotIn("synthetic-person", message)
        self.assertNotIn("requirements.txt", message)

    def test_inline_code_is_never_echoed(self):
        message = self._message_for(_child("print('inline-code-marker')"))
        self.assertNotIn("inline-code-marker", message)
        self.assertIn("diagnostic", message)


class TestSecretsAreRedactedFromEveryTail(unittest.TestCase):
    def _printed_for(self, line: str, *, stream: str = "stderr") -> str:
        printed = _printed_failure_of_child(**{stream: f"{CONTEXT} {line}"})
        # The tail must be shown at all, otherwise an absent secret would prove nothing.
        self.assertIn(CONTEXT, printed)
        return printed

    def test_credentials_in_a_url(self):
        printed = self._printed_for(
            "fatal: unable to access "
            "https://synthetic-user:synthetic-pw-2@example.invalid/org/repo.git/"
        )
        self.assertNotIn("synthetic-pw-2", printed)
        self.assertNotIn("synthetic-user", printed)
        self.assertIn("https://***@example.invalid/org/repo.git/", printed)

    def test_a_user_only_url_credential(self):
        printed = self._printed_for(
            "fetching https://synthetic-user-token@example.invalid/x"
        )
        self.assertNotIn("synthetic-user-token", printed)
        self.assertIn("https://***@example.invalid/x", printed)

    def test_token_values(self):
        for line, secret in (
            ("index token=synthetic-token-1 failed", "synthetic-token-1"),
            ("access_token: synthetic-token-2", "synthetic-token-2"),
            ('{"token": "synthetic-token-3"}', "synthetic-token-3"),
            ("TOKEN = 'synthetic token 4'", "synthetic token 4"),
            ("GET /simple/?token=synthetic-token-5&page=2", "synthetic-token-5"),
        ):
            with self.subTest(line=line):
                printed = self._printed_for(line)
                self.assertNotIn(secret, printed)
                self.assertIn("***", printed)

    def test_the_query_after_a_token_value_is_kept(self):
        printed = self._printed_for("GET /simple/?token=synthetic-token-6&page=2")
        self.assertNotIn("synthetic-token-6", printed)
        self.assertIn("&page=2", printed)

    def test_password_values(self):
        for line, secret in (
            ("password=synthetic-password-1", "synthetic-password-1"),
            ("PIP_PASSWORD: synthetic-password-2", "synthetic-password-2"),
            ('"password": "synthetic-password-3"', "synthetic-password-3"),
            ("Password = 'synthetic password 4'", "synthetic password 4"),
        ):
            with self.subTest(line=line):
                printed = self._printed_for(line)
                self.assertNotIn(secret, printed)
                self.assertIn("***", printed)

    def test_authorization_headers(self):
        for line, secret in (
            ("Authorization: Bearer synthetic-bearer-1", "synthetic-bearer-1"),
            ("authorization: Basic synthetic-basic-1", "synthetic-basic-1"),
            ('"Authorization": "token synthetic-auth-1"', "synthetic-auth-1"),
        ):
            with self.subTest(line=line):
                printed = self._printed_for(line)
                self.assertNotIn(secret, printed)
                self.assertIn("***", printed)

    def test_a_bearer_value_outside_a_header(self):
        printed = self._printed_for(
            "retrying with Bearer synthetic-bearer-2 against the index"
        )
        self.assertNotIn("synthetic-bearer-2", printed)
        self.assertIn("against the index", printed)

    def test_the_current_user_home_directory_becomes_a_tilde(self):
        home = Path.home()
        printed = self._printed_for(f"cannot write {home}{os.sep}cache{os.sep}pip")
        self.assertNotIn(str(home), printed)
        self.assertIn(f"~{os.sep}cache{os.sep}pip", printed)

    def test_a_synthetic_home_in_each_spelling(self):
        synthetic = (
            Path("C:\\Users\\synthetic-person")
            if os.name == "nt"
            else Path("/home/synthetic-person")
        )
        spellings = [
            str(synthetic),
            synthetic.as_posix(),
            str(synthetic).replace("\\", "\\\\"),
        ]
        with unittest.mock.patch.object(Path, "home", return_value=synthetic):
            for spelling in spellings:
                with self.subTest(spelling=spelling):
                    printed = self._printed_for(f"open {spelling}/cache failed")
                    self.assertNotIn("synthetic-person", printed)
                    self.assertIn("~", printed)

    @unittest.skipUnless(
        os.name == "nt", "path comparison is case-insensitive on Windows"
    )
    def test_the_home_directory_is_matched_without_regard_to_case_on_windows(self):
        with unittest.mock.patch.object(
            Path, "home", return_value=Path("C:\\Users\\Synthetic-Person")
        ):
            printed = self._printed_for(
                "open c:\\users\\SYNTHETIC-person\\cache failed"
            )
        self.assertNotIn("synthetic-person", printed.lower())

    def test_a_drive_root_is_never_treated_as_a_home_directory(self):
        root = Path(Path.cwd().anchor)
        with unittest.mock.patch.object(Path, "home", return_value=root):
            printed = self._printed_for(f"path {root} and {root}stays-readable")
        self.assertIn(f"path {root} and {root}stays-readable", printed)

    def test_a_longer_name_that_starts_like_the_home_directory_is_left_alone(self):
        synthetic = (
            Path("C:\\Users\\synthetic-person")
            if os.name == "nt"
            else Path("/home/synthetic-person")
        )
        with unittest.mock.patch.object(Path, "home", return_value=synthetic):
            printed = self._printed_for(
                f"open {synthetic}-other{os.sep}cache and {synthetic}{os.sep}cache"
            )
        self.assertIn(f"{synthetic}-other{os.sep}cache", printed)
        self.assertIn(f"~{os.sep}cache", printed)

    def test_several_secrets_on_one_line(self):
        printed = self._printed_for(
            "GET https://synthetic-user:synthetic-pw-3@example.invalid/"
            "?token=synthetic-token-7 Authorization: Bearer synthetic-bearer-3"
        )
        for secret in (
            "synthetic-pw-3",
            "synthetic-user",
            "synthetic-token-7",
            "synthetic-bearer-3",
        ):
            self.assertNotIn(secret, printed)

    def test_redaction_covers_stdout_too(self):
        printed = self._printed_for("password=synthetic-password-5", stream="stdout")
        self.assertNotIn("synthetic-password-5", printed)

    def test_a_secret_cut_by_the_character_bound_is_still_redacted(self):
        # Truncating first would keep the tail of a long secret without the key
        # that identifies it. The redaction has to see the whole stream.
        printed = _printed_failure_of_child(
            stderr="token=" + "Q" * 6000 + "\nafter-marker\n"
        )
        self.assertIn("after-marker", printed)
        self.assertNotIn("QQQQ", printed)

    def test_ordinary_diagnostics_stay_readable(self):
        printed = self._printed_for(
            "ERROR: Could not find a version that satisfies the requirement "
            "synthetic-package==9.9 (tokenizer=fast)"
        )
        self.assertIn(
            "Could not find a version that satisfies the requirement "
            "synthetic-package==9.9",
            printed,
        )
        self.assertIn("tokenizer=fast", printed)

    def test_redaction_stays_linear_on_long_hostile_input(self):
        started = time.monotonic()
        printed = _printed_failure(_child(CHILD_WRITING_HOSTILE_INPUT), timeout=60)
        self.assertIn(PREFIX, printed)
        self.assertLess(time.monotonic() - started, 30)


class TestSuccessIsUnchanged(unittest.TestCase):
    def test_a_successful_command_returns_its_output_and_prints_nothing(self):
        printed_out, printed_err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(printed_out),
            contextlib.redirect_stderr(printed_err),
        ):
            result = _run(_child(CHILD_SUCCEEDING_WITH_OUTPUT), timeout=60)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "stdout-text")
        self.assertEqual(result.stderr.strip(), "stderr-text")
        self.assertEqual(printed_out.getvalue(), "")
        self.assertEqual(printed_err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
