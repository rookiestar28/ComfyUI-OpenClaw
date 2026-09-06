"""R250 tranche-1 behavior pins.

These tests are written before the annotation rewrite and must pass identically
before and after it. They exist because the tranche modernizes annotations in
packages that are read at runtime: `models/schemas.py` filters incoming payload
keys through `cls.__annotations__`, and the `nodes` package is introspected by
the ComfyUI host. Rewriting an annotation *value* must never change either
surface.
"""

import importlib
import importlib.util
import json
import sys
import unittest
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path

from models import schemas
from nodes import (
    batch_variants,
    image_to_prompt,
    portability_contract,
    prompt_planner,
    prompt_refiner,
)

# Exact annotation key order per dataclass, measured at the tranche base commit.
# `Profile.from_dict` and `GenerationParams.from_dict` filter caller-supplied
# payloads against these keys, so a change here is a public behavior change.
DATACLASS_ANNOTATION_KEYS = {
    "Profile": ("id", "version", "label", "description", "model_config_data"),
    "GenerationParams": (
        "width",
        "height",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "seed",
        "extra",
    ),
    "JobSpec": (
        "positive_prompt",
        "negative_prompt",
        "params",
        "schema_version",
        "metadata",
    ),
    "ParamPatch": ("target_field", "value", "reason"),
    "WebhookJobRequest": (
        "version",
        "template_id",
        "profile_id",
        "inputs",
        "job_id",
        "trace_id",
        "callback",
    ),
}

NODE_CLASS_CONTRACT = {
    "OpenClawBatchVariants": {
        "module": batch_variants,
        "return_types": ("STRING", "STRING", "STRING"),
        "return_names": ("positive_list", "negative_list", "params_json_list"),
        "function": "generate_variants",
        "category": "openclaw",
    },
    "OpenClawImageToPrompt": {
        "module": image_to_prompt,
        "return_types": ("STRING", "STRING", "STRING"),
        "return_names": ("caption", "tags", "prompt_suggestion"),
        "function": "generate_prompt",
        "category": "openclaw",
    },
    "OpenClawPromptPlanner": {
        "module": prompt_planner,
        "return_types": ("STRING", "STRING", "STRING"),
        "return_names": ("positive", "negative", "params_json"),
        "function": "plan_generation",
        "category": "openclaw",
    },
    "OpenClawPromptRefiner": {
        "module": prompt_refiner,
        "return_types": ("STRING", "STRING", "STRING", "STRING"),
        "function": "refine_prompt",
        "category": "openclaw",
    },
}

# Every script file carrying a tranche finding. Loading each one proves the
# annotation rewrite did not break module import or a module-level definition.
# They are loaded the way the gate actually runs them - as top-level modules with
# `scripts/` on sys.path - because several import their siblings by bare name.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
TRANCHE_SCRIPT_FILES = (
    "check_openapi_sync.py",
    "check_supply_chain_hardening.py",
    "generate_provenance.py",
    "lint_implementation_record.py",
    "quality_governance_common.py",
    "run_adversarial_gate.py",
    "run_mutation_test.py",
    "run_unittests.py",
    "verify_exception_boundary_policy.py",
    "verify_quality_governance.py",
    "verify_test_debt_governance.py",
)


def _load_tranche_script(filename):
    """Import one tranche script the way the acceptance gate invokes it."""
    path = SCRIPTS_DIR / filename
    module_name = f"_r250_tranche_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


class TestR250SchemaAnnotationContract(unittest.TestCase):
    def test_dataclass_annotation_keys_are_frozen(self):
        for name, expected_keys in DATACLASS_ANNOTATION_KEYS.items():
            with self.subTest(dataclass=name):
                cls = getattr(schemas, name)
                self.assertTrue(is_dataclass(cls))
                self.assertEqual(tuple(cls.__annotations__.keys()), expected_keys)
                self.assertEqual(
                    tuple(field.name for field in fields(cls)), expected_keys
                )

    def test_profile_from_dict_still_filters_on_annotation_keys(self):
        profile = schemas.Profile.from_dict(
            {
                "id": "sdxl-v1",
                "version": "1",
                "label": "SDXL",
                "unrelated_key": "must be dropped",
                "__annotations__": "must be dropped",
            }
        )

        self.assertEqual(
            (profile.id, profile.version, profile.label), ("sdxl-v1", "1", "SDXL")
        )
        self.assertIsNone(profile.description)
        self.assertEqual(profile.model_config_data, {})
        self.assertFalse(hasattr(profile, "unrelated_key"))

    def test_generation_params_from_dict_filters_and_still_clamps(self):
        params = schemas.GenerationParams.from_dict(
            {
                "width": 99999,
                "height": 3,
                "steps": 0,
                "cfg": 999.0,
                "seed": 7,
                "not_a_field": "dropped",
            }
        )

        self.assertEqual(
            (params.width, params.height, params.steps, params.cfg, params.seed),
            (4096, 256, 1, 30.0, 7),
        )
        self.assertEqual(params.extra, {})
        self.assertFalse(hasattr(params, "not_a_field"))

    def test_generation_params_round_trips_through_asdict_and_json(self):
        params = schemas.GenerationParams(width=1024, height=768, seed=11)

        as_dict = params.dict()
        self.assertEqual(as_dict, asdict(params))
        self.assertEqual(tuple(as_dict), DATACLASS_ANNOTATION_KEYS["GenerationParams"])
        self.assertEqual(
            schemas.GenerationParams.from_dict(json.loads(json.dumps(as_dict))),
            params,
        )

    def test_optional_fields_still_default_to_none(self):
        self.assertIsNone(schemas.Profile(id="a", version="1", label="A").description)
        self.assertIsNone(schemas.GenerationParams().seed)


class TestR250NodeIntrospectionContract(unittest.TestCase):
    def test_host_facing_class_attributes_are_frozen(self):
        for name, contract in NODE_CLASS_CONTRACT.items():
            with self.subTest(node=name):
                cls = getattr(contract["module"], name)
                self.assertEqual(cls.RETURN_TYPES, contract["return_types"])
                self.assertEqual(cls.FUNCTION, contract["function"])
                self.assertEqual(cls.CATEGORY, contract["category"])
                self.assertTrue(callable(getattr(cls, contract["function"])))
                if "return_names" in contract:
                    self.assertEqual(cls.RETURN_NAMES, contract["return_names"])

    def test_input_types_is_a_classmethod_returning_a_mapping(self):
        for name, contract in NODE_CLASS_CONTRACT.items():
            with self.subTest(node=name):
                cls = getattr(contract["module"], name)
                declared = cls.INPUT_TYPES()
                self.assertIsInstance(declared, dict)
                self.assertIn("required", declared)
                self.assertIsInstance(declared["required"], dict)

    def test_the_host_contract_never_reads_python_annotations(self):
        # ComfyUI resolves node signatures from INPUT_TYPES/RETURN_TYPES, so a
        # node class is allowed to carry no annotations at all. Asserting this
        # is what makes the annotation rewrite provably invisible to the host.
        for name, contract in NODE_CLASS_CONTRACT.items():
            with self.subTest(node=name):
                cls = getattr(contract["module"], name)
                self.assertEqual(
                    set(getattr(cls, "__annotations__", {})) & set(cls.INPUT_TYPES()),
                    set(),
                )

    def test_portability_contract_module_still_exposes_its_public_names(self):
        self.assertTrue(
            [name for name in dir(portability_contract) if not name.startswith("_")]
        )


class TestR250TrancheModulesRemainImportable(unittest.TestCase):
    def setUp(self):
        self._original_path = list(sys.path)
        sys.path.insert(0, str(SCRIPTS_DIR))

    def tearDown(self):
        sys.path[:] = self._original_path

    def test_every_tranche_script_loads_and_keeps_its_entry_point(self):
        for filename in TRANCHE_SCRIPT_FILES:
            with self.subTest(script=filename):
                module = _load_tranche_script(filename)
                self.assertTrue(
                    [name for name in dir(module) if not name.startswith("_")]
                )
                if hasattr(module, "main"):
                    self.assertTrue(callable(module.main))

    def test_every_tranche_script_file_exists(self):
        for filename in TRANCHE_SCRIPT_FILES:
            with self.subTest(script=filename):
                self.assertTrue((SCRIPTS_DIR / filename).is_file())


if __name__ == "__main__":
    unittest.main()
