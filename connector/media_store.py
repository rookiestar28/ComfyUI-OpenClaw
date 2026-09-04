"""
Media Store for F33 (LINE Image Delivery).
Handles temporary storage of images and signed URL generation.
"""

import hashlib
import hmac
import io
import logging
import secrets
import time
from pathlib import Path
from typing import Optional, Tuple

from .config import ConnectorConfig
from .path_validation import PathInput, require_concrete_path

logger = logging.getLogger(__name__)


class MediaStore:
    def __init__(
        self, config: ConnectorConfig, storage_path: Optional[PathInput] = None
    ):
        self.config = config
        # Secret for signing tokens.
        # Uses admin token if available (persistence), else random (reset on restart).
        self._secret = (config.admin_token or secrets.token_hex(32)).encode("utf-8")

        # Resolve media directory
        validated_storage_path = (
            require_concrete_path(storage_path, field_name="storage_path")
            if storage_path is not None
            else None
        )
        if validated_storage_path:
            self.media_dir = Path(validated_storage_path)
        else:
            # Default to sibling of state_path, or ./media
            raw_state_path = self.config.state_path
            validated_state_path = (
                require_concrete_path(raw_state_path, field_name="state_path")
                if raw_state_path is not None
                else None
            )
            if validated_state_path:
                state_p = Path(validated_state_path)
                # If state_path looks like a file (has suffix), use parent. Else use it as dir.
                base = state_p.parent if state_p.suffix else state_p
            else:
                base = Path.cwd()
            self.media_dir = base / "media"

        self._ensure_dir()

    def _ensure_dir(self):
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def store_image(self, image_bytes: bytes, ext: str, channel_id: str) -> str:
        """
        Store image bytes and return a signed token.
        Token includes filename, channel_id, and expiry.
        """
        # Periodic cleanup (10% chance per write)
        if secrets.randbelow(10) == 0:
            self.cleanup()

        # Enforce size limit (check before write)
        if len(image_bytes) > self.config.media_max_mb * 1024 * 1024:
            logger.warning("Image exceeds media_max_mb")
            raise ValueError("Image too large")

        filename = f"{secrets.token_hex(8)}{ext}"
        path = self.media_dir / filename

        with open(path, "wb") as f:
            f.write(image_bytes)

        expiry = int(time.time() + self.config.media_ttl_sec)
        token = self._generate_token(filename, channel_id, expiry)
        return token

    def build_preview(
        self, image_bytes: bytes, max_px: int = 240, max_bytes: int = 900 * 1024
    ) -> Optional[bytes]:
        try:
            from PIL import Image
        except Exception:
            return None

        try:
            img = Image.open(io.BytesIO(image_bytes))
            img = img.convert("RGB")
            img.thumbnail((max_px, max_px))
        except Exception:
            return None

        for quality in (80, 70, 60, 50, 40):
            buf = io.BytesIO()
            try:
                img.save(buf, format="JPEG", quality=quality, optimize=True)
            except Exception:
                return None
            data = buf.getvalue()
            if len(data) <= max_bytes:
                return data
        return data

    def get_image_path(self, token: str) -> Optional[Path]:
        """
        Validate token and return file path if valid and not expired.
        """
        payload = self._decode_token(token)
        if not payload:
            return None

        filename, _, expiry = payload

        if int(time.time()) > expiry:
            logger.debug(f"Media token expired for {filename}")
            return None

        path = self.media_dir / filename
        if not path.exists():
            return None

        # Prevent path traversal
        try:
            path.resolve().relative_to(self.media_dir)
        except ValueError:
            logger.warning(f"Path traversal attempt: {filename}")
            return None

        return path

    def cleanup(self):
        """Remove files older than TTL."""
        now = time.time()
        count = 0
        try:
            if not self.media_dir.exists():
                return
            for p in self.media_dir.iterdir():
                if not p.is_file():
                    continue
                # Use mtime + TTL + buffer (60s)
                if p.stat().st_mtime < (now - self.config.media_ttl_sec - 60):
                    p.unlink(missing_ok=True)
                    count += 1
            if count > 0:
                logger.info(f"Cleaned up {count} expired media files")
        except Exception as e:
            logger.error(f"Media cleanup error: {e}")

    def _generate_token(self, filename: str, channel_id: str, expiry: int) -> str:
        # Payload: filename:channel_id:expiry
        payload = f"{filename}:{channel_id}:{expiry}"
        sig = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        # Encode payload to hex to be URL-safe and avoid delimiter confusion
        payload_hex = payload.encode("utf-8").hex()
        return f"{payload_hex}.{sig}"

    def _decode_token(self, token: str) -> Optional[Tuple[str, str, int]]:
        try:
            parts = token.split(".")
            if len(parts) != 2:
                return None

            payload_hex, sig = parts
            payload_str = bytes.fromhex(payload_hex).decode("utf-8")

            # Verify signature
            expected_sig = hmac.new(
                self._secret, payload_str.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig, expected_sig):
                logger.warning("Invalid media token signature")
                return None

            filename, channel_id, expiry_str = payload_str.split(":", 2)
            return filename, channel_id, int(expiry_str)
        except Exception as e:
            logger.debug(f"Token decode error: {e}")
            return None
