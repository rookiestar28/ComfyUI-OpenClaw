import json
import unittest
from urllib.parse import parse_qs, urlparse

from services import comfyui_history
from services.comfyui_history import extract_output_refs


class TestR236Advanced3DResult(unittest.TestCase):
    VALID_SUFFIXES = (
        "glb",
        "gltf",
        "obj",
        "fbx",
        "stl",
        "ply",
        "spz",
        "splat",
        "ksplat",
        "usdz",
    )

    @staticmethod
    def _extract(result):
        return extract_output_refs({"outputs": {"9": {"result": result}}})

    def test_official_result_tuple_projects_only_first_3d_path(self):
        class GuardedResult(list):
            def __getitem__(self, index):
                if index != 0:
                    raise AssertionError("later result entries were inspected")
                return super().__getitem__(index)

        class ExplosiveMetadata:
            def __str__(self):
                raise AssertionError("later result metadata was inspected")

            def __repr__(self):
                raise AssertionError("later result metadata was inspected")

        metadata_canary = "metadata-value-must-not-project"
        outputs = self._extract(
            GuardedResult(
                [
                    "models/scene one.splat",
                    ExplosiveMetadata(),
                    [{"model": metadata_canary}],
                ]
            )
        )

        self.assertEqual(len(outputs), 1)
        output = outputs[0]
        self.assertEqual(
            {
                "filename": output["filename"],
                "subfolder": output["subfolder"],
                "type": output["type"],
                "media_type": output["media_type"],
                "asset_hash": output["asset_hash"],
                "asset_api_id": output["asset_api_id"],
                "asset_api_required": output["asset_api_required"],
                "resolution": output["resolution"],
            },
            {
                "filename": "scene one.splat",
                "subfolder": "models",
                "type": "output",
                "media_type": "3d",
                "asset_hash": "",
                "asset_api_id": "",
                "asset_api_required": False,
                "resolution": "view",
            },
        )
        params = parse_qs(urlparse(output["view_url"]).query)
        self.assertEqual(
            params,
            {
                "filename": ["scene one.splat"],
                "subfolder": ["models"],
                "type": ["output"],
            },
        )
        self.assertNotIn(metadata_canary, json.dumps(output, sort_keys=True))

    def test_accepts_reviewed_suffixes_and_normalizes_backslashes(self):
        history = {
            "outputs": {
                str(index): {
                    "result": [
                        (
                            f"nested\\folder\\scene.{suffix.upper()}"
                            if index == 0
                            else f"nested/scene.{suffix.upper()}"
                        )
                    ]
                }
                for index, suffix in enumerate(self.VALID_SUFFIXES)
            }
        }

        outputs = extract_output_refs(history)

        self.assertEqual(len(outputs), len(self.VALID_SUFFIXES))
        self.assertTrue(all(output["media_type"] == "3d" for output in outputs))
        self.assertEqual(outputs[0]["subfolder"], "nested/folder")
        self.assertEqual(outputs[0]["filename"], "scene.GLB")
        self.assertEqual(
            [output["filename"].rsplit(".", 1)[-1].lower() for output in outputs],
            list(self.VALID_SUFFIXES),
        )
        unicode_output = self._extract(["模型/場景😀.glb"])
        self.assertEqual(
            (unicode_output[0]["subfolder"], unicode_output[0]["filename"]),
            ("模型", "場景😀.glb"),
        )

    def test_enforces_container_and_unicode_path_bounds(self):
        max_path = ("a" * (1024 - len(".glb"))) + ".glb"
        self.assertEqual(len(self._extract([max_path] + [{}] * 7)), 1)

        rejected = (
            [],
            ["scene.glb"] + [{}] * 8,
            [("a" * (1025 - len(".glb"))) + ".glb"],
        )
        for result in rejected:
            with self.subTest(result_length=len(result)):
                self.assertEqual(self._extract(result), [])

    def test_rejects_malformed_or_unsafe_first_entries(self):
        rejected = (
            None,
            "scene.glb",
            {},
            [None],
            [123],
            [""],
            ["   "],
            [" scene.glb"],
            ["scene.glb "],
            ["\u00a0scene.glb"],
            ["/absolute/scene.glb"],
            ["//evil.example/scene.glb"],
            ["https://evil.example/scene.glb"],
            ["file:scene.glb"],
            ["C:\\private\\scene.glb"],
            ["../scene.glb"],
            ["safe/../scene.glb"],
            ["safe/./scene.glb"],
            ["safe//scene.glb"],
            ["safe/\x00scene.glb"],
            ["safe/\u0085scene.glb"],
            ["safe/\u202escene.glb"],
            ["safe/\ud800scene.glb"],
            ["scene.glb?token=secret"],
            ["scene.png"],
            ["scene.glb.exe"],
        )
        for result in rejected:
            with self.subTest(result=result):
                self.assertEqual(self._extract(result), [])

    def test_existing_output_families_remain_unchanged(self):
        outputs = extract_output_refs(
            {
                "outputs": {
                    "1": {
                        "images": [{"filename": "image.png", "type": "output"}],
                        "video": [{"filename": "clip.webm", "type": "output"}],
                        "audio": [{"filename": "sound.wav", "type": "output"}],
                        "3d": ["classic.glb"],
                        "text": ["hello"],
                        "files": [{"filename": "report.txt", "type": "output"}],
                    }
                }
            }
        )

        self.assertEqual(
            [output["media_type"] for output in outputs],
            ["images", "video", "audio", "3d", "text", "text"],
        )


class TestR255AnnotatedAdvanced3dResult(unittest.TestCase):
    """Canonical ComfyUI `<path> [input|output|temp]` Advanced 3D results.

    The upstream producer is `PreviewUI3DAdvanced.as_dict()`, which appends
    `f" [{FolderType(...).value}]"` only when a folder type is supplied, so the
    marker is always terminal, always separated by one ASCII space, and always
    lowercase. The three `Preview3D*` nodes emit `[temp]`; the save nodes emit a
    bare path that must keep resolving as `output`.
    """

    ANNOTATION_TYPES = ("input", "output", "temp")

    @staticmethod
    def _extract(result):
        return extract_output_refs({"outputs": {"9": {"result": result}}})

    def _single(self, result):
        outputs = self._extract(result)
        # Only entry zero may be read, including inside a failure message.
        self.assertEqual(len(outputs), 1, f"expected exactly one ref for {result[0]!r}")
        return outputs[0]

    def test_official_temp_preview_projects_a_temp_view_reference(self):
        output = self._single(["preview.glb [temp]"])

        self.assertEqual(
            (
                output["filename"],
                output["subfolder"],
                output["type"],
                output["media_type"],
                output["resolution"],
            ),
            ("preview.glb", "", "temp", "3d", "view"),
        )
        self.assertEqual(
            parse_qs(urlparse(output["view_url"]).query),
            {"filename": ["preview.glb"], "type": ["temp"]},
        )

    def test_accepts_every_lowercase_terminal_directory_annotation(self):
        cases = {
            "models/scene.splat [output]": ("scene.splat", "models", "output"),
            "preview/scene.glb [temp]": ("scene.glb", "preview", "temp"),
            "input/scene.GLB [input]": ("scene.GLB", "input", "input"),
            "nested\\folder\\scene.glb [temp]": ("scene.glb", "nested/folder", "temp"),
            "模型/場景😀.glb [temp]": ("場景😀.glb", "模型", "temp"),
        }

        for path, expected in cases.items():
            with self.subTest(path=path):
                output = self._single([path])
                self.assertEqual(
                    (output["filename"], output["subfolder"], output["type"]),
                    expected,
                )
                self.assertEqual(
                    parse_qs(urlparse(output["view_url"]).query)["type"],
                    [expected[2]],
                )

    def test_annotation_applies_to_every_reviewed_3d_extension(self):
        suffixes = TestR236Advanced3DResult.VALID_SUFFIXES
        outputs = extract_output_refs(
            {
                "outputs": {
                    str(index): {"result": [f"preview/scene.{suffix} [temp]"]}
                    for index, suffix in enumerate(suffixes)
                }
            }
        )

        self.assertEqual(len(outputs), len(suffixes))
        self.assertEqual(
            [output["filename"].rsplit(".", 1)[-1] for output in outputs],
            list(suffixes),
        )
        self.assertTrue(
            all(
                output["type"] == "temp" and output["media_type"] == "3d"
                for output in outputs
            )
        )

    def test_bare_and_embedded_bracket_paths_keep_legacy_output_type(self):
        legacy = {
            "scene.glb": ("scene.glb", ""),
            "models [temp]/scene.glb": ("scene.glb", "models [temp]"),
            "scene [output].glb": ("scene [output].glb", ""),
            "a [temp] b/scene [input] c.glb": ("scene [input] c.glb", "a [temp] b"),
        }

        for path, (filename, subfolder) in legacy.items():
            with self.subTest(path=path):
                output = self._single([path])
                self.assertEqual(
                    (output["filename"], output["subfolder"], output["type"]),
                    (filename, subfolder, "output"),
                )

    def test_rejects_uppercase_repeated_and_malformed_terminal_annotations(self):
        rejected = (
            ["scene.glb [bogus]"],
            ["scene.glb [TEMP]"],
            ["scene.glb [Temp]"],
            ["scene.glb [OUTPUT]"],
            ["scene.glb [temp][temp]"],
            ["scene.glb [temp] [temp]"],
            ["scene.glb[temp]"],
            ["scene.glb\u00a0[temp]"],
            ["scene.glb\t[temp]"],
            ["scene.glb [temp] "],
            ["scene.glb [temp]\n"],
            ["scene.glb  [temp]"],
            ["scene.glb [ temp]"],
            ["scene.glb [temp ]"],
            ["[temp]"],
            [" [temp]"],
            ["scene.glb [temp].bak"],
            ["scene.png [temp]"],
            ["scene.glb.exe [temp]"],
        )

        for result in rejected:
            with self.subTest(result=result):
                self.assertEqual(self._extract(result), [])

    def test_existing_unsafe_path_families_stay_closed_when_annotated(self):
        rejected = (
            ["/absolute/scene.glb [temp]"],
            ["//evil.example/scene.glb [temp]"],
            ["https://evil.example/scene.glb [temp]"],
            ["file:scene.glb [temp]"],
            ["C:\\private\\scene.glb [temp]"],
            ["../scene.glb [temp]"],
            ["safe/../scene.glb [temp]"],
            ["safe/./scene.glb [temp]"],
            ["safe//scene.glb [temp]"],
            ["safe/\x00scene.glb [temp]"],
            ["safe/\u0085scene.glb [temp]"],
            ["safe/\u202escene.glb [temp]"],
            ["safe/\ud800scene.glb [temp]"],
            ["scene.glb?token=secret [temp]"],
            [" scene.glb [temp]"],
            ["scene.glb [temp]", {}, {}, {}, {}, {}, {}, {}, {}],
        )

        for result in rejected:
            with self.subTest(result=result):
                self.assertEqual(self._extract(result), [])

    def test_raw_wire_and_canonical_bounds_are_pinned_independently(self):
        canonical_max = ("a" * (1024 - len(".glb"))) + ".glb"
        annotated_max = f"{canonical_max} [output]"
        self.assertEqual((len(canonical_max), len(annotated_max)), (1024, 1033))

        output = self._single([annotated_max])
        self.assertEqual(
            (output["filename"], output["type"]), (canonical_max, "output")
        )

        over_canonical_path = ("a" * (1025 - len(".glb"))) + ".glb"

        over_raw = f"{over_canonical_path} [output]"
        self.assertEqual(len(over_raw), 1034)
        self.assertEqual(self._extract([over_raw]), [])

        within_raw_over_canonical = f"{over_canonical_path} [temp]"
        self.assertEqual(len(within_raw_over_canonical), 1032)
        self.assertEqual(self._extract([within_raw_over_canonical]), [])

    def test_annotated_result_still_projects_only_the_first_entry(self):
        class GuardedResult(list):
            def __getitem__(self, index):
                if index != 0:
                    raise AssertionError("later result entries were inspected")
                return super().__getitem__(index)

        class ExplosiveMetadata:
            def __str__(self):
                raise AssertionError("later result metadata was inspected")

            def __repr__(self):
                raise AssertionError("later result metadata was inspected")

        metadata_canary = "metadata-value-must-not-project"
        output = self._single(
            GuardedResult(
                [
                    "preview/scene one.splat [temp]",
                    ExplosiveMetadata(),
                    [{"model": metadata_canary}],
                ]
            )
        )

        self.assertEqual(
            (output["filename"], output["subfolder"], output["type"]),
            ("scene one.splat", "preview", "temp"),
        )
        self.assertNotIn(metadata_canary, json.dumps(output, sort_keys=True))

    def test_annotation_vocabulary_matches_the_host_directory_types(self):
        self.assertEqual(
            set(self.ANNOTATION_TYPES),
            set(comfyui_history.HOST_DIRECTORY_TYPES),
        )


if __name__ == "__main__":
    unittest.main()
