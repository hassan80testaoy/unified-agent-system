"""Chat-First General Agent orchestrator backed by SQLite/PostgreSQL + append-only NDJSON log."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.agents import Run
from app.models.backlinks import BlLink, BlSite
from app.models.google_account import GoogleAccount
from app.models.mailboxes import Mailbox
from app.models.providers import LlmCall
from app.models.v3_harness import ChatMessage, ChatThread, ModePreset
from app.security.crypto import decrypt_secret
from app.security.sanitizer import scrub_text
from app.services.browser_loop import run_flagship_backlink_loop
from app.services.events import ws_hub
from app.services.intent_router import classify_intent
from app.services.llm_router import LLMRouter
from app.services.modes import seed_default_modes
from app.services.runtime_checks import validate_real_email
from app.services.session_log import append_turn, get_session_log_path, rewind_log


def create_thread(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    title: str = "محادثة جديدة",
    mode_key: str = "delegate",
    project_key: str = "default",
    approval_mode: str = "ask_first",
) -> ChatThread:
    seed_default_modes(db, workspace_id)
    thread = ChatThread(
        workspace_id=workspace_id,
        user_id=user_id,
        title=title.strip() or "محادثة جديدة",
        mode_key=mode_key,
        project_key=project_key,
        approval_mode=approval_mode,
        status="idle",
    )
    db.add(thread)
    db.flush()
    path = get_session_log_path(thread.id)
    thread.ndjson_path = str(path)
    append_turn(
        thread.id,
        {
            "type": "session_start",
            "seq": 0,
            "workspace_id": workspace_id,
            "mode_key": mode_key,
            "approval_mode": approval_mode,
            "title": thread.title,
        },
    )
    db.flush()
    return thread


def _next_seq(db: Session, thread_id: str) -> int:
    last = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.seq.desc())
        .first()
    )
    return (last.seq + 1) if last else 1


def _handle_flights(db: Session, workspace_id: str, text: str) -> dict[str, Any]:
    router = LLMRouter(db, workspace_id)
    try:
        res = router.route(
            task_type="flights_search",
            tier="cheap",
            prompt=(
                "User asked about flights / travel:\n"
                f"{text}\n\n"
                "Provide a concise Arabic checklist of how to search live fares (Kayak / Google Flights / Skyscanner), "
                "flexible date tips, and state honestly that live ticket prices require opening the live browser search tool."
            ),
            use_cache=True,
        )
        llm_note = res.get("raw_text", "")
    except Exception:
        llm_note = ""

    content = (
        "✈️ **توجيه لوكيل الطيران (Flights — غير مرتبط بالباك لينك):**\n"
        "لم يتم حجز تذكرة حقيقية بعد لأن الأسعار اللحظية تتطلب فحصاً حياً عبر المتصفح.\n"
        + (f"\n{llm_note}" if llm_note else "")
    )
    return {
        "content": content.strip(),
        "card_type": "approval",
        "card_payload": {
            "action": "browser_flight_search",
            "query": scrub_text(text),
            "requires_approval": True,
            "note": "لن يتم الدفع أو الحجز إلا بموافقتك الصريحة.",
        },
    }


def _handle_mail(db: Session, workspace_id: str) -> dict[str, Any]:
    mailboxes = db.query(Mailbox).filter(Mailbox.workspace_id == workspace_id).all()
    g_accts = db.query(GoogleAccount).filter(GoogleAccount.workspace_id == workspace_id).all()

    if not mailboxes and not g_accts:
        content = (
            "📬 **فحص البريد (Agent Mail — قراءة فقط):**\n"
            "لا يوجد صندوق بريد IMAP أو حساب Google مربوط حالياً في مساحة العمل.\n"
            "يمكنك ربط حسابك من الإعدادات → حسابات Google أو البريد."
        )
    else:
        lines = ["📬 **ملخص صناديق البريد المتصلة (قراءة فقط):**"]
        for m in mailboxes:
            lines.append(f"- صندوق IMAP: {m.email} (الحالة: {m.status})")
        for g in g_accts:
            lines.append(f"- حساب Google: {g.label} · {g.email_masked} (الحالة: {g.status})")
        content = "\n".join(lines)

    return {"content": content, "card_type": None, "card_payload": None}


def _handle_link_monitor(db: Session, workspace_id: str) -> dict[str, Any]:
    links = db.query(BlLink).filter(BlLink.workspace_id == workspace_id).all()
    live = sum(1 for ln in links if ln.live)
    lost = sum(1 for ln in links if not ln.live)
    dofollow = sum(1 for ln in links if ln.live and ln.link_type == "dofollow")
    return {
        "content": (
            f"🔎 **مراقبة الروابط (Link Monitor):**\n"
            f"- إجمالي الروابط المسجلة: {len(links)}\n"
            f"- الروابط الحية: {live} (منها {dofollow} dofollow)\n"
            f"- روابط غير ظاهرة / مفقودة: {lost}"
        ),
        "card_type": None,
        "card_payload": None,
    }


def _handle_reports(db: Session, workspace_id: str) -> dict[str, Any]:
    sites = db.query(BlSite).filter(BlSite.workspace_id == workspace_id).count()
    links = db.query(BlLink).filter(BlLink.workspace_id == workspace_id).count()
    runs = db.query(Run).filter(Run.workspace_id == workspace_id).count()
    calls = db.query(LlmCall).filter(LlmCall.workspace_id == workspace_id).all()
    spend = round(sum(float(c.cost_usd or 0.0) for c in calls), 4)
    return {
        "content": (
            "📊 **التقرير السريع من البيانات الفعلية:**\n"
            f"- مواقع الباك لينك: {sites}\n"
            f"- الروابط المسجلة: {links}\n"
            f"- التشغيلات: {runs}\n"
            f"- إجمالي إنفاق النماذج: ${spend}"
        ),
        "card_type": None,
        "card_payload": None,
    }


async def process_user_message(
    db: Session,
    *,
    thread: ChatThread,
    user_id: str,
    content: str,
    mock_mode: bool = True,
    simulate_captcha: bool = False,
    simulate_2fa: bool = False,
) -> dict[str, Any]:
    """Persist user message, route intent cleanly, produce assistant reply/card, and append NDJSON."""
    clean_text = scrub_text(content).strip()
    user_seq = _next_seq(db, thread.id)

    route_info = classify_intent(clean_text)
    intent = route_info["intent"]
    entities = route_info.get("entities") or {}

    user_msg = ChatMessage(
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        seq=user_seq,
        role="user",
        content=clean_text,
        intent=intent,
    )
    db.add(user_msg)
    db.flush()
    append_turn(
        thread.id,
        {"type": "message", "seq": user_seq, "role": "user", "intent": intent, "content": clean_text},
    )

    # Set thread title automatically on first message
    if user_seq == 1 and thread.title == "محادثة جديدة" and clean_text:
        thread.title = clean_text[:64]

    mode_row = (
        db.query(ModePreset)
        .filter(ModePreset.workspace_id == thread.workspace_id, ModePreset.key == thread.mode_key)
        .first()
    )

    reply_payload: dict[str, Any]
    if intent == "flights":
        reply_payload = _handle_flights(db, thread.workspace_id, clean_text)
    elif intent == "agent-mail":
        reply_payload = _handle_mail(db, thread.workspace_id)
    elif intent == "link_monitor":
        reply_payload = _handle_link_monitor(db, thread.workspace_id)
    elif intent == "reports":
        reply_payload = _handle_reports(db, thread.workspace_id)
    elif intent == "backlink":
        domain = entities.get("domain")
        email = entities.get("email")
        if not email:
            active_google = (
                db.query(GoogleAccount)
                .filter(GoogleAccount.workspace_id == thread.workspace_id, GoogleAccount.status == "active")
                .first()
            )
            if active_google and active_google.email_enc:
                email = decrypt_secret(active_google.email_enc)
        if not domain or not email:
            reply_payload = {
                "content": (
                    "🔗 **وكيل الباك لينك جاهز (Flagship Loop):**\n"
                    "يرجى تحديد اسم الموقع المستهدف والبريد الإلكتروني الحقيقي للتسجيل (مثال: `سجل في about.me بالإيميل yourreal@gmail.com`)."
                ),
                "card_type": None,
                "card_payload": {"need": ["domain", "email"], "mock_mode": bool(mock_mode)},
            }
        else:
            ok_email, email_err = validate_real_email(email)
            if not ok_email:
                reply_payload = {
                    "content": email_err or "يرجى إدخال بريد إلكتروني حقيقي للتسجيل.",
                    "card_type": None,
                    "card_payload": {"status": "invalid_email", "mock_mode": bool(mock_mode)},
                }
            else:
                loop_res = await run_flagship_backlink_loop(
                    db,
                    workspace_id=thread.workspace_id,
                    user_id=user_id,
                    domain=domain,
                    email=email,
                    mock_mode=bool(mock_mode),
                    simulate_captcha=simulate_captcha,
                    simulate_2fa=simulate_2fa,
                )
                loop_res.setdefault("mock_mode", bool(mock_mode))
                reply_payload = {
                    "content": loop_res["report_ar"],
                    "card_type": loop_res.get("card_type"),
                    "card_payload": loop_res,
                }
    elif intent == "coding":
        router = LLMRouter(db, thread.workspace_id)
        try:
            res = router.route(
                task_type="coding",
                tier="mid",
                prompt=clean_text,
                system_prompt="You are a general-purpose software engineer assistant. Provide a concise Arabic plan and next action.",
                use_cache=False,
            )
            ans = res.get("raw_text") or ""
        except Exception:
            ans = ""
        reply_payload = {
            "content": (
                "💻 **تم توجيه طلبك إلى وكيل البرمجة العام (Coding Agent):**\n"
                + (ans or "يمكنك فتح صفحة **وكيل البرمجة (`/coding`)** لإنشاء الخطة ومعاينة التصميم والتشغيل في المتصفح الحي.")
            ),
            "card_type": None,
            "card_payload": {"mode": thread.mode_key, "mock_mode": bool(mock_mode), "coding_route": "/coding"},
        }
    else:
        router = LLMRouter(db, thread.workspace_id)
        sys_prompt = mode_row.system_prompt if mode_row else "You are a helpful Arabic-first assistant."
        if thread.compacted_summary:
            sys_prompt += f"\n\n[Previous compacted context]:\n{thread.compacted_summary}"
        try:
            res = router.route(
                task_type="chat",
                tier="cheap",
                prompt=clean_text,
                system_prompt=sys_prompt,
                use_cache=False,
            )
            answer = res.get("raw_text") or "تم استلام رسالتك."
        except Exception:
            answer = "تم استلام رسالتك. يرجى ضبط مزود ذكاء اصطناعي نشط في الإعدادات للحصول على إجابة من النموذج."
        reply_payload = {"content": answer, "card_type": None, "card_payload": {"mode": thread.mode_key}}

    asst_seq = user_seq + 1
    asst_msg = ChatMessage(
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        seq=asst_seq,
        role="assistant",
        content=scrub_text(reply_payload["content"]),
        intent=intent,
        card_type=reply_payload.get("card_type"),
        card_payload=reply_payload.get("card_payload"),
    )
    db.add(asst_msg)
    db.commit()
    db.refresh(user_msg)
    db.refresh(asst_msg)

    append_turn(
        thread.id,
        {
            "type": "message",
            "seq": asst_seq,
            "role": "assistant",
            "intent": intent,
            "card_type": asst_msg.card_type,
            "content": asst_msg.content,
        },
    )

    await ws_hub.broadcast(
        f"thread:{thread.id}",
        {
            "type": "message",
            "thread_id": thread.id,
            "message": {
                "id": asst_msg.id,
                "seq": asst_msg.seq,
                "role": asst_msg.role,
                "content": asst_msg.content,
                "intent": asst_msg.intent,
                "card_type": asst_msg.card_type,
                "card_payload": asst_msg.card_payload,
            },
        },
    )

    return {
        "thread_id": thread.id,
        "intent": intent,
        "mock_mode": bool(mock_mode),
        "routing": route_info,
        "user_message": {
            "id": user_msg.id,
            "seq": user_msg.seq,
            "role": "user",
            "content": user_msg.content,
            "intent": user_msg.intent,
        },
        "assistant_message": {
            "id": asst_msg.id,
            "seq": asst_msg.seq,
            "role": "assistant",
            "content": asst_msg.content,
            "intent": asst_msg.intent,
            "card_type": asst_msg.card_type,
            "card_payload": asst_msg.card_payload,
        },
    }


def compact_thread(db: Session, thread: ChatThread) -> dict[str, Any]:
    """Summarize older messages in-place without deleting history, appending a compact turn to NDJSON."""
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id, ChatMessage.rewound.is_(False))
        .order_by(ChatMessage.seq.asc())
        .all()
    )
    if len(msgs) <= 2:
        summary = " | ".join(f"{m.role}: {m.content[:80]}" for m in msgs)
    else:
        older = msgs[:-2]
        summary = "\n".join(f"[{m.seq}] {m.role} ({m.intent or 'general'}): {m.content[:140]}" for m in older)

    thread.compacted_summary = summary
    db.commit()
    append_turn(
        thread.id,
        {"type": "compact", "compacted_count": max(0, len(msgs) - 2), "summary": summary},
    )
    return {"thread_id": thread.id, "compacted_summary": summary, "messages_total": len(msgs)}


def undo_thread(db: Session, thread: ChatThread, to_seq: int | None = None) -> dict[str, Any]:
    """Rewind conversation & NDJSON log cleanly back to `to_seq` (or before last turn)."""
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id, ChatMessage.rewound.is_(False))
        .order_by(ChatMessage.seq.asc())
        .all()
    )
    if not msgs:
        return {"thread_id": thread.id, "to_seq": 0, "rewound_count": 0}

    if to_seq is None:
        max_seq = msgs[-1].seq
        # Undo last assistant+user exchange when user invokes Undo
        target = max(0, max_seq - 2)
    else:
        target = max(0, to_seq)

    rewound_count = 0
    for m in msgs:
        if m.seq > target:
            m.rewound = True
            rewound_count += 1
    db.commit()
    rewind_log(thread.id, target)
    return {"thread_id": thread.id, "to_seq": target, "rewound_count": rewound_count}
