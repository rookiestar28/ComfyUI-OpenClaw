import json
import logging
from typing import Any

try:
    from ..services.planner import PlannerService
    from ..services.planner_registry import get_planner_registry
except ImportError as e:
    # Only fall back when ComfyUI loads this module without a proper package context.
    msg = str(e)
    if ("attempted relative import" in msg) or ("no known parent package" in msg):
        from services.planner import PlannerService
        from services.planner_registry import get_planner_registry
    else:
        raise

# Setup logger
logger = logging.getLogger("ComfyUI-OpenClaw.nodes.PromptPlanner")


class OpenClawPromptPlanner:
    """
    Experimental node that uses an LLM to plan the prompt and generation parameters.
    DELEGATES to services.planner.PlannerService (F8 Refactor).
    """

    def __init__(self) -> None:
        self.service = PlannerService()

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        profile_keys = [
            profile.id for profile in get_planner_registry().list_profiles()
        ]
        default_profile = get_planner_registry().get_default_profile_id()
        return {
            "required": {
                "profile": (profile_keys, {"default": default_profile}),
                "requirements": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": False,
                        "placeholder": "Describe what you want to see...",
                    },
                ),
                "style_directives": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": False,
                        "placeholder": "E.g. Photorealistic, 8k, cyberpunk...",
                    },
                ),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive", "negative", "params_json")
    FUNCTION = "plan_generation"
    CATEGORY = "openclaw"

    def plan_generation(
        self, profile: str, requirements: str, style_directives: str, seed: int
    ) -> tuple[str, str, str]:
        # Delegate to service
        positive, negative, params_dict = self.service.plan_generation(
            profile_id=profile,
            requirements=requirements,
            style_directives=style_directives,
            seed=seed,
        )

        # Node expects params as JSON string
        return (positive, negative, json.dumps(params_dict, indent=2))


# IMPORTANT: keep legacy class alias for existing imports and tests.
MoltbotPromptPlanner = OpenClawPromptPlanner
