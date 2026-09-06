import builtins
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

if __package__ and "." in __package__:
    from ..services.request_contracts import (
        MAX_BODY_SIZE,
        MAX_INPUT_STRING_LENGTH,
        MAX_JOB_ID_LENGTH,
        MAX_PROFILE_ID_LENGTH,
        MAX_TEMPLATE_ID_LENGTH,
        MAX_TRACE_ID_LENGTH,
        SCHEMA_VERSION,
        WEBHOOK_JOB_REQUEST_CONTRACT,
    )
else:  # pragma: no cover - top-level test import mode
    from services.request_contracts import (  # type: ignore
        MAX_BODY_SIZE,
        MAX_INPUT_STRING_LENGTH,
        MAX_JOB_ID_LENGTH,
        MAX_PROFILE_ID_LENGTH,
        MAX_TEMPLATE_ID_LENGTH,
        MAX_TRACE_ID_LENGTH,
        SCHEMA_VERSION,
        WEBHOOK_JOB_REQUEST_CONTRACT,
    )


@dataclass
class Profile:
    """
    Defines a generation profile (e.g., SDXL-v1, Flux-Dev).
    This acts as a preset identifier for the planner.
    """

    id: str
    version: str
    label: str
    description: str | None = None
    model_config_data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


@dataclass
class GenerationParams:
    """
    The concrete generation parameters produced by the planner.
    """

    width: int = 1024
    height: int = 1024
    steps: int = 20
    cfg: float = 7.0
    sampler_name: str = "euler"
    scheduler: str = "normal"
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validation / Clamping logic
        # Clamp ranges
        self.width = max(256, min(4096, self.width))
        self.height = max(256, min(4096, self.height))
        self.steps = max(1, min(100, self.steps))
        self.cfg = max(1.0, min(30.0, self.cfg))

        # Round dimensions to nearest 8
        self.width = (self.width // 8) * 8
        self.height = (self.height // 8) * 8

    def dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: builtins.dict[str, Any]) -> "GenerationParams":
        # Filter unrelated keys to avoid TypeError on init
        valid_keys = cls.__annotations__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class JobSpec:
    """
    A top-level wrapper identifying what needs to be done.
    """

    positive_prompt: str
    negative_prompt: str
    params: GenerationParams
    schema_version: str = SCHEMA_VERSION  # Literal equivalent
    metadata: dict[str, Any] = field(
        default_factory=dict
    )  # R25: Trace context, user tags, etc.

    def to_json(self) -> str:
        data = asdict(self)
        # Ensure params is also dictified if not already (asdict handles nested dataclasses)
        return json.dumps(data, indent=2)


@dataclass
class ParamPatch:
    """
    For defining partial updates to GenerationParams (refine loop).
    """

    target_field: str
    value: Any
    reason: str | None = None


@dataclass
class WebhookJobRequest:
    """
    Incoming webhook request schema (S2).
    Strict validation with length limits.
    """

    version: int
    template_id: str
    profile_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    job_id: str | None = None
    trace_id: str | None = None
    callback: dict[str, Any] | None = None  # F16: { url, method?, headers?, mode? }

    def __post_init__(self) -> None:
        # Version validation
        if self.version != 1:
            raise ValueError(f"Unsupported version: {self.version}")

        # Length limits
        if self.job_id and len(self.job_id) > MAX_JOB_ID_LENGTH:
            raise ValueError(f"job_id exceeds max length ({MAX_JOB_ID_LENGTH})")
        if self.trace_id and len(self.trace_id) > MAX_TRACE_ID_LENGTH:
            raise ValueError(f"trace_id exceeds max length ({MAX_TRACE_ID_LENGTH})")
        if self.trace_id and not re.match(r"^[a-zA-Z0-9_-]+$", self.trace_id):
            raise ValueError("trace_id contains invalid characters")
        if len(self.template_id) > MAX_TEMPLATE_ID_LENGTH:
            raise ValueError(
                f"template_id exceeds max length ({MAX_TEMPLATE_ID_LENGTH})"
            )
        if len(self.profile_id) > MAX_PROFILE_ID_LENGTH:
            raise ValueError(f"profile_id exceeds max length ({MAX_PROFILE_ID_LENGTH})")

        # Validate inputs (only allowed keys, string length limits)
        allowed_input_keys = set(WEBHOOK_JOB_REQUEST_CONTRACT["allowed_input_keys"])
        for key, value in self.inputs.items():
            if key not in allowed_input_keys:
                raise ValueError(f"Unknown input key: {key}")
            if isinstance(value, str) and len(value) > MAX_INPUT_STRING_LENGTH:
                raise ValueError(
                    f"Input '{key}' exceeds max length ({MAX_INPUT_STRING_LENGTH})"
                )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WebhookJobRequest":
        """Parse and validate from dict."""
        # 1. Check for unknown keys (Strict validation)
        allowed_top_level = set(WEBHOOK_JOB_REQUEST_CONTRACT["allowed_top_level"])
        unknown = set(data.keys()) - allowed_top_level
        if unknown:
            raise ValueError(f"Unknown fields: {unknown}")

        # 2. Check required fields
        required = set(WEBHOOK_JOB_REQUEST_CONTRACT["required_top_level"])
        missing = required - set(data.keys())
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        return cls(
            version=data["version"],
            template_id=data["template_id"],
            profile_id=data["profile_id"],
            inputs=data.get("inputs", {}),
            job_id=data.get("job_id"),
            trace_id=data.get("trace_id"),
            callback=data.get("callback"),
        )

    def to_normalized(self) -> dict[str, Any]:
        """Return normalized, validated representation."""
        return {
            "version": self.version,
            "job_id": self.job_id,
            "trace_id": self.trace_id,
            "template_id": self.template_id,
            "profile_id": self.profile_id,
            "inputs": self.inputs,
            "callback": self.callback,
        }
