"""6 Instinct Proactive Value Loops — generate explicit approval-gated proposals, NEVER silent mutations (§7B.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.agents import Approval, Goal
from app.models.backlinks import BlLink
from app.models.core import Notification, Workspace
from app.models.phase7 import ProactiveProposal, SiteAudit
from app.models.seo import RankSnapshot
from app.security.auth import record_audit
from app.services.goal_planner import update_goal_step_status
from app.services.hypnos_service import record_feedback_event
from app.services.proxy_health import get_proxy_health

PROACTIVE_LOOP_NAMES = (
    "dead_backlink_watcher",
    "rank_drop_detector",
    "goal_step_due",
    "audit_issue_fixer",
    "stale_approval_nudge",
    "budget_and_proxy_guard",
)


def serialize_proposal(p: ProactiveProposal) -> dict[str, Any]:
    return {
        "id": p.id,
        "workspace_id": p.workspace_id,
        "loop_name": p.loop_name,
        "dedup_key": p.dedup_key,
        "title_ar": p.title_ar,
        "title_en": p.title_en,
        "detail_md": p.detail_md,
        "action_payload": p.action_payload_json or {},
        "status": p.status,
        "decided_by": p.decided_by,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "execution_result": p.execution_result_json,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _upsert_proposal(
    db: Session,
    *,
    workspace_id: str,
    loop_name: str,
    dedup_key: str,
    title_ar: str,
    title_en: str,
    detail_md: str,
    action_payload: dict[str, Any],
) -> tuple[ProactiveProposal, bool]:
    existing = (
        db.query(ProactiveProposal)
        .filter(
            ProactiveProposal.workspace_id == workspace_id,
            ProactiveProposal.loop_name == loop_name,
            ProactiveProposal.dedup_key == dedup_key,
        )
        .first()
    )
    if existing is not None:
        return existing, False
    row = ProactiveProposal(
        workspace_id=workspace_id,
        loop_name=loop_name,
        dedup_key=dedup_key[:128],
        title_ar=title_ar[:250],
        title_en=title_en[:250],
        detail_md=detail_md,
        action_payload_json=action_payload,
        status="pending",
    )
    db.add(row)
    db.flush()
    return row, True


def run_proactive_value_loops(db: Session, workspace_id: str) -> dict[str, Any]:
    """Scan workspace signals across the 6 Instinct loops and create approval-required proposals."""
    created: list[ProactiveProposal] = []
    now = datetime.now(timezone.utc)

    # Loop 1: Dead Backlink Watcher
    dead_links = (
        db.query(BlLink)
        .filter(BlLink.workspace_id == workspace_id, BlLink.live == 0)
        .order_by(BlLink.updated_at.desc())
        .limit(5)
        .all()
    )
    for lnk in dead_links:
        p, is_new = _upsert_proposal(
            db,
            workspace_id=workspace_id,
            loop_name="dead_backlink_watcher",
            dedup_key=f"link:{lnk.id}",
            title_ar=f"باك لينك معطل بحاجة لإعادة فحص أو استبدال ({lnk.check_note})",
            title_en=f"Dead backlink detected ({lnk.check_note})",
            detail_md=f"تم رصد رابط باك لينك غير ظاهر حالياً (`{lnk.id[:8]}` · ملاحظة: `{lnk.check_note}`). اضغط اعتماد لجدولة إعادة التحقق أو فتح بديل.",
            action_payload={"action": "flag_backlink_recheck", "link_id": lnk.id},
        )
        if is_new:
            created.append(p)

    # Loop 2: Rank Drop Detector
    snaps = (
        db.query(RankSnapshot)
        .filter(RankSnapshot.workspace_id == workspace_id)
        .order_by(RankSnapshot.created_at.desc())
        .limit(40)
        .all()
    )
    by_kw: dict[str, list[RankSnapshot]] = {}
    for s in snaps:
        if s.position is not None:
            by_kw.setdefault(s.keyword, []).append(s)
    for kw, items in by_kw.items():
        if len(items) >= 2 and items[0].position and items[1].position:
            delta = float(items[0].position) - float(items[1].position)
            if delta >= 2.0:
                p, is_new = _upsert_proposal(
                    db,
                    workspace_id=workspace_id,
                    loop_name="rank_drop_detector",
                    dedup_key=f"rank:{kw}:{items[0].id[:8]}",
                    title_ar=f"تراجع ترتيب «{kw}» بمقدار {delta:.1f} مركزاً ({items[1].position} ← {items[0].position})",
                    title_en=f"Rank drop on '{kw}' ({items[1].position} -> {items[0].position})",
                    detail_md=f"تراجع ترتيب الكلمة المستهدفة `{kw}` من المركز `{items[1].position}` إلى `{items[0].position}`. يُقترح تدقيق الصفحة المستهدفة وتحديث المحتوى.",
                    action_payload={"action": "audit_rank_drop", "keyword": kw, "target_url": items[0].target_url},
                )
                if is_new:
                    created.append(p)

    # Loop 3: Goal Step Due
    for g in db.query(Goal).filter(Goal.workspace_id == workspace_id, Goal.status == "active").all():
        steps = list((g.plan or {}).get("steps") or [])
        due_step = next((s for s in steps if isinstance(s, dict) and s.get("status") != "done"), None)
        if due_step:
            p, is_new = _upsert_proposal(
                db,
                workspace_id=workspace_id,
                loop_name="goal_step_due",
                dedup_key=f"goal:{g.id}:{due_step.get('id')}",
                title_ar=f"خطوة مستحقة في الهدف «{g.title[:60]}»: {due_step.get('title', '')[:80]}",
                title_en=f"Due goal step on '{g.title[:60]}'",
                detail_md=f"الخطوة التالية في الهدف `{g.title}` تنتظر التنفيذ/الموافقة (الموعد: {due_step.get('deadline') or 'اليوم'}).",
                action_payload={"action": "advance_goal_step", "goal_id": g.id, "step_id": due_step.get("id")},
            )
            if is_new:
                created.append(p)

    # Loop 4: Audit Issue Fixer
    for aud in db.query(SiteAudit).filter(SiteAudit.workspace_id == workspace_id, SiteAudit.score < 90).order_by(SiteAudit.created_at.desc()).limit(3).all():
        crit = [f for f in (aud.findings_json or []) if isinstance(f, dict) and f.get("severity") in {"critical", "warning"}]
        if crit:
            p, is_new = _upsert_proposal(
                db,
                workspace_id=workspace_id,
                loop_name="audit_issue_fixer",
                dedup_key=f"audit:{aud.id}",
                title_ar=f"توصية تحسين موقع {aud.url} (الدرجة {aud.score}/100 — {len(crit)} ملاحظات)",
                title_en=f"Site audit fixes for {aud.url} ({aud.score}/100)",
                detail_md=f"رُصدت {len(crit)} مشاكل فنية على `{aud.url}` أبرزها: {crit[0].get('title_ar')}. اضغط اعتماد لبدء خطة الإصلاح.",
                action_payload={"action": "ack_audit_fix", "audit_id": aud.id, "url": aud.url},
            )
            if is_new:
                created.append(p)

    # Loop 5: Stale Approval Nudge
    stale_cutoff = now - timedelta(hours=1)
    for ap in db.query(Approval).filter(Approval.workspace_id == workspace_id, Approval.status == "pending").limit(5).all():
        created_at = ap.created_at.replace(tzinfo=timezone.utc) if ap.created_at.tzinfo is None else ap.created_at
        if created_at <= stale_cutoff:
            p, is_new = _upsert_proposal(
                db,
                workspace_id=workspace_id,
                loop_name="stale_approval_nudge",
                dedup_key=f"approval:{ap.id}",
                title_ar=f"طلب موافقة معلق منذ أكثر من ساعة ({ap.kind})",
                title_en=f"Pending approval nudge ({ap.kind})",
                detail_md=f"الموافقة `{ap.id[:8]}` من نوع `{ap.kind}` متوقفة بانتظار قرارك.",
                action_payload={"action": "nudge_approval", "approval_id": ap.id},
            )
            if is_new:
                created.append(p)

    # Loop 6: Budget & Proxy Guard
    ph = get_proxy_health(db)
    if ph.get("status") == "degraded_limit" or ph.get("last_fell_back_to_direct"):
        day_k = now.date().isoformat()
        p, is_new = _upsert_proposal(
            db,
            workspace_id=workspace_id,
            loop_name="budget_and_proxy_guard",
            dedup_key=f"proxy:{day_k}",
            title_ar="تحذير البروكسي السكني: تم استنفاد الحصة والانتقال للاتصال المباشر",
            title_en="Residential proxy limit exhausted (direct fallback active)",
            detail_md=f"آخر خطأ من البروكسي: `{ph.get('last_error') or 'limit exceeded'}`. جدّد باقة البروكسي أو فرّغ `RESIDENTIAL_PROXY` في `.env`.",
            action_payload={"action": "ack_proxy_guard"},
        )
        if is_new:
            created.append(p)

    if created:
        ws = db.get(Workspace, workspace_id)
        if ws and ws.owner_id:
            db.add(
                Notification(
                    workspace_id=workspace_id,
                    user_id=ws.owner_id,
                    kind="proactive_proposals",
                    title=f"{len(created)} مقترحات استباقية جديدة بانتظار موافقتك",
                    body=created[0].title_ar,
                    link="/goals",
                )
            )
    db.flush()
    return {
        "loops_evaluated": list(PROACTIVE_LOOP_NAMES),
        "new_proposals_count": len(created),
        "proposals": [serialize_proposal(x) for x in created],
    }


def decide_proactive_proposal(
    db: Session,
    proposal: ProactiveProposal,
    *,
    decision: str,
    user_id: str,
) -> dict[str, Any]:
    """Explicit owner approve/reject handler — only runs the action after approval."""
    clean_dec = decision.strip().lower()
    if clean_dec not in {"approve", "reject"}:
        raise ValueError("Decision must be 'approve' or 'reject'")

    proposal.decided_by = user_id
    proposal.decided_at = datetime.now(timezone.utc)
    if clean_dec == "reject":
        proposal.status = "rejected"
        proposal.execution_result_json = {"executed": False, "decision": "rejected"}
        record_feedback_event(
            db,
            workspace_id=proposal.workspace_id,
            kind="denied",
            context_summary=f"Proposal {proposal.loop_name}: {proposal.title_ar}",
            meta={"proposal_id": proposal.id},
        )
        record_audit(
            db,
            user_id=user_id,
            workspace_id=proposal.workspace_id,
            action="proactive_proposal_rejected",
            target=proposal.id,
        )
        db.flush()
        return serialize_proposal(proposal)

    payload = dict(proposal.action_payload_json or {})
    act = str(payload.get("action") or "")
    exec_res: dict[str, Any] = {"executed": True, "action": act}

    if act == "advance_goal_step" and payload.get("goal_id") and payload.get("step_id"):
        g = db.get(Goal, str(payload["goal_id"]))
        if g and g.workspace_id == proposal.workspace_id:
            g_out = update_goal_step_status(
                db,
                g,
                str(payload["step_id"]),
                "done",
                note=f"تم التنفيذ بعد موافقة المالك على مقترح {proposal.loop_name}",
            )
            exec_res["goal_progress"] = g_out["progress"]

    proposal.status = "executed"
    proposal.execution_result_json = exec_res
    record_feedback_event(
        db,
        workspace_id=proposal.workspace_id,
        kind="approved",
        context_summary=f"Proposal {proposal.loop_name}: {proposal.title_ar}",
        meta={"proposal_id": proposal.id, "action": act},
    )
    record_audit(
        db,
        user_id=user_id,
        workspace_id=proposal.workspace_id,
        action="proactive_proposal_approved",
        target=proposal.id,
        details=exec_res,
    )
    db.flush()
    return serialize_proposal(proposal)
