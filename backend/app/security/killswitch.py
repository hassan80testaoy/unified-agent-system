"""Global Kill Switch (<5s halt) backed by STOP sentinel file, Redis (optional), and PostgreSQL/SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.core import SystemState
from app.models.agents import Run
from app.security.auth import record_audit

KILLSWITCH_STATE_KEY = "global_killswitch"


def _redis_client():
    url = get_settings().redis_url
    if not url:
        return None
    try:
        import redis
        return redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
    except Exception:
        return None


def is_killswitch_active(db: Session | None = None) -> bool:
    settings = get_settings()
    if settings.killswitch_file.exists():
        return True

    r = _redis_client()
    if r is not None:
        try:
            if r.get("uas:killswitch") == "1":
                return True
        except Exception:
            pass

    if db is not None:
        row = db.get(SystemState, KILLSWITCH_STATE_KEY)
        if row and bool((row.value_json or {}).get("active")):
            return True
    return False


def get_killswitch_status(db: Session) -> dict[str, Any]:
    row = db.get(SystemState, KILLSWITCH_STATE_KEY)
    val = dict(row.value_json) if row and row.value_json else {}
    active = is_killswitch_active(db)
    return {
        "active": active,
        "reason": val.get("reason", ""),
        "updated_at": val.get("updated_at"),
    }


def activate_killswitch(
    db: Session,
    *,
    workspace_id: str,
    user_id: str | None,
    reason: str = "Manual emergency stop",
    ip: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    now_iso = datetime.now(timezone.utc).isoformat()
    settings.killswitch_file.write_text(f"STOP {now_iso}\n", encoding="utf-8")

    r = _redis_client()
    if r is not None:
        try:
            r.set("uas:killswitch", "1")
        except Exception:
            pass

    row = db.get(SystemState, KILLSWITCH_STATE_KEY)
    payload = {"active": True, "reason": reason, "updated_at": now_iso, "by_user_id": user_id}
    if row is None:
        row = SystemState(key=KILLSWITCH_STATE_KEY, workspace_id=workspace_id, value_json=payload)
        db.add(row)
    else:
        row.value_json = payload

    # Immediately mark queued / running / waiting runs in this workspace as canceled
    active_runs = (
        db.query(Run)
        .filter(
            Run.workspace_id == workspace_id,
            Run.status.in_(["queued", "running", "awaiting_approval", "awaiting_captcha_manual", "awaiting_verification_code", "paused"]),
        )
        .all()
    )
    canceled_count = 0
    for run in active_runs:
        run.status = "canceled"
        run.error = f"Kill switch activated: {reason}"
        run.finished_at = datetime.now(timezone.utc)
        canceled_count += 1

    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="killswitch.activate",
        target="global",
        ip=ip,
        details={"reason": reason, "canceled_runs": canceled_count},
    )
    db.commit()
    return {"active": True, "reason": reason, "canceled_runs": canceled_count, "updated_at": now_iso}


def deactivate_killswitch(
    db: Session,
    *,
    workspace_id: str,
    user_id: str | None,
    ip: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.killswitch_file.exists():
        settings.killswitch_file.unlink()

    r = _redis_client()
    if r is not None:
        try:
            r.delete("uas:killswitch")
        except Exception:
            pass

    now_iso = datetime.now(timezone.utc).isoformat()
    row = db.get(SystemState, KILLSWITCH_STATE_KEY)
    payload = {"active": False, "reason": "", "updated_at": now_iso, "by_user_id": user_id}
    if row is None:
        row = SystemState(key=KILLSWITCH_STATE_KEY, workspace_id=workspace_id, value_json=payload)
        db.add(row)
    else:
        row.value_json = payload

    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="killswitch.deactivate",
        target="global",
        ip=ip,
    )
    db.commit()
    return {"active": False, "updated_at": now_iso}
