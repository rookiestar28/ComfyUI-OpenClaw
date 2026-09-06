import json
import logging
from functools import partial
from typing import Any

try:
    from ..services.image_utils import tensor_to_base64_png
    from ..services.refiner import RefinerService
except ImportError:
    from services.image_utils import tensor_to_base64_png
    from services.refiner import RefinerService

try:
    from ..services.metrics import metrics
except ImportError:
    from services.metrics import metrics

# Allowed keys (kept for documentation/reference, but service handles logic)
ALLOWED_PATCH_KEYS = {
    "steps",
    "cfg",
    "width",
    "height",
    "sampler_name",
    "scheduler",
    "seed",
}

logger = logging.getLogger("ComfyUI-OpenClaw.nodes.PromptRefiner")


class OpenClawPromptRefiner:
    """
    Critiques and refines prompts/params based on a generated image and identified issues.
    DELEGATES to services.refiner.RefinerService (F21 Refactor).
    """

    def __init__(self) -> None:
        self.service = RefinerService()

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
                "orig_positive": ("STRING", {"multiline": True}),
                "orig_negative": ("STRING", {"multiline": True}),
                "issue": (
                    [
                        "hands_bad",
                        "face_bad",
                        "anatomy_off",
                        "lighting_off",
                        "composition_off",
                        "style_drift",
                        "text_artifacts",
                        "low_detail",
                        "other",
                    ],
                    {"default": "other"},
                ),
            },
            "optional": {
                "params_json": ("STRING", {"multiline": True, "default": "{}"}),
                "goal": ("STRING", {"multiline": True, "default": "Fix the issues"}),
                "max_image_side": ("INT", {"default": 1024, "min": 256, "max": 1536}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "refined_positive",
        "refined_negative",
        "param_patch_json",
        "rationale",
    )
    FUNCTION = "refine_prompt"
    CATEGORY = "openclaw"

    # R154: keep the compatibility method name, but bind the shared helper
    # directly so node wrappers do not duplicate image conversion logic.
    _tensor_to_base64_png = staticmethod(
        partial(tensor_to_base64_png, context="PromptRefiner")
    )

    def refine_prompt(
        self,
        image: Any,
        orig_positive: str,
        orig_negative: str,
        issue: str,
        params_json: str = "{}",
        goal: str = "Fix the issues",
        max_image_side: int = 1024,
    ) -> tuple[str, str, str, str]:

        # 1. Preprocess Image (Node responsibility)
        try:
            image_b64 = self._tensor_to_base64_png(image, max_image_side)
        except Exception as e:
            metrics.increment(
                "errors"
            )  # Keep metrics here for image preprocessing errors
            logger.error(f"Failed to preprocess image: {e}")
            raise ValueError(f"Image preprocessing failed: {e}")

        try:
            # Delegate to Service
            refined_pos, refined_neg, patch_dict, rationale = (
                self.service.refine_prompt(
                    image_b64=image_b64,
                    orig_positive=orig_positive,
                    orig_negative=orig_negative,
                    issue=issue,
                    params_json=params_json,
                    goal=goal,
                )
            )

            return (
                refined_pos,
                refined_neg,
                json.dumps(patch_dict, indent=2),
                rationale,
            )

        except Exception as e:
            # Fallback handled by service? Service raises. Node catches to stay robust?
            # Original code returned fallback on catch. Service raises exception.
            # We should wrap in try/except here to match original behavior if desired,
            # OR let Service handle fallback return.
            # Service implementation above returns fallback on parsing error, but raises on other exceptions.
            # Let's catch generic exception here for safety.
            metrics.increment("errors")  # Add metrics for service errors
            logger.error(f"Refiner Service failed: {e}")
            return (orig_positive, orig_negative, "{}", f"Error: {str(e)}")


# IMPORTANT: keep legacy class alias for existing imports and tests.
MoltbotPromptRefiner = OpenClawPromptRefiner
