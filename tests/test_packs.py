import json
import os
import shutil
import tempfile
import unittest
import zipfile

from services.packs.pack_archive import PackArchive
from services.packs.pack_registry import PackRegistry
from services.packs.pack_manifest import (
    MAX_MANIFEST_FILES,
    PackError,
    validate_manifest_integrity,
    validate_pack_metadata,
)
from services.packs.pack_types import PackMetadata, PackType


class TestPackSecurity(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_metadata_validation(self):
        valid = {
            "name": "test-pack",
            "version": "1.0.0",
            "type": "preset",
            "author": "tester",
            "min_moltbot_version": "0.1.0",
        }
        self.assertEqual(validate_pack_metadata(valid), valid)

        invalid = valid.copy()
        del invalid["name"]
        with self.assertRaisesRegex(PackError, "Missing required field"):
            validate_pack_metadata(invalid)

        invalid_type = valid.copy()
        invalid_type["type"] = "malicious"
        with self.assertRaisesRegex(PackError, "Invalid pack type"):
            validate_pack_metadata(invalid_type)

    def test_integrity_check(self):
        # Create a dummy file
        fpath = os.path.join(self.test_dir, "test.txt")
        with open(fpath, "w") as f:
            f.write("hello")

        # Correct hash for "hello" is 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
        valid_manifest = {
            "files": [
                {
                    "path": "test.txt",
                    "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                }
            ]
        }
        self.assertEqual(validate_manifest_integrity(self.test_dir, valid_manifest), [])

        # Tampered file
        with open(fpath, "w") as f:
            f.write("hacked")

        errors = validate_manifest_integrity(self.test_dir, valid_manifest)
        self.assertTrue(any("Hash mismatch" in e for e in errors))

    def test_path_traversal_prevention(self):
        zip_path = os.path.join(self.test_dir, "traversal.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../../etc/passwd", "root:x:0:0...")

        with self.assertRaisesRegex(PackError, "Unsafe filename"):
            PackArchive.extract_pack(zip_path, os.path.join(self.test_dir, "out"))

    def test_symlink_rejection(self):
        # Python zipfile doesn't make it easy to create symlinks by default without external tools or direct info manipulation.
        # We simulate by mocking ZipInfo or constructing a crafted zip manually if needed.
        # Here we'll try to manually set the external_attr.

        zip_path = os.path.join(self.test_dir, "symlink.zip")
        zinfo = zipfile.ZipInfo("link")
        zinfo.create_system = 3  # Unix
        zinfo.external_attr = 0xA000 << 16 | 0o777  # S_IFLNK

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(zinfo, "target")

        with self.assertRaisesRegex(PackError, "Symlinks not allowed"):
            PackArchive.extract_pack(zip_path, os.path.join(self.test_dir, "out"))

    def test_max_files_limit(self):
        # We can mock ZipFile context to avoid creating huge file
        # But honestly, creating 1001 empty entries in memory is fast.
        zip_path = os.path.join(self.test_dir, "huge.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for i in range(MAX_MANIFEST_FILES + 5):
                zf.writestr(f"f{i}.txt", "")

        with self.assertRaisesRegex(PackError, "Too many files"):
            PackArchive.extract_pack(zip_path, os.path.join(self.test_dir, "out"))


class TestPackRegistryInstallTraversal(unittest.TestCase):
    """Test that install_pack rejects traversal sequences in zip metadata."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.registry = PackRegistry(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _make_pack_zip(self, name, version):
        """Create a minimal valid pack zip with the given name/version in metadata."""
        import hashlib

        zip_path = os.path.join(self.test_dir, "test.zip")
        pack_meta = {
            "name": name,
            "version": version,
            "type": "preset",
            "author": "tester",
            "min_moltbot_version": "0.1.0",
        }
        pack_json = json.dumps(pack_meta).encode("utf-8")
        pack_hash = hashlib.sha256(pack_json).hexdigest()
        manifest = {"files": [{"path": "pack.json", "sha256": pack_hash}]}

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("pack.json", pack_json)
            zf.writestr("manifest.json", json.dumps(manifest))
        return zip_path

    def test_install_rejects_traversal_in_name(self):
        zip_path = self._make_pack_zip("../../etc", "1.0.0")
        with self.assertRaises(PackError):
            self.registry.install_pack(zip_path)

    def test_install_rejects_traversal_in_version(self):
        zip_path = self._make_pack_zip("legit-pack", "../../../tmp")
        with self.assertRaises(PackError):
            self.registry.install_pack(zip_path)

    def test_install_rejects_dotdot_name(self):
        zip_path = self._make_pack_zip("..", "1.0.0")
        with self.assertRaises(PackError):
            self.registry.install_pack(zip_path)

    def test_install_accepts_valid_metadata(self):
        zip_path = self._make_pack_zip("my-pack", "1.0.0")
        meta = self.registry.install_pack(zip_path)
        self.assertEqual(meta["name"], "my-pack")
        self.assertEqual(meta["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
