"""Provider-Based Image Generation Tool & Artifact Output (§8G — configured via Settings/.env, never hardcoded)."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import struct
from typing import Any
import zlib

import httpx
from sqlalchemy.orm import Session

from app.config import get_env_secret_by_ref, get_settings
from app.db import new_uuid
from app.models.agents import Artifact
from app.models.providers import ApiKey, Provider
from app.security.crypto import decrypt_secret
from app.security.sanitizer import scrub_text
from app.services.artifact_builder import serialize_platform_artifact


def _images_dir() -> Path:
    d = get_settings().artifacts_dir / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_minimal_png_bytes(width: int = 64, height: int = 64, rgb: tuple[int, int, int] = (14, 165, 233)) -> bytes:
    """Create a valid uncompressed RGB PNG entirely in Python stdlib (no Pillow required)."""
    raw_row = b"\x00" + bytes(rgb) * width
    raw_data = raw_row * height
    compressed = zlib.compress(raw_data, 9)

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")


def _resolve_image_provider(db: Session, workspace_id: str) -> tuple[str, str, str, str] | None:
    """Return `(provider_id, api_base, api_key, default_model)` for configured image provider, or None."""
    env_base = os.environ.get("IMAGE_PROVIDER_BASE_URL", "").strip()
    env_key = os.environ.get("IMAGE_API_KEY", "").strip() or os.environ.get("FAL_KEY", "").strip()
    env_model = os.environ.get("IMAGE_DEFAULT_MODEL", "").strip() or "gpt-image-1"
    if env_base and env_key:
        return "custom-image", env_base.rstrip("/"), env_key, env_model

    for pid, default_model in (("openai", "gpt-image-1"), ("xai", "grok-2-image"), ("openrouter-free", "openai/gpt-image-1")):
        prov = db.get(Provider, pid)
        if not prov or not prov.enabled:
            continue
        krow = (
            db.query(ApiKey)
            .filter(ApiKey.workspace_id == workspace_id, ApiKey.provider == pid)
            .order_by(ApiKey.created_at.desc())
            .first()
        )
        key_val = decrypt_secret(krow.secret_enc) if krow else get_env_secret_by_ref(prov.auth_ref)
        if key_val:
            return pid, (prov.api_base or "https://api.openai.com/v1").rstrip("/"), key_val, env_model if os.environ.get("IMAGE_DEFAULT_MODEL") else default_model
    return None


def generate_image_artifact(
    db: Session,
    *,
    workspace_id: str,
    prompt: str,
    thread_id: str | None = None,
    size: str = "1024x1024",
    mock_mode: bool = False,
    model_override: str | None = None,
) -> dict[str, Any]:
    """Generate a real image artifact via Settings-configured provider, or honest stop / explicit simulation (§8G)."""
    clean_prompt = scrub_text(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("Image prompt is required.")

    art_id = new_uuid()
    img_path = _images_dir() / f"{art_id}.png"

    resolved = _resolve_image_provider(db, workspace_id)
    if not resolved and not mock_mode:
        return {
            "status": "missing_image_provider",
            "is_mock": False,
            "missing_config": ["OPENAI_API_KEY / XAI_API_KEY / IMAGE_API_KEY"],
            "report_ar": (
                "🎨 **تعذر توليد الصورة حقيقياً لعدم توفر مزود صور مفعّل:**\n"
                "- أضف مفتاح المزود (`OPENAI_API_KEY` أو `XAI_API_KEY` أو مزود صور مخصص) من صفحة **الإعدادات ← المزودين** ثم أعد المحاولة، أو فعّل وضع المحاكاة للمعاينة التجريبية."
            ),
        }

    is_mock_out = False
    model_used = model_override or "local-sim-png"
    provider_used = "mock-local"

    if resolved and not mock_mode:
        provider_used, api_base, api_key, default_model = resolved
        model_used = (model_override or default_model).strip()
        try:
            with httpx.Client(timeout=45.0) as client:
                resp = client.post(
                    f"{api_base}/images/generations",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model_used, "prompt": clean_prompt, "size": size, "response_format": "b64_json"},
                )
            if resp.status_code >= 400:
                return {
                    "status": "provider_error",
                    "is_mock": False,
                    "error": f"{provider_used}_http_{resp.status_code}",
                    "report_ar": f"⛔ تعذر توليد الصورة من `{provider_used}` (HTTP {resp.status_code}).",
                }
            data = ((resp.json() or {}).get("data") or [{}])[0]
            if data.get("b64_json"):
                img_bytes = base64.b64decode(data["b64_json"])
            elif data.get("url"):
                with httpx.Client(timeout=30.0) as dl:
                    img_bytes = dl.get(str(data["url"])).content
            else:
                return {
                    "status": "empty_image_response",
                    "is_mock": False,
                    "report_ar": f"⛔ استجابة مزود الصور `{provider_used}` لم تحتوِ على بيانات صورة صالحة.",
                }
            img_path.write_bytes(img_bytes)
        except Exception as exc:
            return {
                "status": "provider_error",
                "is_mock": False,
                "error": type(exc).__name__,
                "report_ar": f"⛔ تعذر الاتصال بمزود الصور `{provider_used}` ({type(exc).__name__}).",
            }
    else:
        is_mock_out = True
        img_path.write_bytes(_make_minimal_png_bytes())

    art = Artifact(
        id=art_id,
        workspace_id=workspace_id,
        thread_id=None,
        run_id=None,
        kind="image",
        path=str(img_path),
        size=img_path.stat().st_size,
        metadata_json={
            "title": clean_prompt[:80],
            "prompt": clean_prompt[:300],
            "provider": provider_used,
            "model": model_used,
            "chat_thread_id": thread_id,
            "is_mock": is_mock_out,
        },
    )
    db.add(art)
    db.flush()
    ser = serialize_platform_artifact(art)
    prefix = "محاكاة — " if is_mock_out else ""
    return {
        "status": "simulated" if is_mock_out else "completed",
        "is_mock": is_mock_out,
        "artifact": ser,
        "artifact_id": art.id,
        "preview_url": ser["preview_url"],
        "download_url": ser["download_url"],
        "report_ar": (
            f"{prefix}🖼️ **تم توليد الصورة وحفظها كمُخرَج مستقل:**\n"
            f"- **المزود:** `{provider_used}` (`{model_used}`)\n"
            f"- **المعاينة:** `{ser['preview_url']}` · **التنزيل:** `{ser['download_url']}`"
        ),
    }
