"""One-Sentence Goal Planner executing autonomous agent steps & surfacing user-owned steps (§7A.1)."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db import utcnow
from app.models.agents import Goal
from app.security.sanitizer import scrub_text
from app.services.gsc_service import fetch_gsc_report, get_gsc_status
from app.services.planner_brain import run_planner_brain_workflow
from app.services.site_auditor import audit_site_url

_URL_RE = re.compile(r"(https?://[^\s]+|[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?)")
_KEYWORD_QUOTED_RE = re.compile(r"(?:كلمة|keyword)\s+[\"«']?([^\"»'\n]{2,60})[\"»']?", re.I)


def _extract_url_from_sentence(sentence: str) -> str | None:
    for m in _URL_RE.finditer(sentence):
        cand = m.group(1).strip(".,;!?؟،")
        if "@" in cand or cand.endswith((".png", ".jpg", ".json")):
            continue
        return cand if cand.startswith(("http://", "https://")) else f"https://{cand}"
    return None


def _extract_keyword(sentence: str) -> str:
    m = _KEYWORD_QUOTED_RE.search(sentence)
    if m:
        return m.group(1).strip()
    return sentence.strip()[:80]


def serialize_goal_plan(goal: Goal) -> dict[str, Any]:
    plan = dict(goal.plan or {})
    ckpt = dict(goal.checkpoint_json or {})
    steps = list(plan.get("steps") or [])
    return {
        "id": goal.id,
        "workspace_id": goal.workspace_id,
        "thread_id": goal.thread_id,
        "title": goal.title,
        "outcome": goal.outcome,
        "status": goal.status,
        "metric_name": goal.metric_name,
        "metric_start": goal.metric_start,
        "metric_target": goal.metric_target,
        "deadline": goal.deadline.isoformat() if goal.deadline else None,
        "success_metric": plan.get("success_metric") or f"{goal.metric_name} -> {goal.metric_target}",
        "target_url": plan.get("target_url"),
        "keyword": plan.get("keyword"),
        "steps": steps,
        "planner_split": plan.get("planner_split"),
        "progress": {
            "completed_steps": int(ckpt.get("completed_steps", 0)),
            "total_steps": int(ckpt.get("total_steps", len(steps))),
            "progress_pct": float(ckpt.get("progress_pct", 0.0)),
            "agent_done": list(ckpt.get("agent_done") or []),
            "needs_user": list(ckpt.get("needs_user") or []),
        },
        "updated_at": goal.updated_at.isoformat() if goal.updated_at else None,
    }


def _recompute_progress(goal: Goal) -> None:
    plan = copy.deepcopy(goal.plan or {})
    steps = [s for s in (plan.get("steps") or []) if isinstance(s, dict)]
    done_steps = [s for s in steps if s.get("status") == "done"]
    agent_done = [s["title"] for s in done_steps if s.get("owner") == "agent"]
    needs_user = [
        {"id": s.get("id"), "title": s.get("title"), "deadline": s.get("deadline")}
        for s in steps
        if s.get("owner") == "user" and s.get("status") != "done"
    ]
    total = len(steps)
    pct = round((len(done_steps) / total) * 100.0, 1) if total else 0.0
    ckpt = copy.deepcopy(goal.checkpoint_json or {})
    ckpt.update(
        {
            "completed_steps": len(done_steps),
            "total_steps": total,
            "progress_pct": pct,
            "agent_done": agent_done,
            "needs_user": needs_user,
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    goal.checkpoint_json = ckpt
    flag_modified(goal, "checkpoint_json")
    goal.metric_start = float(len(done_steps))
    goal.metric_target = float(max(total, 1))
    if total > 0 and len(done_steps) == total:
        goal.status = "done"


async def execute_goal_agent_steps(db: Session, goal: Goal, user_id: str | None = None) -> dict[str, Any]:
    """Execute runnable agent steps in the Goal plan and surface user-owned steps."""
    plan = copy.deepcopy(goal.plan or {})
    steps = list(plan.get("steps") or [])
    target_url = plan.get("target_url")
    keyword = plan.get("keyword") or goal.title

    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("status") == "done":
            continue
        if step.get("owner") != "agent":
            if step.get("status") == "pending":
                step["status"] = "needs_user"
            continue

        tool = str(step.get("tool") or "roadmap")
        if tool == "gsc_report":
            gsc_st = get_gsc_status(db, goal.workspace_id)
            if gsc_st["connected"]:
                gsc_res = fetch_gsc_report(db, workspace_id=goal.workspace_id, days=28)
                step["status"] = "done"
                totals = gsc_res.get("totals") or {}
                step["result_summary"] = (
                    f"تم جلب بيانات GSC الحقيقية: نقرات={totals.get('clicks', 0)}، ظهور={totals.get('impressions', 0)}"
                )
            else:
                step["status"] = "needs_user"
                step["owner"] = "user"
                step["result_summary"] = "Google Search Console غير متصل — اربطه من الإعدادات لقراءة الترتيب الفعلي."
        elif tool == "site_audit":
            if target_url:
                audit_res = await audit_site_url(db, workspace_id=goal.workspace_id, url=str(target_url))
                step["status"] = "done"
                step["result_summary"] = (
                    f"فحص حي لـ {audit_res['url']}: الدرجة {audit_res['score']}/100 ({len(audit_res['findings'])} ملاحظات) [{audit_res['network_path']}]"
                )
            else:
                step["status"] = "needs_user"
                step["owner"] = "user"
                step["result_summary"] = "أدخل رابط الموقع لإجراء الفحص التقني المباشر."
        elif tool == "keyword_plan":
            step["status"] = "done"
            step["result_summary"] = (
                f"خطة استهداف `{keyword}`: تحسين Title/H1/Meta + محتوى متعمق + روابط داخلية + متابعة أسبوعية في GSC."
            )
        else:
            step["status"] = "done"
            step["result_summary"] = f"تم تجهيز مسار التنفيذ للخطوة: {step.get('title')}"

    plan["steps"] = steps
    goal.plan = plan
    flag_modified(goal, "plan")
    goal.plan_version = int(goal.plan_version or 1) + 1
    _recompute_progress(goal)
    db.flush()
    return serialize_goal_plan(goal)


async def plan_goal_from_sentence(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    sentence: str,
    thread_id: str | None = None,
    execute_auto_steps: bool = True,
) -> dict[str, Any]:
    """Turn a one-sentence goal into a structured Goal record, execute agent steps, and format report."""
    clean_sentence = scrub_text(sentence).strip()
    if not clean_sentence:
        raise ValueError("Goal sentence cannot be empty")

    target_url = _extract_url_from_sentence(clean_sentence)
    keyword = _extract_keyword(clean_sentence)
    now = datetime.now(timezone.utc)
    deadline_dt = now + timedelta(days=14)

    split_meta: dict[str, Any] | None = None
    ai_notes = ""
    try:
        pb = run_planner_brain_workflow(
            db,
            workspace_id=workspace_id,
            task_name="goal_planner",
            goal_prompt=clean_sentence,
            executor_prompt_builder=lambda p: (
                f"Produce a concise Arabic SEO/execution checklist for goal: {clean_sentence}\nPlan:\n{p}"
            ),
        )
        split_meta = pb.get("split")
        ai_notes = str(pb.get("execution_text") or "")[:400]
    except Exception:
        pass

    d3 = (now + timedelta(days=2)).date().isoformat()
    d7 = (now + timedelta(days=7)).date().isoformat()
    d14 = deadline_dt.date().isoformat()
    success_metric = f"تقدم ملموس في هدف: {keyword}"

    steps: list[dict[str, Any]] = [
        {
            "id": "step-1",
            "title": f"فحص خط الأساس في Google Search Console لكلمة «{keyword}»",
            "owner": "agent",
            "deadline": d3,
            "success_metric": "قراءة النقرات ومتوسط الترتيب الحقيقي",
            "tool": "gsc_report",
            "status": "pending",
            "result_summary": "",
        },
        {
            "id": "step-2",
            "title": f"بناء خطة الكلمات والمحتوى والروابط الداخلية لـ «{keyword}»",
            "owner": "agent",
            "deadline": d3,
            "success_metric": "قائمة تحسينات قابلة للتنفيذ",
            "tool": "keyword_plan",
            "status": "pending",
            "result_summary": "",
        },
    ]
    if target_url:
        steps.append(
            {
                "id": "step-3",
                "title": f"فحص تقني حي للموقع ({target_url}) عبر المتصفح",
                "owner": "agent",
                "deadline": d3,
                "success_metric": "درجة فحص > 80 وحصر المشاكل بالأدلة",
                "tool": "site_audit",
                "status": "pending",
                "result_summary": "",
            }
        )
    steps.extend(
        [
            {
                "id": f"step-{len(steps) + 1}",
                "title": "اعتماد نشر تحديثات الصفحة والروابط المستهدفة",
                "owner": "user",
                "deadline": d7,
                "success_metric": "موافقة المالك الصريحة قبل النشر",
                "tool": "user_action",
                "status": "needs_user",
                "result_summary": "",
            },
            {
                "id": f"step-{len(steps) + 2}",
                "title": f"مراجعة الترتيب الأسبوعي ومقارنة النتائج مع خط الأساس ({d14})",
                "owner": "user",
                "deadline": d14,
                "success_metric": success_metric,
                "tool": "user_action",
                "status": "needs_user",
                "result_summary": "",
            },
        ]
    )

    goal = Goal(
        workspace_id=workspace_id,
        agent_id=None,
        thread_id=thread_id,
        title=clean_sentence[:250],
        outcome=clean_sentence,
        metric_name="steps_completed",
        metric_start=0.0,
        metric_target=float(len(steps)),
        deadline=deadline_dt,
        autopilot=True,
        status="active",
        plan={
            "goal_sentence": clean_sentence,
            "keyword": keyword,
            "target_url": target_url,
            "success_metric": success_metric,
            "ai_notes": ai_notes,
            "planner_split": split_meta,
            "steps": steps,
        },
        plan_version=1,
        checkpoint_json={"stage": "planned", "created_at": utcnow().isoformat()},
    )
    _recompute_progress(goal)
    db.add(goal)
    db.flush()

    serialized = await execute_goal_agent_steps(db, goal, user_id=user_id) if execute_auto_steps else serialize_goal_plan(goal)

    prog = serialized["progress"]
    lines = [
        f"🎯 **تم تحويل هدفك إلى خطة تنفيذية محفوظة (`{goal.id[:8]}`):**",
        f"- **الهدف:** {serialized['title']}",
        f"- **معيار النجاح:** {serialized['success_metric']} · **الموعد النهائي:** {d14}",
        f"- **التقدم الحالي:** {prog['completed_steps']}/{prog['total_steps']} ({prog['progress_pct']}%)",
        "",
        "📋 **خطوات الخطة:**",
    ]
    for st in serialized["steps"]:
        badge = "✅" if st["status"] == "done" else ("👤 بانتظارك" if st["owner"] == "user" else "⏳")
        who = "الوكيل" if st["owner"] == "agent" else "المالك"
        extra = f" — {st['result_summary']}" if st.get("result_summary") else ""
        lines.append(f"- {badge} [{who}] {st['title']} (مهلة: {st['deadline']}){extra}")

    return {
        "goal": serialized,
        "report_ar": "\n".join(lines),
    }


def update_goal_step_status(db: Session, goal: Goal, step_id: str, status: str, note: str = "") -> dict[str, Any]:
    plan = copy.deepcopy(goal.plan or {})
    steps = list(plan.get("steps") or [])
    matched = False
    for st in steps:
        if isinstance(st, dict) and st.get("id") == step_id:
            st["status"] = status
            if note:
                st["result_summary"] = scrub_text(note)[:300]
            matched = True
            break
    if not matched:
        raise ValueError(f"Step '{step_id}' not found on goal")
    plan["steps"] = steps
    goal.plan = plan
    flag_modified(goal, "plan")
    _recompute_progress(goal)
    db.flush()
    return serialize_goal_plan(goal)
