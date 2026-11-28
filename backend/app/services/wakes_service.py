"""Event-driven Wakes (NO polling): fire on real incoming events (e.g. Gmail), run subscribed task once, post once in chat (§7B.1)."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db import utcnow
from app.models.agents import RunEvent
from app.models.core import Notification, Workspace
from app.models.instinct import WakeSubscription
from app.models.phase7 import WakeEventLog
from app.models.v3_harness import ChatMessage, ChatThread
from app.security.sanitizer import discard_ephemeral_secret, register_ephemeral_secret, scrub_text
from app.services.mailbox import find_verification_from_messages
from app.services.session_log import append_turn


def serialize_wake_subscription(sub: WakeSubscription) -> dict[str, Any]:
    return {
        "id": sub.id,
        "workspace_id": sub.workspace_id,
        "source": sub.source,
        "trigger_prompt": sub.trigger_prompt,
        "filter": sub.filter_json or {},
        "action_type": sub.action_type,
        "thread_id": sub.thread_id,
        "run_id": sub.run_id,
        "enabled": bool(sub.enabled),
        "last_fired_at": sub.last_fired_at.isoformat() if sub.last_fired_at else None,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


def create_wake_subscription(
    db: Session,
    *,
    workspace_id: str,
    source: str,
    trigger_prompt: str,
    filter_json: dict[str, Any] | None = None,
    action_type: str = "chat_notify",
    thread_id: str | None = None,
    run_id: str | None = None,
    enabled: bool = True,
) -> WakeSubscription:
    sub = WakeSubscription(
        workspace_id=workspace_id,
        source=(source or "gmail").strip().lower(),
        trigger_prompt=scrub_text(trigger_prompt).strip(),
        filter_json=dict(filter_json or {}),
        action_type=(action_type or "chat_notify").strip(),
        thread_id=thread_id,
        run_id=run_id,
        enabled=bool(enabled),
    )
    db.add(sub)
    db.flush()
    return sub


def _matches_filter(flt: dict[str, Any], payload: dict[str, Any]) -> bool:
    sender = str(payload.get("from") or payload.get("sender") or "").lower()
    subject = str(payload.get("subject") or "").lower()
    body = str(payload.get("body_text") or payload.get("snippet") or "").lower()

    fc = str(flt.get("from_contains") or "").strip().lower()
    if fc and fc not in sender:
        return False
    sc = str(flt.get("subject_contains") or "").strip().lower()
    if sc and sc not in subject:
        return False
    kw = str(flt.get("keyword") or "").strip().lower()
    if kw and kw not in f"{sender}\n{subject}\n{body}":
        return False
    return True


def _resolve_wake_chat_thread(db: Session, workspace_id: str, sub: WakeSubscription) -> ChatThread:
    if sub.thread_id:
        th = db.get(ChatThread, sub.thread_id)
        if th and th.workspace_id == workspace_id:
            return th
    th = (
        db.query(ChatThread)
        .filter(ChatThread.workspace_id == workspace_id)
        .order_by(ChatThread.updated_at.desc())
        .first()
    )
    if th is not None:
        return th
    th = ChatThread(workspace_id=workspace_id, title="تنبيهات الاستيقاظ الفوري (Wakes)", mode_key="delegate")
    db.add(th)
    db.flush()
    return th


def dispatch_wake_event(
    db: Session,
    *,
    workspace_id: str,
    source: str,
    event_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Process one incoming real event and fire matching enabled WakeSubscriptions at most once."""
    clean_source = (source or "gmail").strip().lower()
    ext_id = (event_id or "").strip()
    if not ext_id:
        raise ValueError("event_id is required for idempotent wake dispatch")

    subs = (
        db.query(WakeSubscription)
        .filter(
            WakeSubscription.workspace_id == workspace_id,
            WakeSubscription.source == clean_source,
            WakeSubscription.enabled.is_(True),
        )
        .all()
    )

    fired: list[dict[str, Any]] = []
    deduped: list[str] = []
    skipped = 0

    for sub in subs:
        if not _matches_filter(sub.filter_json or {}, payload):
            skipped += 1
            continue

        already = (
            db.query(WakeEventLog)
            .filter(
                WakeEventLog.workspace_id == workspace_id,
                WakeEventLog.subscription_id == sub.id,
                WakeEventLog.external_event_id == ext_id,
            )
            .first()
        )
        if already is not None:
            deduped.append(sub.id)
            continue

        sender = scrub_text(str(payload.get("from") or payload.get("sender") or clean_source))
        subject = scrub_text(str(payload.get("subject") or ""))
        body_text = str(payload.get("body_text") or payload.get("snippet") or "")

        action_note_ar = ""
        activation_found = False
        code_detected = False
        ephemeral_values: list[str] = []

        if clean_source == "gmail" and sub.action_type in {"activation", "auto", "chat_notify"}:
            from_addr = parseaddr(str(payload.get("from") or payload.get("sender") or ""))[1]
            site_dom = (
                str((sub.filter_json or {}).get("site_domain") or "").strip()
                or (from_addr.split("@")[-1] if "@" in from_addr else "")
                or "localhost"
            )
            to_addr = str(payload.get("to") or (sub.filter_json or {}).get("target_address") or "owner@localhost").strip()
            msg = EmailMessage()
            msg["From"] = from_addr or f"noreply@{site_dom}"
            msg["To"] = to_addr
            msg["Subject"] = subject or "Verification"
            msg["Date"] = format_datetime(datetime.now(timezone.utc))
            msg["Message-ID"] = f"<{ext_id}@wake.local>"
            msg.set_content(body_text or "")
            extracted = find_verification_from_messages(
                [msg.as_bytes()],
                site_domain=site_dom,
                target_address=to_addr,
            )
            if extracted:
                if extracted.get("activation_url"):
                    u = str(extracted["activation_url"])
                    ephemeral_values.append(u)
                    register_ephemeral_secret(u)
                    activation_found = True
                if extracted.get("code"):
                    c = str(extracted["code"])
                    ephemeral_values.append(c)
                    register_ephemeral_secret(c)
                    code_detected = True

        try:
            if activation_found or code_detected:
                kind_lbl = "رابط تفعيل" if activation_found else "كود تحقق"
                action_note_ar = f"تم التقاط {kind_lbl} بأمان عبر حدث Gmail حقيقي ومعالجته مؤقتاً دون تخزين القيمة السرّية."
            else:
                action_note_ar = scrub_text(body_text)[:220] or sub.trigger_prompt
        finally:
            for val in ephemeral_values:
                discard_ephemeral_secret(val)

        thread = _resolve_wake_chat_thread(db, workspace_id, sub)
        last_msg = (
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.seq.desc())
            .first()
        )
        next_seq = (last_msg.seq + 1) if last_msg else 1

        report_ar = (
            f"⚡ **استيقاظ فوري على حدث حقيقي (`{clean_source}`):**\n"
            f"- الاشتراك: {sub.trigger_prompt}\n"
            f"- المرسل / العنوان: `{sender}` — {subject or 'بدون عنوان'}\n"
            f"- النتيجة: {action_note_ar}"
        )
        msg = ChatMessage(
            workspace_id=workspace_id,
            thread_id=thread.id,
            seq=next_seq,
            role="assistant",
            intent="wake",
            card_type="wake_event",
            card_payload={
                "subscription_id": sub.id,
                "source": clean_source,
                "event_id": ext_id,
                "activation_found": activation_found,
                "code_detected": code_detected,
                "run_id": sub.run_id,
                "is_mock": False,
            },
            content=scrub_text(report_ar),
        )
        db.add(msg)
        db.flush()

        append_turn(
            thread.id,
            {
                "type": "wake_event",
                "seq": next_seq,
                "subscription_id": sub.id,
                "source": clean_source,
                "external_event_id": ext_id,
                "activation_found": activation_found,
                "code_detected": code_detected,
            },
        )

        if sub.run_id:
            db.add(
                RunEvent(
                    workspace_id=workspace_id,
                    run_id=sub.run_id,
                    level="info",
                    kind="wake_event",
                    message=scrub_text(f"Wake '{sub.trigger_prompt}' fired on {clean_source} event {ext_id}"),
                    data={"subscription_id": sub.id, "activation_found": activation_found, "code_detected": code_detected},
                )
            )

        ws = db.get(Workspace, workspace_id)
        if ws and ws.owner_id:
            db.add(
                Notification(
                    workspace_id=workspace_id,
                    user_id=ws.owner_id,
                    kind="wake_event",
                    title=f"استيقاظ {clean_source}: {sub.trigger_prompt[:80]}",
                    body=scrub_text(action_note_ar)[:240],
                    link="/goals",
                )
            )

        now = utcnow()
        sub.last_fired_at = now
        ev_log = WakeEventLog(
            workspace_id=workspace_id,
            subscription_id=sub.id,
            source=clean_source,
            external_event_id=ext_id,
            status="fired",
            summary=scrub_text(action_note_ar)[:400],
            chat_thread_id=thread.id,
            chat_message_id=msg.id,
            payload_json={
                "from": sender[:120],
                "subject": subject[:160],
                "activation_found": activation_found,
                "code_detected": code_detected,
            },
            fired_at=now,
        )
        db.add(ev_log)
        db.flush()
        fired.append(
            {
                "log_id": ev_log.id,
                "subscription_id": sub.id,
                "thread_id": thread.id,
                "message_id": msg.id,
                "activation_found": activation_found,
                "code_detected": code_detected,
                "summary": action_note_ar,
            }
        )

    return {
        "source": clean_source,
        "event_id": ext_id,
        "fired_count": len(fired),
        "deduplicated_count": len(deduped),
        "skipped_count": skipped,
        "fired": fired,
        "deduplicated_subscription_ids": deduped,
    }


def parse_gmail_push_webhook(body: dict[str, Any]) -> dict[str, Any]:
    """Decode Gmail Pub/Sub webhook wrapper or direct Gmail event dict into normalized event fields."""
    msg = body.get("message")
    if isinstance(msg, dict) and msg.get("data"):
        raw_b64 = str(msg["data"]).strip()
        padded = raw_b64 + "=" * (-len(raw_b64) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        event_id = str(msg.get("messageId") or decoded.get("event_id") or decoded.get("historyId") or "")
        return {
            "event_id": event_id,
            "from": decoded.get("from") or decoded.get("emailAddress") or "",
            "subject": decoded.get("subject") or "",
            "body_text": decoded.get("body_text") or decoded.get("snippet") or "",
        }
    return {
        "event_id": str(body.get("event_id") or body.get("message_id") or body.get("historyId") or ""),
        "from": str(body.get("from") or ""),
        "subject": str(body.get("subject") or ""),
        "body_text": str(body.get("body_text") or body.get("snippet") or ""),
    }
