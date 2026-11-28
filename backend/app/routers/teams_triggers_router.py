"""Phase 5 Bots, Teams, Channels, Jev Routing, Package Import/Share & Triggers Router (§5C)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db, new_uuid
from app.models.phase5 import AutomationTrigger, BotContact, TeamChannel, TriggerRunLog
from app.security.auth import AuthContext, get_current_auth, require_roles
from app.services.teams_service import (
    apply_bot_action,
    chat_with_bot,
    duplicate_bot,
    export_share_package,
    get_jev_routing_config,
    get_model_picker_rail,
    install_team_markdown,
    post_channel_message,
    preview_team_markdown,
    seed_default_bots_and_channels,
    serialize_bot,
    serialize_channel,
    update_jev_routing_config,
)
from app.services.triggers_service import (
    emit_trigger_event,
    fire_trigger,
    serialize_trigger,
    serialize_trigger_log,
)

router = APIRouter(tags=["teams_triggers"])


class BotCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    handle: str = Field(min_length=1, max_length=64)
    avatar_emoji: str = "🤖"
    role_title: str = "Assistant"
    personality: str = ""
    provider_id: str | None = "freellmapi"
    model_name: str = "auto:fast"
    computer_mode: str = "workspace_sandbox"
    skills: list[str] = Field(default_factory=list)


class BotPatchRequest(BaseModel):
    name: str | None = None
    avatar_emoji: str | None = None
    role_title: str | None = None
    personality: str | None = None
    provider_id: str | None = None
    model_name: str | None = None
    computer_mode: str | None = None
    pinned: bool | None = None
    hidden: bool | None = None
    is_lead: bool | None = None


class BotActionRequest(BaseModel):
    action: str  # pin|unpin|mark_unread|mark_read|hide|unhide


class BotModelSwitchRequest(BaseModel):
    provider_id: str | None = None
    model_name: str = Field(min_length=1)


class BotChatRequest(BaseModel):
    content: str = Field(min_length=1)
    mock_mode: bool = False


class ChannelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    context_type: str = "project"
    shared_instructions: str = ""
    working_dir: str = ""
    lead_bot_id: str | None = None
    bot_ids: list[str] = Field(default_factory=list)


class ChannelPatchRequest(BaseModel):
    name: str | None = None
    shared_instructions: str | None = None
    working_dir: str | None = None
    lead_bot_id: str | None = None
    bot_ids: list[str] | None = None


class TeamMarkdownRequest(BaseModel):
    raw_md: str = Field(min_length=1)
    confirmed: bool = False


class TeamShareRequest(BaseModel):
    title: str = "Shared AI Team"
    target: str = "folder"  # folder | github
    bot_ids: list[str] | None = None


class TriggerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    trigger_type: str = "schedule"  # schedule | event
    cron_expr: str | None = "* * * * *"
    event_name: str | None = None
    target_bot_id: str | None = None
    target_channel_id: str | None = None
    action_prompt: str = ""
    enabled: bool = True
    paused: bool = False


class TriggerPatchRequest(BaseModel):
    name: str | None = None
    cron_expr: str | None = None
    event_name: str | None = None
    action_prompt: str | None = None
    enabled: bool | None = None
    paused: bool | None = None


@router.get("/api/bots")
def list_bots(
    include_hidden: bool = False,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    seed_default_bots_and_channels(db, auth.workspace.id, auth.user.id)
    db.commit()
    q = db.query(BotContact).filter(BotContact.workspace_id == auth.workspace.id)
    if not include_hidden:
        q = q.filter(BotContact.hidden.is_(False))
    rows = q.order_by(BotContact.pinned.desc(), BotContact.created_at.asc()).all()
    return {"bots": [serialize_bot(b) for b in rows]}


@router.get("/api/bots/model-picker")
def get_bots_model_picker(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return {"rail": get_model_picker_rail(db, auth.workspace.id)}


@router.post("/api/bots")
def create_bot(
    req: BotCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    b = BotContact(
        workspace_id=auth.workspace.id,
        name=req.name.strip(),
        handle=req.handle.strip().lower(),
        avatar_emoji=req.avatar_emoji,
        role_title=req.role_title,
        personality=req.personality,
        provider_id=req.provider_id,
        model_name=req.model_name,
        computer_mode=req.computer_mode,
        conversation_id=new_uuid(),
        skills_json=req.skills,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return {"bot": serialize_bot(b)}


@router.patch("/api/bots/{bot_id}")
def patch_bot(
    bot_id: str,
    req: BotPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    b = db.get(BotContact, bot_id)
    if not b or b.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "البوت غير موجود", "message_en": "Bot not found"})
    for field in ("name", "avatar_emoji", "role_title", "personality", "provider_id", "model_name", "computer_mode", "pinned", "hidden", "is_lead"):
        val = getattr(req, field)
        if val is not None:
            setattr(b, field, val)
    db.commit()
    return {"bot": serialize_bot(b)}


@router.post("/api/bots/{bot_id}/action")
def bot_context_action(
    bot_id: str,
    req: BotActionRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    b = db.get(BotContact, bot_id)
    if not b or b.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "البوت غير موجود", "message_en": "Bot not found"})
    try:
        apply_bot_action(db, b, req.action)
        db.commit()
        return {"bot": serialize_bot(b)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_action", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/bots/{bot_id}/duplicate")
def duplicate_bot_endpoint(
    bot_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    b = db.get(BotContact, bot_id)
    if not b or b.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "البوت غير موجود", "message_en": "Bot not found"})
    dup = duplicate_bot(db, b)
    db.commit()
    return {"bot": serialize_bot(dup)}


@router.patch("/api/bots/{bot_id}/model")
def switch_bot_model(
    bot_id: str,
    req: BotModelSwitchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    b = db.get(BotContact, bot_id)
    if not b or b.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "البوت غير موجود", "message_en": "Bot not found"})
    if req.provider_id is not None:
        b.provider_id = req.provider_id or None
    b.model_name = req.model_name.strip()
    db.commit()
    return {"bot": serialize_bot(b)}


@router.delete("/api/bots/{bot_id}")
def delete_bot(
    bot_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    b = db.get(BotContact, bot_id)
    if not b or b.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "البوت غير موجود", "message_en": "Bot not found"})
    db.delete(b)
    db.commit()
    return {"deleted": True, "id": bot_id}


@router.post("/api/bots/{bot_id}/chat")
def message_bot(
    bot_id: str,
    req: BotChatRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    b = db.get(BotContact, bot_id)
    if not b or b.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "البوت غير موجود", "message_en": "Bot not found"})
    res = chat_with_bot(db, workspace_id=auth.workspace.id, bot=b, content=req.content, mock_mode=req.mock_mode)
    db.commit()
    return res


@router.get("/api/channels")
def list_channels(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    seed_default_bots_and_channels(db, auth.workspace.id, auth.user.id)
    db.commit()
    rows = db.query(TeamChannel).filter(TeamChannel.workspace_id == auth.workspace.id).order_by(TeamChannel.created_at.asc()).all()
    return {"channels": [serialize_channel(c) for c in rows]}


@router.post("/api/channels")
def create_channel(
    req: ChannelCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    c = TeamChannel(
        workspace_id=auth.workspace.id,
        name=req.name.strip(),
        slug=req.name.strip().lower().replace(" ", "-")[:64],
        context_type=req.context_type,
        shared_instructions=req.shared_instructions,
        working_dir=req.working_dir,
        lead_bot_id=req.lead_bot_id,
        bot_ids_json=req.bot_ids,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"channel": serialize_channel(c)}


@router.patch("/api/channels/{channel_id}")
def patch_channel(
    channel_id: str,
    req: ChannelPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    c = db.get(TeamChannel, channel_id)
    if not c or c.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "القناة غير موجودة", "message_en": "Channel not found"})
    if req.name is not None:
        c.name = req.name
    if req.shared_instructions is not None:
        c.shared_instructions = req.shared_instructions
    if req.working_dir is not None:
        c.working_dir = req.working_dir
    if req.lead_bot_id is not None:
        c.lead_bot_id = req.lead_bot_id or None
    if req.bot_ids is not None:
        c.bot_ids_json = list(req.bot_ids)
    db.commit()
    return {"channel": serialize_channel(c)}


@router.post("/api/channels/{channel_id}/messages")
def send_channel_message(
    channel_id: str,
    req: BotChatRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    c = db.get(TeamChannel, channel_id)
    if not c or c.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "القناة غير موجودة", "message_en": "Channel not found"})
    res = post_channel_message(db, workspace_id=auth.workspace.id, channel=c, content=req.content, mock_mode=req.mock_mode)
    db.commit()
    return res


@router.get("/api/settings/routing")
def get_routing_settings(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return {"routing": get_jev_routing_config(db, auth.workspace.id)}


@router.put("/api/settings/routing")
def put_routing_settings(
    req: dict[str, Any],
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    res = update_jev_routing_config(db, auth.workspace.id, req)
    db.commit()
    return {"routing": res}


@router.post("/api/teams/preview")
def preview_team_package(req: TeamMarkdownRequest, auth: AuthContext = Depends(get_current_auth)):
    try:
        return {"preview": preview_team_markdown(req.raw_md)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_team_md", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/teams/install")
def install_team_package(
    req: TeamMarkdownRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        res = install_team_markdown(db, workspace_id=auth.workspace.id, raw_md=req.raw_md, confirmed=req.confirmed)
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "install_requires_review", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/teams/share")
def share_team_package(
    req: TeamShareRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    return export_share_package(db, workspace_id=auth.workspace.id, title=req.title, target=req.target, bot_ids=req.bot_ids)


# --- Triggers (§5C) ---

@router.get("/api/triggers")
def list_triggers(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(AutomationTrigger).filter(AutomationTrigger.workspace_id == auth.workspace.id).order_by(AutomationTrigger.created_at.desc()).all()
    logs = (
        db.query(TriggerRunLog)
        .filter(TriggerRunLog.workspace_id == auth.workspace.id)
        .order_by(TriggerRunLog.fired_at.desc())
        .limit(30)
        .all()
    )
    return {
        "triggers": [serialize_trigger(r) for r in rows],
        "history": [serialize_trigger_log(l) for l in logs],
    }


@router.post("/api/triggers")
def create_trigger(
    req: TriggerCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    tr = AutomationTrigger(
        workspace_id=auth.workspace.id,
        name=req.name.strip(),
        trigger_type=req.trigger_type,
        cron_expr=req.cron_expr,
        event_name=req.event_name,
        target_bot_id=req.target_bot_id,
        target_channel_id=req.target_channel_id,
        action_prompt=req.action_prompt,
        enabled=req.enabled,
        paused=req.paused,
    )
    db.add(tr)
    db.commit()
    db.refresh(tr)
    return {"trigger": serialize_trigger(tr)}


@router.patch("/api/triggers/{trigger_id}")
def patch_trigger(
    trigger_id: str,
    req: TriggerPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    tr = db.get(AutomationTrigger, trigger_id)
    if not tr or tr.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المحفّز غير موجود", "message_en": "Trigger not found"})
    for field in ("name", "cron_expr", "event_name", "action_prompt", "enabled", "paused"):
        val = getattr(req, field)
        if val is not None:
            setattr(tr, field, val)
    db.commit()
    return {"trigger": serialize_trigger(tr)}


@router.delete("/api/triggers/{trigger_id}")
def delete_trigger(
    trigger_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    tr = db.get(AutomationTrigger, trigger_id)
    if not tr or tr.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المحفّز غير موجود", "message_en": "Trigger not found"})
    db.query(TriggerRunLog).filter(TriggerRunLog.trigger_id == tr.id).delete()
    db.delete(tr)
    db.commit()
    return {"deleted": True, "id": trigger_id}


@router.post("/api/triggers/{trigger_id}/fire")
def fire_trigger_endpoint(
    trigger_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    tr = db.get(AutomationTrigger, trigger_id)
    if not tr or tr.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المحفّز غير موجود", "message_en": "Trigger not found"})
    log = fire_trigger(db, tr, reason="manual")
    db.commit()
    return {"trigger": serialize_trigger(tr), "log": serialize_trigger_log(log)}


@router.get("/api/triggers/{trigger_id}/history")
def trigger_history_endpoint(
    trigger_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    logs = (
        db.query(TriggerRunLog)
        .filter(TriggerRunLog.workspace_id == auth.workspace.id, TriggerRunLog.trigger_id == trigger_id)
        .order_by(TriggerRunLog.fired_at.desc())
        .limit(50)
        .all()
    )
    return {"history": [serialize_trigger_log(l) for l in logs]}


@router.post("/api/triggers/emit-event")
def emit_trigger_event_endpoint(
    req: dict[str, Any],
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    ev_name = str(req.get("event_name") or "").strip()
    if not ev_name:
        raise HTTPException(status_code=400, detail={"code": "missing_event", "message_ar": "اسم الحدث مطلوب", "message_en": "event_name required"})
    logs = emit_trigger_event(db, auth.workspace.id, ev_name, dict(req.get("payload") or {}))
    db.commit()
    return {"fired_count": len(logs), "logs": [serialize_trigger_log(l) for l in logs]}
