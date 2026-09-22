"""
Tests for ComfyUI History Parsing (F17).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestComfyUIHistoryParsing(unittest.TestCase):
    """Test extract_images parses history correctly."""

    def test_extract_images_basic(self):
        from services.comfyui_history import extract_images

        # Minimal history item fixture
        history_item = {
            "outputs": {
                "9": {
                    "images": [
                        {"filename": "test_001.png", "subfolder": "", "type": "output"},
                        {
                            "filename": "test_002.png",
                            "subfolder": "subfolder1",
                            "type": "output",
                        },
                    ]
                },
                "12": {
                    "images": [
                        {"filename": "another.jpg", "subfolder": "", "type": "temp"},
                    ]
                },
            }
        }

        images = extract_images(history_item)

        self.assertEqual(len(images), 3)

        # Check first image
        img1 = images[0]
        self.assertEqual(img1["filename"], "test_001.png")
        self.assertEqual(img1["subfolder"], "")
        self.assertEqual(img1["type"], "output")
        self.assertIn("filename=test_001.png", img1["view_url"])
        self.assertIn("type=output", img1["view_url"])

        # Check subfolder is URL encoded
        img2 = images[1]
        self.assertEqual(img2["subfolder"], "subfolder1")
        self.assertIn("subfolder=subfolder1", img2["view_url"])

    def test_extract_images_empty(self):
        from services.comfyui_history import extract_images

        history_item = {"outputs": {}}
        images = extract_images(history_item)
        self.assertEqual(len(images), 0)

    def test_extract_images_no_filename(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "1": {
                    "images": [
                        {"subfolder": "", "type": "output"},  # Missing filename
                    ]
                }
            }
        }
        images = extract_images(history_item)
        self.assertEqual(len(images), 0)

    def test_extract_images_prefers_asset_hash_view_url(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "2": {
                    "images": [
                        {
                            "filename": "preview.png",
                            "subfolder": "nested",
                            "type": "temp",
                            "asset_hash": "blake3:abc123",
                        }
                    ]
                }
            }
        }

        images = extract_images(history_item)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["filename"], "preview.png")
        self.assertEqual(images[0]["type"], "temp")
        self.assertEqual(images[0]["asset_hash"], "blake3:abc123")
        self.assertIn("filename=blake3%3Aabc123", images[0]["view_url"])
        self.assertNotIn("subfolder=nested", images[0]["view_url"])
        self.assertNotIn("type=temp", images[0]["view_url"])
        self.assertFalse(images[0]["asset_api_required"])
        self.assertEqual(images[0]["resolution"], "view")

    def test_extract_images_filename_refs_do_not_require_asset_hash(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "2": {
                    "images": [
                        {
                            "filename": "filename-only.png",
                            "subfolder": "session-a",
                            "type": "output",
                            "asset": {
                                "id": "asset-without-hash",
                            },
                        }
                    ]
                }
            }
        }

        images = extract_images(history_item)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["filename"], "filename-only.png")
        self.assertEqual(images[0]["asset_hash"], "")
        self.assertEqual(images[0]["asset_api_id"], "asset-without-hash")
        self.assertFalse(images[0]["asset_api_required"])
        self.assertEqual(images[0]["resolution"], "view")
        self.assertIn("filename=filename-only.png", images[0]["view_url"])
        self.assertIn("type=output", images[0]["view_url"])
        self.assertIn("subfolder=session-a", images[0]["view_url"])

    def test_extract_images_preserves_enriched_top_level_id_without_changing_view(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "2": {
                    "images": [
                        {
                            "filename": "cached.png",
                            "subfolder": "session-a",
                            "type": "output",
                            "id": "asset-cached-42",
                        }
                    ]
                }
            }
        }

        images = extract_images(history_item)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["asset_api_id"], "asset-cached-42")
        self.assertFalse(images[0]["asset_api_required"])
        self.assertEqual(images[0]["resolution"], "view")
        self.assertIn("filename=cached.png", images[0]["view_url"])
        self.assertNotIn("asset-cached-42", images[0]["view_url"])

    def test_extract_images_keeps_enriched_id_only_ref_explicit(self):
        from services.comfyui_history import extract_images

        images = extract_images(
            {"outputs": {"2": {"images": [{"id": "asset-only-42"}]}}}
        )
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["asset_api_id"], "asset-only-42")
        self.assertTrue(images[0]["asset_api_required"])
        self.assertEqual(images[0]["view_url"], "")

    def test_extract_images_accepts_top_level_hash_alias(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "2": {
                    "images": [
                        {
                            "filename": "hash-alias.png",
                            "type": "output",
                            "hash": "blake3:alias123",
                        }
                    ]
                }
            }
        }

        images = extract_images(history_item)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["filename"], "hash-alias.png")
        self.assertEqual(images[0]["asset_hash"], "blake3:alias123")
        self.assertIn("filename=blake3%3Aalias123", images[0]["view_url"])
        self.assertFalse(images[0]["asset_api_required"])
        self.assertEqual(images[0]["resolution"], "view")

    def test_extract_images_accepts_nested_hash_alias(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "2": {
                    "images": [
                        {
                            "name": "nested-hash-alias.png",
                            "asset": {
                                "hash": "blake3:nested-alias",
                            },
                        }
                    ]
                }
            }
        }

        images = extract_images(history_item)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["filename"], "nested-hash-alias.png")
        self.assertEqual(images[0]["asset_hash"], "blake3:nested-alias")
        self.assertIn("filename=blake3%3Anested-alias", images[0]["view_url"])
        self.assertFalse(images[0]["asset_api_required"])
        self.assertEqual(images[0]["resolution"], "view")

    def test_extract_images_prefers_asset_hash_over_hash_alias(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "2": {
                    "images": [
                        {
                            "filename": "preferred.png",
                            "asset_hash": "blake3:preferred",
                            "hash": "blake3:alias",
                        }
                    ]
                }
            }
        }

        images = extract_images(history_item)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["asset_hash"], "blake3:preferred")
        self.assertIn("filename=blake3%3Apreferred", images[0]["view_url"])
        self.assertNotIn("filename=blake3%3Aalias", images[0]["view_url"])

    def test_extract_images_preserves_asset_api_only_refs_as_explicit_no_go_contract(
        self,
    ):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "3": {
                    "images": [
                        {
                            "asset": {
                                "id": "asset-only-42",
                            }
                        }
                    ]
                }
            }
        }

        images = extract_images(history_item)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["filename"], "asset-only-42")
        self.assertEqual(images[0]["asset_api_id"], "asset-only-42")
        self.assertTrue(images[0]["asset_api_required"])
        self.assertEqual(images[0]["resolution"], "asset_api_required")
        self.assertEqual(images[0]["view_url"], "")

    def test_extract_output_refs_collects_previewable_media_types(self):
        from services.comfyui_history import extract_output_refs

        history_item = {
            "outputs": {
                "1": {
                    "images": [{"filename": "image.png", "type": "output"}],
                    "video": [
                        {
                            "filename": "clip.webm",
                            "type": "output",
                            "format": "video/webm",
                        }
                    ],
                    "audio": [{"filename": "sound.wav", "type": "output"}],
                    "3d": ["mesh.glb"],
                    "text": ["hello from text output"],
                }
            }
        }

        outputs = extract_output_refs(history_item)
        self.assertEqual(
            [output["media_type"] for output in outputs],
            ["images", "video", "audio", "3d", "text"],
        )
        self.assertIn("filename=image.png", outputs[0]["view_url"])
        self.assertIn("filename=clip.webm", outputs[1]["view_url"])
        self.assertIn("filename=sound.wav", outputs[2]["view_url"])
        self.assertIn("filename=mesh.glb", outputs[3]["view_url"])
        self.assertEqual(outputs[4]["resolution"], "inline_text")
        self.assertEqual(outputs[4]["content"], "hello from text output")
        self.assertEqual(outputs[4]["view_url"], "")

    def test_extract_output_refs_accepts_saveimage_refs_with_output_metadata(self):
        from services.comfyui_history import extract_images, extract_output_refs

        history_item = {
            "outputs": {
                "9": {
                    "images": [
                        {
                            "filename": "ComfyUI_00001_.png",
                            "subfolder": "",
                            "type": "output",
                            "width": 1024,
                            "height": 768,
                        }
                    ]
                }
            }
        }

        outputs = extract_output_refs(history_item)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["filename"], "ComfyUI_00001_.png")
        self.assertEqual(outputs[0]["media_type"], "images")
        self.assertFalse(outputs[0]["asset_api_required"])
        self.assertEqual(outputs[0]["resolution"], "view")
        self.assertIn("filename=ComfyUI_00001_.png", outputs[0]["view_url"])
        self.assertIn("type=output", outputs[0]["view_url"])
        self.assertNotIn("width=", outputs[0]["view_url"])
        self.assertNotIn("height=", outputs[0]["view_url"])
        self.assertEqual(extract_images(history_item), outputs)

    def test_extract_output_refs_accepts_load3dadvanced_object_refs(self):
        from services.comfyui_history import extract_output_refs

        history_item = {
            "outputs": {
                "7": {
                    "3d": [
                        {
                            "filename": "scene.glb",
                            "subfolder": "previews",
                            "type": "output",
                            "asset_hash": "blake3:mesh123",
                            "width": 1200,
                            "height": 800,
                        }
                    ]
                }
            }
        }

        outputs = extract_output_refs(history_item)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["filename"], "scene.glb")
        self.assertEqual(outputs[0]["media_type"], "3d")
        self.assertEqual(outputs[0]["asset_hash"], "blake3:mesh123")
        self.assertFalse(outputs[0]["asset_api_required"])
        self.assertEqual(outputs[0]["resolution"], "view")
        self.assertIn("filename=blake3%3Amesh123", outputs[0]["view_url"])
        self.assertNotIn("subfolder=previews", outputs[0]["view_url"])
        self.assertNotIn("type=output", outputs[0]["view_url"])
        self.assertNotIn("width=", outputs[0]["view_url"])
        self.assertNotIn("height=", outputs[0]["view_url"])

    def test_extract_images_remains_image_only_for_callbacks(self):
        from services.comfyui_history import extract_images

        history_item = {
            "outputs": {
                "1": {
                    "images": [{"filename": "image.png", "type": "output"}],
                    "video": [{"filename": "clip.webm", "type": "output"}],
                    "audio": [{"filename": "sound.wav", "type": "output"}],
                    "3d": ["mesh.glb"],
                    "text": ["hello"],
                }
            }
        }

        images = extract_images(history_item)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["filename"], "image.png")
        self.assertEqual(images[0]["media_type"], "images")

    def test_extract_output_refs_bounds_inline_text(self):
        from services.comfyui_history import (
            TEXT_PREVIEW_MAX_LENGTH,
            extract_output_refs,
        )

        long_text = "x" * (TEXT_PREVIEW_MAX_LENGTH + 10)
        history_item = {"outputs": {"1": {"text": [long_text]}}}

        outputs = extract_output_refs(history_item)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["media_type"], "text")
        self.assertEqual(len(outputs[0]["content"]), TEXT_PREVIEW_MAX_LENGTH)
        self.assertTrue(outputs[0]["text_truncated"])

    def test_get_job_status(self):
        from services.comfyui_history import get_job_status

        # None -> pending
        self.assertEqual(get_job_status(None), "pending")

        # With outputs -> completed
        self.assertEqual(get_job_status({"outputs": {"1": {}}}), "completed")

        # Empty -> unknown
        self.assertEqual(get_job_status({}), "unknown")

        # Explicit status
        self.assertEqual(
            get_job_status({"status": {"status_str": "success"}}), "completed"
        )
        self.assertEqual(get_job_status({"status": {"status_str": "error"}}), "error")


if __name__ == "__main__":
    unittest.main()
