"""Captcha plugin endpoints, Blind-fill Vault, Memory, Reports, Settings, Killswitch, Dashboard & Health."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.captcha.solver import get_captcha_solver
from app.captcha.solvers.manual import (
    get_captcha_stats,
    manual_captcha_coordinator,
    novnc_tunnel_instructions,
)
from app.config import get_settings
from app.db import get_db
from app.models.agents import Approval, Report, Run, RunEvent
from app.models.backlinks import BlLink, BlSite
from app.models.captcha import CaptchaAttempt
from app.models.core import AuditLog, Notification
from app.models.instinct import MemoryFact, VaultItem
from app.models.providers import ApiKey, LlmCall, ModelRoute, Provider
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles
from app.security.crypto import decrypt_secret, encrypt_secret, mask_last4
from app.security.killswitch import activate_killswitch, deactivate_killswitch, get_killswitch_status
from app.services.model_catalogue import AUTO_SYNC_PROVIDERS, sync_provider_catalogue
from app.services.reports import compute_real_metrics, generate_report, render_report_printable_html
from app.services.runtime_checks import get_runtime_readiness
from app.services.vault import create_vault_request, get_vault_request_status, submit_vault_request

router = APIRouter(tags=["captcha_vault_reports"])


class CaptchaSkipRequest(BaseModel):
    reason: str = "manual_skipped"


class VaultRequestCreate(BaseModel):
    kind: str = "login"
    label: str
    allowed_sites: list[str] = Field(default_factory=list)
    reason: str = ""
    ttl_minutes: int = 15


class VaultSubmitPayload(BaseModel):
    username: str | None = None
    secret: str = Field(min_length=1)
    meta: dict[str, Any] = Field(default_factory=dict)


class MemoryCreateRequest(BaseModel):
    category: str = "preference"
    content: str
    allow_training: bool = False  # Opt-in only, default OFF


class ReportGenerateRequest(BaseModel):
    period: str = "daily"


class ApiKeyPutRequest(BaseModel):
    secret: str = Field(min_length=4)
    label: str = ""


class RouterUpdateRequest(BaseModel):
    routes: list[dict[str, Any]]


class KillswitchRequest(BaseModel):
    active: bool
    reason: str = "Manual trigger"


@router.get("/api/captcha/stats")
def captcha_stats_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return get_captcha_stats(db, auth.workspace.id)


@router.get("/api/captcha/attempts")
def list_captcha_attempts(
    site: str | None = None,
    type: str | None = None,
    method: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    q = db.query(CaptchaAttempt).filter(CaptchaAttempt.workspace_id == auth.workspace.id)
    if site:
        q = q.filter(CaptchaAttempt.site.contains(site))
    if type:
        q = q.filter(CaptchaAttempt.challenge_type == type)
    if method:
        q = q.filter(CaptchaAttempt.method == method)
    rows = q.order_by(CaptchaAttempt.created_at.desc()).limit(200).all()
    return {
        "attempts": [
            {
                "id": r.id,
                "run_id": r.run_id,
                "site": r.site,
                "challenge_type": r.challenge_type,
                "method": r.method,
                "proxy_type": r.proxy_type,
                "duration_ms": r.duration_ms,
                "cost_usd": r.cost_usd,
                "result": r.result,
                "blocked_reason": r.blocked_reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/api/captcha/{run_id}/manual-solve")
def start_manual_captcha(run_id: str, auth: AuthContext = Depends(get_current_auth)):
    return {
        "run_id": run_id,
        "mode": "manual",
        "tunnel": novnc_tunnel_instructions(),
        "instructions_ar": "افتح نفق SSH ثم افتح الرابط المحلي http://127.0.0.1:6080/vnc.html وأكمل الكابتشا بنفسك ثم اضغط (تم الحل).",
        "instructions_en": "Open the SSH tunnel, visit http://127.0.0.1:6080/vnc.html, solve the challenge manually, then click Mark Solved.",
    }


@router.post("/api/captcha/{run_id}/confirm-solved")
def confirm_manual_captcha(
    run_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
):
    solver = get_captcha_solver()
    solver.on_manual_solved(run_id)
    return {"ok": True, "run_id": run_id}


@router.post("/api/captcha/{run_id}/skip")
def skip_manual_captcha(
    run_id: str,
    req: CaptchaSkipRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
):
    signaled = manual_captcha_coordinator.mark_skipped(run_id, reason=req.reason)
    return {"ok": True, "signaled": signaled, "reason": req.reason}


@router.post("/api/vault/request")
def create_vault_link(
    req: VaultRequestCreate,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    return create_vault_request(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        kind=req.kind,
        label=req.label,
        allowed_sites=req.allowed_sites,
        reason=req.reason,
        ttl_minutes=req.ttl_minutes,
    )


@router.get("/api/vault/request/{raw_token}")
def check_vault_link(raw_token: str, db: Session = Depends(get_db)):
    req = get_vault_request_status(db, raw_token)
    if not req:
        raise HTTPException(status_code=404, detail={"code": "vault_link_not_found", "message_ar": "الرابط غير صالح", "message_en": "Vault link not found"})
    return {
        "status": req.status,
        "kind": req.kind,
        "label": req.label,
        "allowed_sites": req.allowed_sites,
        "reason": req.reason,
        "expires_at": req.expires_at.isoformat(),
    }


@router.post("/api/vault/submit/{raw_token}")
def submit_vault_link(raw_token: str, payload: VaultSubmitPayload, request: Request, db: Session = Depends(get_db)):
    try:
        item = submit_vault_request(
            db,
            raw_token=raw_token,
            username=payload.username,
            secret_value=payload.secret,
            meta=payload.meta,
            ip=request.client.host if request.client else None,
        )
        return {"ok": True, "item_id": item.id, "label": item.label, "allowed_sites": item.allowed_sites}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "vault_submit_failed", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.get("/api/vault/items")
def list_vault_items(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(VaultItem).filter(VaultItem.workspace_id == auth.workspace.id).order_by(VaultItem.created_at.desc()).all()
    return {
        "items": [
            {
                "id": v.id,
                "kind": v.kind,
                "label": v.label,
                "allowed_sites": v.allowed_sites,
                "username_masked": v.username_masked,
                "created_at": v.created_at.isoformat(),
            }
            for v in rows
        ]
    }


@router.get("/api/memory")
def list_memory(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(MemoryFact).filter(MemoryFact.workspace_id == auth.workspace.id).order_by(MemoryFact.created_at.desc()).all()
    return {
        "memory": [
            {
                "id": m.id,
                "category": m.category,
                "content": decrypt_secret(m.content_enc),
                "allow_training": m.allow_training,
                "created_at": m.created_at.isoformat(),
            }
            for m in rows
        ]
    }


@router.post("/api/memory")
def add_memory(req: MemoryCreateRequest, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    row = MemoryFact(
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        category=req.category,
        content_enc=encrypt_secret(req.content),
        allow_training=bool(req.allow_training),
    )
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.delete("/api/memory/{memory_id}")
def delete_memory(memory_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    row = db.get(MemoryFact, memory_id)
    if not row or row.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "غير موجود", "message_en": "Not found"})
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/api/reports")
def list_reports(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(Report).filter(Report.workspace_id == auth.workspace.id).order_by(Report.created_at.desc()).limit(50).all()
    return {
        "reports": [
            {
                "id": r.id,
                "period": r.period,
                "title": r.title,
                "body_md": r.body_md,
                "metrics_json": r.metrics_json,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "live_metrics": compute_real_metrics(db, auth.workspace.id, days=1),
    }


@router.post("/api/reports/generate")
def generate_report_endpoint(
    req: ReportGenerateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    rep = generate_report(db, workspace_id=auth.workspace.id, period=req.period, skip_if_empty=False)
    return {"id": rep.id, "title": rep.title, "body_md": rep.body_md, "metrics_json": rep.metrics_json}  # type: ignore[union-attr]


@router.get("/api/reports/{report_id}/pdf")
def export_report_pdf_html(report_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rep = db.get(Report, report_id)
    if not rep or rep.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "التقرير غير موجود", "message_en": "Report not found"})
    html = render_report_printable_html(rep)
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f"inline; filename=report_{rep.id}.html"},
    )


@router.get("/api/settings")
def get_settings_summary(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    settings = get_settings()
    keys = db.query(ApiKey).filter(ApiKey.workspace_id == auth.workspace.id).all()
    routes = db.query(ModelRoute).filter(ModelRoute.workspace_id == auth.workspace.id).all()
    return {
        "captcha_solver": settings.captcha_solver,
        "captcha_max_spend_usd": settings.captcha_max_spend_usd,
        "captcha_manual_timeout_sec": settings.captcha_manual_timeout_sec,
        "verification_code_timeout_sec": settings.verification_code_timeout_sec,
        "tunnel": novnc_tunnel_instructions(),
        "killswitch": get_killswitch_status(db),
        "api_keys": [{"provider": k.provider, "label": k.label, "last4": k.last4} for k in keys],
        "model_routes": [
            {"id": r.id, "tier": r.tier, "provider_id": r.provider_id, "model": r.model, "max_cost_per_call": r.max_cost_per_call}
            for r in routes
        ],
    }


@router.put("/api/settings/keys/{provider}")
async def put_provider_key(
    provider: str,
    req: ApiKeyPutRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    existing = db.query(ApiKey).filter(ApiKey.workspace_id == auth.workspace.id, ApiKey.provider == provider).first()
    enc = encrypt_secret(req.secret.strip())
    l4 = mask_last4(req.secret.strip())
    if existing:
        existing.secret_enc = enc
        existing.last4 = l4
        if req.label:
            existing.label = req.label
    else:
        db.add(ApiKey(workspace_id=auth.workspace.id, provider=provider, secret_enc=enc, label=req.label or provider, last4=l4))

    prov = db.get(Provider, provider)
    if prov and prov.workspace_id == auth.workspace.id:
        prov.enabled = True

    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="settings.key_updated",
        target=provider,
        ip=request.client.host if request.client else None,
        details={"last4": l4},
    )
    db.flush()

    cat_summary = None
    if prov and prov.workspace_id == auth.workspace.id and prov.id in AUTO_SYNC_PROVIDERS:
        sync_res = await sync_provider_catalogue(db, auth.workspace.id, prov)
        cat_summary = sync_res.get("catalogue")

    db.commit()
    return {"provider": provider, "last4": l4, "catalogue": cat_summary}


@router.put("/api/settings/router")
def put_router_config(
    req: RouterUpdateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    db.query(ModelRoute).filter(ModelRoute.workspace_id == auth.workspace.id).delete()
    for item in req.routes:
        db.add(
            ModelRoute(
                workspace_id=auth.workspace.id,
                tier=str(item.get("tier", "cheap")),
                provider_id=str(item["provider_id"]),
                model=str(item["model"]),
                max_cost_per_call=float(item.get("max_cost_per_call", 0.5)),
            )
        )
    db.commit()
    return {"ok": True, "count": len(req.routes)}


@router.get("/api/killswitch")
def get_killswitch_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return get_killswitch_status(db)


@router.post("/api/killswitch")
def toggle_killswitch_endpoint(
    req: KillswitchRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else None
    if req.active:
        return activate_killswitch(db, workspace_id=auth.workspace.id, user_id=auth.user.id, reason=req.reason, ip=ip)
    return deactivate_killswitch(db, workspace_id=auth.workspace.id, user_id=auth.user.id, ip=ip)


@router.get("/api/dashboard/summary")
def dashboard_summary(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    ws = auth.workspace.id
    sites = db.query(BlSite).filter(BlSite.workspace_id == ws).all()
    links = db.query(BlLink).filter(BlLink.workspace_id == ws).all()
    active_runs = (
        db.query(Run)
        .filter(
            Run.workspace_id == ws,
            Run.status.in_(["queued", "running", "awaiting_approval", "awaiting_captcha_manual", "awaiting_verification_code"]),
        )
        .order_by(Run.created_at.desc())
        .limit(10)
        .all()
    )
    approvals = (
        db.query(Approval)
        .filter(Approval.workspace_id == ws, Approval.status == "pending")
        .order_by(Approval.created_at.desc())
        .limit(10)
        .all()
    )
    recent_events = (
        db.query(RunEvent)
        .filter(RunEvent.workspace_id == ws)
        .order_by(RunEvent.ts.desc())
        .limit(15)
        .all()
    )

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    spend_today = float(
        db.query(func.coalesce(func.sum(LlmCall.cost_usd), 0.0))
        .filter(LlmCall.workspace_id == ws, LlmCall.created_at >= today_start)
        .scalar()
        or 0.0
    )

    # 14-day trend (real DB counts grouped by day)
    days_14 = []
    for offset in range(13, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=offset)).date().isoformat()
        days_14.append({"date": day, "registrations": 0, "live_links": 0})
    by_day = {d["date"]: d for d in days_14}
    for s in sites:
        d_str = s.updated_at.date().isoformat()
        if d_str in by_day and s.status in {"live", "pending_review"}:
            by_day[d_str]["registrations"] += 1
    for l in links:
        d_str = l.updated_at.date().isoformat()
        if d_str in by_day and l.live == 1:
            by_day[d_str]["live_links"] += 1

    return {
        "kpis": {
            "registered": sum(1 for s in sites if s.status in {"live", "pending_review"}),
            "live": sum(1 for l in links if l.live == 1),
            "pending": sum(1 for s in sites if s.status in {"queued", "pending_review"}),
            "needs_you": len(approvals),
            "failed": sum(1 for s in sites if s.status in {"failed", "blocked"}),
        },
        "spend_today_usd": round(spend_today, 4),
        "killswitch": get_killswitch_status(db),
        "active_runs": [
            {
                "id": r.id,
                "status": r.status,
                "progress_done": r.progress_done,
                "progress_total": r.progress_total,
                "cost_usd": r.cost_usd,
            }
            for r in active_runs
        ],
        "pending_approvals": [
            {"id": a.id, "run_id": a.run_id, "kind": a.kind, "payload": a.payload, "created_at": a.created_at.isoformat()}
            for a in approvals
        ],
        "recent_events": [
            {"id": e.id, "run_id": e.run_id, "ts": e.ts.isoformat(), "level": e.level, "kind": e.kind, "message": e.message}
            for e in recent_events
        ],
        "chart_14d": days_14,
    }


@router.get("/api/audit-log")
def list_audit_log(auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    rows = db.query(AuditLog).filter(AuditLog.workspace_id == auth.workspace.id).order_by(AuditLog.ts.desc()).limit(100).all()
    return {
        "audit_log": [
            {"id": l.id, "user_id": l.user_id, "action": l.action, "target": l.target, "ip": l.ip, "details": l.details, "ts": l.ts.isoformat()}
            for l in rows
        ]
    }


@router.get("/api/notifications")
def list_notifications(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(Notification)
        .filter(Notification.workspace_id == auth.workspace.id, Notification.user_id == auth.user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    return {
        "notifications": [
            {
                "id": n.id,
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "read_at": n.read_at.isoformat() if n.read_at else None,
                "created_at": n.created_at.isoformat(),
            }
            for n in rows
        ]
    }


@router.post("/api/notifications/{note_id}/read")
def mark_notification_read(note_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    n = db.get(Notification, note_id)
    if n and n.workspace_id == auth.workspace.id and n.user_id == auth.user.id:
        n.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}


@router.get("/api/health")
def health_check():
    settings = get_settings()
    return {
        "status": "ok",
        "env": settings.env,
        "captcha_solver": settings.captcha_solver,
        "captcha_max_spend_usd": settings.captcha_max_spend_usd,
    }


@router.get("/api/system/runtime-status")
def system_runtime_status(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return get_runtime_readiness(db, auth.workspace.id)
