from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from services.env_aliases import (
    ENV_ALIAS_REGISTRY,
    LEGACY_MOLTBOT_ENV_KEYS,
    REJECTED_LEGACY_ENV_KEYS,
    SUPPORTED_CLAWDBOT_ENV_KEYS,
    EnvLookupMode,
    get_env_value,
    reset_warning_state_for_tests,
    resolve_env,
)


class TestLegacyEnvironmentAliasReproduction(unittest.TestCase):
    def test_central_resolver_contract_exists(self):
        self.assertTrue(callable(get_env_value))

    def test_repository_policy_declares_environment_alias_contract(self):
        repo_root = Path(__file__).resolve().parents[1]
        policy = json.loads(
            (repo_root / "tests" / "architecture_dependency_policy.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn("environment_alias_contract", policy)

    def test_operator_doctor_state_dir_prefers_canonical_when_both_are_set(self):
        from services.operator_doctor import DoctorReport, check_state_dir

        with (
            tempfile.TemporaryDirectory() as canonical_dir,
            tempfile.TemporaryDirectory() as legacy_dir,
        ):
            with patch.dict(
                os.environ,
                {
                    "OPENCLAW_STATE_DIR": canonical_dir,
                    "MOLTBOT_STATE_DIR": legacy_dir,
                },
                clear=False,
            ):
                report = DoctorReport()
                check_state_dir(report)

        result = report.checks[-1]
        self.assertEqual(result.name, "state_dir")
        self.assertEqual(result.severity, "pass")
        self.assertIn(canonical_dir, result.message)
        self.assertNotIn(legacy_dir, result.message)


class TestLegacyEnvironmentAliasResolver(unittest.TestCase):
    def setUp(self) -> None:
        reset_warning_state_for_tests()

    def test_registry_is_exact_immutable_and_excludes_rejected_marker(self):
        self.assertEqual(len(LEGACY_MOLTBOT_ENV_KEYS), 86)
        self.assertEqual(len(ENV_ALIAS_REGISTRY), 86)
        self.assertEqual(SUPPORTED_CLAWDBOT_ENV_KEYS, {"CLAWDBOT_LLM_API_KEY"})
        self.assertEqual(REJECTED_LEGACY_ENV_KEYS, {"CLAWDBOT_GATEWAY_TOKEN"})
        self.assertEqual(
            ENV_ALIAS_REGISTRY["OPENCLAW_LLM_API_KEY"].aliases,
            ("MOLTBOT_LLM_API_KEY", "CLAWDBOT_LLM_API_KEY"),
        )
        all_aliases = {
            alias for spec in ENV_ALIAS_REGISTRY.values() for alias in spec.aliases
        }
        self.assertNotIn("CLAWDBOT_GATEWAY_TOKEN", all_aliases)
        with self.assertRaises(TypeError):
            ENV_ALIAS_REGISTRY["OPENCLAW_NEW"] = object()  # type: ignore[index]

    def test_presence_mode_preserves_empty_canonical_override(self):
        result = resolve_env(
            "OPENCLAW_ADMIN_TOKEN",
            mode=EnvLookupMode.PRESENCE,
            env={"OPENCLAW_ADMIN_TOKEN": "", "MOLTBOT_ADMIN_TOKEN": "legacy"},
        )

        self.assertEqual(result.value, "")
        self.assertEqual(result.selected_key, "OPENCLAW_ADMIN_TOKEN")
        self.assertFalse(result.used_legacy)

    def test_nonempty_mode_falls_through_empty_canonical(self):
        result = resolve_env(
            "OPENCLAW_ADMIN_TOKEN",
            mode=EnvLookupMode.NONEMPTY,
            env={"OPENCLAW_ADMIN_TOKEN": "", "MOLTBOT_ADMIN_TOKEN": "legacy"},
        )

        self.assertEqual(result.value, "legacy")
        self.assertEqual(result.selected_key, "MOLTBOT_ADMIN_TOKEN")
        self.assertTrue(result.used_legacy)

    def test_truthy_any_preserves_ordered_flag_behavior(self):
        result = resolve_env(
            "OPENCLAW_LOG_TRUNCATE_ON_START",
            mode=EnvLookupMode.TRUTHY_ANY,
            env={
                "OPENCLAW_LOG_TRUNCATE_ON_START": "0",
                "MOLTBOT_LOG_TRUNCATE_ON_START": "yes",
            },
        )

        self.assertEqual(result.value, "yes")
        self.assertEqual(result.selected_key, "MOLTBOT_LOG_TRUNCATE_ON_START")

    def test_absent_key_returns_default_without_provenance(self):
        result = resolve_env(
            "OPENCLAW_ADMIN_TOKEN",
            default="fallback",
            env={},
        )

        self.assertEqual(result.value, "fallback")
        self.assertIsNone(result.selected_key)
        self.assertFalse(result.used_legacy)

    def test_supported_clawdbot_key_has_third_precedence(self):
        result = resolve_env(
            "OPENCLAW_LLM_API_KEY",
            env={"CLAWDBOT_LLM_API_KEY": "older"},
        )

        self.assertEqual(result.value, "older")
        self.assertEqual(result.selected_key, "CLAWDBOT_LLM_API_KEY")
        self.assertTrue(result.used_legacy)

    def test_winning_legacy_key_warns_once_without_value_disclosure(self):
        secret_canary = "DO-NOT-LOG-SECRET-CANARY"
        test_logger = logging.getLogger("tests.env-alias.alias-warning")

        with self.assertLogs(test_logger, level="WARNING") as captured:
            for _ in range(4):
                self.assertEqual(
                    get_env_value(
                        "OPENCLAW_ADMIN_TOKEN",
                        env={"MOLTBOT_ADMIN_TOKEN": secret_canary},
                        warn_legacy=True,
                        target_logger=test_logger,
                    ),
                    secret_canary,
                )

        self.assertEqual(len(captured.output), 1)
        warning = captured.output[0]
        self.assertIn("OPENCLAW_LEGACY_ENV_ALIAS_USED", warning)
        self.assertIn("MOLTBOT_ADMIN_TOKEN", warning)
        self.assertIn("OPENCLAW_ADMIN_TOKEN", warning)
        self.assertNotIn(secret_canary, warning)
        self.assertNotIn(str(len(secret_canary)), warning)

    def test_concurrent_legacy_reads_still_warn_once(self):
        test_logger = logging.getLogger("tests.env-alias.alias-concurrency")

        with self.assertLogs(test_logger, level="WARNING") as captured:
            with ThreadPoolExecutor(max_workers=8) as executor:
                values = list(
                    executor.map(
                        lambda _index: get_env_value(
                            "OPENCLAW_STATE_DIR",
                            env={"MOLTBOT_STATE_DIR": "legacy-state"},
                            warn_legacy=True,
                            target_logger=test_logger,
                        ),
                        range(64),
                    )
                )

        self.assertEqual(values, ["legacy-state"] * 64)
        self.assertEqual(len(captured.output), 1)

    def test_canonical_absent_and_injected_mapping_defaults_are_silent(self):
        test_logger = logging.getLogger("tests.env-alias.alias-silence")

        with self.assertNoLogs(test_logger, level="WARNING"):
            self.assertEqual(
                get_env_value(
                    "OPENCLAW_STATE_DIR",
                    env={"OPENCLAW_STATE_DIR": "canonical"},
                    target_logger=test_logger,
                ),
                "canonical",
            )
            self.assertIsNone(
                get_env_value(
                    "OPENCLAW_STATE_DIR",
                    env={},
                    target_logger=test_logger,
                )
            )
            self.assertEqual(
                get_env_value(
                    "OPENCLAW_STATE_DIR",
                    env={"MOLTBOT_STATE_DIR": "legacy"},
                    target_logger=test_logger,
                ),
                "legacy",
            )

    def test_process_lookup_warns_once_and_canonical_winner_is_silent(self):
        test_logger = logging.getLogger("tests.env-alias.process-warning")
        secret_canary = "PROCESS-SECRET-CANARY"

        with patch.dict(
            os.environ,
            {"MOLTBOT_ADMIN_TOKEN": secret_canary},
            clear=True,
        ):
            with self.assertLogs(test_logger, level="WARNING") as captured:
                for _ in range(3):
                    self.assertEqual(
                        get_env_value(
                            "OPENCLAW_ADMIN_TOKEN", target_logger=test_logger
                        ),
                        secret_canary,
                    )

        self.assertEqual(len(captured.output), 1)
        self.assertNotIn(secret_canary, captured.output[0])

        reset_warning_state_for_tests()
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_ADMIN_TOKEN": "canonical",
                "MOLTBOT_ADMIN_TOKEN": secret_canary,
            },
            clear=True,
        ):
            with self.assertNoLogs(test_logger, level="WARNING"):
                self.assertEqual(
                    get_env_value("OPENCLAW_ADMIN_TOKEN", target_logger=test_logger),
                    "canonical",
                )

    def test_real_auth_seam_preserves_nonempty_legacy_fallback(self):
        from services.access_control import is_auth_configured

        with patch.dict(
            os.environ,
            {
                "OPENCLAW_ADMIN_TOKEN": "",
                "MOLTBOT_ADMIN_TOKEN": "legacy-token",
            },
            clear=True,
        ):
            self.assertTrue(is_auth_configured())

    def test_presence_helpers_preserve_empty_canonical_override(self):
        from services.deployment_profile import _env_get as deployment_env_get
        from services.webhook_auth import _env_get as webhook_env_get

        env = {
            "OPENCLAW_WEBHOOK_AUTH_MODE": "",
            "MOLTBOT_WEBHOOK_AUTH_MODE": "hmac",
        }
        self.assertEqual(
            deployment_env_get(
                env,
                "OPENCLAW_WEBHOOK_AUTH_MODE",
                "MOLTBOT_WEBHOOK_AUTH_MODE",
                "bearer",
            ),
            "",
        )
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                webhook_env_get(
                    "OPENCLAW_WEBHOOK_AUTH_MODE",
                    "MOLTBOT_WEBHOOK_AUTH_MODE",
                    "bearer",
                ),
                "",
            )

    def test_provider_key_and_worker_family_order_remain_stable(self):
        from services.async_utils import _parse_worker_count
        from services.secret_providers import EnvSecretProvider

        with patch.dict(
            os.environ,
            {
                "OPENCLAW_OPENAI_API_KEY": "canonical-provider",
                "MOLTBOT_OPENAI_API_KEY": "legacy-provider",
            },
            clear=True,
        ):
            self.assertEqual(
                EnvSecretProvider().get_secret("openai", "default"),
                "canonical-provider",
            )

        worker_keys = (
            "OPENCLAW_LLM_EXECUTOR_WORKERS",
            "MOLTBOT_LLM_EXECUTOR_WORKERS",
            "OPENCLAW_THREAD_POOL_WORKERS",
            "MOLTBOT_THREAD_POOL_WORKERS",
        )
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_LLM_EXECUTOR_WORKERS": "",
                "MOLTBOT_LLM_EXECUTOR_WORKERS": "7",
                "OPENCLAW_THREAD_POOL_WORKERS": "9",
            },
            clear=True,
        ):
            self.assertEqual(
                _parse_worker_count(worker_keys, 6, minimum=1, maximum=12),
                7,
            )

    def test_security_telemetry_process_legacy_lookup_uses_central_warning_contract(
        self,
    ):
        from services.security_telemetry import is_security_telemetry_enabled

        with patch.dict(
            os.environ,
            {"MOLTBOT_TELEMETRY_OPT_OUT": "true"},
            clear=True,
        ):
            with self.assertLogs(
                "ComfyUI-OpenClaw.services.env_aliases", level="WARNING"
            ) as captured:
                self.assertFalse(is_security_telemetry_enabled())

        self.assertEqual(len(captured.output), 1)
        self.assertIn("MOLTBOT_TELEMETRY_OPT_OUT", captured.output[0])


if __name__ == "__main__":
    unittest.main()
