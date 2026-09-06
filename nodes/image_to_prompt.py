import logging
from functools import partial
from typing import Any

try:
    from ..services.image_utils import tensor_to_base64_png
    from ..services.llm_client import LLMClient
    from ..services.llm_output import (
        extract_json_object,
        sanitize_list_to_string,
        sanitize_string,
    )
except ImportError:
    from services.image_utils import tensor_to_base64_png
    from services.llm_client import LLMClient
    from services.llm_output import (
        extract_json_object,
        sanitize_list_to_string,
        sanitize_string,
    )

try:
    from ..services.metrics import metrics
except ImportError:
    from services.metrics import metrics

logger = logging.getLogger("ComfyUI-OpenClaw.nodes.ImageToPrompt")


class OpenClawImageToPrompt:
    """
    Experimental node that uses Vision LLM to generate prompt starters from an image.
    """

    def __init__(self) -> None:
        self.llm_client = LLMClient()

    def _get_request_llm_client(self) -> LLMClient:
        # CRITICAL: this node instance can persist across multiple UI runs.
        # Refresh the default LLMClient per call so provider/key changes from Settings/UI
        # apply without restarting ComfyUI. Preserve injected mocks/fakes for tests.
        if isinstance(self.llm_client, LLMClient):
            return LLMClient()
        return self.llm_client

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
                "goal": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "Describe this image for regeneration",
                    },
                ),
                "detail_level": (["low", "medium", "high"], {"default": "medium"}),
                "max_image_side": ("INT", {"default": 1024, "min": 256, "max": 1536}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("caption", "tags", "prompt_suggestion")
    FUNCTION = "generate_prompt"
    CATEGORY = "openclaw"

    # R154: keep the compatibility method name, but bind the shared helper
    # directly so node wrappers do not duplicate image conversion logic.
    _tensor_to_base64_png = staticmethod(
        partial(tensor_to_base64_png, context="ImageToPrompt")
    )

    def generate_prompt(
        self, image: Any, goal: str, detail_level: str, max_image_side: int
    ) -> tuple[str, str, str]:
        metrics.increment("vision_calls")

        # 1. Preprocess Image
        try:
            image_b64 = self._tensor_to_base64_png(image, max_image_side)
        except Exception as e:
            metrics.increment("errors")
            logger.error(f"Failed to preprocess image: {e}")
            raise ValueError(f"Image preprocessing failed: {e}")

        # 2. Construct System Prompt
        system_prompt = f"""
You are an expert AI art prompter. analyze the image and the user's goal.
Detail Level: {detail_level}

Output strictly valid JSON:
{{
  "caption": "Concise visual description",
  "tags": ["tag1", "tag2", "tag3"],
  "prompt_suggestion": "The actual prompt to generate this image"
}}

Do not use markdown blocks.
"""

        # 3. Construct User Message
        user_message = f"Goal: {goal}"

        try:
            # 4. Call Vision LLM
            logger.info("Sending vision request to LLM...")
            # IMPORTANT: resolve client at call time to avoid stale provider/key state.
            llm_client = self._get_request_llm_client()

            # Using updated client signature
            response = llm_client.complete(
                system=system_prompt, user_message=user_message, image_base64=image_b64
            )

            content = response.get("text", "")

            # Extract JSON using shared sanitizer (S3 defense)
            data = extract_json_object(content)

            if data is None:
                logger.warning("Failed to extract JSON from LLM response")
                metrics.increment("errors")
                return ("", "", "")

            # Extract with sanitization (only expected keys, treated as plain text)
            caption = sanitize_string(data.get("caption"), default="")
            tags_str = sanitize_list_to_string(data.get("tags"))
            prompt_suggestion = sanitize_string(
                data.get("prompt_suggestion"), default=""
            )

            return (caption, tags_str, prompt_suggestion)

        except Exception:
            metrics.increment("errors")
            logger.error("Failed to generate prompt from image", exc_info=True)
            raise


# IMPORTANT: keep legacy class alias for existing imports and tests.
MoltbotImageToPrompt = OpenClawImageToPrompt
