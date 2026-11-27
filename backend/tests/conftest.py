"""Pytest fixtures providing an isolated temporary database and FastAPI TestClient."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import Base, build_engine, get_db
from app.main import app
import app.services.runner as runner_mod
import app.db as db_mod


@pytest.fixture()
def temp_db_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Session, None, None]:
    db_file = tmp_path / "test_unified_agent.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    settings = get_settings()
    settings.data_dir = tmp_path / "data"
    settings.browser_profiles_dir = tmp_path / "browser_profiles"
    settings.artifacts_dir = tmp_path / "artifacts"
    settings.ensure_dirs()
    if settings.killswitch_file.exists():
        settings.killswitch_file.unlink()

    test_engine = build_engine(db_url)
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)

    monkeypatch.setattr(db_mod, "engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(runner_mod, "SessionLocal", TestSessionLocal)

    def _override_get_db() -> Generator[Session, None, None]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()
        app.dependency_overrides.clear()
        if settings.killswitch_file.exists():
            settings.killswitch_file.unlink()
        test_engine.dispose()


@pytest.fixture()
def client(temp_db_session: Session) -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def owner_session(client: TestClient) -> dict:
    resp = client.post(
        "/api/auth/setup",
        json={
            "email": "owner@example.com",
            "password": "StrongOwnerPassword123!",
            "workspace_name": "مكتب الاختبار",
            "enable_2fa": True,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    return {"data": data, "headers": headers, "client": client}
