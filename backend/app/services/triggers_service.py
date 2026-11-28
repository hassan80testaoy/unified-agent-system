"""Phase 5 Schedule & Event Automation Triggers with Explicit Firing History (§5C)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal, utcnow
from app.models.core import Notification, Workspace
from app.models.phase5 import AutomationTrigger, BotContact, TeamChannel, TriggerRunLog
from app.services.teams_service import chat_with_bot, post_channel_message


def _match_field(expr: str, value: int) -> bool:
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "*":
            return True
        if part.startswith("*/"):
            step = max(1, int(part[2:]))
            if value % step == 0:
                return True
        elif "-" in part:
            lo_s, hi_s = part.split("-", 1)
            if int(lo_s) <= value <= int(hi_s):
                return True
        else:
            if int(part) == value:
                return True
    return False


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    fields = (cron_expr or "").strip().split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    # Python weekday(): Mon=0..Sun=6; cron: Sun=0/7..Sat=6
    cron_dow = (dt.weekday() + 1) % 7
    try:
        return (
            _match_field(minute, dt.minute)
            and _match_field(hour, dt.hour)
            and _match_field(day, dt.day)
            and _match_field(month, dt.month)
            and (_match_field(weekday, cron_dow) or (cron_dow == 0 and _match_field(weekday, 7)))
        )
    except ValueError:
        return False


def fire_trigger(
    db: Session,
    trigger: AutomationTrigger,
    *,
    reason: str = "manual",
    payload: dict[str, Any] | None = None,
) -> TriggerRunLog:
    now = utcnow()
    prompt = trigger.action_prompt or trigger.name
    if payload:
        prompt = f"{prompt}\n\nEvent payload: {payload}"

    status = "success"
    output_preview = ""
    error_text: str | None = None
    action_summary = f"Triggered via {reason}: {trigger.name}"

    try:
        if trigger.target_channel_id:
            ch = db.get(TeamChannel, trigger.target_channel_id)
            if ch and ch.workspace_id == trigger.workspace_id:
                res = post_channel_message(db, workspace_id=trigger.workspace_id, channel=ch, content=prompt, mock_mode=False)
                output_preview = str(res.get("reply") or "")[:400]
                action_summary = f"Posted to channel '{ch.name}' -> bot '{res['routed_bot']['name']}'"
        elif trigger.target_bot_id:
            bot = db.get(BotContact, trigger.target_bot_id)
            if bot and bot.workspace_id == trigger.workspace_id:
                res = chat_with_bot(db, workspace_id=trigger.workspace_id, bot=bot, content=prompt, mock_mode=False)
                output_preview = str(res.get("reply") or "")[:400]
                action_summary = f"Messaged bot '{bot.name}'"
        else:
            output_preview = f"Executed trigger action: {prompt[:240]}"
    except Exception as exc:
        status = "failed"
        error_text = f"{type(exc).__name__}: {exc}"[:400]

    trigger.last_fired_at = now
    trigger.fire_count = int(trigger.fire_count or 0) + 1

    log = TriggerRunLog(
        workspace_id=trigger.workspace_id,
        trigger_id=trigger.id,
        fired_at=now,
        reason=reason,
        action_summary=action_summary,
        status=status,
        output_preview=output_preview,
        error_text=error_text,
    )
    db.add(log)

    # Never run silently: emit a visible workspace notification
    ws = db.get(Workspace, trigger.workspace_id)
    if ws and ws.owner_id:
        db.add(
            Notification(
                workspace_id=trigger.workspace_id,
                user_id=ws.owner_id,
                kind=f"trigger_{status}",
                title=f"تم تشغيل المحفّز: {trigger.name}" if status == "success" else f"فشل المحفّز: {trigger.name}",
                body=action_summary if status == "success" else (error_text or action_summary),
            )
        )
    db.flush()
    return log


def emit_trigger_event(db: Session, workspace_id: str, event_name: str, payload: dict[str, Any] | None = None) -> list[TriggerRunLog]:
    rows = (
        db.query(AutomationTrigger)
        .filter(
            AutomationTrigger.workspace_id == workspace_id,
            AutomationTrigger.trigger_type == "event",
            AutomationTrigger.event_name == event_name,
            AutomationTrigger.enabled.is_(True),
            AutomationTrigger.paused.is_(False),
        )
        .all()
    )
    return [fire_trigger(db, tr, reason=f"event:{event_name}", payload=payload) for tr in rows]


def tick_due_schedule_triggers(db: Session, now: datetime | None = None) -> list[TriggerRunLog]:
    cur = now or datetime.now(timezone.utc)
    rows = (
        db.query(AutomationTrigger)
        .filter(
            AutomationTrigger.trigger_type == "schedule",
            AutomationTrigger.enabled.is_(True),
            AutomationTrigger.paused.is_(False),
        )
        .all()
    )
    logs: list[TriggerRunLog] = []
    for tr in rows:
        if not tr.cron_expr or not cron_matches(tr.cron_expr, cur):
            continue
        if tr.last_fired_at:
            lf = tr.last_fired_at
            if lf.tzinfo is None:
                lf = lf.replace(tzinfo=timezone.utc)
            if lf.strftime("%Y-%m-%d %H:%M") == cur.strftime("%Y-%m-%d %H:%M"):
                continue
        logs.append(fire_trigger(db, tr, reason="schedule"))
    db.flush()
    return logs


async def trigger_scheduler_loop(stop_event: asyncio.Event) -> None:
    """Minute-granularity scheduler loop checking due cron triggers & 08:00 owner digests."""
    from app.services.daily_report_service import tick_daily_0800_reports

    while not stop_event.is_set():
        db = SessionLocal()
        try:
            tick_due_schedule_triggers(db)
            tick_daily_0800_reports(db)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            continue


def serialize_trigger(tr: AutomationTrigger) -> dict[str, Any]:
    return {
        "id": tr.id,
        "name": tr.name,
        "trigger_type": tr.trigger_type,
        "cron_expr": tr.cron_expr,
        "event_name": tr.event_name,
        "target_bot_id": tr.target_bot_id,
        "target_channel_id": tr.target_channel_id,
        "action_prompt": tr.action_prompt,
        "enabled": bool(tr.enabled),
        "paused": bool(tr.paused),
        "last_fired_at": tr.last_fired_at.isoformat() if tr.last_fired_at else None,
        "fire_count": int(tr.fire_count or 0),
    }


def serialize_trigger_log(log: TriggerRunLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "trigger_id": log.trigger_id,
        "fired_at": log.fired_at.isoformat(),
        "reason": log.reason,
        "action_summary": log.action_summary,
        "status": log.status,
        "output_preview": log.output_preview,
        "error_text": log.error_text,
    }
