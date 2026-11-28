"""API endpoints for Agent Modes & Per-Project Mode Defaults (§1G)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.v3_harness import ModePreset, ProjectMode
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles
from app.services.modes import get_effective_project_mode, seed_default_modes

router = APIRouter(tags=["modes"])


class ModeCreateRequest(BaseModel):
    key: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name_ar: str = Field(min_length=1, max_length=128)
    name_en: str = Field(min_length=1, max_length=128)
    description_ar: str = ""
    description_en: str = ""
    system_prompt: str = ""
    tool_allowlist: list[str] = Field(default_factory=list)
    sandbox_mode: str = "workspace-write"
    approval_policy: str = "ask_first"
    is_default: bool = False


class ModePatchRequest(BaseModel):
    name_ar: str | None = None
    name_en: str | None = None
    description_ar: str | None = None
    description_en: str | None = None
    system_prompt: str | None = None
    tool_allowlist: list[str] | None = None
    sandbox_mode: str | None = None
    approval_policy: str | None = None
    is_default: bool | None = None


class ProjectModeUpdateRequest(BaseModel):
    mode_key: str = "delegate"
    model_provider_id: str | None = None
    model_name: str | None = None
    delegation_lanes: dict[str, Any] = Field(default_factory=dict)


def _serialize_mode(m: ModePreset) -> dict[str, Any]:
    return {
        "id": m.id,
        "key": m.key,
        "name_ar": m.name_ar,
        "name_en": m.name_en,
        "description_ar": m.description_ar,
        "description_en": m.description_en,
        "system_prompt": m.system_prompt,
        "tool_allowlist": list(m.tool_allowlist or []),
        "sandbox_mode": m.sandbox_mode,
        "approval_policy": m.approval_policy,
        "is_default": bool(m.is_default),
        "is_builtin": bool(m.is_builtin),
    }


@router.get("/api/modes")
def list_modes(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = seed_default_modes(db, auth.workspace.id)
    db.commit()
    return {"modes": [_serialize_mode(m) for m in rows]}


@router.post("/api/modes")
def create_mode(
    req: ModeCreateRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    seed_default_modes(db, auth.workspace.id)
    key = req.key.strip().lower()
    dup = db.query(ModePreset).filter(ModePreset.workspace_id == auth.workspace.id, ModePreset.key == key).first()
    if dup:
        raise HTTPException(
            status_code=409,
            detail={"code": "mode_exists", "message_ar": "الوضع موجود بالفعل", "message_en": "Mode already exists"},
        )
    if req.is_default:
        db.query(ModePreset).filter(ModePreset.workspace_id == auth.workspace.id).update({"is_default": False})

    row = ModePreset(
        workspace_id=auth.workspace.id,
        key=key,
        name_ar=req.name_ar,
        name_en=req.name_en,
        description_ar=req.description_ar,
        description_en=req.description_en,
        system_prompt=req.system_prompt,
        tool_allowlist=list(req.tool_allowlist),
        sandbox_mode=req.sandbox_mode,
        approval_policy=req.approval_policy,
        is_default=req.is_default,
        is_builtin=False,
    )
    db.add(row)
    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="mode.create",
        target=key,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"mode": _serialize_mode(row)}


@router.patch("/api/modes/{key}")
def patch_mode(
    key: str,
    req: ModePatchRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    seed_default_modes(db, auth.workspace.id)
    row = db.query(ModePreset).filter(ModePreset.workspace_id == auth.workspace.id, ModePreset.key == key).first()
    if not row:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الوضع غير موجود", "message_en": "Mode not found"})

    if req.is_default is True:
        db.query(ModePreset).filter(ModePreset.workspace_id == auth.workspace.id).update({"is_default": False})
        row.is_default = True
    elif req.is_default is False:
        row.is_default = False

    if req.name_ar is not None:
        row.name_ar = req.name_ar
    if req.name_en is not None:
        row.name_en = req.name_en
    if req.description_ar is not None:
        row.description_ar = req.description_ar
    if req.description_en is not None:
        row.description_en = req.description_en
    if req.system_prompt is not None:
        row.system_prompt = req.system_prompt
    if req.tool_allowlist is not None:
        row.tool_allowlist = list(req.tool_allowlist)
    if req.sandbox_mode is not None:
        row.sandbox_mode = req.sandbox_mode
    if req.approval_policy is not None:
        row.approval_policy = req.approval_policy

    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="mode.update",
        target=key,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"mode": _serialize_mode(row)}


@router.get("/api/projects/{project_key}/mode")
def get_project_mode(
    project_key: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    res = get_effective_project_mode(db, auth.workspace.id, project_key)
    db.commit()
    return res


@router.put("/api/projects/{project_key}/mode")
def put_project_mode(
    project_key: str,
    req: ProjectModeUpdateRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    seed_default_modes(db, auth.workspace.id)
    mode_row = db.query(ModePreset).filter(ModePreset.workspace_id == auth.workspace.id, ModePreset.key == req.mode_key).first()
    if not mode_row:
        raise HTTPException(status_code=404, detail={"code": "mode_not_found", "message_ar": "الوضع غير موجود", "message_en": "Mode not found"})

    pm = (
        db.query(ProjectMode)
        .filter(ProjectMode.workspace_id == auth.workspace.id, ProjectMode.project_key == project_key)
        .first()
    )
    if not pm:
        pm = ProjectMode(
            workspace_id=auth.workspace.id,
            project_key=project_key,
            mode_key=req.mode_key,
            model_provider_id=req.model_provider_id,
            model_name=req.model_name,
            delegation_lanes_json=dict(req.delegation_lanes),
        )
        db.add(pm)
    else:
        pm.mode_key = req.mode_key
        pm.model_provider_id = req.model_provider_id
        pm.model_name = req.model_name
        pm.delegation_lanes_json = dict(req.delegation_lanes)

    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="project_mode.update",
        target=project_key,
        details={"mode_key": req.mode_key},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return get_effective_project_mode(db, auth.workspace.id, project_key)
