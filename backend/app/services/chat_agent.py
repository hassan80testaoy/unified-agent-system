"""Chat-First General Agent orchestrator backed by SQLite/PostgreSQL + append-only NDJSON log."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
import time
from typing import Any, AsyncGenerator

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.agents import Run
from app.models.backlinks import BlLink, BlSite
from app.models.google_account import GoogleAccount
from app.models.mailboxes import Mailbox
from app.models.providers import LlmCall
from app.models.v3_harness import ChatMessage, ChatThread, ModePreset
from app.security.crypto import decrypt_secret
from app.security.sanitizer import scrub_text
from app.services.at_mentions import expand_at_mentions
from app.services.browser_agent import run_general_browser_agent
from app.services.browser_loop import run_flagship_backlink_loop
from app.services.chat_output_guard import StreamingOutputFilter, sanitize_chat_output
from app.services.events import ws_hub
from app.services.goal_planner import plan_goal_from_sentence
from app.services.gsc_service import fetch_gsc_report
from app.services.intent_router import classify_intent
from app.services.llm_router import LLMRouter
from app.services.memory_service import (
    auto_writeback_memory,
    build_memory_context_block,
    compact_thread_to_memory,
    index_chat_message_fts,
)
from app.services.modes import seed_default_modes
from app.services.runtime_checks import get_runtime_readiness, validate_real_email
from app.services.session_log import append_turn, get_session_log_path, rewind_log
from app.services.site_auditor import audit_site_url


_THREAD_CONTROLLERS: dict[str, dict[str, Any]] = {}


def _get_controller(thread_id: str) -> dict[str, Any]:
    ctrl = _THREAD_CONTROLLERS.get(thread_id)
    if ctrl is None:
        ev = asyncio.Event()
        ev.set()
        ctrl = {"status": "idle", "resume_ev": ev, "paused": False, "steering": []}
        _THREAD_CONTROLLERS[thread_id] = ctrl
    return ctrl


def pause_thread_run(db: Session, thread: ChatThread) -> dict[str, Any]:
    thread.paused = True
    if thread.status == "running":
        thread.status = "paused"
    ctrl = _get_controller(thread.id)
    ctrl["paused"] = True
    ctrl["status"] = "paused"
    ctrl["resume_ev"].clear()
    db.commit()
    append_turn(thread.id, {"type": "control", "action": "pause"})
    return {"thread_id": thread.id, "paused": True, "status": thread.status}


def resume_thread_run(db: Session, thread: ChatThread) -> dict[str, Any]:
    thread.paused = False
    if thread.status == "paused":
        thread.status = "running"
    ctrl = _get_controller(thread.id)
    ctrl["paused"] = False
    ctrl["status"] = "running" if thread.id in _ACTIVE_STREAMS else "idle"
    ctrl["resume_ev"].set()
    db.commit()
    append_turn(thread.id, {"type": "control", "action": "resume"})
    return {"thread_id": thread.id, "paused": False, "status": thread.status}


def steer_thread_run(db: Session, thread: ChatThread, instruction: str, user_id: str) -> dict[str, Any]:
    """Queue a mid-run edit/correction onto a thread so running or resumed execution incorporates it immediately (§8.0.3)."""
    clean = scrub_text(instruction or "").strip()
    if not clean:
        raise ValueError("Steering instruction cannot be empty.")
    pending = list(thread.pending_steering_json or [])
    entry = {"instruction": clean, "ts": datetime.now(timezone.utc).isoformat()}
    pending.append(entry)
    thread.pending_steering_json = pending
    flag_modified(thread, "pending_steering_json")

    ctrl = _get_controller(thread.id)
    ctrl["steering"].append(clean)

    seq = _next_seq(db, thread.id)
    msg = ChatMessage(
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        seq=seq,
        role="user",
        content=f"[توجيه أثناء التنفيذ] {clean}",
        intent="steer",
    )
    db.add(msg)
    db.flush()
    index_chat_message_fts(
        db,
        message_id=msg.id,
        thread_id=thread.id,
        workspace_id=thread.workspace_id,
        user_id=user_id,
        role="user",
        content=msg.content,
    )
    append_turn(thread.id, {"type": "steer", "seq": seq, "instruction": clean})
    db.commit()
    return {
        "thread_id": thread.id,
        "queued_count": len(pending),
        "status": thread.status,
        "paused": bool(thread.paused),
        "steering_message_id": msg.id,
    }


def _consume_pending_steering(db: Session, thread: ChatThread) -> list[str]:
    pending = [str(item.get("instruction") or "") for item in (thread.pending_steering_json or []) if isinstance(item, dict)]
    ctrl = _get_controller(thread.id)
    while ctrl["steering"]:
        val = ctrl["steering"].pop(0)
        if val not in pending:
            pending.append(val)
    if thread.pending_steering_json:
        thread.pending_steering_json = []
        flag_modified(thread, "pending_steering_json")
        db.flush()
    return [p for p in pending if p]


def get_threads_live_status(db: Session, workspace_id: str) -> list[dict[str, Any]]:
    rows = (
        db.query(ChatThread)
        .filter(ChatThread.workspace_id == workspace_id)
        .order_by(ChatThread.updated_at.desc())
        .limit(100)
        .all()
    )
    out: list[dict[str, Any]] = []
    for t in rows:
        ctrl = _THREAD_CONTROLLERS.get(t.id) or {}
        eff_status = "paused" if t.paused else (ctrl.get("status") or t.status or "idle")
        out.append(
            {
                "thread_id": t.id,
                "title": t.title,
                "status": eff_status,
                "paused": bool(t.paused),
                "autonomy_mode": getattr(t, "autonomy_mode", "checkpoint"),
                "pending_steering_count": len(t.pending_steering_json or []),
                "loaded_memories_count": len(getattr(t, "loaded_memory_ids_json", None) or []),
            }
        )
    return out


def _build_turn_stats(
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    context_window: int = 32768,
    cache_hit: bool = False,
    model_latency_ms: int = 0,
    tool_latency_ms: int = 0,
    total_latency_ms: int | None = None,
    model_used: str | None = None,
    provider_id: str | None = None,
    routing_profile: str | None = None,
    cost_usd: float = 0.0,
) -> dict[str, Any]:
    used = int(tokens_in or 0) + int(tokens_out or 0)
    win = max(1024, int(context_window or 32768))
    tot_ms = int(total_latency_ms if total_latency_ms is not None else (int(model_latency_ms or 0) + int(tool_latency_ms or 0)))
    return {
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "context_tokens_used": used,
        "context_window": win,
        "context_usage_pct": round(min(100.0, (used / win) * 100.0), 2),
        "cache_hit": bool(cache_hit),
        "model_latency_ms": int(model_latency_ms or 0),
        "tool_latency_ms": int(tool_latency_ms or 0),
        "total_latency_ms": tot_ms,
        "model_used": model_used,
        "provider_id": provider_id,
        "routing_profile": routing_profile,
        "cost_usd": round(float(cost_usd or 0.0), 6),
    }


def create_thread(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    title: str = "محادثة جديدة",
    mode_key: str = "delegate",
    project_key: str = "default",
    approval_mode: str = "ask_first",
    autonomy_mode: str = "checkpoint",
    agent_id: str | None = None,
) -> ChatThread:
    seed_default_modes(db, workspace_id)
    thread = ChatThread(
        workspace_id=workspace_id,
        user_id=user_id,
        title=title.strip() or "محادثة جديدة",
        mode_key=mode_key,
        project_key=project_key,
        approval_mode=approval_mode,
        autonomy_mode=autonomy_mode if autonomy_mode in {"autonomous", "checkpoint", "supervised"} else "checkpoint",
        agent_id=agent_id,
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


async def _dispatch_platform_special_intent(
    db: Session,
    *,
    thread: ChatThread,
    user_id: str,
    intent: str,
    clean_text: str,
    entities: dict[str, Any],
    mock_mode: bool,
) -> dict[str, Any] | None:
    """Shared DRY dispatcher for specialized platform & Phase 8 intents across sync & SSE paths."""
    if intent == "flights":
        return _handle_flights(db, thread.workspace_id, clean_text)
    if intent == "agent-mail":
        return _handle_mail(db, thread.workspace_id)
    if intent == "link_monitor":
        return _handle_link_monitor(db, thread.workspace_id)
    if intent == "reports":
        return _handle_reports(db, thread.workspace_id)
    if intent == "gsc_report":
        g_res = fetch_gsc_report(db, workspace_id=thread.workspace_id, days=28)
        return {"content": g_res["report_ar"], "card_type": "gsc_report", "card_payload": {**g_res, "is_mock": False}}
    if intent == "site_audit":
        target_aud_url = str(entities.get("url") or (f"https://{entities['domain']}" if entities.get("domain") else "")).strip()
        if not target_aud_url:
            return {
                "content": "🔍 **فاحص الموقع (Site Auditor):** يرجى ذكر رابط الموقع المطلوب فحصه (مثال: `افحص موقع https://example.com`).",
                "card_type": None,
                "card_payload": {"need": ["url"], "is_mock": False},
            }
        aud_res = await audit_site_url(db, workspace_id=thread.workspace_id, url=target_aud_url)
        return {"content": aud_res["report_md"], "card_type": "site_audit", "card_payload": {**aud_res, "is_mock": False}}
    if intent == "goal_planner":
        gp_res = await plan_goal_from_sentence(
            db,
            workspace_id=thread.workspace_id,
            user_id=user_id,
            sentence=clean_text,
            thread_id=thread.id,
            execute_auto_steps=True,
        )
        return {"content": gp_res["report_ar"], "card_type": "goal_plan", "card_payload": {"goal": gp_res["goal"], "is_mock": False}}
    if intent == "register_skill":
        from app.services.skill_builder import register_skill_from_prompt

        sk = register_skill_from_prompt(db, workspace_id=thread.workspace_id, prompt=clean_text, user_id=user_id)
        return {
            "content": f"🧩 **تم تسجيل المهارة الجديدة بنجاح:** `{sk['slug']}`\n- الاسم: {sk['name']}\n- الوصف: {sk['description']}\n- الملفات: `SKILL.md` + `skill.py` + `tests/test_skill.py` — جاهزة للاستدعاء الفوري في الدردشة ومتجر `/skills`.",
            "card_type": "skill_registered",
            "card_payload": {"skill": sk, "is_mock": False},
        }
    if intent == "add_backlink_site":
        from app.services.backlink_library import discover_and_add_library_site

        target_site = str(entities.get("url") or entities.get("domain") or "").strip()
        if not target_site:
            return {
                "content": "🌐 يرجى كتابة اسم النطاق أو الرابط لإضافته إلى مكتبة الباك لينك (مثال: `ضيف موقع dev.to لقائمة الباك لينك`).",
                "card_type": None,
                "card_payload": {"is_mock": False},
            }
        entry = await discover_and_add_library_site(db, workspace_id=thread.workspace_id, domain_or_url=target_site)
        return {
            "content": (
                f"🌐 **تمت إضافة `{entry['domain']}` إلى مكتبة مواقع الباك لينك مع دليل Playbook:**\n"
                f"- رابط التسجيل: `{entry['signup_url']}`\n"
                f"- نوع التسجيل المكتشف: `{entry['signup_flow']}`\n"
                f"- الحقول: {', '.join(entry['discovered_fields']) or 'email, website, bio'}"
            ),
            "card_type": "backlink_library_site",
            "card_payload": {"site": entry, "is_mock": False},
        }
    if intent == "self_develop":
        from app.services.self_dev_service import propose_self_dev_worktree

        prop = propose_self_dev_worktree(db, workspace_id=thread.workspace_id, user_id=user_id, thread_id=thread.id, prompt=clean_text)
        return {
            "content": (
                f"🛠️ **مقترح التطوير الذاتي في Worktree معزول (`{prop['branch_name']}`):**\n"
                f"- العنوان: {prop['title']}\n"
                f"- الحالة: `{prop['status']}` · نتيجة الاختبارات: `{'ناجحة' if prop['tests_passed'] else 'قيد المراجعة'}`\n"
                "- لن يتم الدمج في `main` أبداً إلا بعد موافقتك الصريحة عبر زر الاعتماد."
            ),
            "card_type": "self_dev_approval",
            "card_payload": {"proposal": prop, "requires_approval": True, "is_mock": False},
        }
    if intent == "generate_image":
        from app.services.image_gen_service import generate_image_artifact

        res_img = generate_image_artifact(
            db,
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            prompt=clean_text,
            mock_mode=bool(mock_mode),
        )
        return {"content": res_img["report_ar"], "card_type": "image_artifact", "card_payload": res_img}
    if intent == "build_app":
        from app.services.artifact_builder import build_web_app_artifact

        app_res = build_web_app_artifact(db, workspace_id=thread.workspace_id, thread_id=thread.id, prompt=clean_text)
        return {"content": app_res["report_ar"], "card_type": "web_app_artifact", "card_payload": app_res}
    if intent == "wide_research":
        from app.services.wide_research import run_wide_research

        raw_items = [s.strip() for s in re.split(r"[,،\n]+", clean_text) if s.strip()][:20]
        if len(raw_items) < 2:
            raw_items = [clean_text, f"{clean_text} — بدائل ومقارنة", f"{clean_text} — مراجعة تقنية"]
        wr = run_wide_research(db, workspace_id=thread.workspace_id, thread_id=thread.id, query=clean_text, items=raw_items)
        return {"content": wr["report_ar"], "card_type": "wide_research", "card_payload": wr}
    if intent == "create_agent":
        from app.services.agent_delegation import create_custom_agent_from_prompt

        ag = create_custom_agent_from_prompt(db, workspace_id=thread.workspace_id, prompt=clean_text)
        return {
            "content": f"🤖 **تم إنشاء الوكيل المخصص:** `{ag['name_ar']}` (`{ag['key']}`)\n- الوضع: `{ag['mode_key']}` · النموذج: `{ag.get('model_name') or 'auto'}`",
            "card_type": "agent_created",
            "card_payload": {"agent": ag, "is_mock": False},
        }
    if intent in {"slash_test", "slash_lint", "slash_git_undo"}:
        from app.services.dev_tools_runner import run_workspace_checks

        cmd_kind = "test" if intent == "slash_test" else ("lint" if intent == "slash_lint" else "git_undo")
        chk = run_workspace_checks(action=cmd_kind)
        return {"content": chk["summary_ar"], "card_type": "dev_check", "card_payload": {**chk, "is_mock": False}}
    if intent == "general":
        from app.services.skill_builder import try_match_and_run_prompt_skill

        skill_hit = try_match_and_run_prompt_skill(db, workspace_id=thread.workspace_id, prompt=clean_text)
        if skill_hit is not None:
            return {
                "_override_intent": f"skill:{skill_hit['slug']}",
                "content": skill_hit["output_ar"],
                "card_type": "skill_execution",
                "card_payload": {"skill_slug": skill_hit["slug"], "result": skill_hit["result"], "is_mock": False},
            }
    return None


def _inject_thread_memory_and_steering(
    db: Session,
    *,
    thread: ChatThread,
    user_id: str,
    clean_text: str,
) -> tuple[str, list[dict[str, Any]], str]:
    """Expand @-mentions, pull USER.md/MEMORY.md + vector + FTS5 recall, drain mid-run steering, and write back durable facts."""
    mention_ctx, _ = expand_at_mentions(clean_text)
    mem_ctx, loaded_mems = build_memory_context_block(
        db,
        workspace_id=thread.workspace_id,
        user_id=user_id,
        query=clean_text,
        current_thread_id=thread.id,
    )
    auto_writeback_memory(db, workspace_id=thread.workspace_id, user_id=user_id, text=clean_text)
    if loaded_mems:
        thread.loaded_memory_ids_json = [str(m["id"]) for m in loaded_mems[:12]]

    steers = _consume_pending_steering(db, thread)
    effective_prompt = clean_text
    if steers:
        effective_prompt += "\n\n[تعديل/توجيه المالك أثناء التشغيل — يجب أخذه في الحسبان]:\n- " + "\n- ".join(steers)
    if mention_ctx:
        effective_prompt += f"\n\n[سياق @-mention المرفق]:\n{mention_ctx}"

    recent_msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id, ChatMessage.rewound.is_(False))
        .order_by(ChatMessage.seq.desc())
        .limit(8)
        .all()
    )
    history_lines = [
        f"{m.role}: {scrub_text(m.content)[:260]}"
        for m in reversed(recent_msgs[1:])  # exclude the just-added current message
    ]
    if history_lines:
        mem_ctx = (mem_ctx + "\n\n## Recent Thread History\n" + "\n".join(history_lines)).strip()
    return mem_ctx, loaded_mems, effective_prompt


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
    """Persist user message, route intent cleanly with Memory & Output Guard, produce reply/card, and append NDJSON."""
    clean_text = scrub_text(content).strip()
    user_seq = _next_seq(db, thread.id)

    ctrl = _get_controller(thread.id)
    ctrl["status"] = "paused" if thread.paused else "running"
    thread.status = "paused" if thread.paused else "running"

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
    index_chat_message_fts(
        db,
        message_id=user_msg.id,
        thread_id=thread.id,
        workspace_id=thread.workspace_id,
        user_id=user_id,
        role="user",
        content=clean_text,
    )
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
    mem_ctx, loaded_mems, effective_prompt = _inject_thread_memory_and_steering(
        db, thread=thread, user_id=user_id, clean_text=clean_text
    )

    timeline: list[dict[str, Any]] = [
        {"stage": "received", "label_ar": "استلام الطلب", "ts": datetime.now(timezone.utc).isoformat()},
        {"stage": "memory", "label_ar": f"تحميل {len(loaded_mems)} سياق ذاكرة", "ts": datetime.now(timezone.utc).isoformat()},
        {"stage": "intent", "label_ar": f"توجيه المسار ({intent})", "ts": datetime.now(timezone.utc).isoformat()},
    ]

    t_msg_start = time.monotonic()
    reply_payload: dict[str, Any]
    try:
        special = await _dispatch_platform_special_intent(
            db,
            thread=thread,
            user_id=user_id,
            intent=intent,
            clean_text=effective_prompt,
            entities=entities,
            mock_mode=bool(mock_mode),
        )
        if special is not None:
            override_intent = special.pop("_override_intent", None)
            if override_intent:
                intent = str(override_intent)
                user_msg.intent = intent
            reply_payload = special
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
                        thread_id=thread.id,
                    )
                    reply_payload = {
                        "content": loop_res["report_ar"],
                        "card_type": loop_res.get("card_type"),
                        "card_payload": loop_res,
                    }
        elif intent == "browser":
            t_tool0 = time.monotonic()
            b_res = await run_general_browser_agent(
                db,
                thread_id=thread.id,
                workspace_id=thread.workspace_id,
                user_id=user_id,
                prompt=effective_prompt,
                entities=entities,
                mock_mode=bool(mock_mode),
            )
            tool_ms = int((time.monotonic() - t_tool0) * 1000)
            reply_payload = {
                "content": b_res["report_ar"],
                "card_type": b_res.get("card_type"),
                "card_payload": {
                    "status": b_res.get("status"),
                    "current_url": b_res.get("current_url"),
                    "page_title": b_res.get("page_title"),
                    "network_path": b_res.get("network_path"),
                    "proxy_fallback_reason": b_res.get("proxy_fallback_reason"),
                    "screenshots": b_res.get("screenshots") or [],
                    "links": b_res.get("links") or [],
                    "stages": b_res.get("stages") or [],
                    "runtime_status": b_res.get("runtime_status"),
                    "model_used": b_res.get("model_used"),
                    "latency_ms": b_res.get("latency_ms"),
                    "stats": _build_turn_stats(
                        model_latency_ms=int(b_res.get("latency_ms") or 0),
                        tool_latency_ms=tool_ms,
                        total_latency_ms=tool_ms,
                        model_used=b_res.get("model_used"),
                        routing_profile="research",
                    ),
                    "is_mock": bool(b_res.get("is_mock", False)),
                },
            }
        elif intent == "coding":
            router = LLMRouter(db, thread.workspace_id)
            coding_sys = "You are a general-purpose software engineer assistant. Provide a concise Arabic plan and next action."
            if mem_ctx:
                coding_sys += f"\n\n{mem_ctx}"
            try:
                res = router.route(
                    task_type="coding",
                    tier="mid",
                    toolsets=["terminal", "file"],
                    prompt=effective_prompt,
                    system_prompt=coding_sys,
                    use_cache=False,
                )
                ans, _ = sanitize_chat_output(res.get("raw_text") or "")
                is_mock_coding = bool(res.get("is_mock", False))
                stats = _build_turn_stats(
                    tokens_in=int(res.get("tokens_in") or 0),
                    tokens_out=int(res.get("tokens_out") or 0),
                    cache_hit=bool(res.get("cached")),
                    model_latency_ms=int(res.get("latency_ms") or 0),
                    model_used=res.get("model"),
                    provider_id=res.get("provider_id"),
                    routing_profile=res.get("routing_profile") or "coding",
                    cost_usd=float(res.get("cost_usd") or 0.0),
                )
            except Exception:
                ans = ""
                is_mock_coding = False
                stats = _build_turn_stats(routing_profile="coding")
            reply_payload = {
                "content": (
                    "💻 **تم توجيه طلبك إلى وكيل البرمجة العام (Coding Agent):**\n"
                    + (ans or "يمكنك فتح صفحة **وكيل البرمجة (`/coding`)** لإنشاء الخطة ومعاينة التصميم والتشغيل في المتصفح الحي.")
                ),
                "card_type": None,
                "card_payload": {"mode": thread.mode_key, "is_mock": is_mock_coding, "coding_route": "/coding", "stats": stats},
            }
        else:
            router = LLMRouter(db, thread.workspace_id)
            sys_prompt = (
                (mode_row.system_prompt if mode_row else "You are a helpful Arabic-first assistant.")
                + "\nRespond to the user in clear Arabic or English text. NEVER output raw <tool_call> tags or raw internal JSON plan objects."
            )
            readiness = get_runtime_readiness(db, thread.workspace_id)
            if readiness.get("browser_worker"):
                sys_prompt += "\nThe platform has a live Playwright browser online; never claim you cannot browse the web."
            if thread.compacted_summary:
                sys_prompt += f"\n\n[Previous compacted context]:\n{thread.compacted_summary}"
            if mem_ctx:
                sys_prompt += f"\n\n{mem_ctx}"
            t_start = time.monotonic()
            model_used = None
            latency_ms = None
            is_mock_reply = False
            stats = _build_turn_stats(routing_profile="chat")
            try:
                res = router.route(
                    task_type="chat",
                    tier="cheap",
                    goal_text=clean_text,
                    prompt=effective_prompt,
                    system_prompt=sys_prompt,
                    use_cache=False,
                )
                cleaned_ans, _ = sanitize_chat_output(res.get("raw_text") or "")
                answer = cleaned_ans or "لم يُصدر النموذج نصاً قابلاً للعرض؛ يُرجى إعادة المحاولة أو التحقق من المزود في الإعدادات."
                model_used = res.get("model")
                latency_ms = res.get("latency_ms")
                is_mock_reply = bool(res.get("is_mock"))
                stats = _build_turn_stats(
                    tokens_in=int(res.get("tokens_in") or 0),
                    tokens_out=int(res.get("tokens_out") or 0),
                    cache_hit=bool(res.get("cached")),
                    model_latency_ms=int(latency_ms or 0),
                    total_latency_ms=int((time.monotonic() - t_start) * 1000),
                    model_used=model_used,
                    provider_id=res.get("provider_id"),
                    routing_profile=res.get("routing_profile") or "chat",
                    cost_usd=float(res.get("cost_usd") or 0.0),
                )
            except Exception as exc:
                answer = f"تعذر إتمام الاتصال بمزود الذكاء الاصطناعي حالياً ({type(exc).__name__}). يُرجى التحقق من المزود النشط في الإعدادات."
                latency_ms = int((time.monotonic() - t_start) * 1000)
                stats = _build_turn_stats(total_latency_ms=latency_ms, routing_profile="chat")
            if is_mock_reply and not answer.startswith("محاكاة — "):
                answer = f"محاكاة — {answer}"
            elif not is_mock_reply and answer.startswith("محاكاة — "):
                answer = answer[len("محاكاة — ") :].lstrip()
            reply_payload = {
                "content": answer,
                "card_type": None,
                "card_payload": {
                    "mode": thread.mode_key,
                    "model_used": model_used,
                    "latency_ms": latency_ms,
                    "stats": stats,
                    "is_mock": is_mock_reply,
                },
            }

        safe_content, guard_meta = sanitize_chat_output(reply_payload["content"])
        if not safe_content:
            safe_content = "تم تنفيذ الخطوة بنجاح (راجِع السجل أو المخرجات المرفقة)."
        reply_payload["content"] = safe_content

        cp_obj = dict(reply_payload.get("card_payload") or {})
        timeline.append({"stage": "completed", "label_ar": "اكتمل التنفيذ", "ts": datetime.now(timezone.utc).isoformat()})
        cp_obj["timeline"] = timeline
        cp_obj["loaded_memories"] = loaded_mems
        cp_obj["guard_meta"] = {k: v for k, v in guard_meta.items() if k != "extracted_calls"}
        if "stats" not in cp_obj:
            elapsed_ms = int((time.monotonic() - t_msg_start) * 1000)
            cp_obj["stats"] = _build_turn_stats(
                tool_latency_ms=elapsed_ms,
                total_latency_ms=elapsed_ms,
                model_used=cp_obj.get("model_used"),
            )
        reply_payload["card_payload"] = cp_obj

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
        db.flush()
        index_chat_message_fts(
            db,
            message_id=asst_msg.id,
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            user_id=user_id,
            role="assistant",
            content=asst_msg.content,
        )
        thread.status = "paused" if thread.paused else "done"
        ctrl["status"] = thread.status
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
            "loaded_memories": loaded_mems,
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
    except Exception:
        thread.status = "idle"
        ctrl["status"] = "idle"
        raise


def compact_thread(db: Session, thread: ChatThread, user_id: str | None = None) -> dict[str, Any]:
    """Summarize older messages AND preserve key facts into long-term memory (§8K.3)."""
    uid = user_id or thread.user_id
    res = compact_thread_to_memory(db, thread, uid)
    db.commit()
    return res


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


_ACTIVE_STREAMS: dict[str, asyncio.Event] = {}


def cancel_active_stream(thread_id: str) -> bool:
    ev = _ACTIVE_STREAMS.get(thread_id)
    if ev is not None:
        ev.set()
        return True
    return False


async def stream_user_message(
    db: Session,
    *,
    thread: ChatThread,
    user_id: str,
    content: str,
    mock_mode: bool = False,
    simulate_captcha: bool = False,
    simulate_2fa: bool = False,
    disconnected_check: Any = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream chat replies token-by-token (SSE payloads) with cancel, budget/killswitch guard, & model/latency tracking."""
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
    if user_seq == 1 and thread.title == "محادثة جديدة" and clean_text:
        thread.title = clean_text[:64]
    db.commit()
    db.refresh(user_msg)
    append_turn(
        thread.id,
        {"type": "message", "seq": user_seq, "role": "user", "intent": intent, "content": clean_text},
    )

    ctrl = _get_controller(thread.id)
    ctrl["status"] = "paused" if thread.paused else "running"
    thread.status = "paused" if thread.paused else "running"
    index_chat_message_fts(
        db,
        message_id=user_msg.id,
        thread_id=thread.id,
        workspace_id=thread.workspace_id,
        user_id=user_id,
        role="user",
        content=clean_text,
    )
    mem_ctx, loaded_mems, effective_prompt = _inject_thread_memory_and_steering(
        db, thread=thread, user_id=user_id, clean_text=clean_text
    )
    timeline: list[dict[str, Any]] = [
        {"stage": "received", "label_ar": "استلام الطلب", "ts": datetime.now(timezone.utc).isoformat()},
        {"stage": "memory", "label_ar": f"تحميل {len(loaded_mems)} سياق ذاكرة", "ts": datetime.now(timezone.utc).isoformat()},
        {"stage": "intent", "label_ar": f"توجيه المسار ({intent})", "ts": datetime.now(timezone.utc).isoformat()},
    ]

    yield {
        "event": "start",
        "data": {
            "thread_id": thread.id,
            "user_seq": user_seq,
            "intent": intent,
            "mock_mode": bool(mock_mode),
            "loaded_memories": loaded_mems,
        },
    }

    cancel_ev = asyncio.Event()
    _ACTIVE_STREAMS[thread.id] = cancel_ev
    try:
        # Specialized workflows execute via shared dispatcher and emit live progress + sanitized tokens
        special_intents = {
            "flights",
            "agent-mail",
            "link_monitor",
            "reports",
            "backlink",
            "browser",
            "goal_planner",
            "gsc_report",
            "site_audit",
            "register_skill",
            "add_backlink_site",
            "self_develop",
            "generate_image",
            "build_app",
            "wide_research",
            "create_agent",
            "slash_test",
            "slash_lint",
            "slash_git_undo",
        }
        pre_special = None
        if intent == "general":
            pre_special = await _dispatch_platform_special_intent(
                db,
                thread=thread,
                user_id=user_id,
                intent=intent,
                clean_text=effective_prompt,
                entities=entities,
                mock_mode=bool(mock_mode),
            )
            if pre_special is not None:
                override_intent = pre_special.pop("_override_intent", None)
                if override_intent:
                    intent = str(override_intent)
                    user_msg.intent = intent

        if intent in special_intents or pre_special is not None:
            t0 = time.monotonic()
            model_used: str | None = None
            card_type: str | None = None
            card_payload: dict[str, Any] | None = None
            progress_q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def _push_progress(step_info: dict[str, Any]) -> None:
                await progress_q.put(step_info)

            special_res = pre_special if pre_special is not None else await _dispatch_platform_special_intent(
                db,
                thread=thread,
                user_id=user_id,
                intent=intent,
                clean_text=effective_prompt,
                entities=entities,
                mock_mode=bool(mock_mode),
            )
            if special_res is not None:
                r = special_res
            elif intent == "browser":
                async def _run_browser() -> dict[str, Any]:
                    try:
                        return await run_general_browser_agent(
                            db,
                            thread_id=thread.id,
                            workspace_id=thread.workspace_id,
                            user_id=user_id,
                            prompt=effective_prompt,
                            entities=entities,
                            mock_mode=bool(mock_mode),
                            on_progress=_push_progress,
                            cancel_event=cancel_ev,
                        )
                    finally:
                        await progress_q.put(None)

                task = asyncio.create_task(_run_browser())
                while True:
                    if disconnected_check and await disconnected_check():
                        cancel_ev.set()
                    item = await progress_q.get()
                    if item is None:
                        break
                    yield {"event": "progress", "data": item}
                b_res = await task
                model_used = b_res.get("model_used")
                r = {
                    "content": b_res["report_ar"],
                    "card_type": b_res.get("card_type"),
                    "card_payload": {
                        "status": b_res.get("status"),
                        "current_url": b_res.get("current_url"),
                        "page_title": b_res.get("page_title"),
                        "network_path": b_res.get("network_path"),
                        "proxy_fallback_reason": b_res.get("proxy_fallback_reason"),
                        "screenshots": b_res.get("screenshots") or [],
                        "links": b_res.get("links") or [],
                        "stages": b_res.get("stages") or [],
                        "runtime_status": b_res.get("runtime_status"),
                        "model_used": model_used,
                        "latency_ms": b_res.get("latency_ms"),
                        "is_mock": bool(b_res.get("is_mock", False)),
                    },
                }
            else:
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
                    r = {
                        "content": "🔗 **وكيل الباك لينك جاهز (Flagship Loop):**\nيرجى تحديد اسم الموقع المستهدف والبريد الإلكتروني الحقيقي.",
                        "card_type": None,
                        "card_payload": {"need": ["domain", "email"], "is_mock": False},
                    }
                else:
                    ok_email, email_err = validate_real_email(email)
                    if not ok_email:
                        r = {
                            "content": email_err or "يرجى إدخال بريد إلكتروني حقيقي للتسجيل.",
                            "card_type": None,
                            "card_payload": {"status": "invalid_email", "is_mock": False},
                        }
                    else:
                        async def _run_bl() -> dict[str, Any]:
                            try:
                                return await run_flagship_backlink_loop(
                                    db,
                                    workspace_id=thread.workspace_id,
                                    user_id=user_id,
                                    domain=domain,
                                    email=email,
                                    mock_mode=bool(mock_mode),
                                    simulate_captcha=simulate_captcha,
                                    simulate_2fa=simulate_2fa,
                                    thread_id=thread.id,
                                    on_progress=_push_progress,
                                )
                            finally:
                                await progress_q.put(None)

                        task = asyncio.create_task(_run_bl())
                        while True:
                            item = await progress_q.get()
                            if item is None:
                                break
                            yield {"event": "progress", "data": item}
                        loop_res = await task
                        r = {"content": loop_res["report_ar"], "card_type": loop_res.get("card_type"), "card_payload": loop_res}

            latency_ms = int((time.monotonic() - t0) * 1000)
            full_text, _ = sanitize_chat_output(str(r["content"]))
            if not full_text:
                full_text = "تم تنفيذ الخطوة بنجاح."
            card_type = r.get("card_type")
            card_payload = dict(r.get("card_payload") or {})
            card_payload.setdefault("latency_ms", latency_ms)
            timeline.append({"stage": "completed", "label_ar": "اكتمل التنفيذ", "ts": datetime.now(timezone.utc).isoformat()})
            card_payload["timeline"] = timeline
            card_payload["loaded_memories"] = loaded_mems
            if model_used:
                card_payload["model_used"] = model_used
            card_payload["stats"] = _build_turn_stats(
                model_latency_ms=int(card_payload.get("latency_ms") or 0) if model_used else 0,
                tool_latency_ms=latency_ms,
                total_latency_ms=latency_ms,
                model_used=model_used,
                routing_profile="research" if intent in {"browser", "site_audit", "gsc_report"} else ("planning" if intent == "goal_planner" else None),
            )
            actual_is_mock = bool(card_payload.get("is_mock", False))
            yield {"event": "token", "data": {"token": full_text, "model": model_used or "", "is_mock": actual_is_mock}}

            asst_seq = user_seq + 1
            asst_msg = ChatMessage(
                workspace_id=thread.workspace_id,
                thread_id=thread.id,
                seq=asst_seq,
                role="assistant",
                content=scrub_text(full_text),
                intent=intent,
                card_type=card_type,
                card_payload=card_payload,
            )
            db.add(asst_msg)
            db.flush()
            index_chat_message_fts(
                db,
                message_id=asst_msg.id,
                thread_id=thread.id,
                workspace_id=thread.workspace_id,
                user_id=user_id,
                role="assistant",
                content=asst_msg.content,
            )
            thread.status = "paused" if thread.paused else "done"
            ctrl["status"] = thread.status
            db.commit()
            db.refresh(asst_msg)
            append_turn(thread.id, {"type": "message", "seq": asst_seq, "role": "assistant", "intent": intent, "content": asst_msg.content})
            yield {
                "event": "done",
                "data": {
                    "message_id": asst_msg.id,
                    "seq": asst_seq,
                    "model_used": model_used,
                    "latency_ms": latency_ms,
                    "stats": card_payload["stats"],
                    "is_mock": actual_is_mock,
                    "content": asst_msg.content,
                    "card_type": card_type,
                    "card_payload": card_payload,
                },
            }
            return

        # General / Coding streaming completion (with Memory Injection + Output Guard + Steering)
        mode_row = (
            db.query(ModePreset)
            .filter(ModePreset.workspace_id == thread.workspace_id, ModePreset.key == thread.mode_key)
            .first()
        )
        router = LLMRouter(db, thread.workspace_id)
        if intent == "coding":
            sys_prompt = "You are a general-purpose software engineer assistant. Provide a concise Arabic plan and next action."
            if mem_ctx:
                sys_prompt += f"\n\n{mem_ctx}"
            task_type = "coding"
            tier = "mid"
        else:
            sys_prompt = (
                (mode_row.system_prompt if mode_row else "You are a helpful Arabic-first assistant.")
                + "\nRespond to the user in clear Arabic or English text. NEVER emit raw <tool_call> tags or raw internal JSON plans."
            )
            readiness = get_runtime_readiness(db, thread.workspace_id)
            if readiness.get("browser_worker"):
                sys_prompt += "\nThe platform has a live Playwright browser online; never claim you cannot browse the web."
            if thread.compacted_summary:
                sys_prompt += f"\n\n[Previous compacted context]:\n{thread.compacted_summary}"
            if mem_ctx:
                sys_prompt += f"\n\n{mem_ctx}"
            task_type = "chat"
            tier = "cheap"

        parts: list[str] = []
        out_filter = StreamingOutputFilter()
        model_used = ""
        provider_used = ""
        routing_profile = "coding" if intent == "coding" else "chat"
        tok_in_s = 0
        tok_out_s = 0
        cost_usd_s = 0.0
        req_model = ""
        latency_ms = 0
        is_mock_reply = False
        aborted_reason: str | None = None
        prefixed = False
        prefix_text = "💡 **وكيل البرمجة العام (`/coding`):**\n" if intent == "coding" else ""

        if prefix_text:
            parts.append(prefix_text)
            yield {"event": "token", "data": {"token": prefix_text, "model": "", "is_mock": is_mock_reply}}

        async for chunk in router.route_stream(
            task_type=task_type,
            tier=tier,
            toolsets=["terminal", "file"] if intent == "coding" else None,
            goal_text=clean_text,
            prompt=effective_prompt,
            system_prompt=sys_prompt,
            allow_mock_fallback=True,
            cancel_event=cancel_ev,
        ):
            if ctrl.get("paused"):
                try:
                    await asyncio.wait_for(ctrl["resume_ev"].wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    aborted_reason = "paused_by_user"
                    break
            if disconnected_check and await disconnected_check():
                cancel_ev.set()

            ctype = chunk.get("type")
            if ctype == "token":
                raw_tok = str(chunk.get("token") or "")
                tok = out_filter.feed(raw_tok)
                model_used = str(chunk.get("model") or model_used)
                provider_used = str(chunk.get("provider_id") or provider_used)
                is_mock_reply = bool(is_mock_reply or chunk.get("is_mock"))
                if tok:
                    if is_mock_reply and not prefixed:
                        prefixed = True
                        if not tok.startswith("محاكاة — ") and not (parts and parts[0].startswith("محاكاة — ")):
                            tok = f"محاكاة — {tok}"
                    parts.append(tok)
                    yield {"event": "token", "data": {"token": tok, "model": model_used, "is_mock": is_mock_reply}}
            elif ctype == "aborted":
                aborted_reason = str(chunk.get("reason") or "aborted")
                model_used = str(chunk.get("model") or model_used)
                provider_used = str(chunk.get("provider_id") or provider_used)
                latency_ms = int(chunk.get("latency_ms") or 0)
                is_mock_reply = bool(is_mock_reply or chunk.get("is_mock"))
                break
            elif ctype == "done":
                tail_tok = out_filter.flush()
                if tail_tok:
                    parts.append(tail_tok)
                    yield {"event": "token", "data": {"token": tail_tok, "model": model_used, "is_mock": is_mock_reply}}
                model_used = str(chunk.get("model") or model_used)
                provider_used = str(chunk.get("provider_id") or provider_used)
                routing_profile = str(chunk.get("routing_profile") or routing_profile)
                req_model = str(chunk.get("requested_model") or "")
                latency_ms = int(chunk.get("latency_ms") or 0)
                tok_in_s = int(chunk.get("tokens_in") or 0)
                tok_out_s = int(chunk.get("tokens_out") or 0)
                cost_usd_s = float(chunk.get("cost_usd") or 0.0)
                is_mock_reply = bool(is_mock_reply or chunk.get("is_mock"))
                break

        raw_joined = "".join(parts)
        cleaned_full, _ = sanitize_chat_output(raw_joined)
        full_content = cleaned_full.strip() or ("تم إيقاف البث." if aborted_reason else "لم يُنتج النموذج مخرجات نصية؛ تحقّق من المزود النشط في الإعدادات.")
        if is_mock_reply and not full_content.startswith("محاكاة — "):
            full_content = f"محاكاة — {full_content}"

        turn_stats = _build_turn_stats(
            tokens_in=tok_in_s,
            tokens_out=tok_out_s,
            model_latency_ms=latency_ms,
            total_latency_ms=latency_ms,
            model_used=model_used or None,
            provider_id=provider_used or None,
            routing_profile=routing_profile,
            cost_usd=cost_usd_s,
        )
        timeline.append({"stage": "completed", "label_ar": "اكتمل التنفيذ", "ts": datetime.now(timezone.utc).isoformat()})
        asst_seq = user_seq + 1
        card_payload = {
            "mode": thread.mode_key,
            "model_used": model_used or None,
            "requested_model": req_model or None,
            "latency_ms": latency_ms,
            "stats": turn_stats,
            "timeline": timeline,
            "loaded_memories": loaded_mems,
            "is_mock": is_mock_reply,
            "aborted": bool(aborted_reason),
            "abort_reason": aborted_reason,
        }
        if intent == "coding":
            card_payload["coding_route"] = "/coding"

        asst_msg = ChatMessage(
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            seq=asst_seq,
            role="assistant",
            content=scrub_text(full_content),
            intent=intent,
            card_type=None,
            card_payload=card_payload,
        )
        db.add(asst_msg)
        db.flush()
        index_chat_message_fts(
            db,
            message_id=asst_msg.id,
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
            user_id=user_id,
            role="assistant",
            content=asst_msg.content,
        )
        thread.status = "paused" if thread.paused else "done"
        ctrl["status"] = thread.status
        db.commit()
        db.refresh(asst_msg)
        append_turn(
            thread.id,
            {
                "type": "message",
                "seq": asst_seq,
                "role": "assistant",
                "intent": intent,
                "model_used": model_used,
                "latency_ms": latency_ms,
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
                    "model_used": model_used,
                    "latency_ms": latency_ms,
                    "card_payload": card_payload,
                },
            },
        )

        ev_name = "aborted" if aborted_reason else "done"
        yield {
            "event": ev_name,
            "data": {
                "message_id": asst_msg.id,
                "seq": asst_seq,
                "reason": aborted_reason,
                "model_used": model_used or None,
                "requested_model": req_model or None,
                "latency_ms": latency_ms,
                "stats": turn_stats,
                "is_mock": is_mock_reply,
                "content": asst_msg.content,
                "card_payload": card_payload,
            },
        }
    finally:
        _ACTIVE_STREAMS.pop(thread.id, None)
        ctrl = _THREAD_CONTROLLERS.get(thread.id)
        if ctrl and ctrl.get("status") == "running":
            ctrl["status"] = "paused" if thread.paused else "done"
