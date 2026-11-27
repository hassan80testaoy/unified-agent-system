"""Real-time event emitter & WebSocket pub/sub hub (Redis or in-process asyncio on Windows)."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
from sqlalchemy.orm import Session

from app.models.agents import RunEvent
from app.security.sanitizer import scrub_text, scrub_value


class RunWebSocketHub:
    """Broadcasts live run events to subscribed dashboard WebSocket clients."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, run_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._subscribers[run_id].add(websocket)

    async def disconnect(self, run_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers[run_id].discard(websocket)
            if not self._subscribers[run_id]:
                self._subscribers.pop(run_id, None)

    async def broadcast(self, run_id: str, payload: dict[str, Any]) -> None:
        clean_payload = scrub_value(payload)
        message = json.dumps(clean_payload, ensure_ascii=False, default=str)
        async with self._lock:
            targets = list(self._subscribers.get(run_id, set()))
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._subscribers[run_id].discard(ws)


ws_hub = RunWebSocketHub()


def emit_run_event(
    db: Session,
    *,
    workspace_id: str,
    run_id: str,
    level: str,
    kind: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> RunEvent:
    """Persist a sanitized RunEvent (never stores secrets, codes, or raw activation links)."""
    clean_msg = scrub_text(message)
    clean_data = scrub_value(data) if data else None
    ev = RunEvent(
        workspace_id=workspace_id,
        run_id=run_id,
        ts=datetime.now(timezone.utc),
        level=level,
        kind=kind,
        message=clean_msg,
        data=clean_data,
    )
    db.add(ev)
    db.flush()
    return ev


async def emit_and_broadcast_run_event(
    db: Session,
    *,
    workspace_id: str,
    run_id: str,
    level: str,
    kind: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> RunEvent:
    ev = emit_run_event(
        db,
        workspace_id=workspace_id,
        run_id=run_id,
        level=level,
        kind=kind,
        message=message,
        data=data,
    )
    db.commit()
    await ws_hub.broadcast(
        run_id,
        {
            "id": ev.id,
            "run_id": run_id,
            "ts": ev.ts.isoformat(),
            "level": ev.level,
            "kind": ev.kind,
            "message": ev.message,
            "data": ev.data or {},
        },
    )
    return ev
