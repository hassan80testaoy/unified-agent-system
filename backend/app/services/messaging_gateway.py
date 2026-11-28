"""Unified Multi-Platform Messaging Gateway (WhatsApp + Telegram + Email + Voice/TTS — Hermes/Instinct §8C, §8L.5)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.core import Workspace
from app.models.phase8 import (
    MessagingConversationMap,
    MessagingEventLog,
    MessagingGatewayConfig,
)
from app.models.v3_harness import ChatThread
from app.security.crypto import decrypt_secret, encrypt_secret
from app.security.sanitizer import scrub_text
from app.services.chat_agent import create_thread, process_user_message

CHANNELS = ("whatsapp", "telegram", "email", "voice")


def _get_or_create_channel(db: Session, workspace_id: str, channel: str) -> MessagingGatewayConfig:
    ch = channel.strip().lower()
    if ch not in CHANNELS:
        raise ValueError(f"Unsupported channel '{channel}'. Expected one of {CHANNELS}.")
    row = (
        db.query(MessagingGatewayConfig)
        .filter(MessagingGatewayConfig.workspace_id == workspace_id, MessagingGatewayConfig.channel == ch)
        .first()
    )
    if row is None:
        row = MessagingGatewayConfig(
            workspace_id=workspace_id,
            channel=ch,
            enabled=False,
            continuity_mode="shared_owner_thread",
            status="disconnected",
        )
        db.add(row)
        db.flush()
    return row


def _read_config(row: MessagingGatewayConfig) -> dict[str, Any]:
    if not row.config_enc:
        return {}
    try:
        data = json.loads(decrypt_secret(row.config_enc))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def serialize_gateway_channel(row: MessagingGatewayConfig) -> dict[str, Any]:
    cfg = _read_config(row)
    masked_keys = [k for k in cfg.keys()]
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "channel": row.channel,
        "enabled": bool(row.enabled),
        "continuity_mode": row.continuity_mode,
        "status": row.status,
        "configured_fields": masked_keys,
        "bot_username": cfg.get("bot_username") or "",
        "phone_number": cfg.get("phone_number") or "",
        "last_event_at": row.last_event_at.isoformat() if row.last_event_at else None,
        "setup_hint_ar": _channel_setup_hint_ar(row.channel),
    }


def _channel_setup_hint_ar(channel: str) -> str:
    hints = {
        "telegram": "أدخل Bot Token من @BotFather ثم انسخ رابط Webhook — لا حاجة لتعديل ملفات يدوياً.",
        "whatsapp": "أدخل Phone Number ID وAccess Token (أو رابط بوابة WhatsApp) ثم احفظ الاتصال.",
        "email": "يستخدم صناديق البريد المتصلة في /mailboxes مع التحقق بالرد فقط.",
        "voice": "يدعم تفريغ الملاحظات الصوتية (STT) وقراءة الردود صوتياً (TTS).",
    }
    return hints.get(channel, "")


def list_gateway_channels(db: Session, workspace_id: str) -> list[dict[str, Any]]:
    return [serialize_gateway_channel(_get_or_create_channel(db, workspace_id, ch)) for ch in CHANNELS]


def configure_gateway_channel(
    db: Session,
    *,
    workspace_id: str,
    channel: str,
    enabled: bool,
    config: dict[str, Any],
    webhook_secret: str | None = None,
    continuity_mode: str = "shared_owner_thread",
) -> dict[str, Any]:
    """Guided one-screen setup stored encrypted cleanly without touching config files (§8C.2)."""
    row = _get_or_create_channel(db, workspace_id, channel)
    merged = _read_config(row)
    for k, v in (config or {}).items():
        if v is None or v == "":
            continue
        merged[str(k)] = str(v) if not isinstance(v, (bool, int, float)) else v
    row.config_enc = encrypt_secret(json.dumps(merged))
    if webhook_secret:
        row.webhook_secret_enc = encrypt_secret(webhook_secret.strip())
    if continuity_mode in {"shared_owner_thread", "per_peer"}:
        row.continuity_mode = continuity_mode
    row.enabled = bool(enabled)
    row.status = "connected" if row.enabled else "disconnected"
    db.flush()
    return serialize_gateway_channel(row)


def verify_webhook_signature(row: MessagingGatewayConfig, raw_body: bytes, signature_header: str | None) -> bool:
    if not row.webhook_secret_enc:
        return True
    try:
        secret = decrypt_secret(row.webhook_secret_enc).encode("utf-8")
    except Exception:
        return False
    if not signature_header:
        return False
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.replace("sha256=", "").strip()
    return hmac.compare_digest(expected, provided)


def transcribe_voice_memo(audio_bytes: bytes | None = None, *, transcript_hint: str = "") -> str:
    """Transcribe an incoming voice memo (uses configured Whisper endpoint if available, or clean hint/fallback)."""
    if transcript_hint and transcript_hint.strip():
        return scrub_text(transcript_hint.strip())
    stt_url = os.environ.get("VOICE_STT_URL", "").strip()
    stt_key = os.environ.get("VOICE_STT_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
    if stt_url and stt_key and audio_bytes:
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(
                    stt_url,
                    headers={"Authorization": f"Bearer {stt_key}"},
                    files={"file": ("voice.webm", audio_bytes, "audio/webm")},
                    data={"model": "whisper-1"},
                )
            if resp.status_code < 400:
                txt = str((resp.json() or {}).get("text") or "").strip()
                if txt:
                    return scrub_text(txt)
        except Exception:
            pass
    return "[رسالة صوتية — أدخل مفتاح STT أو VOICE_STT_URL للتفريغ التلقائي]"


def _resolve_unified_thread(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    channel: str,
    peer_id: str,
    peer_name: str,
    continuity_mode: str,
) -> ChatThread:
    """Cross-platform continuity: shared_owner_thread maps WhatsApp/Telegram onto the SAME owner thread (§8C.1)."""
    if continuity_mode == "shared_owner_thread":
        any_map = (
            db.query(MessagingConversationMap)
            .filter(
                MessagingConversationMap.workspace_id == workspace_id,
                MessagingConversationMap.external_peer_id == f"owner:{workspace_id}",
            )
            .first()
        )
        if any_map:
            existing_th = db.get(ChatThread, any_map.thread_id)
            if existing_th and existing_th.workspace_id == workspace_id:
                any_map.last_message_at = datetime.now(timezone.utc)
                return existing_th

        # Also check exact (channel, peer_id) or latest gateway thread
        prev_map = (
            db.query(MessagingConversationMap)
            .filter(MessagingConversationMap.workspace_id == workspace_id)
            .order_by(MessagingConversationMap.updated_at.desc())
            .first()
        )
        if prev_map:
            th = db.get(ChatThread, prev_map.thread_id)
            if th and th.workspace_id == workspace_id:
                # Register mapping for this channel as well so both point to the exact same thread
                if not (
                    db.query(MessagingConversationMap)
                    .filter(
                        MessagingConversationMap.workspace_id == workspace_id,
                        MessagingConversationMap.channel == channel,
                        MessagingConversationMap.external_peer_id == peer_id,
                    )
                    .first()
                ):
                    db.add(
                        MessagingConversationMap(
                            workspace_id=workspace_id,
                            channel=channel,
                            external_peer_id=peer_id,
                            external_peer_name=peer_name or peer_id,
                            thread_id=th.id,
                            last_message_at=datetime.now(timezone.utc),
                        )
                    )
                return th

    peer_map = (
        db.query(MessagingConversationMap)
        .filter(
            MessagingConversationMap.workspace_id == workspace_id,
            MessagingConversationMap.channel == channel,
            MessagingConversationMap.external_peer_id == peer_id,
        )
        .first()
    )
    if peer_map:
        th = db.get(ChatThread, peer_map.thread_id)
        if th and th.workspace_id == workspace_id:
            peer_map.last_message_at = datetime.now(timezone.utc)
            return th

    th = create_thread(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        title="محادثة القنوات الموحدة (WhatsApp + Telegram)",
        mode_key="delegate",
    )
    db.flush()
    db.add(
        MessagingConversationMap(
            workspace_id=workspace_id,
            channel=channel,
            external_peer_id=peer_id,
            external_peer_name=peer_name or peer_id,
            thread_id=th.id,
            last_message_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    return th


async def handle_inbound_gateway_message(
    db: Session,
    *,
    workspace_id: str,
    channel: str,
    peer_id: str,
    text: str = "",
    peer_name: str = "",
    voice_transcript: str = "",
    mock_mode: bool = False,
) -> dict[str, Any]:
    """Process an inbound message from WhatsApp/Telegram/Email/Voice via the exact same Chat Agent & return outbound reply (§8C)."""
    gw = _get_or_create_channel(db, workspace_id, channel)
    ws = db.get(Workspace, workspace_id)
    owner_id = ws.owner_id if ws else "owner"

    effective_text = (text or "").strip()
    media_kind: str | None = None
    transcript: str | None = None
    if voice_transcript:
        media_kind = "voice"
        transcript = transcribe_voice_memo(transcript_hint=voice_transcript)
        effective_text = f"{effective_text}\n{transcript}".strip() if effective_text else transcript
    if not effective_text:
        raise ValueError("Inbound message text or voice transcript is required.")

    thread = _resolve_unified_thread(
        db,
        workspace_id=workspace_id,
        user_id=owner_id,
        channel=gw.channel,
        peer_id=(peer_id or "owner").strip(),
        peer_name=peer_name or peer_id or "Owner",
        continuity_mode=gw.continuity_mode,
    )

    in_log = MessagingEventLog(
        workspace_id=workspace_id,
        channel=gw.channel,
        direction="inbound",
        external_peer_id=(peer_id or "owner").strip(),
        thread_id=thread.id,
        content_preview=scrub_text(effective_text)[:400],
        media_kind=media_kind,
        transcript=transcript,
        status="received",
    )
    db.add(in_log)
    gw.last_event_at = datetime.now(timezone.utc)
    db.flush()

    res = await process_user_message(
        db,
        thread=thread,
        user_id=owner_id,
        content=effective_text,
        mock_mode=mock_mode,
    )
    reply_text = str((res.get("assistant_message") or {}).get("content") or "")

    # Deliver outbound response ONLY back to the requesting user on that channel (never unsolicited)
    delivery_status = deliver_channel_reply(db, gw, peer_id=(peer_id or "owner").strip(), reply_text=reply_text)
    out_log = MessagingEventLog(
        workspace_id=workspace_id,
        channel=gw.channel,
        direction="outbound",
        external_peer_id=(peer_id or "owner").strip(),
        thread_id=thread.id,
        content_preview=scrub_text(reply_text)[:400],
        status=delivery_status,
    )
    db.add(out_log)
    db.flush()
    return {
        "channel": gw.channel,
        "thread_id": thread.id,
        "continuity_mode": gw.continuity_mode,
        "reply": reply_text,
        "delivery_status": delivery_status,
        "intent": res.get("intent"),
        "loaded_memories": res.get("loaded_memories") or [],
    }


def deliver_channel_reply(db: Session, gw: MessagingGatewayConfig, *, peer_id: str, reply_text: str) -> str:
    """Deliver outbound reply over Telegram/WhatsApp HTTP API when credentials are configured, else record clean queued/local status."""
    cfg = _read_config(gw)
    if gw.channel == "telegram" and cfg.get("bot_token") and peer_id:
        try:
            with httpx.Client(timeout=8.0) as client:
                r = client.post(
                    f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
                    json={"chat_id": peer_id, "text": reply_text[:3800]},
                )
            return "sent_live" if r.status_code < 400 else f"http_{r.status_code}"
        except Exception as exc:
            return f"error_{type(exc).__name__}"
    if gw.channel == "whatsapp" and cfg.get("access_token") and cfg.get("phone_number_id") and peer_id:
        try:
            with httpx.Client(timeout=8.0) as client:
                r = client.post(
                    f"https://graph.facebook.com/v20.0/{cfg['phone_number_id']}/messages",
                    headers={"Authorization": f"Bearer {cfg['access_token']}"},
                    json={"messaging_product": "whatsapp", "to": peer_id, "type": "text", "text": {"body": reply_text[:3800]}},
                )
            return "sent_live" if r.status_code < 400 else f"http_{r.status_code}"
        except Exception as exc:
            return f"error_{type(exc).__name__}"
    return "recorded_local"


def deliver_scheduled_summary(
    db: Session,
    *,
    workspace_id: str,
    channels: list[str],
    summary_text: str,
) -> dict[str, str]:
    """Explicit owner-subscribed cron delivery across enabled platforms (§8H.7)."""
    outcomes: dict[str, str] = {}
    for ch in channels:
        if ch == "web":
            outcomes[ch] = "delivered_web"
            continue
        if ch not in CHANNELS:
            continue
        gw = _get_or_create_channel(db, workspace_id, ch)
        if not gw.enabled:
            outcomes[ch] = "skipped_disabled"
            continue
        cfg = _read_config(gw)
        peer = str(cfg.get("owner_chat_id") or cfg.get("phone_number") or "owner")
        status = deliver_channel_reply(db, gw, peer_id=peer, reply_text=summary_text)
        db.add(
            MessagingEventLog(
                workspace_id=workspace_id,
                channel=ch,
                direction="outbound",
                external_peer_id=peer,
                content_preview=scrub_text(summary_text)[:300],
                status=status,
            )
        )
        outcomes[ch] = status
    db.flush()
    return outcomes
