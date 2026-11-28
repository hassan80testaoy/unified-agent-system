"""FastAPI entrypoint for Unified Agent System."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app import db as db_mod
from app.db import Base
from app.routers import (
    agents_providers_router,
    auth_router,
    backlinks_router,
    captcha_vault_reports_router,
    chat_router,
    coding_router,
    mail_google_router,
    mcp_router,
    modes_router,
    phase7_router,
    phase8_router,
    tasks_runs_router,
    teams_triggers_router,
)
from app.security.sanitizer import install_log_sanitizer
from app.models.core import Workspace
from app.services.backlink_library import ensure_seeded_backlink_library
from app.services.browser_worker_manager import ensure_local_browser_worker_started, stop_local_browser_worker
from app.services.coding_agent import resume_interrupted_threads
from app.services.hypnos_service import get_or_create_hypnos_config
from app.services.llm_router import seed_default_providers
from app.services.memory_service import ensure_session_fts_table
from app.services.model_catalogue import catalogue_daily_refresh_loop
from app.services.routing_profiles import ensure_default_routing_profiles
from app.services.triggers_service import trigger_scheduler_loop
from app.services.vault import sync_vault_secrets_from_db
from app.skills.coded.catalog import ensure_coded_skill_packages_on_disk


@asynccontextmanager
async def lifespan(_app: FastAPI):
    install_log_sanitizer()
    get_settings().ensure_dirs()
    Base.metadata.create_all(bind=db_mod.engine)
    db = db_mod.SessionLocal()
    try:
        ensure_coded_skill_packages_on_disk()
        ensure_session_fts_table(db)
        sync_vault_secrets_from_db(db)
        for ws in db.query(Workspace).all():
            seed_default_providers(db, ws.id)
            ensure_default_routing_profiles(db, ws.id)
            get_or_create_hypnos_config(db, ws.id)
            ensure_seeded_backlink_library(db, ws.id)
        resume_interrupted_threads(db)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    stop_event = asyncio.Event()
    refresh_task: asyncio.Task[None] | None = None
    trigger_task: asyncio.Task[None] | None = None
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        ensure_local_browser_worker_started()
        refresh_task = asyncio.create_task(catalogue_daily_refresh_loop(stop_event))
        trigger_task = asyncio.create_task(trigger_scheduler_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        stop_local_browser_worker()
        for bg_task in (refresh_task, trigger_task):
            if bg_task is not None:
                bg_task.cancel()
                try:
                    await bg_task
                except asyncio.CancelledError:
                    pass


app = FastAPI(
    title="Unified Agent System API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3010",
        "http://127.0.0.1:3010",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(phase8_router.router)
app.include_router(agents_providers_router.router)
app.include_router(tasks_runs_router.router)
app.include_router(backlinks_router.router)
app.include_router(mail_google_router.router)
app.include_router(captcha_vault_reports_router.router)
app.include_router(modes_router.router)
app.include_router(chat_router.router)
app.include_router(coding_router.router)
app.include_router(mcp_router.router)
app.include_router(teams_triggers_router.router)
app.include_router(phase7_router.router)
