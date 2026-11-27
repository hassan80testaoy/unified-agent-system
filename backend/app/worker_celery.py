"""Celery worker definition for Docker / VPS queued execution."""

from __future__ import annotations

import asyncio

from celery import Celery

from app.config import get_settings

settings = get_settings()
broker_url = settings.redis_url or "memory://"
celery_app = Celery("unified_agent", broker=broker_url, backend=broker_url)


@celery_app.task(name="unified_agent.execute_run")
def execute_run_task(run_id: str, workspace_id: str, user_id: str) -> dict[str, str]:
    from app.services.runner import _execute_run_async

    asyncio.run(_execute_run_async(run_id, workspace_id, user_id))
    return {"status": "dispatched", "run_id": run_id}
