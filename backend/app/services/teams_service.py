"""Phase 5 Bots-as-Contacts, Contextual Channels, Jev Fast Routing, Team Markdown Install & Share (§5C)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from sqlalchemy.orm import Session
import yaml

from app.config import get_settings
from app.db import new_uuid, utcnow
from app.models.core import SystemState
from app.models.phase5 import AutomationTrigger, BotContact, TeamChannel, TeamPackageImport
from app.models.providers import ApiKey, Provider, ProviderModel
from app.security.sanitizer import scrub_text
from app.services.llm_router import LLMRouter
from app.services.runtime_checks import is_local_endpoint

_FORBIDDEN_IMPORT_KEYS = {
    "credentials",
    "keys",
    "api_keys",
    "tokens",
    "secrets",
    "conversations",
    "transcripts",
    "chats",
    "messages",
    "permissions",
    "memory",
    "computer_access",
}

_SENSITIVE_TEXT_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{12,}|AIza[0-9A-Za-z\-_]{16,}|ghp_[A-Za-z0-9]{16,}|https?://[^\s'\"`]+)",
    re.I,
)


def _jev_state_key(workspace_id: str) -> str:
    return f"jev_routing:{workspace_id}"


def get_jev_routing_config(db: Session, workspace_id: str) -> dict[str, Any]:
    row = db.get(SystemState, _jev_state_key(workspace_id))
    defaults = {
        "enabled": True,
        "decision_provider_id": None,
        "decision_model": "auto:fast",
        "confidence_threshold": 0.65,
        "fallback_to_lead": True,
    }
    if not row or not row.value_json:
        return defaults
    return {**defaults, **dict(row.value_json)}


def update_jev_routing_config(db: Session, workspace_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    cur = get_jev_routing_config(db, workspace_id)
    merged = {
        "enabled": bool(cfg.get("enabled", cur["enabled"])),
        "decision_provider_id": (cfg.get("decision_provider_id") or "").strip() or None if "decision_provider_id" in cfg else cur["decision_provider_id"],
        "decision_model": (cfg.get("decision_model") or cur["decision_model"]).strip(),
        "confidence_threshold": float(cfg.get("confidence_threshold", cur["confidence_threshold"])),
        "fallback_to_lead": bool(cfg.get("fallback_to_lead", cur["fallback_to_lead"])),
    }
    key = _jev_state_key(workspace_id)
    row = db.get(SystemState, key)
    if row is None:
        db.add(SystemState(key=key, value_json=merged))
    else:
        row.value_json = merged
    db.flush()
    return merged


def seed_default_bots_and_channels(db: Session, workspace_id: str, user_id: str) -> tuple[list[BotContact], list[TeamChannel]]:
    bots = db.query(BotContact).filter(BotContact.workspace_id == workspace_id).order_by(BotContact.created_at.asc()).all()
    if not bots:
        b_chief = BotContact(
            workspace_id=workspace_id,
            name="رئيس الأركان (Chief)",
            handle="chief",
            avatar_emoji="🧠",
            role_title="Chief of Staff",
            personality="منسق رئيسي يدير الفريق ويوزع المهام ويراجع المخرجات.",
            provider_id="freellmapi",
            model_name="auto:fast",
            pinned=True,
            is_lead=True,
        )
        b_coder = BotContact(
            workspace_id=workspace_id,
            name="مهندس البرمجة",
            handle="coder",
            avatar_emoji="💻",
            role_title="Software Engineer",
            personality="خبير برمجة وبناء واجهات واختبارات تلقائية.",
            provider_id="freellmapi",
            model_name="auto:fast",
            is_lead=False,
        )
        b_research = BotContact(
            workspace_id=workspace_id,
            name="باحث المحتوى والسيو",
            handle="research",
            avatar_emoji="🔍",
            role_title="Research & SEO",
            personality="محلل أبحاث، أسواق، وروابط حية.",
            provider_id="freellmapi",
            model_name="auto:fast",
            is_lead=False,
        )
        db.add_all([b_chief, b_coder, b_research])
        db.flush()
        bots = [b_chief, b_coder, b_research]

    channels = db.query(TeamChannel).filter(TeamChannel.workspace_id == workspace_id).order_by(TeamChannel.created_at.asc()).all()
    if not channels:
        lead_id = next((b.id for b in bots if b.is_lead), bots[0].id)
        all_ids = [b.id for b in bots]
        c_work = TeamChannel(
            workspace_id=workspace_id,
            name="العمل (Work)",
            slug="work",
            context_type="work",
            shared_instructions="قناة مهام العمل والبرمجة والمشاريع التشغيلية.",
            lead_bot_id=lead_id,
            bot_ids_json=all_ids,
        )
        c_personal = TeamChannel(
            workspace_id=workspace_id,
            name="شخصي (Personal)",
            slug="personal",
            context_type="personal",
            shared_instructions="قناة المهام الشخصية والسفر والبحث السريع.",
            lead_bot_id=lead_id,
            bot_ids_json=all_ids,
        )
        db.add_all([c_work, c_personal])
        db.flush()
        channels = [c_work, c_personal]

    return bots, channels


def serialize_bot(b: BotContact) -> dict[str, Any]:
    return {
        "id": b.id,
        "name": b.name,
        "handle": b.handle,
        "avatar_emoji": b.avatar_emoji,
        "role_title": b.role_title,
        "personality": b.personality,
        "provider_id": b.provider_id,
        "model_name": b.model_name,
        "computer_mode": b.computer_mode,
        "connected_apps": list(b.connected_apps_json or []),
        "memory_count": len(b.memory_json or []),
        "memory": list(b.memory_json or [])[-20:],
        "conversation_id": b.conversation_id,
        "pinned": bool(b.pinned),
        "unread_count": int(b.unread_count or 0),
        "hidden": bool(b.hidden),
        "is_lead": bool(b.is_lead),
        "skills": list(b.skills_json or []),
        "created_at": b.created_at.isoformat(),
    }


def serialize_channel(c: TeamChannel) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "slug": c.slug,
        "context_type": c.context_type,
        "shared_instructions": c.shared_instructions,
        "working_dir": c.working_dir,
        "lead_bot_id": c.lead_bot_id,
        "bot_ids": list(c.bot_ids_json or []),
        "responder_rules": dict(c.responder_rules_json or {}),
        "transcript": list(c.transcript_json or []),
        "created_at": c.created_at.isoformat(),
    }


def get_model_picker_rail(db: Session, workspace_id: str) -> list[dict[str, Any]]:
    """Return side-by-side provider rail with defaults marked and unavailable entries dimmed with reason."""
    provs = db.query(Provider).filter(Provider.workspace_id == workspace_id, Provider.kind != "captcha").all()
    key_pids = {k.provider for k in db.query(ApiKey).filter(ApiKey.workspace_id == workspace_id).all()}
    is_prod = get_settings().is_production
    rail: list[dict[str, Any]] = []

    for p in provs:
        unavailable_reason: str | None = None
        if not p.enabled:
            unavailable_reason = "disabled"
        elif is_prod and (is_local_endpoint(p.api_base) or p.kind == "llm-local"):
            unavailable_reason = "local_only_in_production"
        elif p.kind == "llm" and p.id not in key_pids and not is_local_endpoint(p.api_base):
            unavailable_reason = "no_api_key"

        models_list: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        tier_defaults = dict(p.models_json or {})
        for tier_k, mid in tier_defaults.items():
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                models_list.append({"id": mid, "tier": tier_k, "is_default": True})

        cat_rows = (
            db.query(ProviderModel)
            .filter(ProviderModel.workspace_id == workspace_id, ProviderModel.provider_id == p.id)
            .limit(40)
            .all()
        )
        for cr in cat_rows:
            if cr.model_id not in seen_ids:
                seen_ids.add(cr.model_id)
                models_list.append({"id": cr.model_id, "tier": "catalogue", "is_default": False})

        rail.append(
            {
                "provider_id": p.id,
                "name": p.name,
                "available": unavailable_reason is None,
                "unavailable_reason": unavailable_reason,
                "local_only": is_local_endpoint(p.api_base) or p.kind == "llm-local",
                "models": models_list,
            }
        )
    return rail


def apply_bot_action(db: Session, bot: BotContact, action: str) -> BotContact:
    act = action.strip().lower()
    if act == "pin":
        bot.pinned = True
    elif act == "unpin":
        bot.pinned = False
    elif act == "mark_unread":
        bot.unread_count = max(1, int(bot.unread_count or 0) + 1)
    elif act == "mark_read":
        bot.unread_count = 0
    elif act == "hide":
        bot.hidden = True
    elif act == "unhide":
        bot.hidden = False
    else:
        raise ValueError(f"Unsupported bot action: {action}")
    db.flush()
    return bot


def duplicate_bot(db: Session, src: BotContact) -> BotContact:
    dup = BotContact(
        workspace_id=src.workspace_id,
        name=f"{src.name} (نسخة)",
        handle=f"{src.handle}_{new_uuid()[:4]}",
        avatar_emoji=src.avatar_emoji,
        role_title=src.role_title,
        personality=src.personality,
        provider_id=src.provider_id,
        model_name=src.model_name,
        computer_mode=src.computer_mode,
        connected_apps_json=[{**a, "enabled": False} for a in list(src.connected_apps_json or [])],
        memory_json=[],
        conversation_id=new_uuid(),
        pinned=False,
        unread_count=0,
        hidden=False,
        is_lead=False,
        skills_json=list(src.skills_json or []),
    )
    db.add(dup)
    db.flush()
    return dup


def chat_with_bot(
    db: Session,
    *,
    workspace_id: str,
    bot: BotContact,
    content: str,
    mock_mode: bool = False,
) -> dict[str, Any]:
    clean = scrub_text(content).strip()
    if not clean:
        raise ValueError("Empty message")

    mem = list(bot.memory_json or [])
    mem.append({"role": "user", "content": clean, "at": utcnow().isoformat()})

    router = LLMRouter(db, workspace_id)
    sys_prompt = f"You are {bot.name} ({bot.role_title}). {bot.personality}".strip()
    pref_prov = bot.provider_id or None
    pref_model = bot.model_name or None

    t0 = time.monotonic()
    model_used = pref_model or "auto"
    is_mock = bool(mock_mode)
    try:
        res = router.route(
            task_type=f"bot_{bot.handle}",
            tier="cheap",
            prompt=clean,
            system_prompt=sys_prompt,
            preferred_provider=pref_prov,
            preferred_model=pref_model,
            use_cache=False,
            allow_mock_fallback=True,
        )
        reply = str(res.get("raw_text") or "")
        model_used = str(res.get("model") or model_used)
        latency_ms = int(res.get("latency_ms") or ((time.monotonic() - t0) * 1000))
        is_mock = bool(is_mock or res.get("is_mock"))
    except Exception as exc:
        reply = f"تعذر الوصول إلى المزود المختار ({type(exc).__name__})."
        latency_ms = int((time.monotonic() - t0) * 1000)

    if is_mock and not reply.startswith("محاكاة — "):
        reply = f"محاكاة — {reply}"

    mem.append(
        {
            "role": "assistant",
            "content": scrub_text(reply),
            "model_used": model_used,
            "latency_ms": latency_ms,
            "at": utcnow().isoformat(),
        }
    )
    bot.memory_json = mem[-100:]
    bot.unread_count = 0
    db.flush()
    return {
        "bot_id": bot.id,
        "conversation_id": bot.conversation_id,
        "reply": scrub_text(reply),
        "model_used": model_used,
        "latency_ms": latency_ms,
        "is_mock": is_mock,
    }


def _jev_pick_bot(
    db: Session,
    workspace_id: str,
    channel: TeamChannel,
    content: str,
    roster: list[BotContact],
) -> tuple[BotContact, float, str]:
    """Select bot by @mention first, then Jev keyword/role/model decision, falling back to lead bot."""
    mention_match = re.search(r"@([A-Za-z0-9_-]+)", content)
    if mention_match:
        handle = mention_match.group(1).lower()
        for b in roster:
            if b.handle.lower() == handle:
                return b, 1.0, f"mention:@{handle}"

    jev_cfg = get_jev_routing_config(db, workspace_id)
    lead = next((b for b in roster if b.id == channel.lead_bot_id or b.is_lead), roster[0])
    if not jev_cfg.get("enabled", True) or len(roster) == 1:
        return lead, 1.0, "lead_default"

    lower = content.lower()
    scores: list[tuple[BotContact, float, str]] = []
    for b in roster:
        s = 0.2
        reason = "baseline"
        h = b.handle.lower()
        r = b.role_title.lower()
        if ("code" in h or "engineer" in r or "برمج" in b.name) and any(
            kw in lower for kw in ("code", "bug", "python", "react", "api", "برمجة", "كود", "خطأ", "ملف")
        ):
            s = 0.9
            reason = "jev_coding_signal"
        elif ("research" in h or "seo" in r or "بحث" in b.name) and any(
            kw in lower for kw in ("search", "seo", "link", "research", "بحث", "سيو", "رابط", "مقال")
        ):
            s = 0.88
            reason = "jev_research_signal"
        elif any(kw in lower for kw in (h, b.name.lower())):
            s = 0.92
            reason = "jev_name_match"
        scores.append((b, s, reason))

    best_bot, best_score, best_reason = max(scores, key=lambda x: x[1])
    threshold = float(jev_cfg.get("confidence_threshold") or 0.65)
    if best_score < threshold and jev_cfg.get("fallback_to_lead", True):
        return lead, best_score, "jev_unsure_fallback_to_lead"
    return best_bot, best_score, best_reason


def post_channel_message(
    db: Session,
    *,
    workspace_id: str,
    channel: TeamChannel,
    content: str,
    mock_mode: bool = False,
) -> dict[str, Any]:
    clean = scrub_text(content).strip()
    if not clean:
        raise ValueError("Empty channel message")

    all_bots = db.query(BotContact).filter(BotContact.workspace_id == workspace_id).all()
    by_id = {b.id: b for b in all_bots}
    roster = [by_id[bid] for bid in (channel.bot_ids_json or []) if bid in by_id] or all_bots
    if not roster:
        raise ValueError("Channel has no bots assigned")

    chosen, confidence, reason = _jev_pick_bot(db, workspace_id, channel, clean, roster)
    prompt = f"[Channel: {channel.name} | Context: {channel.context_type}]\n{channel.shared_instructions}\n\nUser: {clean}".strip()
    res = chat_with_bot(db, workspace_id=workspace_id, bot=chosen, content=prompt, mock_mode=mock_mode)

    transcript = list(channel.transcript_json or [])
    transcript.append({"role": "user", "content": clean, "at": utcnow().isoformat()})
    transcript.append(
        {
            "role": "assistant",
            "bot_id": chosen.id,
            "bot_name": chosen.name,
            "bot_handle": chosen.handle,
            "routing_reason": reason,
            "confidence": confidence,
            "model_used": res["model_used"],
            "latency_ms": res["latency_ms"],
            "content": res["reply"],
            "at": utcnow().isoformat(),
        }
    )
    channel.transcript_json = transcript[-200:]
    db.flush()
    return {
        "channel": serialize_channel(channel),
        "routed_bot": serialize_bot(chosen),
        "routing": {"reason": reason, "confidence": confidence},
        "reply": res["reply"],
        "model_used": res["model_used"],
        "latency_ms": res["latency_ms"],
    }


def _split_frontmatter_markdown(raw_md: str) -> tuple[dict[str, Any], str]:
    text = raw_md.lstrip("\ufeff")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1]) or {}
            if not isinstance(fm, dict):
                raise ValueError("YAML frontmatter must be a mapping.")
            return fm, parts[2].strip()
    raise ValueError("Team file must start with YAML frontmatter (--- ... ---).")


def preview_team_markdown(raw_md: str) -> dict[str, Any]:
    """Parse team markdown without touching DB, stripping any forbidden credentials/chats/memory/permissions."""
    fm, body = _split_frontmatter_markdown(raw_md)
    stripped = sorted(k for k in fm.keys() if str(k).lower() in _FORBIDDEN_IMPORT_KEYS)

    bots_in: list[dict[str, Any]] = []
    for b in fm.get("bots") or []:
        if not isinstance(b, dict):
            continue
        clean_b = {k: v for k, v in b.items() if str(k).lower() not in _FORBIDDEN_IMPORT_KEYS}
        bots_in.append(
            {
                "name": str(clean_b.get("name") or "Bot"),
                "handle": str(clean_b.get("handle") or "bot").lower(),
                "avatar_emoji": str(clean_b.get("avatar_emoji") or "🤖"),
                "role_title": str(clean_b.get("role_title") or "Member"),
                "personality": str(clean_b.get("instructions") or clean_b.get("personality") or ""),
                "model_name": str(clean_b.get("model") or "auto:fast"),
                "is_lead": bool(clean_b.get("is_lead", False)),
                "skills": [str(s) for s in (clean_b.get("skills") or [])],
            }
        )

    chief_raw = fm.get("chief_of_staff")
    chief: dict[str, Any] | None = None
    if isinstance(chief_raw, dict):
        chief = {
            "name": str(chief_raw.get("name") or "Chief of Staff"),
            "handle": str(chief_raw.get("handle") or "chief").lower(),
            "avatar_emoji": str(chief_raw.get("avatar_emoji") or "🧠"),
            "role_title": str(chief_raw.get("role_title") or "Chief of Staff"),
            "personality": str(chief_raw.get("instructions") or chief_raw.get("personality") or ""),
            "model_name": str(chief_raw.get("model") or "auto:fast"),
            "is_lead": True,
            "skills": [str(s) for s in (chief_raw.get("skills") or [])],
        }

    channels_in = [
        {
            "name": str(c.get("name") or "Channel"),
            "slug": str(c.get("slug") or c.get("name") or "channel").lower().replace(" ", "-"),
            "context_type": str(c.get("context_type") or "work"),
            "shared_instructions": str(c.get("shared_instructions") or ""),
        }
        for c in (fm.get("channels") or [])
        if isinstance(c, dict)
    ]

    connectors = [{"name": str(cn), "enabled": False} for cn in (fm.get("connectors") or fm.get("apps") or [])]
    routines = [
        {
            "name": str(rt.get("name") or "Routine") if isinstance(rt, dict) else str(rt),
            "cron_expr": str(rt.get("cron_expr") or rt.get("cron") or "0 9 * * *") if isinstance(rt, dict) else "0 9 * * *",
            "action_prompt": str(rt.get("prompt") or rt.get("action") or "") if isinstance(rt, dict) else "",
            "paused": True,
            "enabled": False,
        }
        for rt in (fm.get("routines") or [])
    ]
    playbooks = [str(p) for p in (fm.get("playbooks") or [])]

    return {
        "team_name": str(fm.get("name") or "Imported Team"),
        "version": str(fm.get("version") or "1.0"),
        "description": str(fm.get("description") or body[:240]),
        "chief_of_staff": chief,
        "bots": bots_in,
        "channels": channels_in,
        "playbooks": playbooks,
        "connectors": connectors,
        "routines": routines,
        "stripped_forbidden_fields": stripped,
        "review_notice_ar": "لن يتم تفعيل أي موصل (Connection) أو روتين تلقائياً — الروتينات تبدأ متوقفة (PAUSED) والموصلات مغلقة (OFF) حتى موافقتك.",
    }


def install_team_markdown(
    db: Session,
    *,
    workspace_id: str,
    raw_md: str,
    confirmed: bool,
) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("Team installation requires explicit confirmation after reviewing the package screen.")

    preview = preview_team_markdown(raw_md)
    created_bots: list[BotContact] = []
    lead_id: str | None = None

    candidates = ([preview["chief_of_staff"]] if preview["chief_of_staff"] else []) + list(preview["bots"])
    for item in candidates:
        apps_off = [{"name": c["name"], "enabled": False} for c in preview["connectors"]]
        b = BotContact(
            workspace_id=workspace_id,
            name=item["name"],
            handle=item["handle"],
            avatar_emoji=item["avatar_emoji"],
            role_title=item["role_title"],
            personality=item["personality"],
            provider_id="freellmapi",
            model_name=item["model_name"],
            connected_apps_json=apps_off,
            memory_json=[],
            conversation_id=new_uuid(),
            is_lead=bool(item.get("is_lead")),
            skills_json=list(item.get("skills") or []),
        )
        db.add(b)
        db.flush()
        created_bots.append(b)
        if b.is_lead and lead_id is None:
            lead_id = b.id

    if not lead_id and created_bots:
        lead_id = created_bots[0].id

    created_channels: list[TeamChannel] = []
    bot_ids = [b.id for b in created_bots]
    pb_note = f"\n\nPlaybooks: {', '.join(preview['playbooks'])}" if preview["playbooks"] else ""
    for ch in preview["channels"]:
        row = TeamChannel(
            workspace_id=workspace_id,
            name=ch["name"],
            slug=ch["slug"],
            context_type=ch["context_type"],
            shared_instructions=(ch["shared_instructions"] + pb_note).strip(),
            lead_bot_id=lead_id,
            bot_ids_json=bot_ids,
            transcript_json=[],
        )
        db.add(row)
        db.flush()
        created_channels.append(row)

    created_routines: list[AutomationTrigger] = []
    for rt in preview["routines"]:
        trig = AutomationTrigger(
            workspace_id=workspace_id,
            name=rt["name"],
            trigger_type="schedule",
            cron_expr=rt["cron_expr"],
            target_bot_id=lead_id,
            target_channel_id=created_channels[0].id if created_channels else None,
            action_prompt=rt["action_prompt"] or rt["name"],
            enabled=False,
            paused=True,
        )
        db.add(trig)
        db.flush()
        created_routines.append(trig)

    pkg = TeamPackageImport(
        workspace_id=workspace_id,
        name=preview["team_name"],
        version=preview["version"],
        source_hash=hashlib.sha256(raw_md.encode("utf-8")).hexdigest(),
        summary_json={
            "bots_count": len(created_bots),
            "channels_count": len(created_channels),
            "routines_count": len(created_routines),
            "stripped_forbidden_fields": preview["stripped_forbidden_fields"],
        },
    )
    db.add(pkg)
    db.flush()
    return {
        "installed": True,
        "package": preview["team_name"],
        "bots": [serialize_bot(b) for b in created_bots],
        "channels": [serialize_channel(c) for c in created_channels],
        "routines_paused_count": len(created_routines),
        "connectors_disabled": [c["name"] for c in preview["connectors"]],
        "stripped_forbidden_fields": preview["stripped_forbidden_fields"],
    }


def _scrub_share_text(raw: str) -> str:
    return _SENSITIVE_TEXT_RE.sub("[REDACTED]", scrub_text(raw or ""))


def export_share_package(
    db: Session,
    *,
    workspace_id: str,
    title: str = "Shared AI Team",
    target: str = "folder",  # folder | github
    bot_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Generate sanitized shareable package (zero chats, zero memory, zero keys, zero URLs/addresses)."""
    bots = db.query(BotContact).filter(BotContact.workspace_id == workspace_id).all()
    if bot_ids:
        wanted = set(bot_ids)
        bots = [b for b in bots if b.id in wanted]
    channels = db.query(TeamChannel).filter(TeamChannel.workspace_id == workspace_id).all()
    triggers = db.query(AutomationTrigger).filter(AutomationTrigger.workspace_id == workspace_id).all()

    chief = next((b for b in bots if b.is_lead), None)
    app_names = sorted({str(a.get("name")) for b in bots for a in (b.connected_apps_json or []) if isinstance(a, dict) and a.get("name")})

    frontmatter = {
        "name": title,
        "version": "1.0",
        "chief_of_staff": (
            {
                "name": chief.name,
                "handle": chief.handle,
                "avatar_emoji": chief.avatar_emoji,
                "role_title": chief.role_title,
                "instructions": _scrub_share_text(chief.personality),
                "model": chief.model_name,
                "skills": list(chief.skills_json or []),
            }
            if chief
            else None
        ),
        "bots": [
            {
                "name": b.name,
                "handle": b.handle,
                "avatar_emoji": b.avatar_emoji,
                "role_title": b.role_title,
                "instructions": _scrub_share_text(b.personality),
                "model": b.model_name,
                "skills": list(b.skills_json or []),
            }
            for b in bots
            if chief is None or b.id != chief.id
        ],
        "channels": [
            {
                "name": c.name,
                "slug": c.slug,
                "context_type": c.context_type,
                "shared_instructions": _scrub_share_text(c.shared_instructions),
            }
            for c in channels
        ],
        "routines": [
            {
                "name": tr.name,
                "cron": tr.cron_expr or "0 9 * * *",
                "prompt": _scrub_share_text(tr.action_prompt),
                "paused": True,
            }
            for tr in triggers
        ],
        "apps": app_names,
    }
    clean_fm = {k: v for k, v in frontmatter.items() if v is not None}
    yaml_txt = yaml.safe_dump(clean_fm, allow_unicode=True, sort_keys=False)
    markdown = f"---\n{yaml_txt}---\n\n# {title}\n\nPortable Unified Agent System share card (chats, memory, keys, and addresses stripped).\n"

    slug = re.sub(r"[^a-z0-9_-]+", "-", title.lower()).strip("-") or "shared-team"
    saved_path: str | None = None
    if target == "folder":
        out_dir = get_settings().data_dir / "shared_packages" / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "TEAM.md").write_text(markdown, encoding="utf-8")
        saved_path = str(out_dir / "TEAM.md")

    return {
        "target": target,
        "saved_path": saved_path,
        "share_card": clean_fm,
        "markdown": markdown,
        "github_bundle": {"filename": f"{slug}/TEAM.md", "content": markdown} if target == "github" else None,
    }
