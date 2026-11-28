"""Chat-First General Agent API router (REST + WebSocket)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.v3_harness import ChatMessage, ChatThread
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles
from app.services.chat_agent import compact_thread, create_thread, process_user_message, undo_thread
from app.services.events import ws_hub
from app.services.session_log import read_log

router = APIRouter(tags=["chat"])


class ThreadCreateRequest(BaseModel):
    title: str = "محادثة جديدة"
    mode_key: str = "delegate"
    project_key: str = "default"
    approval_mode: str = "ask_first"


class ThreadPatchRequest(BaseModel):
    title: str | None = None
    mode_key: str | None = None
    approval_mode: str | None = None


class ChatMessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16000)
    mock_mode: bool = True
    simulate_captcha: bool = False
    simulate_2fa: bool = False


class ThreadUndoRequest(BaseModel):
    to_seq: int | None = None


def _serialize_thread(t: ChatThread) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "mode_key": t.mode_key,
        "project_key": t.project_key,
        "approval_mode": t.approval_mode,
        "ndjson_path": t.ndjson_path,
        "compacted_summary": t.compacted_summary,
        "status": t.status,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _serialize_message(m: ChatMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "seq": m.seq,
        "role": m.role,
        "content": m.content,
        "intent": m.intent,
        "card_type": m.card_type,
        "card_payload": m.card_payload,
        "run_id": m.run_id,
        "created_at": m.created_at.isoformat(),
    }


@router.get("/api/chat/threads")
def list_threads(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(ChatThread)
        .filter(ChatThread.workspace_id == auth.workspace.id)
        .order_by(ChatThread.updated_at.desc())
        .limit(100)
        .all()
    )
    return {"threads": [_serialize_thread(t) for t in rows]}


@router.post("/api/chat/threads")
def create_chat_thread(
    req: ThreadCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    thread = create_thread(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        title=req.title,
        mode_key=req.mode_key,
        project_key=req.project_key,
        approval_mode=req.approval_mode,
    )
    db.commit()
    db.refresh(thread)
    return {"thread": _serialize_thread(thread)}


@router.get("/api/chat/threads/{thread_id}")
def get_chat_thread(
    thread_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    t = db.get(ChatThread, thread_id)
    if not t or t.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المحادثة غير موجودة", "message_en": "Thread not found"})
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == t.id, ChatMessage.rewound.is_(False))
        .order_by(ChatMessage.seq.asc())
        .all()
    )
    return {
        "thread": _serialize_thread(t),
        "messages": [_serialize_message(m) for m in msgs],
        "session_log_count": len(read_log(t.id)),
    }


@router.patch("/api/chat/threads/{thread_id}")
def patch_chat_thread(
    thread_id: str,
    req: ThreadPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = db.get(ChatThread, thread_id)
    if not t or t.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المحادثة غير موجودة", "message_en": "Thread not found"})
    if req.title is not None:
        t.title = req.title.strip() or t.title
    if req.mode_key is not None:
        t.mode_key = req.mode_key
    if req.approval_mode is not None:
        t.approval_mode = req.approval_mode
    db.commit()
    return {"thread": _serialize_thread(t)}


@router.post("/api/chat/threads/{thread_id}/messages")
async def send_chat_message(
    thread_id: str,
    req: ChatMessageCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = db.get(ChatThread, thread_id)
    if not t or t.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المحادثة غير موجودة", "message_en": "Thread not found"})
    return await process_user_message(
        db,
        thread=t,
        user_id=auth.user.id,
        content=req.content,
        mock_mode=req.mock_mode,
        simulate_captcha=req.simulate_captcha,
        simulate_2fa=req.simulate_2fa,
    )


@router.post("/api/chat/threads/{thread_id}/compact")
def compact_chat_thread(
    thread_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = db.get(ChatThread, thread_id)
    if not t or t.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المحادثة غير موجودة", "message_en": "Thread not found"})
    return compact_thread(db, t)


@router.post("/api/chat/threads/{thread_id}/undo")
def undo_chat_thread(
    thread_id: str,
    req: ThreadUndoRequest | None = None,
    request: Request = None,  # type: ignore[assignment]
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = db.get(ChatThread, thread_id)
    if not t or t.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المحادثة غير موجودة", "message_en": "Thread not found"})
    res = undo_thread(db, t, to_seq=req.to_seq if req else None)
    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="chat.undo",
        target=thread_id,
        details=res,
        ip=request.client.host if request and request.client else None,
    )
    db.commit()
    return res


@router.websocket("/api/ws/threads/{thread_id}")
async def thread_events_websocket(websocket: WebSocket, thread_id: str):
    key = f"thread:{thread_id}"
    await ws_hub.connect(key, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_hub.disconnect(key, websocket)
