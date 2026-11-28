"""Phase 3 Coding Agent REST & WebSocket API router (§1A-§1G)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models.agents import Artifact, Goal
from app.models.coding import CodingThread, HookRule, ModelRace
from app.security.auth import AuthContext, get_current_auth, require_roles
from app.services.coding_agent import (
    SUBAGENT_ROLES,
    approve_plan_and_execute,
    create_coding_thread,
    create_goal,
    create_thread_worktree,
    fork_coding_thread,
    get_thread_changes,
    merge_thread_worktree,
    resume_interrupted_threads,
    run_model_race,
    send_coding_message,
    serialize_artifact,
    serialize_coding_thread,
    serialize_goal,
    serialize_hook,
    serialize_race,
    spawn_subagent,
    step_goal,
    undo_coding_thread,
    update_todo_list,
)
from app.services.events import ws_hub

router = APIRouter(tags=["coding"])


class CodingThreadCreateRequest(BaseModel):
    title: str = "مهمة برمجة جديدة"
    goal: str = ""
    workspace_dir: str | None = None
    mode_key: str = "delegate"
    model_provider_id: str | None = None
    model_name: str | None = None
    use_worktree: bool = False


class CodingThreadPatchRequest(BaseModel):
    title: str | None = None
    mode_key: str | None = None
    model_provider_id: str | None = None
    model_name: str | None = None
    status: str | None = None


class CodingMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=24000)
    inject_initial_bug: bool = True
    skip_browser_verify: bool = False


class ApprovePlanRequest(BaseModel):
    design_index: int = 0
    inject_initial_bug: bool = True
    skip_browser_verify: bool = False


class TodoUpdateRequest(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class UndoRequest(BaseModel):
    to_seq: int = 0


class ForkRequest(BaseModel):
    from_seq: int | None = None
    new_title: str | None = None
    use_worktree: bool = True


class WorktreeCreateRequest(BaseModel):
    branch_name: str | None = None


class ModelRaceRequest(BaseModel):
    prompt: str = Field(min_length=1)
    test_command: str = Field(min_length=1)
    candidates: list[dict[str, Any]] = Field(min_length=2)


class SubagentSpawnRequest(BaseModel):
    role: str
    prompt: str = Field(min_length=1)
    use_worktree: bool = False


class GoalCreateRequest(BaseModel):
    thread_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    outcome: str = Field(min_length=1)
    autopilot: bool = True


class HookCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    event: str = Field(pattern="^(pre_tool|post_tool|on_stop)$")
    tool_pattern: str = "*"
    command: str = Field(min_length=1, max_length=512)
    enabled: bool = True
    block_on_fail: bool = True


def _get_thread_or_404(db: Session, workspace_id: str, thread_id: str) -> CodingThread:
    t = db.get(CodingThread, thread_id)
    if not t or t.workspace_id != workspace_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message_ar": "سير البرمجة غير موجود", "message_en": "Coding thread not found"},
        )
    return t


@router.get("/api/threads")
def list_coding_threads(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(CodingThread)
        .filter(CodingThread.workspace_id == auth.workspace.id)
        .order_by(CodingThread.updated_at.desc())
        .limit(100)
        .all()
    )
    active_running = sum(1 for r in rows if r.status == "running")
    max_parallel = max(1, int(get_settings().max_parallel_coding_threads or 5))
    return {
        "threads": [serialize_coding_thread(t) for t in rows],
        "active_running": active_running,
        "max_parallel": max_parallel,
        "subagent_roles": list(SUBAGENT_ROLES),
    }


@router.post("/api/threads")
def create_coding_thread_endpoint(
    req: CodingThreadCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = create_coding_thread(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        title=req.title,
        goal=req.goal,
        workspace_dir=req.workspace_dir,
        mode_key=req.mode_key,
        model_provider_id=req.model_provider_id,
        model_name=req.model_name,
        use_worktree=req.use_worktree,
    )
    db.commit()
    db.refresh(t)
    return {"thread": serialize_coding_thread(t)}


@router.post("/api/threads/resume-interrupted")
def resume_interrupted_endpoint(
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    resumed = resume_interrupted_threads(db)
    db.commit()
    return {"resumed": resumed, "count": len(resumed)}


@router.get("/api/threads/{thread_id}")
def get_coding_thread_endpoint(
    thread_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    arts = (
        db.query(Artifact)
        .filter(Artifact.workspace_id == auth.workspace.id, Artifact.thread_id == t.id)
        .order_by(Artifact.created_at.desc())
        .all()
    )
    races = (
        db.query(ModelRace)
        .filter(ModelRace.workspace_id == auth.workspace.id, ModelRace.thread_id == t.id)
        .order_by(ModelRace.created_at.desc())
        .all()
    )
    subagents = (
        db.query(CodingThread)
        .filter(CodingThread.workspace_id == auth.workspace.id, CodingThread.parent_thread_id == t.id)
        .order_by(CodingThread.created_at.desc())
        .all()
    )
    return {
        "thread": serialize_coding_thread(t),
        "artifacts": [serialize_artifact(a) for a in arts],
        "races": [serialize_race(r) for r in races],
        "subagents": [serialize_coding_thread(s) for s in subagents],
    }


@router.patch("/api/threads/{thread_id}")
def patch_coding_thread_endpoint(
    thread_id: str,
    req: CodingThreadPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    if req.title is not None and req.title.strip():
        t.title = req.title.strip()
    if req.mode_key is not None:
        t.mode_key = req.mode_key
    if req.model_provider_id is not None:
        t.model_provider_id = req.model_provider_id or None
    if req.model_name is not None:
        t.model_name = req.model_name or None
    if req.status is not None:
        t.status = req.status
    db.commit()
    db.refresh(t)
    return {"thread": serialize_coding_thread(t)}


@router.post("/api/threads/{thread_id}/messages")
def send_coding_message_endpoint(
    thread_id: str,
    req: CodingMessageRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    try:
        res = send_coding_message(
            db,
            t,
            req.content,
            inject_initial_bug=req.inject_initial_bug,
            skip_browser_verify=req.skip_browser_verify,
        )
        db.commit()
        return res
    except ValueError as exc:
        msg = str(exc)
        status_code = 429 if msg.startswith("max_parallel_threads_reached") else 400
        raise HTTPException(status_code=status_code, detail={"code": "coding_error", "message_ar": msg, "message_en": msg}) from exc


@router.post("/api/threads/{thread_id}/approve-plan")
def approve_plan_endpoint(
    thread_id: str,
    req: ApprovePlanRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    try:
        res = approve_plan_and_execute(
            db,
            t,
            design_index=req.design_index,
            inject_initial_bug=req.inject_initial_bug,
            skip_browser_verify=req.skip_browser_verify,
        )
        db.commit()
        return res
    except ValueError as exc:
        msg = str(exc)
        status_code = 429 if msg.startswith("max_parallel_threads_reached") else 400
        raise HTTPException(status_code=status_code, detail={"code": "coding_error", "message_ar": msg, "message_en": msg}) from exc


@router.get("/api/threads/{thread_id}/artifacts")
def list_thread_artifacts_endpoint(
    thread_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    rows = (
        db.query(Artifact)
        .filter(Artifact.workspace_id == auth.workspace.id, Artifact.thread_id == t.id)
        .order_by(Artifact.created_at.desc())
        .all()
    )
    return {"artifacts": [serialize_artifact(a) for a in rows]}


@router.get("/api/threads/{thread_id}/artifacts/{artifact_id}/file")
def download_thread_artifact_file(
    thread_id: str,
    artifact_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    art = db.get(Artifact, artifact_id)
    if not art or art.workspace_id != auth.workspace.id or art.thread_id != t.id:
        raise HTTPException(status_code=404, detail={"code": "artifact_not_found", "message_ar": "المخرج غير موجود", "message_en": "Artifact not found"})
    target = Path(art.path).resolve()
    allowed_roots = [get_settings().artifacts_dir.resolve(), Path(t.workspace_dir).resolve()]
    if not any(root in target.parents or root == target for root in allowed_roots) or not target.is_file():
        raise HTTPException(status_code=404, detail={"code": "file_missing", "message_ar": "الملف غير متاح", "message_en": "Artifact file missing"})
    return FileResponse(path=str(target), filename=target.name)


@router.get("/api/threads/{thread_id}/changes")
def get_thread_changes_endpoint(
    thread_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    return get_thread_changes(t)


@router.put("/api/threads/{thread_id}/todo")
def update_todo_endpoint(
    thread_id: str,
    req: TodoUpdateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    res = update_todo_list(db, t, req.items)
    db.commit()
    return res


@router.post("/api/threads/{thread_id}/undo")
def undo_coding_thread_endpoint(
    thread_id: str,
    req: UndoRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    res = undo_coding_thread(db, t, to_seq=req.to_seq)
    db.commit()
    return res


@router.post("/api/threads/{thread_id}/fork")
def fork_coding_thread_endpoint(
    thread_id: str,
    req: ForkRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    child = fork_coding_thread(
        db,
        t,
        from_seq=req.from_seq,
        new_title=req.new_title,
        use_worktree=req.use_worktree,
    )
    db.commit()
    db.refresh(child)
    return {"thread": serialize_coding_thread(child)}


@router.post("/api/threads/{thread_id}/worktree")
def create_worktree_endpoint(
    thread_id: str,
    req: WorktreeCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    res = create_thread_worktree(db, t, branch_name=req.branch_name)
    db.commit()
    return res


@router.post("/api/threads/{thread_id}/worktree/merge")
def merge_worktree_endpoint(
    thread_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    try:
        res = merge_thread_worktree(db, t)
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "no_worktree", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/threads/{thread_id}/race")
def run_race_endpoint(
    thread_id: str,
    req: ModelRaceRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    try:
        race = run_model_race(
            db,
            t,
            prompt=req.prompt,
            candidates=req.candidates,
            test_command=req.test_command,
        )
        db.commit()
        db.refresh(race)
        return {"race": serialize_race(race)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "race_error", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/threads/{thread_id}/subagents")
def spawn_subagent_endpoint(
    thread_id: str,
    req: SubagentSpawnRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    t = _get_thread_or_404(db, auth.workspace.id, thread_id)
    try:
        res = spawn_subagent(db, t, role=req.role, prompt=req.prompt, use_worktree=req.use_worktree)
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "subagent_error", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.get("/api/goals")
def list_goals_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(Goal)
        .filter(Goal.workspace_id == auth.workspace.id)
        .order_by(Goal.updated_at.desc())
        .limit(100)
        .all()
    )
    return {"goals": [serialize_goal(g) for g in rows]}


@router.post("/api/goals")
def create_goal_endpoint(
    req: GoalCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    g = create_goal(
        db,
        workspace_id=auth.workspace.id,
        thread_id=req.thread_id,
        title=req.title,
        outcome=req.outcome,
        autopilot=req.autopilot,
    )
    db.commit()
    db.refresh(g)
    return {"goal": serialize_goal(g)}


@router.post("/api/goals/{goal_id}/step")
def step_goal_endpoint(
    goal_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    g = db.get(Goal, goal_id)
    if not g or g.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "goal_not_found", "message_ar": "الهدف غير موجود", "message_en": "Goal not found"})
    res = step_goal(db, g)
    db.commit()
    return {"goal": res}


@router.get("/api/hooks")
def list_hooks_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(HookRule)
        .filter(HookRule.workspace_id == auth.workspace.id)
        .order_by(HookRule.created_at.desc())
        .all()
    )
    return {"hooks": [serialize_hook(h) for h in rows]}


@router.post("/api/hooks")
def create_hook_endpoint(
    req: HookCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    h = HookRule(
        workspace_id=auth.workspace.id,
        name=req.name,
        event=req.event,
        tool_pattern=req.tool_pattern,
        command=req.command,
        enabled=req.enabled,
        block_on_fail=req.block_on_fail,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return {"hook": serialize_hook(h)}


@router.delete("/api/hooks/{hook_id}")
def delete_hook_endpoint(
    hook_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    h = db.get(HookRule, hook_id)
    if not h or h.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "hook_not_found", "message_ar": "القاعدة غير موجودة", "message_en": "Hook not found"})
    db.delete(h)
    db.commit()
    return {"deleted": True, "id": hook_id}


@router.websocket("/api/ws/coding-threads/{thread_id}")
async def coding_thread_ws(websocket: WebSocket, thread_id: str):
    key = f"coding:{thread_id}"
    await ws_hub.connect(key, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_hub.disconnect(key, websocket)
