"""Providers registry, per-agent delegation assignments, agent plugin registry, and Instinct skills endpoints."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.catalog import SEO_AGENT_CATALOG
from app.agents.registry import discover_agents
from app.config import get_settings
from app.db import get_db, utcnow
from app.models.agents import AgentModel, AgentSetting
from app.models.providers import ApiKey, Provider, ProviderAccount
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles
from app.security.crypto import decrypt_secret, encrypt_secret, mask_last4
from app.services.llm_router import (
    LLMRouter,
    ReviewerProviderConflictError,
    get_agent_assignment,
    seed_default_providers,
    upsert_agent_assignment,
)
from app.services.runtime_checks import is_local_endpoint
from app.skills.registry import list_all_skills

router = APIRouter(tags=["agents_providers"])


class ProviderCreateRequest(BaseModel):
    id: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=128)
    kind: str = "llm"
    connect_type: str = "api_key"  # signin | api_key | custom_endpoint
    protocol: str = "openai-completions"
    path_id: str | None = None
    api_base: str = ""
    auth_ref: str = "none"
    models: dict[str, str] = Field(default_factory=dict)
    budget_usd_daily: float = 0.0
    budget_usd_monthly: float = 0.0
    enabled: bool = True
    api_key: str | None = None


class ProviderPatchRequest(BaseModel):
    name: str | None = None
    connect_type: str | None = None
    protocol: str | None = None
    path_id: str | None = None
    api_base: str | None = None
    models: dict[str, str] | None = None
    budget_usd_daily: float | None = None
    budget_usd_monthly: float | None = None
    enabled: bool | None = None
    api_key: str | None = None


class FetchModelsRequest(BaseModel):
    api_base: str
    api_key: str | None = None
    protocol: str = "openai-completions"
    provider_id: str | None = None


class ProviderAccountCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    auth_type: str = "api_key"  # api_key | oauth_signin | cli_login
    api_key: str | None = None
    priority: int = 0
    quota_limit: float = 0.0


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


def _serialize_account(a: ProviderAccount) -> dict[str, Any]:
    return {
        "id": a.id,
        "provider_id": a.provider_id,
        "label": a.label,
        "auth_type": a.auth_type,
        "last4": a.last4,
        "has_key": bool(a.key_ref_enc),
        "status": a.status,
        "priority": a.priority,
        "quota_used": a.quota_used,
        "quota_limit": a.quota_limit,
        "last_failover_at": a.last_failover_at.isoformat() if a.last_failover_at else None,
        "failover_reason": a.failover_reason,
    }


def _validate_connect_type(connect_type: str, auth_type: str | None = None) -> None:
    if connect_type not in {"signin", "api_key", "custom_endpoint"}:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_connect_type", "message_ar": "نوع الاتصال غير صالح", "message_en": "Invalid connect_type"},
        )
    if get_settings().is_production and (connect_type == "signin" or auth_type in {"oauth_signin", "cli_login"}):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "signin_forbidden_in_production",
                "message_ar": "تسجيل الدخول بالحساب الشخصي متاح في التشغيل المحلي فقط (ممنوع في بيئة الإنتاج).",
                "message_en": "Account sign-in / CLI login is allowed in local development mode only (forbidden in production).",
            },
        )


@router.get("/api/providers")
def list_providers(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    seed_default_providers(db, auth.workspace.id)
    db.commit()
    rows = db.query(Provider).filter(Provider.workspace_id == auth.workspace.id).all()
    keys = {
        k.provider: k.last4
        for k in db.query(ApiKey).filter(ApiKey.workspace_id == auth.workspace.id).all()
    }
    acct_rows = (
        db.query(ProviderAccount)
        .filter(ProviderAccount.workspace_id == auth.workspace.id)
        .order_by(ProviderAccount.priority.asc(), ProviderAccount.created_at.asc())
        .all()
    )
    accts_by_prov: dict[str, list[dict[str, Any]]] = {}
    for a in acct_rows:
        accts_by_prov.setdefault(a.provider_id, []).append(_serialize_account(a))

    return {
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "connect_type": p.connect_type,
                "protocol": p.protocol,
                "path_id": p.path_id,
                "api_base": p.api_base,
                "local_only": is_local_endpoint(p.api_base) or p.kind == "llm-local",
                "auth_ref": p.auth_ref,
                "models": p.models_json,
                "budget_usd_daily": p.budget_usd_daily,
                "budget_usd_monthly": p.budget_usd_monthly,
                "enabled": p.enabled,
                "has_key": p.id in keys or bool(accts_by_prov.get(p.id)),
                "key_last4": keys.get(p.id, ""),
                "accounts_count": len(accts_by_prov.get(p.id, [])),
                "accounts": accts_by_prov.get(p.id, []),
                "config": p.config_json,
            }
            for p in rows
        ]
    }


@router.post("/api/providers/fetch-models")
async def fetch_models_from_endpoint(
    req: FetchModelsRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    base = req.api_base.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_url", "message_ar": "رابط نقطة النهاية غير صالح", "message_en": "Invalid endpoint URL"},
        )
    api_key = (req.api_key or "").strip() or None
    if not api_key and req.provider_id:
        key_row = (
            db.query(ApiKey)
            .filter(ApiKey.workspace_id == auth.workspace.id, ApiKey.provider == req.provider_id)
            .first()
        )
        if key_row:
            api_key = decrypt_secret(key_row.secret_enc)

    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        if req.protocol == "anthropic":
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    target_url = f"{base}/api/tags" if (req.protocol == "ollama" or ":11434" in base) and not base.endswith("/v1") else f"{base}/models"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(target_url, headers=headers)
            if resp.status_code >= 400:
                return {"ok": False, "error": f"http_{resp.status_code}", "models": []}
            body = resp.json()
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "models": []}

    raw_items = body.get("data") or body.get("models") or (body if isinstance(body, list) else [])
    parsed: list[dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "").strip()
            name = str(item.get("displayName") or item.get("name") or mid).strip()
            if mid:
                parsed.append({"id": mid, "name": name})
        elif isinstance(item, str) and item.strip():
            parsed.append({"id": item.strip(), "name": item.strip()})
    return {"ok": True, "models": parsed}


@router.post("/api/providers")
def create_provider(
    req: ProviderCreateRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    _validate_connect_type(req.connect_type)
    if req.connect_type == "custom_endpoint" and (not req.api_base.strip() or not req.models):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "custom_endpoint_incomplete",
                "message_ar": "نقطة النهاية المخصصة تتطلب رابط الأساس وموديلاً واحداً على الأقل",
                "message_en": "Custom endpoint requires Base URL and at least one model",
            },
        )
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
        connect_type=req.connect_type,
        protocol=req.protocol,
        path_id=req.path_id,
        api_base=req.api_base,
        auth_ref=req.auth_ref,
        models_json=req.models,
        budget_usd_daily=req.budget_usd_daily,
        budget_usd_monthly=req.budget_usd_monthly,
        enabled=req.enabled,
    )
    db.add(row)
    if req.api_key:
        clean_key = req.api_key.strip()
        db.add(
            ApiKey(
                workspace_id=auth.workspace.id,
                provider=row.id,
                secret_enc=encrypt_secret(clean_key),
                label=req.name,
                last4=mask_last4(clean_key),
            )
        )
        db.add(
            ProviderAccount(
                workspace_id=auth.workspace.id,
                provider_id=row.id,
                label=f"{req.name} #1",
                auth_type="api_key",
                key_ref_enc=encrypt_secret(clean_key),
                last4=mask_last4(clean_key),
                status="active",
                priority=0,
            )
        )
    elif req.connect_type == "signin":
        db.add(
            ProviderAccount(
                workspace_id=auth.workspace.id,
                provider_id=row.id,
                label=f"{req.name} (local)",
                auth_type="oauth_signin",
                status="active",
                priority=0,
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
    return {"id": row.id, "enabled": row.enabled, "connect_type": row.connect_type, "protocol": row.protocol}


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

    if req.connect_type is not None:
        _validate_connect_type(req.connect_type)
        row.connect_type = req.connect_type
    if req.protocol is not None:
        row.protocol = req.protocol
    if req.path_id is not None:
        row.path_id = req.path_id
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


@router.get("/api/providers/{provider_id}/accounts")
def list_provider_accounts(
    provider_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    prov = db.get(Provider, provider_id)
    if not prov or prov.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المزود غير موجود", "message_en": "Provider not found"})
    rows = (
        db.query(ProviderAccount)
        .filter(ProviderAccount.workspace_id == auth.workspace.id, ProviderAccount.provider_id == provider_id)
        .order_by(ProviderAccount.priority.asc(), ProviderAccount.created_at.asc())
        .all()
    )
    return {"accounts": [_serialize_account(a) for a in rows]}


@router.post("/api/providers/{provider_id}/accounts")
def add_provider_account(
    provider_id: str,
    req: ProviderAccountCreateRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    prov = db.get(Provider, provider_id)
    if not prov or prov.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المزود غير موجود", "message_en": "Provider not found"})
    _validate_connect_type("signin" if req.auth_type != "api_key" else "api_key", auth_type=req.auth_type)

    clean_key = (req.api_key or "").strip()
    row = ProviderAccount(
        workspace_id=auth.workspace.id,
        provider_id=provider_id,
        label=req.label,
        auth_type=req.auth_type,
        key_ref_enc=encrypt_secret(clean_key) if clean_key else None,
        last4=mask_last4(clean_key) if clean_key else "",
        status="active",
        priority=req.priority,
        quota_limit=req.quota_limit,
    )
    db.add(row)
    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="provider.account.add",
        target=f"{provider_id}:{req.label}",
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"account": _serialize_account(row)}


@router.delete("/api/providers/{provider_id}/accounts/{account_id}")
def delete_provider_account(
    provider_id: str,
    account_id: str,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    row = db.get(ProviderAccount, account_id)
    if not row or row.workspace_id != auth.workspace.id or row.provider_id != provider_id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الحساب غير موجود", "message_en": "Account not found"})
    db.delete(row)
    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="provider.account.delete",
        target=account_id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"deleted": True}


@router.post("/api/providers/{provider_id}/failover")
def manual_failover_provider_account(
    provider_id: str,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    prov = db.get(Provider, provider_id)
    if not prov or prov.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المزود غير موجود", "message_en": "Provider not found"})
    rows = (
        db.query(ProviderAccount)
        .filter(ProviderAccount.workspace_id == auth.workspace.id, ProviderAccount.provider_id == provider_id)
        .order_by(ProviderAccount.priority.asc(), ProviderAccount.created_at.asc())
        .all()
    )
    if not rows:
        raise HTTPException(status_code=400, detail={"code": "no_accounts", "message_ar": "لا توجد حسابات مسجلة لهذا المزود", "message_en": "No accounts configured for provider"})

    active = [a for a in rows if a.status == "active"]
    rested_id: str | None = None
    if active:
        curr = active[0]
        curr.status = "resting"
        curr.last_failover_at = utcnow()
        curr.failover_reason = "manual_failover"
        rested_id = curr.id
        remaining = [a for a in active[1:]]
    else:
        # Reactivate oldest-resting backup if operator rotates cleanly
        resting_rows = sorted(rows, key=lambda r: (r.priority, r.last_failover_at or utcnow()))
        resting_rows[0].status = "active"
        remaining = [resting_rows[0]]

    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="provider.failover",
        target=provider_id,
        details={"rested_account_id": rested_id, "next_account_id": remaining[0].id if remaining else None},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {
        "rested_account_id": rested_id,
        "active_account": _serialize_account(remaining[0]) if remaining else None,
        "accounts": [_serialize_account(a) for a in rows],
    }


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
