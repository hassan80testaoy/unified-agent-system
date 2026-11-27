"""Task preview, approval gating, and live run execution engine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import jsonschema
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, KillSwitchTriggeredError
from app.agents.registry import get_agent_instance
from app.db import SessionLocal
from app.models.agents import AgentModel, Approval, Run, Task
from app.security.auth import record_audit
from app.security.killswitch import is_killswitch_active
from app.services.events import emit_and_broadcast_run_event
from app.services.llm_router import LLMRouter

_ACTIVE_TASKS: dict[str, asyncio.Task[Any]] = {}


def preview_task(
    db: Session,
    *,
    workspace_id: str,
    agent: AgentModel,
    input_json: dict[str, Any],
) -> dict[str, Any]:
    if agent.input_schema:
        jsonschema.validate(instance=input_json, schema=agent.input_schema)

    item_count = int(input_json.get("max_per_day") or input_json.get("limit") or 10)
    mock_mode = bool(input_json.get("mock_mode", True))
    est_cost_usd = 0.0 if mock_mode else round(item_count * 0.012, 3)
    est_minutes = 1 if mock_mode else max(2, item_count * 2)

    approval_reasons: list[str] = []
    if agent.needs_approval:
        approval_reasons.append("agent_requires_approval")
    if item_count > 20:
        approval_reasons.append("bulk_batch_over_20")
    if est_cost_usd > 0:
        approval_reasons.append("estimated_api_spend")

    return {
        "agent_id": agent.id,
        "agent_key": agent.key,
        "item_count": item_count,
        "est_cost_usd": est_cost_usd,
        "est_minutes": est_minutes,
        "requires_approval": len(approval_reasons) > 0,
        "approval_reasons": approval_reasons,
    }


async def _execute_run_async(run_id: str, workspace_id: str, user_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        if not run or run.status == "canceled":
            return
        task = db.get(Task, run.task_id)
        agent_row = db.get(AgentModel, run.agent_id)
        if not task or not agent_row:
            return

        router = LLMRouter(db, workspace_id)
        ctx = AgentContext(
            db=db,
            workspace_id=workspace_id,
            user_id=user_id,
            run=run,
            agent_id=agent_row.id,
            allowed_tools=list(agent_row.tools_allowed or []),
            router=router,
            run_max_budget_usd=agent_row.daily_budget_usd,
        )
        plugin = get_agent_instance(agent_row.key)
        await plugin.run(ctx, dict(task.input_json or {}))
    except KillSwitchTriggeredError:
        run = db.get(Run, run_id)
        if run:
            run.status = "canceled"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        run = db.get(Run, run_id)
        if run:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            await emit_and_broadcast_run_event(
                db,
                workspace_id=workspace_id,
                run_id=run_id,
                level="error",
                kind="run_failed",
                message=f"توقف التشغيل بخطأ: {type(exc).__name__}",
            )
    finally:
        _ACTIVE_TASKS.pop(run_id, None)
        db.close()


def schedule_run_execution(run_id: str, workspace_id: str, user_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_execute_run_async(run_id, workspace_id, user_id))
        _ACTIVE_TASKS[run_id] = task
    except RuntimeError:
        asyncio.run(_execute_run_async(run_id, workspace_id, user_id))


def create_task_and_run(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    agent: AgentModel,
    request_text: str,
    input_json: dict[str, Any],
    mode: str = "once",
    schedule_cron: str | None = None,
    ip: str | None = None,
) -> tuple[Task, Run, dict[str, Any]]:
    if is_killswitch_active(db):
        raise RuntimeError("Global kill switch is active. Deactivate kill switch before starting tasks.")
    if not agent.enabled:
        raise ValueError(f"Agent '{agent.key}' is disabled.")

    prev = preview_task(db, workspace_id=workspace_id, agent=agent, input_json=input_json)

    task = Task(
        workspace_id=workspace_id,
        agent_id=agent.id,
        request_text=request_text,
        input_json=input_json,
        mode=mode,
        schedule_cron=schedule_cron,
        status="created",
        created_by=user_id,
    )
    db.add(task)
    db.flush()

    initial_status = "awaiting_approval" if prev["requires_approval"] else "queued"
    run = Run(
        workspace_id=workspace_id,
        task_id=task.id,
        agent_id=agent.id,
        status=initial_status,
        progress_total=prev["item_count"],
        progress_done=0,
    )
    db.add(run)
    db.flush()

    if prev["requires_approval"]:
        kind = "bulk" if "bulk_batch_over_20" in prev["approval_reasons"] else "spend"
        db.add(
            Approval(
                workspace_id=workspace_id,
                run_id=run.id,
                kind=kind,
                payload={
                    "agent_key": agent.key,
                    "item_count": prev["item_count"],
                    "est_cost_usd": prev["est_cost_usd"],
                    "reasons": prev["approval_reasons"],
                },
                status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
        )

    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="task.create",
        target=agent.key,
        ip=ip,
        details={"run_id": run.id, "status": initial_status, "item_count": prev["item_count"]},
    )
    db.commit()
    db.refresh(task)
    db.refresh(run)

    if not prev["requires_approval"]:
        schedule_run_execution(run.id, workspace_id, user_id)

    return task, run, prev
