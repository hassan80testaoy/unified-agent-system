"""Providers registry, per-agent delegation assignments, agent plugin registry, and Instinct skills endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.catalog import SEO_AGENT_CATALOG
from app.agents.registry import discover_agents
from app.db import get_db
from app.models.agents import AgentModel, AgentSetting
from app.models.providers import ApiKey, Provider
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles
from app.security.crypto import encrypt_secret, mask_last4
from app.services.llm_router import (
    LLMRouter,
    ReviewerProviderConflictError,
    get_agent_assignment,
    seed_default_providers,
    upsert_agent_assignment,
)
from app.skills.registry import list_all_skills

router = APIRouter(tags=["agents_providers"])


class ProviderCreateRequest(BaseModel):
    id: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=128)
    kind: str = "llm"
    api_base: str = ""
    auth_ref: str = "none"
    models: dict[str, str] = Field(default_factory=dict)
    budget_usd_daily: float = 0.0
    budget_usd_monthly: float = 0.0
    enabled: bool = True
    api_key: str | None = None


class ProviderPatchRequest(BaseModel):
    name: str | None = None
    api_base: str | None = None
    models: dict[str, str] | None = None
    budget_usd_daily: float | None = None
    budget_usd_monthly: float | None = None
    enabled: bool | None = None
    api_key: str | None = None


class AgentDelegationUpdateRequest(BaseModel):
    orchestrator: dict[str, str]
    workers: dict[str, dict[str, str]] = Field(default_factory=dict)
    reviewer: dict[str, str]


class AgentSettingsPatchRequest(BaseModel):
    enabled: bool | None = None
    daily_limit: int | None = None
    daily_budget_usd: float | None = None
    needs_approval: bool | None = None
    settings: dict[str, Any] | None = None


@router.get("/api/providers")
def list_providers(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    seed_default_providers(db, auth.workspace.id)
    db.commit()
    rows = db.query(Provider).filter(Provider.workspace_id == auth.workspace.id).all()
    keys = {
        k.provider: k.last4
        for k in db.query(ApiKey).filter(ApiKey.workspace_id == auth.workspace.id).all()
    }
    return {
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "api_base": p.api_base,
                "auth_ref": p.auth_ref,
                "models": p.models_json,
                "budget_usd_daily": p.budget_usd_daily,
                "budget_usd_monthly": p.budget_usd_monthly,
                "enabled": p.enabled,
                "has_key": p.id in keys,
                "key_last4": keys.get(p.id, ""),
                "config": p.config_json,
            }
            for p in rows
        ]
    }


@router.post("/api/providers")
def create_provider(
    req: ProviderCreateRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    if db.get(Provider, req.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "provider_exists", "message_ar": "المزود موجود بالفعل", "message_en": "Provider already exists"},
        )
    row = Provider(
        id=req.id.strip().lower(),
        workspace_id=auth.workspace.id,
        name=req.name,
        kind=req.kind,
        api_base=req.api_base,
        auth_ref=req.auth_ref,
        models_json=req.models,
        budget_usd_daily=req.budget_usd_daily,
        budget_usd_monthly=req.budget_usd_monthly,
        enabled=req.enabled,
    )
    db.add(row)
    if req.api_key:
        db.add(
            ApiKey(
                workspace_id=auth.workspace.id,
                provider=row.id,
                secret_enc=encrypt_secret(req.api_key.strip()),
                label=req.name,
                last4=mask_last4(req.api_key.strip()),
            )
        )
    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="provider.create",
        target=row.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"id": row.id, "enabled": row.enabled}


@router.patch("/api/providers/{provider_id}")
def patch_provider(
    provider_id: str,
    req: ProviderPatchRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    row = db.get(Provider, provider_id)
    if not row or row.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المزود غير موجود", "message_en": "Provider not found"})

    if req.name is not None:
        row.name = req.name
    if req.api_base is not None:
        row.api_base = req.api_base
    if req.models is not None:
        row.models_json = req.models
    if req.budget_usd_daily is not None:
        row.budget_usd_daily = req.budget_usd_daily
    if req.budget_usd_monthly is not None:
        row.budget_usd_monthly = req.budget_usd_monthly
    if req.enabled is not None:
        row.enabled = req.enabled
    if req.api_key:
        existing_key = db.query(ApiKey).filter(ApiKey.workspace_id == auth.workspace.id, ApiKey.provider == row.id).first()
        if existing_key:
            existing_key.secret_enc = encrypt_secret(req.api_key.strip())
            existing_key.last4 = mask_last4(req.api_key.strip())
        else:
            db.add(
                ApiKey(
                    workspace_id=auth.workspace.id,
                    provider=row.id,
                    secret_enc=encrypt_secret(req.api_key.strip()),
                    label=row.name,
                    last4=mask_last4(req.api_key.strip()),
                )
            )

    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="provider.update",
        target=row.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"id": row.id, "enabled": row.enabled, "budget_usd_daily": row.budget_usd_daily}


@router.post("/api/providers/{provider_id}/test")
def test_provider(
    provider_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    prov = db.get(Provider, provider_id)
    if not prov or prov.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المزود غير موجود", "message_en": "Provider not found"})

    if prov.kind == "captcha":
        return {"ok": True, "kind": "captcha", "solver": prov.config_json.get("solver", "manual")}

    router_svc = LLMRouter(db, auth.workspace.id)
    try:
        res = router_svc.route(
            task_type="healthcheck",
            tier="cheap",
            prompt="Reply with the single word: OK",
            preferred_provider=prov.id,
            use_cache=False,
            allow_mock_fallback=(prov.kind == "llm-mock"),
        )
        db.commit()
        return {"ok": True, "provider_id": res["provider_id"], "model": res["model"], "latency_ms": res["latency_ms"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/agents")
def list_agents(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    discover_agents(db, auth.workspace.id)
    rows = db.query(AgentModel).filter(AgentModel.workspace_id == auth.workspace.id).all()
    installed_keys = {a.key for a in rows}
    return {
        "agents": [
            {
                "id": a.id,
                "key": a.key,
                "name_ar": a.name_ar,
                "name_en": a.name_en,
                "version": a.version,
                "description_ar": a.description_ar,
                "description_en": a.description_en,
                "input_schema": a.input_schema,
                "output_schema": a.output_schema,
                "tools_allowed": a.tools_allowed,
                "daily_limit": a.daily_limit,
                "daily_budget_usd": a.daily_budget_usd,
                "needs_approval": a.needs_approval,
                "enabled": a.enabled,
                "category": a.category,
                "priority": a.priority,
            }
            for a in rows
        ],
        "catalog": [c for c in SEO_AGENT_CATALOG if c["key"] not in installed_keys],
    }


@router.post("/api/agents/discover")
def trigger_discover_agents(
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    discovered = discover_agents(db, auth.workspace.id)
    return {"discovered_count": len(discovered), "keys": [d["key"] for d in discovered]}


@router.get("/api/agents/{agent_id}")
def get_agent_detail(agent_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    agent = db.get(AgentModel, agent_id) or db.query(AgentModel).filter(AgentModel.key == agent_id).first()
    if not agent or agent.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "agent_not_found", "message_ar": "الوكيل غير موجود", "message_en": "Agent not found"})
    assign = get_agent_assignment(db, auth.workspace.id, agent.id)
    settings_row = db.query(AgentSetting).filter(AgentSetting.agent_id == agent.id, AgentSetting.workspace_id == auth.workspace.id).first()
    return {
        "agent": {
            "id": agent.id,
            "key": agent.key,
            "name_ar": agent.name_ar,
            "name_en": agent.name_en,
            "version": agent.version,
            "description_ar": agent.description_ar,
            "description_en": agent.description_en,
            "input_schema": agent.input_schema,
            "output_schema": agent.output_schema,
            "tools_allowed": agent.tools_allowed,
            "daily_limit": agent.daily_limit,
            "daily_budget_usd": agent.daily_budget_usd,
            "needs_approval": agent.needs_approval,
            "enabled": agent.enabled,
            "category": agent.category,
            "priority": agent.priority,
        },
        "delegation": {
            "orchestrator": assign.orchestrator_json if assign else {},
            "workers": assign.workers_json if assign else {},
            "reviewer": assign.reviewer_json if assign else {},
        },
        "settings": settings_row.settings if settings_row else {},
    }


@router.patch("/api/agents/{agent_id}/settings")
def patch_agent_settings(
    agent_id: str,
    req: AgentSettingsPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    agent = db.get(AgentModel, agent_id)
    if not agent or agent.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "agent_not_found", "message_ar": "الوكيل غير موجود", "message_en": "Agent not found"})

    if req.enabled is not None:
        agent.enabled = req.enabled
    if req.daily_limit is not None:
        agent.daily_limit = req.daily_limit
    if req.daily_budget_usd is not None:
        agent.daily_budget_usd = req.daily_budget_usd
    if req.needs_approval is not None:
        agent.needs_approval = req.needs_approval
    if req.settings is not None:
        row = db.query(AgentSetting).filter(AgentSetting.agent_id == agent.id, AgentSetting.workspace_id == auth.workspace.id).first()
        if row is None:
            db.add(AgentSetting(agent_id=agent.id, workspace_id=auth.workspace.id, settings=req.settings))
        else:
            row.settings = req.settings
    db.commit()
    return {"ok": True, "enabled": agent.enabled, "daily_limit": agent.daily_limit, "daily_budget_usd": agent.daily_budget_usd}


@router.get("/api/agents/{agent_id}/providers")
def get_agent_providers(agent_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    agent = db.get(AgentModel, agent_id)
    if not agent or agent.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "agent_not_found", "message_ar": "الوكيل غير موجود", "message_en": "Agent not found"})
    assign = get_agent_assignment(db, auth.workspace.id, agent.id)
    return {
        "agent_id": agent.id,
        "orchestrator": assign.orchestrator_json if assign else {},
        "workers": assign.workers_json if assign else {},
        "reviewer": assign.reviewer_json if assign else {},
    }


@router.put("/api/agents/{agent_id}/providers")
def update_agent_providers(
    agent_id: str,
    req: AgentDelegationUpdateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    agent = db.get(AgentModel, agent_id)
    if not agent or agent.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "agent_not_found", "message_ar": "الوكيل غير موجود", "message_en": "Agent not found"})
    try:
        assign = upsert_agent_assignment(
            db,
            workspace_id=auth.workspace.id,
            agent_id=agent.id,
            orchestrator=req.orchestrator,
            workers=req.workers,
            reviewer=req.reviewer,
        )
        db.commit()
        return {
            "agent_id": agent.id,
            "orchestrator": assign.orchestrator_json,
            "workers": assign.workers_json,
            "reviewer": assign.reviewer_json,
        }
    except ReviewerProviderConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "reviewer_provider_conflict",
                "message_ar": "يجب أن يكون مزود المراجعة مختلفاً عن مزود التنفيذ/التنسيق",
                "message_en": str(exc),
            },
        ) from exc


@router.get("/api/skills")
def get_skills(auth: AuthContext = Depends(get_current_auth)):
    return list_all_skills()
