"""Tests for Step 2 Captcha Interface Fixes (precedence, generic dispatch, passive site_key, proxy config)."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.backlink.agent import BacklinkAgent
from app.agents.base import AgentContext
from app.agents.registry import discover_agents
from app.captcha.solver import CaptchaChallenge, CaptchaResult, dispatch_solve, get_configured_solver_name
from app.captcha.solvers.manual import ManualSolver
from app.config import get_settings
from app.models.agents import AgentModel, Approval, Run, Task
from app.models.captcha import CaptchaAttempt
from app.services.llm_router import LLMRouter


def test_captcha_solver_env_overrides_yaml(monkeypatch):
    monkeypatch.setenv("CAPTCHA_SOLVER", "plugin_demo")
    get_settings.cache_clear()
    try:
        assert get_configured_solver_name() == "plugin_demo"
    finally:
        monkeypatch.delenv("CAPTCHA_SOLVER", raising=False)
        get_settings.cache_clear()


def test_passive_site_key_extraction_manual_and_browser_worker():
    html = (
        '<form><div class="g-recaptcha" data-sitekey="6LeTestSiteKey1234567890"></div>'
        '<button type="submit">Go</button></form>'
    )
    ch = ManualSolver().detect(html)
    assert ch is not None
    assert ch.challenge_type == "recaptcha"
    assert ch.site_key == "6LeTestSiteKey1234567890"

    worker_path = Path(__file__).resolve().parents[2] / "browser-worker" / "worker.py"
    spec = importlib.util.spec_from_file_location("bw_worker_test", worker_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    bw_ch = mod._detect_challenge_from_html(html)
    assert bw_ch is not None
    assert bw_ch["site_key"] == "6LeTestSiteKey1234567890"

    parsed = mod._parse_proxy("http://alice:s3cr3t@10.20.30.40:8080")
    assert parsed == {
        "server": "http://10.20.30.40:8080",
        "username": "alice",
        "password": "s3cr3t",
    }


class _FakeOkSolver:
    name = "fake_ok"

    def detect(self, page):
        return ManualSolver().detect(page)

    def solve(self, challenge: CaptchaChallenge) -> CaptchaResult:
        assert challenge.site_key == "6LeFakeKey999"
        assert challenge.proxy == "http://127.0.0.1:8888"
        return CaptchaResult(status="passed", method="fake_ok", duration_ms=120, cost_usd=0.001)

    def on_manual_solved(self, run_id: str) -> None:
        return None


class _BrokenSolver:
    def detect(self, page):
        return ManualSolver().detect(page)

    def solve(self, challenge: CaptchaChallenge) -> CaptchaResult:
        raise RuntimeError("solver_boom")

    def on_manual_solved(self, run_id: str) -> None:
        return None


def test_fake_solver_generic_dispatch_and_error_fallback(owner_session, temp_db_session: Session, monkeypatch):
    ws_id = owner_session["data"]["workspace"]["id"]
    user_id = owner_session["data"]["user"]["id"]

    # Safe fallback when a custom solver raises
    broken_ch = CaptchaChallenge(run_id="r1", site="about.me", challenge_type="recaptcha", page_url="https://about.me")
    fallback_res = asyncio.run(dispatch_solve(_BrokenSolver(), broken_ch))
    assert fallback_res.status == "fallback_manual"
    assert fallback_res.cost_usd == 0.0

    # End-to-end BacklinkAgent using _FakeOkSolver skips manual approval pause and logs CaptchaAttempt
    discover_agents(temp_db_session, ws_id)
    agent_row = temp_db_session.query(AgentModel).filter(AgentModel.workspace_id == ws_id, AgentModel.key == "backlink").first()
    assert agent_row is not None
    task = Task(workspace_id=ws_id, agent_id=agent_row.id, request_text="test fake solver", created_by=user_id)
    temp_db_session.add(task)
    temp_db_session.flush()
    run = Run(workspace_id=ws_id, task_id=task.id, agent_id=agent_row.id, status="queued")
    temp_db_session.add(run)
    temp_db_session.commit()

    monkeypatch.setattr("app.agents.base.get_captcha_solver", lambda name=None: _FakeOkSolver())
    ctx = AgentContext(
        db=temp_db_session,
        workspace_id=ws_id,
        user_id=user_id,
        run=run,
        agent_id=agent_row.id,
        allowed_tools=["llm", "captcha", "bl_tables", "mail_read", "browser"],
        router=LLMRouter(temp_db_session, ws_id),
    )
    summary = asyncio.run(
        BacklinkAgent().run(
            ctx,
            {
                "target_domain": "moaazhassan.com",
                "max_per_day": 1,
                "mock_mode": True,
                "simulate_captcha_sites": ["gravatar.com"],
                "simulate_site_key": "6LeFakeKey999",
                "proxy": "http://127.0.0.1:8888",
            },
        )
    )
    assert summary["live"] == 1
    attempt = temp_db_session.query(CaptchaAttempt).filter(CaptchaAttempt.run_id == run.id).first()
    assert attempt is not None
    assert attempt.method == "fake_ok"
    assert attempt.proxy_type == "proxy"
    assert attempt.result == "passed"
    assert temp_db_session.query(Approval).filter(Approval.run_id == run.id, Approval.kind == "captcha_manual").count() == 0
