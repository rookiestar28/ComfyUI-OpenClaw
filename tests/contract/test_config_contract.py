import os
import unittest
from unittest.mock import MagicMock, patch


class ConfigContractTests(unittest.TestCase):
    # Contract: Config Precedence
    def test_config_precedence(self):
        """
        Contract: OPENCLAW_* env vars > MOLTBOT_* env vars > runtime override > file config > defaults.
        """
        # Mocking json load to avoid file I/O dependencies
        mock_json_load = MagicMock(return_value={})

        with (
            patch("builtins.open", new_callable=MagicMock),
            patch("json.load", mock_json_load),
            patch("os.path.exists", return_value=True),
        ):

            from services.runtime_config import get_effective_config

            with patch.dict(
                os.environ,
                {
                    "OPENCLAW_LLM_PROVIDER": "openai",
                    "MOLTBOT_LLM_PROVIDER": "anthropic",
                },
            ):
                config, sources = get_effective_config()
                self.assertEqual(config["provider"], "openai")
                # services/runtime_config.py records the raw source type for env overrides.
                self.assertEqual(sources["provider"], "env")

    def test_runtime_override_precedence_without_env(self):
        """
        Contract: runtime override wins over persisted/default when env overrides are absent.
        """
        from services.runtime_config import (
            clear_runtime_overrides,
            get_effective_config,
            set_runtime_overrides,
        )

        clear_runtime_overrides()
        ok, errors = set_runtime_overrides({"provider": "openrouter"})
        self.assertIs(ok, True)
        self.assertEqual(errors, [])
        try:
            config, sources = get_effective_config()
            self.assertEqual(config["provider"], "openrouter")
            self.assertEqual(sources["provider"], "runtime_override")
        finally:
            clear_runtime_overrides()

    # Contract: Secret Safety
    def test_secrets_never_exposed(self):
        """
        Contract: get_effective_config() MUST NOT return api_key in plain text.
        Contract: __str__ or __repr__ of config objects MUST NOT leak secrets.
        """
        # Mock json load
        mock_json_load = MagicMock(return_value={})

        with (
            patch("builtins.open", new_callable=MagicMock),
            patch("json.load", mock_json_load),
            patch("os.path.exists", return_value=True),
        ):

            from services.runtime_config import get_effective_config

            DUMMY_KEY = "sk-danger-12345"

            with patch.dict(os.environ, {"OPENCLAW_API_KEY": DUMMY_KEY}):
                config, _ = get_effective_config()

                # 1. Verify key is NOT in the returned config dict (runtime_config filters it)
                self.assertNotIn("api_key", config)

                # 2. Verify key is NOT in string representation of the config dict
                self.assertNotIn(DUMMY_KEY, str(config))

                # 3. Verify key is NOT in repr
                self.assertNotIn(DUMMY_KEY, repr(config))


if __name__ == "__main__":
    unittest.main()
