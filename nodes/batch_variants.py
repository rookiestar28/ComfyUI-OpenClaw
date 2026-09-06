import json
import logging
import random
from typing import Any

try:
    from ..models.schemas import GenerationParams
except ImportError:
    from models.schemas import GenerationParams

try:
    from ..services.metrics import metrics
except ImportError:
    from services.metrics import metrics

logger = logging.getLogger("ComfyUI-OpenClaw.nodes.BatchVariants")


class OpenClawBatchVariants:
    """
    Generates deterministic variants for batch processing.
    """

    def __init__(self) -> None:
        pass

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "positive": ("STRING", {"multiline": True}),
                "negative": ("STRING", {"multiline": True}),
                "count": ("INT", {"default": 4, "min": 1, "max": 100}),
                "seed_base": (
                    "INT",
                    {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF},
                ),
                "seed_policy": (
                    ["fixed", "increment", "randomized"],
                    {"default": "increment"},
                ),
                "variant_policy": (
                    ["none", "cfg_sweep", "steps_sweep", "size_sweep"],
                    {"default": "none"},
                ),
            },
            "optional": {
                "params_json": ("STRING", {"multiline": True, "default": "{}"}),
                "sweep_start": ("FLOAT", {"default": 0.0, "step": 0.1}),
                "sweep_end": ("FLOAT", {"default": 0.0, "step": 0.1}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive_list", "negative_list", "params_json_list")
    OUTPUT_IS_LIST = (True, True, True)

    FUNCTION = "generate_variants"
    CATEGORY = "openclaw"

    def generate_variants(
        self,
        positive: str,
        negative: str,
        count: int,
        seed_base: int,
        seed_policy: str,
        variant_policy: str,
        params_json: str = "{}",
        sweep_start: float = 0.0,
        sweep_end: float = 0.0,
    ) -> tuple[list[str], list[str], list[str]]:
        metrics.increment("variants_calls")

        # 1. Parse Baseline Params
        try:
            base_dict = json.loads(params_json) if params_json.strip() else {}
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in params_json, using empty dict.")
            base_dict = {}

        pos_list = []
        neg_list = []
        params_list = []

        for i in range(count):
            # 2. Calculate Seed
            current_seed = seed_base
            if seed_policy == "increment":
                current_seed = seed_base + i
            elif seed_policy == "randomized":
                # Deterministic pseudo-random based on base+i
                # Using a simple LCG or hash for stability without numpy
                # seed = hash(f"{seed_base}_{i}") & 0xffffffffffffffff
                # But simple increment is usually what users want for "variations"
                # Let's stick to simple increment for now or random python if implied?
                # "randomized" usually means unpredictable.
                # Let's implement a simple hash for now to be deterministic but "jumpy"
                r = random.Random(seed_base + i)
                current_seed = r.randint(0, 0xFFFFFFFFFFFFFFFF)

            # 3. Apply Variant Policy (Sweep)
            # Create a fresh copy of base dict
            current_params = base_dict.copy()
            current_params["seed"] = current_seed

            if variant_policy != "none" and count > 1:
                # Interpolation factor (0.0 to 1.0)
                t = i / (count - 1)
                value = sweep_start + (sweep_end - sweep_start) * t

                if variant_policy == "cfg_sweep":
                    current_params["cfg"] = value
                elif variant_policy == "steps_sweep":
                    current_params["steps"] = int(round(value))
                elif variant_policy == "size_sweep":
                    # Assume value is the target "size" (width/height)
                    # We override both width and height to be square or keep aspect ratio?
                    # MVP: Set both width and height to the int(value)
                    # Use with caution
                    s = int(round(value))
                    current_params["width"] = s
                    current_params["height"] = s

            # 4. Validate & Clamp
            # Using GenerationParams.from_dict to enforce schema constraints
            validated = GenerationParams.from_dict(current_params)

            # 5. Append
            pos_list.append(positive)
            neg_list.append(negative)
            params_list.append(json.dumps(validated.dict(), indent=2))

        return (pos_list, neg_list, params_list)


# IMPORTANT: keep legacy class alias for existing imports and tests.
MoltbotBatchVariants = OpenClawBatchVariants
