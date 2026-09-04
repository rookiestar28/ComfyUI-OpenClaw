"""
Connector State Management (F29 Phase 2).
Simple JSON persistence for offsets and cancel markers.
"""

import json
import logging
import os
from pathlib import PurePath
from typing import Dict, List, Set

from .path_validation import require_concrete_path

logger = logging.getLogger(__name__)

STATE_FILE = "connector_state.json"


class ConnectorState:
    def __init__(self, path: str | PurePath | None = None):
        validated_path = (
            require_concrete_path(path, field_name="path") if path is not None else None
        )
        self.path = validated_path or STATE_FILE
        self.data: Dict = {}
        self.cancelled_prompts: Set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state from {self.path}: {e}")
                self.data = {}

    def save(self):
        try:
            tmp_path = f"{self.path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.error(f"Failed to save state to {self.path}: {e}")

    # Offset Management
    def get_offset(self, platform: str) -> int:
        return self.data.get(f"{platform}_offset", 0)

    def set_offset(self, platform: str, offset: int):
        self.data[f"{platform}_offset"] = offset
        self.save()

    # Cancel Markers (Transient)
    def mark_cancelled(self, prompt_id: str):
        self.cancelled_prompts.add(prompt_id)

    def is_cancelled(self, prompt_id: str) -> bool:
        return prompt_id in self.cancelled_prompts
