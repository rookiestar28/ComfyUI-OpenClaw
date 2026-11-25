"""
Connector configuration: how the Slack allow-lists are read from the environment.

Both lists are comma-separated, trimmed, and stripped of empty items; an unset or empty
variable leaves the empty default. The loader must not name a local ``su``: a source
scanner reads that bare token as the Unix privilege-escalation command.
"""

import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from connector.config import load_config

USERS = "OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS"
CHANNELS = "OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS"
CONFIG_SOURCE = Path(__file__).resolve().parents[1] / "connector" / "config.py"
BARE_SU = re.compile(r"\bsu\b")


def _load(users=None, channels=None):
    """Load the config with exactly these two variables set (None means unset)."""
    with patch.dict(os.environ):
        os.environ.pop(USERS, None)
        os.environ.pop(CHANNELS, None)
        if users is not None:
            os.environ[USERS] = users
        if channels is not None:
            os.environ[CHANNELS] = channels
        return load_config()


class SlackAllowlistEnvTests(unittest.TestCase):
    def test_items_are_split_on_commas_and_trimmed(self):
        config = _load(users="U1, U2 ,U3", channels=" C1,C2 ")

        self.assertEqual(config.slack_allowed_users, ["U1", "U2", "U3"])
        self.assertEqual(config.slack_allowed_channels, ["C1", "C2"])

    def test_empty_items_are_dropped(self):
        config = _load(users="U1,, ,U2,", channels=",C1,,")

        self.assertEqual(config.slack_allowed_users, ["U1", "U2"])
        self.assertEqual(config.slack_allowed_channels, ["C1"])

    def test_unset_and_empty_variables_leave_the_empty_default(self):
        for users, channels in ((None, None), ("", "")):
            with self.subTest(users=users, channels=channels):
                config = _load(users=users, channels=channels)

                self.assertEqual(config.slack_allowed_users, [])
                self.assertEqual(config.slack_allowed_channels, [])

    def test_whitespace_only_items_give_an_empty_list(self):
        config = _load(users="  ,  ", channels=" , ,")

        self.assertEqual(config.slack_allowed_users, [])
        self.assertEqual(config.slack_allowed_channels, [])

    def test_the_two_lists_are_independent(self):
        only_users = _load(users="U1")
        self.assertEqual(only_users.slack_allowed_users, ["U1"])
        self.assertEqual(only_users.slack_allowed_channels, [])

        only_channels = _load(channels="C1")
        self.assertEqual(only_channels.slack_allowed_users, [])
        self.assertEqual(only_channels.slack_allowed_channels, ["C1"])

    def test_the_loader_source_has_no_bare_su_token(self):
        text = CONFIG_SOURCE.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            self.assertIsNone(
                BARE_SU.search(line),
                f"connector/config.py line {number} has a bare su token: {line.strip()}",
            )


if __name__ == "__main__":
    unittest.main()
