"""FastAPI entrypoint for Unified Agent System."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import Base, engine
from app.routers import (
    agents_providers_router,
    auth_router,
    backlinks_router,
    captcha_vault_reports_router,
    chat_router,
    mail_google_router,
    modes_router,
    tasks_runs_router,
)
from app.security.sanitizer import install_log_sanitizer


@asynccontextmanager
async def lifespan(_app: FastAPI):
    install_log_sanitizer()
    get_settings().ensure_dirs()
    Base.metadata.create_all(bind=engine)
    yield


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
app.include_router(agents_providers_router.router)
app.include_router(tasks_runs_router.router)
app.include_router(backlinks_router.router)
app.include_router(mail_google_router.router)
app.include_router(captcha_vault_reports_router.router)
app.include_router(modes_router.router)
app.include_router(chat_router.router)
