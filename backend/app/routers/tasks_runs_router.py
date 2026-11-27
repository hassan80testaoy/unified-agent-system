"""Tasks, Dispatcher, Runs, Live WebSocket Events, and Approvals endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.agents import AgentModel, Approval, Run, RunEvent, RunItem, Task
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles
from app.services.dispatcher import dispatch_request
from app.services.events import ws_hub
from app.services.runner import create_task_and_run, preview_task, schedule_run_execution

router = APIRouter(tags=["tasks_runs"])


class DispatchRequest(BaseModel):
    request_text: str


class TaskPreviewRequest(BaseModel):
    agent_id: str | None = None
    request_text: str = ""
    input_json: dict[str, Any] = Field(default_factory=dict)


class TaskCreateRequest(BaseModel):
    agent_id: str | None = None
    request_text: str = ""
    input_json: dict[str, Any] = Field(default_factory=dict)
    mode: str = "once"
    schedule_cron: str | None = None


class ReviewItemRequest(BaseModel):
    review_state: str  # accepted | redo | dropped


def _resolve_agent(db: Session, workspace_id: str, agent_id: str | None, request_text: str) -> AgentModel:
    if agent_id:
        agent = db.get(AgentModel, agent_id) or db.query(AgentModel).filter(AgentModel.key == agent_id).first()
        if not agent or agent.workspace_id != workspace_id:
            raise HTTPException(
                status_code=404,
                detail={"code": "agent_not_found", "message_ar": "الوكيل غير موجود", "message_en": "Agent not found"},
            )
        return agent

    dispatched = dispatch_request(db, workspace_id, request_text)
    if not dispatched.get("matched"):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "no_matching_agent",
                "message_ar": dispatched.get("message_ar"),
                "message_en": dispatched.get("message_en"),
            },
        )
    return db.get(AgentModel, dispatched["agent_id"])  # type: ignore[return-value]


@router.post("/api/tasks/dispatch")
def run_dispatcher(req: DispatchRequest, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return dispatch_request(db, auth.workspace.id, req.request_text)


@router.post("/api/tasks/preview")
def preview_task_endpoint(req: TaskPreviewRequest, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    agent = _resolve_agent(db, auth.workspace.id, req.agent_id, req.request_text)
    try:
        return preview_task(db, workspace_id=auth.workspace.id, agent=agent, input_json=req.input_json)
    except jsonschema.ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_input", "message_ar": f"خطأ في الحقول: {exc.message}", "message_en": exc.message},
        ) from exc


@router.post("/api/tasks")
async def create_task_endpoint(
    req: TaskCreateRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    agent = _resolve_agent(db, auth.workspace.id, req.agent_id, req.request_text)
    try:
        task, run, prev = create_task_and_run(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            agent=agent,
            request_text=req.request_text,
            input_json=req.input_json,
            mode=req.mode,
            schedule_cron=req.schedule_cron,
            ip=request.client.host if request.client else None,
        )
        return {
            "task": {"id": task.id, "agent_id": task.agent_id, "mode": task.mode, "status": task.status},
            "run": {"id": run.id, "status": run.status, "progress_total": run.progress_total},
            "preview": prev,
        }
    except jsonschema.ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_input", "message_ar": f"بيانات غير صالحة: {exc.message}", "message_en": exc.message},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "killswitch_active", "message_ar": str(exc), "message_en": str(exc)},
        ) from exc


@router.get("/api/tasks")
def list_tasks(
    status_filter: str | None = None,
    agent: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    q = db.query(Task).filter(Task.workspace_id == auth.workspace.id)
    if status_filter:
        q = q.filter(Task.status == status_filter)
    if agent:
        q = q.filter(Task.agent_id == agent)
    rows = q.order_by(Task.created_at.desc()).limit(100).all()
    return {
        "tasks": [
            {
                "id": t.id,
                "agent_id": t.agent_id,
                "request_text": t.request_text,
                "input_json": t.input_json,
                "mode": t.mode,
                "status": t.status,
                "created_at": t.created_at.isoformat(),
            }
            for t in rows
        ]
    }


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t or t.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المهمة غير موجودة", "message_en": "Task not found"})
    return {
        "id": t.id,
        "agent_id": t.agent_id,
        "request_text": t.request_text,
        "input_json": t.input_json,
        "mode": t.mode,
        "status": t.status,
        "created_at": t.created_at.isoformat(),
    }


@router.get("/api/runs")
def list_runs(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(Run).filter(Run.workspace_id == auth.workspace.id).order_by(Run.created_at.desc()).limit(100).all()
    agents_map = {a.id: a for a in db.query(AgentModel).filter(AgentModel.workspace_id == auth.workspace.id).all()}
    return {
        "runs": [
            {
                "id": r.id,
                "task_id": r.task_id,
                "agent_id": r.agent_id,
                "agent_key": agents_map[r.agent_id].key if r.agent_id in agents_map else "",
                "agent_name_ar": agents_map[r.agent_id].name_ar if r.agent_id in agents_map else "",
                "status": r.status,
                "progress_done": r.progress_done,
                "progress_total": r.progress_total,
                "cost_usd": r.cost_usd,
                "error": r.error,
                "summary_json": r.summary_json,
                "created_at": r.created_at.isoformat(),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]
    }


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r or r.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "التشغيل غير موجود", "message_en": "Run not found"})
    task = db.get(Task, r.task_id)
    agent = db.get(AgentModel, r.agent_id)
    events = db.query(RunEvent).filter(RunEvent.run_id == r.id).order_by(RunEvent.ts.asc()).all()
    items = db.query(RunItem).filter(RunItem.run_id == r.id).order_by(RunItem.created_at.asc()).all()
    approvals = db.query(Approval).filter(Approval.run_id == r.id, Approval.status == "pending").all()
    return {
        "run": {
            "id": r.id,
            "task_id": r.task_id,
            "agent_id": r.agent_id,
            "agent_key": agent.key if agent else "",
            "agent_name_ar": agent.name_ar if agent else "",
            "status": r.status,
            "progress_done": r.progress_done,
            "progress_total": r.progress_total,
            "cost_usd": r.cost_usd,
            "error": r.error,
            "summary_json": r.summary_json,
            "settings_used": task.input_json if task else {},
            "created_at": r.created_at.isoformat(),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        },
        "events": [
            {
                "id": e.id,
                "ts": e.ts.isoformat(),
                "level": e.level,
                "kind": e.kind,
                "message": e.message,
                "data": e.data or {},
            }
            for e in events
        ],
        "items": [
            {
                "id": i.id,
                "item_type": i.item_type,
                "item_ref": i.item_ref,
                "status": i.status,
                "review_state": i.review_state,
                "result": i.result or {},
            }
            for i in items
        ],
        "pending_approvals": [
            {"id": a.id, "kind": a.kind, "payload": a.payload, "status": a.status}
            for a in approvals
        ],
    }


@router.get("/api/runs/{run_id}/items")
def get_run_items(run_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r or r.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "التشغيل غير موجود", "message_en": "Run not found"})
    items = db.query(RunItem).filter(RunItem.run_id == r.id).order_by(RunItem.created_at.asc()).all()
    return {
        "items": [
            {
                "id": i.id,
                "item_type": i.item_type,
                "item_ref": i.item_ref,
                "status": i.status,
                "review_state": i.review_state,
                "result": i.result or {},
            }
            for i in items
        ]
    }


@router.post("/api/runs/{run_id}/items/{item_id}/review")
def review_run_item(
    run_id: str,
    item_id: str,
    req: ReviewItemRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    if req.review_state not in {"accepted", "redo", "dropped"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_state", "message_ar": "حالة مراجعة غير صالحة", "message_en": "Invalid review state"})
    item = db.get(RunItem, item_id)
    if not item or item.run_id != run_id or item.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "العنصر غير موجود", "message_en": "Item not found"})
    item.review_state = req.review_state
    db.commit()
    return {"id": item.id, "review_state": item.review_state}


@router.post("/api/runs/{run_id}/{action}")
async def control_run(
    run_id: str,
    action: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    if action not in {"pause", "resume", "cancel", "retry"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_action", "message_ar": "إجراء غير صالح", "message_en": "Invalid action"})
    r = db.get(Run, run_id)
    if not r or r.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "التشغيل غير موجود", "message_en": "Run not found"})

    if action == "pause":
        r.status = "paused"
    elif action == "cancel":
        r.status = "canceled"
        r.finished_at = datetime.now(timezone.utc)
    elif action in {"resume", "retry"}:
        r.status = "queued"
        r.error = None
        db.commit()
        schedule_run_execution(r.id, auth.workspace.id, auth.user.id)
        return {"id": r.id, "status": r.status}
    db.commit()
    return {"id": r.id, "status": r.status}


@router.websocket("/api/ws/runs/{run_id}")
async def run_events_websocket(websocket: WebSocket, run_id: str):
    await ws_hub.connect(run_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_hub.disconnect(run_id, websocket)


@router.get("/api/approvals")
def list_approvals(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(Approval).filter(Approval.workspace_id == auth.workspace.id).order_by(Approval.created_at.desc()).limit(100).all()
    return {
        "approvals": [
            {
                "id": a.id,
                "run_id": a.run_id,
                "kind": a.kind,
                "payload": a.payload,
                "status": a.status,
                "created_at": a.created_at.isoformat(),
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            }
            for a in rows
        ]
    }


@router.post("/api/approvals/{approval_id}/{decision}")
async def decide_approval(
    approval_id: str,
    decision: str,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_decision", "message_ar": "قرار غير صالح", "message_en": "Invalid decision"})
    a = db.get(Approval, approval_id)
    if not a or a.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الطلب غير موجود", "message_en": "Approval not found"})

    a.status = "approved" if decision == "approve" else "rejected"
    a.decided_by = auth.user.id
    a.decided_at = datetime.now(timezone.utc)
    run = db.get(Run, a.run_id)

    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action=f"approval.{decision}",
        target=a.id,
        ip=request.client.host if request.client else None,
        details={"kind": a.kind, "run_id": a.run_id},
    )

    if run and a.kind in {"spend", "bulk", "mailbox", "outbound"}:
        if decision == "approve":
            run.status = "queued"
            db.commit()
            schedule_run_execution(run.id, auth.workspace.id, auth.user.id)
            return {"id": a.id, "status": a.status, "run_status": "queued"}
        else:
            run.status = "canceled"
            run.finished_at = datetime.now(timezone.utc)

    db.commit()
    return {"id": a.id, "status": a.status}
