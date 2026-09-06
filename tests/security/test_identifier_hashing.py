import unittest
from unittest.mock import patch

import services.audit as audit_module
from services.bridge_token_lifecycle import BridgeTokenStore


class TestAuditIdentifierHashing(unittest.TestCase):
    def test_chain_hash_is_keyed(self):
        entry = {"action": "config.update", "target": "settings.json"}

        with patch.object(audit_module, "_AUDIT_CHAIN_KEY", b"key-a"):
            hash_a = audit_module._chain_hash("GENESIS", entry)

        with patch.object(audit_module, "_AUDIT_CHAIN_KEY", b"key-b"):
            hash_b = audit_module._chain_hash("GENESIS", entry)

        self.assertNotEqual(hash_a, hash_b)

    def test_chain_hash_is_stable_for_same_key(self):
        entry = {"action": "config.update", "target": "settings.json"}

        with patch.object(audit_module, "_AUDIT_CHAIN_KEY", b"fixed-key"):
            hash_a = audit_module._chain_hash("GENESIS", entry)
            hash_b = audit_module._chain_hash("GENESIS", entry)

        self.assertEqual(hash_a, hash_b)


class TestBridgeTokenHashing(unittest.TestCase):
    def test_constant_time_lookup_accepts_issued_token(self):
        store = BridgeTokenStore()
        token = store.issue_token("device-1")

        result = store.validate_token(token.device_token)

        self.assertTrue(result.ok)
        self.assertEqual(result.token.token_id, token.token_id)

    def test_lookup_does_not_rely_on_hash_index_internals(self):
        store = BridgeTokenStore()
        token = store.issue_token("device-1")
        token.status = "active"
        store._tokens[token.token_id] = token

        token_id, resolved = store._resolve_token_for_value(token.device_token)

        self.assertEqual(token_id, token.token_id)
        self.assertIs(resolved, token)
