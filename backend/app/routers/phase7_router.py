"""Phase 7 Unified Router: Goal Planner, GSC, Site Auditor, Event Wakes, 08:00 Reports, Proactive Loops, Hermes Routing, Hypnos, Stats, Connectors & Unified Code Reviews."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.agents import Goal
from app.models.google_account import GoogleAccount
from app.models.instinct import WakeSubscription
from app.models.mailboxes import Mailbox
from app.models.phase5 import McpServer
from app.models.phase7 import (
    CodeReviewItem,
    ProactiveProposal,
    RoutingConfigBackup,
    RoutingProfile,
    SiteAudit,
    WakeEventLog,
)
from app.models.providers import Connection, LlmCall
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles
from app.services.code_review_service import (
    approve_review,
    comment_on_review,
    connect_git_provider,
    serialize_review_item,
    sync_git_reviews,
)
from app.services.coding_cli_delegation import detect_local_coding_clis
from app.services.daily_report_service import generate_daily_0800_report, get_owner_timezone, set_owner_timezone
from app.services.goal_planner import (
    execute_goal_agent_steps,
    plan_goal_from_sentence,
    serialize_goal_plan,
    update_goal_step_status,
)
from app.services.gsc_service import build_gsc_oauth_url, connect_gsc, disconnect_gsc, fetch_gsc_report, get_gsc_status
from app.services.hypnos_service import (
    get_hypnos_status,
    get_or_create_hypnos_config,
    promote_lora_version,
    record_feedback_event,
    run_hypnos_night_cycle,
    unlearn_from_clean_ancestor,
)
from app.services.planner_brain import (
    get_planner_brain_config,
    get_planner_brain_stats,
    run_planner_brain_workflow,
    set_planner_brain_config,
)
from app.services.proactive_loops import decide_proactive_proposal, run_proactive_value_loops, serialize_proposal
from app.services.proxy_health import get_proxy_health
from app.services.routing_profiles import (
    ROUTING_CONTEXT_ORDER,
    backup_routing_config,
    configure_model_routing_wizard,
    ensure_default_routing_profiles,
    restore_routing_backup,
    serialize_routing_profile,
)
from app.services.site_auditor import audit_site_url
from app.services.wakes_service import (
    create_wake_subscription,
    dispatch_wake_event,
    parse_gmail_push_webhook,
    serialize_wake_subscription,
)

router = APIRouter(tags=["phase7"])


# --- Request Schemas ---


class PlanGoalFromTextRequest(BaseModel):
    sentence: str = Field(min_length=2, max_length=1000)
    thread_id: str | None = None
    execute_auto_steps: bool = True


class UpdateGoalStepRequest(BaseModel):
    status: str = Field(pattern="^(pending|done|needs_user)$")
    note: str = ""


class GscConnectRequest(BaseModel):
    site_url: str = Field(min_length=4, max_length=512)
    account_email: str = Field(min_length=4, max_length=255)
    access_token: str | None = None
    refresh_token: str | None = None


class GscReportRequest(BaseModel):
    days: int = Field(default=28, ge=1, le=365)
    site_url: str | None = None


class SiteAuditRequest(BaseModel):
    url: str = Field(min_length=3, max_length=512)
    target_backlink_url: str | None = None
    profile_url: str | None = None
    max_links_check: int = Field(default=12, ge=0, le=30)


class WakeSubscriptionCreateRequest(BaseModel):
    source: str = "gmail"
    trigger_prompt: str = Field(min_length=2, max_length=500)
    filter: dict[str, Any] = Field(default_factory=dict)
    action_type: str = "activation"
    thread_id: str | None = None
    run_id: str | None = None
    enabled: bool = True


class WakeSubscriptionPatchRequest(BaseModel):
    enabled: bool | None = None
    trigger_prompt: str | None = None
    filter: dict[str, Any] | None = None


class GmailWakeEventRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    sender: str = Field(default="", alias="from")
    subject: str = ""
    body_text: str = ""
    snippet: str = ""

    model_config = {"populate_by_name": True}


class TimezoneUpdateRequest(BaseModel):
    timezone: str = Field(min_length=2, max_length=64)


class Daily0800RunRequest(BaseModel):
    enforce_hour: bool = False
    post_to_chat: bool = True
    now_iso: str | None = None


class RoutingProfileUpdateRequest(BaseModel):
    provider_id: str | None = None
    model: str = "auto"
    base_url: str = ""
    api_key_env: str = ""
    tier: str = "mid"
    rules: dict[str, Any] | None = None
    enabled: bool = True


class ConfigureRoutingWizardRequest(BaseModel):
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    fetch_live_models: bool = True


class PlannerBrainConfigRequest(BaseModel):
    enabled: bool
    tasks: list[str] | None = None


class PlannerBrainRunRequest(BaseModel):
    task_name: str = "goal_planner"
    goal_prompt: str = Field(min_length=3)
    require_review: bool = False


class HypnosConfigPatchRequest(BaseModel):
    enabled: bool
    schedule_hour: int | None = Field(default=None, ge=0, le=23)


class HypnosFeedbackRequest(BaseModel):
    kind: str = "corrected"  # approved|denied|corrected|cmd_passed|cmd_failed
    context_summary: str = Field(min_length=1, max_length=500)
    correction_text: str = ""
    command_name: str = ""


class HypnosUnlearnRequest(BaseModel):
    exclude_event_ids: list[str] = Field(min_length=1)
    ancestor_version_id: str | None = None


class ReviewConnectRequest(BaseModel):
    provider: str  # github | gitlab
    repo: str
    token: str
    base_url: str = ""


class ReviewCreateLocalRequest(BaseModel):
    provider: str = "local"
    repo: str
    external_number: int = 1
    title: str
    author: str = "owner"
    source_branch: str = "feat/local"
    target_branch: str = "main"
    url: str = ""
    diff_text: str = ""
    checks: list[dict[str, Any]] = Field(default_factory=list)


class ReviewCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class ReviewApproveRequest(BaseModel):
    note: str = ""
    run_ai_second_opinion: bool = False


# --- §7A.1 Goal Planner Endpoints ---


@router.post("/api/goals/plan-from-text")
async def plan_goal_from_text_endpoint(
    req: PlanGoalFromTextRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        res = await plan_goal_from_sentence(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            sentence=req.sentence,
            thread_id=req.thread_id,
            execute_auto_steps=req.execute_auto_steps,
        )
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "goal_invalid", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.get("/api/goals/{goal_id}")
def get_goal_detail_endpoint(
    goal_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    g = db.get(Goal, goal_id)
    if not g or g.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "goal_not_found", "message_ar": "الهدف غير موجود", "message_en": "Goal not found"})
    return {"goal": serialize_goal_plan(g)}


@router.post("/api/goals/{goal_id}/execute")
async def execute_goal_steps_endpoint(
    goal_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    g = db.get(Goal, goal_id)
    if not g or g.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "goal_not_found", "message_ar": "الهدف غير موجود", "message_en": "Goal not found"})
    out = await execute_goal_agent_steps(db, g, user_id=auth.user.id)
    db.commit()
    return {"goal": out}


@router.patch("/api/goals/{goal_id}/steps/{step_id}")
def update_goal_step_endpoint(
    goal_id: str,
    step_id: str,
    req: UpdateGoalStepRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    g = db.get(Goal, goal_id)
    if not g or g.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "goal_not_found", "message_ar": "الهدف غير موجود", "message_en": "Goal not found"})
    try:
        out = update_goal_step_status(db, g, step_id, req.status, req.note)
        db.commit()
        return {"goal": out}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "step_not_found", "message_ar": str(exc), "message_en": str(exc)}) from exc


# --- §7A.2 Google Search Console Endpoints ---


@router.get("/api/gsc/status")
def gsc_status_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return get_gsc_status(db, auth.workspace.id)


@router.get("/api/gsc/oauth/url")
def gsc_oauth_url_endpoint(redirect_uri: str | None = None, auth: AuthContext = Depends(require_roles("owner", "admin"))):
    return build_gsc_oauth_url(redirect_uri=redirect_uri)


@router.post("/api/gsc/connect")
def gsc_connect_endpoint(
    req: GscConnectRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        res = connect_gsc(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            site_url=req.site_url,
            account_email=req.account_email,
            access_token=req.access_token,
            refresh_token=req.refresh_token,
        )
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "gsc_invalid", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.delete("/api/gsc/disconnect")
def gsc_disconnect_endpoint(auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    res = disconnect_gsc(db, workspace_id=auth.workspace.id, user_id=auth.user.id)
    db.commit()
    return res


@router.get("/api/gsc/report")
def gsc_report_get_endpoint(
    days: int = Query(default=28, ge=1, le=365),
    site_url: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    res = fetch_gsc_report(db, workspace_id=auth.workspace.id, days=days, site_url=site_url)
    db.commit()
    return res


@router.post("/api/gsc/report")
def gsc_report_post_endpoint(
    req: GscReportRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    res = fetch_gsc_report(db, workspace_id=auth.workspace.id, days=req.days, site_url=req.site_url)
    db.commit()
    return res


# --- §7A.3 Site Auditor Endpoints ---


@router.post("/api/audit/site")
async def audit_site_endpoint(
    req: SiteAuditRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    res = await audit_site_url(
        db,
        workspace_id=auth.workspace.id,
        url=req.url,
        target_backlink_url=req.target_backlink_url,
        profile_url=req.profile_url,
        max_links_check=req.max_links_check,
    )
    db.commit()
    return {"audit": res}


@router.get("/api/audit/sites")
def list_site_audits_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(SiteAudit)
        .filter(SiteAudit.workspace_id == auth.workspace.id)
        .order_by(SiteAudit.created_at.desc())
        .limit(30)
        .all()
    )
    return {
        "audits": [
            {
                "id": a.id,
                "url": a.url,
                "score": a.score,
                "network_path": a.network_path,
                "screenshot_url": a.screenshot_url,
                "summary": a.summary_json,
                "findings": a.findings_json,
                "report_md": a.report_md,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ]
    }


@router.get("/api/audit/sites/{audit_id}")
def get_site_audit_endpoint(audit_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    a = db.get(SiteAudit, audit_id)
    if not a or a.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "audit_not_found", "message_ar": "الفحص غير موجود", "message_en": "Audit not found"})
    return {
        "audit": {
            "id": a.id,
            "url": a.url,
            "score": a.score,
            "network_path": a.network_path,
            "screenshot_url": a.screenshot_url,
            "summary": a.summary_json,
            "findings": a.findings_json,
            "report_md": a.report_md,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
    }


# --- §7B Event-Driven Wakes + Daily 08:00 + 6 Proactive Value Loops ---


@router.get("/api/wakes/subscriptions")
def list_wake_subscriptions_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(WakeSubscription)
        .filter(WakeSubscription.workspace_id == auth.workspace.id)
        .order_by(WakeSubscription.created_at.desc())
        .all()
    )
    return {"subscriptions": [serialize_wake_subscription(s) for s in rows]}


@router.post("/api/wakes/subscriptions")
def create_wake_subscription_endpoint(
    req: WakeSubscriptionCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    sub = create_wake_subscription(
        db,
        workspace_id=auth.workspace.id,
        source=req.source,
        trigger_prompt=req.trigger_prompt,
        filter_json=req.filter,
        action_type=req.action_type,
        thread_id=req.thread_id,
        run_id=req.run_id,
        enabled=req.enabled,
    )
    db.commit()
    db.refresh(sub)
    return {"subscription": serialize_wake_subscription(sub)}


@router.patch("/api/wakes/subscriptions/{sub_id}")
def patch_wake_subscription_endpoint(
    sub_id: str,
    req: WakeSubscriptionPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    sub = db.get(WakeSubscription, sub_id)
    if not sub or sub.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الاشتراك غير موجود", "message_en": "Subscription not found"})
    if req.enabled is not None:
        sub.enabled = bool(req.enabled)
    if req.trigger_prompt is not None:
        sub.trigger_prompt = req.trigger_prompt.strip()
    if req.filter is not None:
        sub.filter_json = dict(req.filter)
    db.commit()
    return {"subscription": serialize_wake_subscription(sub)}


@router.delete("/api/wakes/subscriptions/{sub_id}")
def delete_wake_subscription_endpoint(
    sub_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    sub = db.get(WakeSubscription, sub_id)
    if not sub or sub.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الاشتراك غير موجود", "message_en": "Subscription not found"})
    db.query(WakeEventLog).filter(WakeEventLog.subscription_id == sub.id).delete()
    db.delete(sub)
    db.commit()
    return {"deleted": True, "id": sub_id}


@router.post("/api/wakes/events/gmail")
def dispatch_gmail_wake_event_endpoint(
    req: GmailWakeEventRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    res = dispatch_wake_event(
        db,
        workspace_id=auth.workspace.id,
        source="gmail",
        event_id=req.event_id,
        payload={
            "from": req.sender,
            "subject": req.subject,
            "body_text": req.body_text,
            "snippet": req.snippet,
        },
    )
    db.commit()
    return res


@router.post("/api/wakes/gmail/push")
def gmail_push_webhook_endpoint(
    body: dict[str, Any],
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    parsed = parse_gmail_push_webhook(body)
    if not parsed.get("event_id"):
        raise HTTPException(status_code=400, detail={"code": "invalid_webhook", "message_ar": "معرف الحدث مفقود", "message_en": "Missing event_id"})
    res = dispatch_wake_event(
        db,
        workspace_id=auth.workspace.id,
        source="gmail",
        event_id=str(parsed["event_id"]),
        payload=parsed,
    )
    db.commit()
    return res


@router.get("/api/wakes/logs")
def list_wake_logs_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(WakeEventLog)
        .filter(WakeEventLog.workspace_id == auth.workspace.id)
        .order_by(WakeEventLog.fired_at.desc())
        .limit(50)
        .all()
    )
    return {
        "logs": [
            {
                "id": l.id,
                "subscription_id": l.subscription_id,
                "source": l.source,
                "external_event_id": l.external_event_id,
                "status": l.status,
                "summary": l.summary,
                "chat_thread_id": l.chat_thread_id,
                "chat_message_id": l.chat_message_id,
                "payload": l.payload_json,
                "fired_at": l.fired_at.isoformat() if l.fired_at else None,
            }
            for l in rows
        ]
    }


@router.get("/api/reports/daily-0800/timezone")
def get_daily_0800_timezone_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return {"timezone": get_owner_timezone(db, auth.workspace.id)}


@router.put("/api/reports/daily-0800/timezone")
def set_daily_0800_timezone_endpoint(
    req: TimezoneUpdateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        tz = set_owner_timezone(db, auth.workspace.id, req.timezone)
        db.commit()
        return {"timezone": tz}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_timezone", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/reports/daily-0800/run")
def run_daily_0800_endpoint(
    req: Daily0800RunRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    dt = datetime.fromisoformat(req.now_iso) if req.now_iso else None
    res = generate_daily_0800_report(
        db,
        workspace_id=auth.workspace.id,
        now=dt,
        enforce_hour=req.enforce_hour,
        post_to_chat=req.post_to_chat,
    )
    db.commit()
    return res


@router.post("/api/proactive/scan")
def run_proactive_scan_endpoint(
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    res = run_proactive_value_loops(db, auth.workspace.id)
    db.commit()
    return res


@router.get("/api/proactive/proposals")
def list_proactive_proposals_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(ProactiveProposal)
        .filter(ProactiveProposal.workspace_id == auth.workspace.id)
        .order_by(ProactiveProposal.created_at.desc())
        .limit(60)
        .all()
    )
    return {"proposals": [serialize_proposal(p) for p in rows]}


@router.post("/api/proactive/proposals/{proposal_id}/{decision}")
def decide_proactive_proposal_endpoint(
    proposal_id: str,
    decision: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    prop = db.get(ProactiveProposal, proposal_id)
    if not prop or prop.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المقترح غير موجود", "message_en": "Proposal not found"})
    try:
        out = decide_proactive_proposal(db, prop, decision=decision, user_id=auth.user.id)
        db.commit()
        return {"proposal": out}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_decision", "message_ar": str(exc), "message_en": str(exc)}) from exc


# --- §7C Hermes Routing Profiles + Wizard + Backups + Planner-Brain ---


@router.get("/api/routing/profiles")
def list_routing_profiles_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    profiles = ensure_default_routing_profiles(db, auth.workspace.id)
    db.commit()
    return {"profiles": [serialize_routing_profile(p) for p in profiles]}


@router.put("/api/routing/profiles/{context_key}")
def update_routing_profile_endpoint(
    context_key: str,
    req: RoutingProfileUpdateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    ck = context_key.strip().lower()
    if ck not in ROUTING_CONTEXT_ORDER:
        raise HTTPException(status_code=400, detail={"code": "invalid_context", "message_ar": "سياق غير صالح", "message_en": "Invalid context_key"})
    ensure_default_routing_profiles(db, auth.workspace.id)
    bk = backup_routing_config(db, auth.workspace.id, reason=f"manual_edit:{ck}", user_id=auth.user.id)
    prof = (
        db.query(RoutingProfile)
        .filter(RoutingProfile.workspace_id == auth.workspace.id, RoutingProfile.context_key == ck)
        .first()
    )
    assert prof is not None
    prof.provider_id = req.provider_id or None
    prof.model = (req.model or "auto").strip()
    prof.base_url = req.base_url.strip()
    prof.api_key_env = req.api_key_env.strip()
    prof.tier = req.tier.strip()
    if req.rules is not None:
        prof.rules_json = dict(req.rules)
    prof.enabled = bool(req.enabled)
    db.commit()
    return {"profile": serialize_routing_profile(prof), "backup_id": bk.id}


@router.post("/api/routing/wizard/configure-model-routing")
def run_routing_wizard_endpoint(
    req: ConfigureRoutingWizardRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    res = configure_model_routing_wizard(
        db,
        auth.workspace.id,
        user_id=auth.user.id,
        overrides=req.overrides,
        fetch_live_models=req.fetch_live_models,
    )
    db.commit()
    return res


@router.get("/api/routing/backups")
def list_routing_backups_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(RoutingConfigBackup)
        .filter(RoutingConfigBackup.workspace_id == auth.workspace.id)
        .order_by(RoutingConfigBackup.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "backups": [
            {
                "id": b.id,
                "reason": b.reason,
                "file_path": b.file_path,
                "profiles_count": len((b.snapshot_json or {}).get("profiles") or []),
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in rows
        ]
    }


@router.post("/api/routing/backups/{backup_id}/restore")
def restore_routing_backup_endpoint(
    backup_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        res = restore_routing_backup(db, auth.workspace.id, backup_id, user_id=auth.user.id)
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.get("/api/routing/planner-brain")
def get_planner_brain_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return {
        "config": get_planner_brain_config(db, auth.workspace.id),
        "stats": get_planner_brain_stats(db, auth.workspace.id),
    }


@router.put("/api/routing/planner-brain")
def update_planner_brain_endpoint(
    req: PlannerBrainConfigRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    cfg = set_planner_brain_config(db, auth.workspace.id, enabled=req.enabled, tasks=req.tasks)
    db.commit()
    return {"config": cfg}


@router.get("/api/routing/planner-brain/stats")
def get_planner_brain_stats_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return get_planner_brain_stats(db, auth.workspace.id)


@router.post("/api/routing/planner-brain/run")
def run_planner_brain_endpoint(
    req: PlannerBrainRunRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    res = run_planner_brain_workflow(
        db,
        workspace_id=auth.workspace.id,
        task_name=req.task_name,
        goal_prompt=req.goal_prompt,
        require_review=req.require_review,
    )
    db.commit()
    return res


# --- §7D Hypnos (OPT-IN ONLY, OFF BY DEFAULT) ---


@router.get("/api/hypnos/status")
def hypnos_status_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    res = get_hypnos_status(db, auth.workspace.id)
    db.commit()
    return res


@router.patch("/api/hypnos/config")
def hypnos_config_patch_endpoint(
    req: HypnosConfigPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    cfg = get_or_create_hypnos_config(db, auth.workspace.id)
    cfg.enabled = bool(req.enabled)
    if req.schedule_hour is not None:
        cfg.schedule_hour = int(req.schedule_hour)
    record_audit(
        db,
        user_id=auth.user.id,
        workspace_id=auth.workspace.id,
        action="hypnos_config_updated",
        target=cfg.id,
        details={"enabled": bool(cfg.enabled), "schedule_hour": cfg.schedule_hour},
    )
    db.commit()
    return get_hypnos_status(db, auth.workspace.id)


@router.post("/api/hypnos/feedback")
def hypnos_feedback_endpoint(
    req: HypnosFeedbackRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    ev = record_feedback_event(
        db,
        workspace_id=auth.workspace.id,
        kind=req.kind,
        context_summary=req.context_summary,
        correction_text=req.correction_text,
        command_name=req.command_name,
    )
    db.commit()
    return {"id": ev.id, "kind": ev.kind, "created_at": ev.created_at.isoformat()}


@router.post("/api/hypnos/night-run")
def hypnos_night_run_endpoint(
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    res = run_hypnos_night_cycle(db, auth.workspace.id)
    db.commit()
    return res


@router.post("/api/hypnos/versions/{version_id}/promote")
def hypnos_promote_endpoint(
    version_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        res = promote_lora_version(db, workspace_id=auth.workspace.id, version_id=version_id, user_id=auth.user.id)
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "hypnos_promote_refused", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/hypnos/unlearn")
def hypnos_unlearn_endpoint(
    req: HypnosUnlearnRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        res = unlearn_from_clean_ancestor(
            db,
            workspace_id=auth.workspace.id,
            exclude_event_ids=req.exclude_event_ids,
            ancestor_version_id=req.ancestor_version_id,
            user_id=auth.user.id,
        )
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "hypnos_unlearn_error", "message_ar": str(exc), "message_en": str(exc)}) from exc


# --- §7E.1 Stats Detail + §7E.3 Connectors Summary + §7E.4 Unified Reviews ---


@router.get("/api/stats/usage")
def usage_stats_endpoint(
    days: int = Query(default=7, ge=1, le=90),
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    calls = (
        db.query(LlmCall)
        .filter(LlmCall.workspace_id == auth.workspace.id, LlmCall.created_at >= since)
        .order_by(LlmCall.created_at.desc())
        .limit(500)
        .all()
    )
    total = len(calls)
    cache_hits = sum(1 for c in calls if c.cached)
    tok_in = sum(int(c.tokens_in or 0) for c in calls)
    tok_out = sum(int(c.tokens_out or 0) for c in calls)
    avg_latency = round(sum(int(c.latency_ms or 0) for c in calls) / max(1, total), 1) if total else 0.0
    total_cost = round(sum(float(c.cost_usd or 0.0) for c in calls), 6)

    by_profile: dict[str, dict[str, Any]] = {}
    for c in calls:
        pkey = c.route_id or f"task:{c.task_type}"
        b = by_profile.setdefault(pkey, {"calls": 0, "tokens": 0, "cache_hits": 0, "cost_usd": 0.0, "avg_latency_ms": 0.0, "_lat_sum": 0})
        b["calls"] += 1
        b["tokens"] += int(c.tokens_in or 0) + int(c.tokens_out or 0)
        b["cache_hits"] += 1 if c.cached else 0
        b["cost_usd"] = round(b["cost_usd"] + float(c.cost_usd or 0.0), 6)
        b["_lat_sum"] += int(c.latency_ms or 0)
    for b in by_profile.values():
        b["avg_latency_ms"] = round(b.pop("_lat_sum") / max(1, b["calls"]), 1)

    return {
        "days": days,
        "total_calls": total,
        "cache_hits": cache_hits,
        "cache_hit_rate_pct": round((cache_hits / total) * 100.0, 2) if total else 0.0,
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "total_context_tokens": tok_in + tok_out,
        "avg_latency_ms": avg_latency,
        "total_cost_usd": total_cost,
        "by_route_profile": by_profile,
        "recent_calls": [
            {
                "id": c.id,
                "provider_id": c.provider_id,
                "model": c.model,
                "route_id": c.route_id,
                "task_type": c.task_type,
                "tier": c.tier,
                "tokens_in": c.tokens_in,
                "tokens_out": c.tokens_out,
                "cached": bool(c.cached),
                "latency_ms": c.latency_ms,
                "cost_usd": c.cost_usd,
                "ok": bool(c.ok),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in calls[:25]
        ],
    }


@router.get("/api/connectors/summary")
def connectors_summary_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    ws_id = auth.workspace.id
    g_accts = db.query(GoogleAccount).filter(GoogleAccount.workspace_id == ws_id).count()
    mboxes = db.query(Mailbox).filter(Mailbox.workspace_id == ws_id).count()
    mcps = db.query(McpServer).filter(McpServer.workspace_id == ws_id).all()
    git_conns = db.query(Connection).filter(Connection.workspace_id == ws_id, Connection.kind.in_(["github", "gitlab"])).all()
    return {
        "google_accounts_count": g_accts,
        "gsc": get_gsc_status(db, ws_id),
        "mailboxes_count": mboxes,
        "mcp_servers": [
            {
                "id": m.id,
                "name": m.name,
                "status": m.status,
                "is_remote": bool(m.is_remote),
                "oauth_configured": bool(m.oauth_config_enc),
                "oauth_auto_refresh": bool(m.oauth_auto_refresh),
                "oauth_expires_at": m.oauth_expires_at.isoformat() if m.oauth_expires_at else None,
            }
            for m in mcps
        ],
        "git_connections": [
            {"id": c.id, "kind": c.kind, "status": c.status, "updated_at": c.updated_at.isoformat() if c.updated_at else None}
            for c in git_conns
        ],
        "local_clis": detect_local_coding_clis(),
        "proxy_health": get_proxy_health(db),
    }


@router.get("/api/reviews")
def list_reviews_endpoint(
    provider: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    q = db.query(CodeReviewItem).filter(CodeReviewItem.workspace_id == auth.workspace.id)
    if provider:
        q = q.filter(CodeReviewItem.provider == provider.strip().lower())
    rows = q.order_by(CodeReviewItem.updated_at.desc()).limit(60).all()
    return {"reviews": [serialize_review_item(r) for r in rows]}


@router.post("/api/reviews")
def create_local_review_endpoint(
    req: ReviewCreateLocalRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    item = CodeReviewItem(
        workspace_id=auth.workspace.id,
        provider=req.provider.strip().lower(),
        repo=req.repo.strip(),
        external_number=req.external_number,
        title=req.title.strip(),
        author=req.author.strip(),
        source_branch=req.source_branch.strip(),
        target_branch=req.target_branch.strip(),
        url=req.url.strip(),
        status="open",
        diff_text=req.diff_text,
        checks_json=req.checks,
        comments_json=[],
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"review": serialize_review_item(item)}


@router.post("/api/reviews/connect")
def connect_review_provider_endpoint(
    req: ReviewConnectRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        res = connect_git_provider(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            provider=req.provider,
            repo=req.repo,
            token=req.token,
            base_url=req.base_url,
        )
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "git_connect_error", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/reviews/sync")
def sync_reviews_endpoint(
    provider: str | None = None,
    repo: str | None = None,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    res = sync_git_reviews(db, workspace_id=auth.workspace.id, provider=provider, repo=repo)
    db.commit()
    return res


@router.get("/api/reviews/{review_id}")
def get_review_detail_endpoint(
    review_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    item = db.get(CodeReviewItem, review_id)
    if not item or item.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المراجعة غير موجودة", "message_en": "Review item not found"})
    return {"review": serialize_review_item(item)}


@router.post("/api/reviews/{review_id}/comment")
def comment_review_endpoint(
    review_id: str,
    req: ReviewCommentRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    item = db.get(CodeReviewItem, review_id)
    if not item or item.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المراجعة غير موجودة", "message_en": "Review item not found"})
    try:
        out = comment_on_review(db, item=item, user_id=auth.user.id, body=req.body, author_label=auth.user.email)
        db.commit()
        return {"review": serialize_review_item(out)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "comment_error", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/reviews/{review_id}/approve")
def approve_review_endpoint(
    review_id: str,
    req: ReviewApproveRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    item = db.get(CodeReviewItem, review_id)
    if not item or item.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "المراجعة غير موجودة", "message_en": "Review item not found"})
    res = approve_review(
        db,
        item=item,
        user_id=auth.user.id,
        note=req.note,
        run_ai_second_opinion=req.run_ai_second_opinion,
    )
    db.commit()
    return res
