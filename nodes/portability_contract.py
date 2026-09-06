"""
Stable portability metadata for exported OpenClaw nodes.

This module must stay dependency-light because it is imported from the package
entrypoint during ComfyUI custom-node loading.
"""

from __future__ import annotations

import copy
from typing import Any

PORTABILITY_CONTRACT_VERSION = 1

_NODE_PORTABILITY_MAPPINGS: dict[str, dict[str, Any]] = {
    "MoltbotPromptPlanner": {
        "display_name": "openclaw: Prompt Planner",
        "portable_mode": "materialize_standard_fields",
        "fallback_kind": "manual_rewire",
        "portable_summary": (
            "Resolve planner outputs ahead of runtime and feed standard prompt/"
            "parameter fields into downstream ComfyUI nodes."
        ),
        "standard_field_targets": ["positive", "negative", "params_json"],
        "return_names": ["positive", "negative", "params_json"],
        "replacement_hints": [
            "Run the planning step before export and persist the generated positive/negative prompts plus params_json as standard workflow inputs.",
            "Remove the OpenClaw planner node from the portable workflow and wire the downstream sampler from normal STRING/parameter fields.",
        ],
    },
    "MoltbotPromptRefiner": {
        "display_name": "openclaw: Prompt Refiner",
        "portable_mode": "materialize_standard_fields",
        "fallback_kind": "manual_rewire",
        "portable_summary": (
            "Persist refined prompt text and param_patch_json outside the graph, "
            "then inject the reviewed values through standard ComfyUI fields."
        ),
        "standard_field_targets": [
            "refined_positive",
            "refined_negative",
            "param_patch_json",
            "rationale",
        ],
        "return_names": [
            "refined_positive",
            "refined_negative",
            "param_patch_json",
            "rationale",
        ],
        "replacement_hints": [
            "Review the refinement result outside the portable workflow and store the refined prompt text plus param patch as static inputs.",
            "Keep the rationale as operator notes only; the portable workflow should consume the reviewed prompt fields, not the OpenClaw node.",
        ],
    },
    "MoltbotImageToPrompt": {
        "display_name": "openclaw: Image to Prompt",
        "portable_mode": "materialize_standard_fields",
        "fallback_kind": "manual_rewire",
        "portable_summary": (
            "Precompute caption/tags/prompt suggestion externally and feed the "
            "chosen text into standard ComfyUI prompt fields."
        ),
        "standard_field_targets": ["caption", "tags", "prompt_suggestion"],
        "return_names": ["caption", "tags", "prompt_suggestion"],
        "replacement_hints": [
            "Run the image-to-prompt step before export and store the selected text outputs as normal workflow inputs.",
            "Do not expect vanilla ComfyUI to reproduce the vision analysis step inside the graph; only the resulting text fields are portable.",
        ],
    },
    "MoltbotBatchVariants": {
        "display_name": "openclaw: Batch Variants",
        "portable_mode": "materialize_standard_fields",
        "fallback_kind": "manual_rewire",
        "portable_summary": (
            "Expand variant lists before export and execute the resulting prompt/"
            "parameter combinations through standard batch or queue tooling."
        ),
        "standard_field_targets": [
            "positive_list",
            "negative_list",
            "params_json_list",
        ],
        "return_names": ["positive_list", "negative_list", "params_json_list"],
        "replacement_hints": [
            "Generate the variant list before export and save the expanded prompt/parameter combinations outside the portable workflow.",
            "Use standard ComfyUI batch/queue mechanisms to run each prepared variant instead of relying on the OpenClaw batch node.",
        ],
    },
}


def get_node_portability_mappings() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(_NODE_PORTABILITY_MAPPINGS)


NODE_PORTABILITY_MAPPINGS = get_node_portability_mappings()
