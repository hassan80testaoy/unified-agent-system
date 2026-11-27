"""Dashboard notifications & optional Telegram alert delivery (never includes secrets)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.core import Notification
from app.security.sanitizer import scrub_text

logger = logging.getLogger(__name__)


def create_notification(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    link: str | None = None,
) -> Notification:
    note = Notification(
        workspace_id=workspace_id,
        user_id=user_id,
        kind=kind,
        title=scrub_text(title),
        body=scrub_text(body),
        link=link,
    )
    db.add(note)
    db.flush()
    return note


async def send_telegram_alert(text: str) -> dict[str, Any]:
    """Send optional Telegram alert if TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID are configured."""
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_chat_id.strip()
    if not token or not chat_id:
        return {"sent": False, "reason": "telegram_not_configured"}

    safe_text = scrub_text(text)[:3500]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": safe_text})
            return {"sent": resp.status_code == 200, "status_code": resp.status_code}
    except Exception as exc:
        logger.warning("Telegram alert delivery failed: %s", type(exc).__name__)
        return {"sent": False, "reason": type(exc).__name__}
