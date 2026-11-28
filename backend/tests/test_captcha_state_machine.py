"""Tests for CAPTCHA Plugin Interface (`CaptchaSolver`), `ManualSolver`, state transitions, timeout, skip, and real stats."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.orm import Session

from app.captcha.solver import CaptchaChallenge, CaptchaSolver, get_captcha_solver
from app.config import get_settings
from app.captcha.solvers.manual import ManualSolver, manual_captcha_coordinator, pause_for_manual_captcha
from app.models.agents import AgentModel, Run, Task
from app.models.captcha import CaptchaAttempt


def test_captcha_plugin_interface_and_detection(monkeypatch):
    # Isolate from a real .env: the configured solver comes from .env.
    monkeypatch.setenv("CAPTCHA_SOLVER", "")
    get_settings.cache_clear()
    solver = get_captcha_solver()
    assert isinstance(solver, ManualSolver)
    assert isinstance(solver, CaptchaSolver)

    ch_recap = solver.detect('<div class="g-recaptcha" data-sitekey="abc"></div>')
    assert ch_recap is not None
    assert ch_recap.challenge_type == "recaptcha"

    ch_cf = solver.detect('<div class="cf-turnstile"></div>')
    assert ch_cf is not None
    assert ch_cf.challenge_type == "turnstile"
    assert ch_cf.blocked_reason == "cloudflare"

    ch_phone = solver.detect({"site": "x.com", "challenge_type": "phone_verify"})
    assert ch_phone is not None
    res_phone = solver.solve(ch_phone)
    assert res_phone.status == "phone_verify_blocked"
    assert res_phone.cost_usd == 0.0


@pytest.mark.asyncio
async def test_manual_captcha_solve_skip_timeout_and_stats(owner_session, temp_db_session: Session):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]
    user_id = owner_session["data"]["user"]["id"]

    agent = temp_db_session.query(AgentModel).filter(AgentModel.workspace_id == ws_id, AgentModel.key == "backlink").first()
    task = Task(workspace_id=ws_id, agent_id=agent.id, request_text="captcha test", input_json={}, created_by=user_id)
    temp_db_session.add(task)
    temp_db_session.flush()
    run = Run(workspace_id=ws_id, task_id=task.id, agent_id=agent.id, status="running")
    temp_db_session.add(run)
    temp_db_session.commit()

    solver = get_captcha_solver()
    challenge = CaptchaChallenge(run_id=run.id, site="carrd.co", challenge_type="recaptcha", page_url="https://carrd.co/signup")

    # 1. Manual solved path via API endpoint -> solver.on_manual_solved
    async def _confirm_soon():
        await asyncio.sleep(0.05)
        res = client.post(f"/api/captcha/{run.id}/confirm-solved", headers=headers)
        assert res.status_code == 200

    res1, _ = await asyncio.gather(
        pause_for_manual_captcha(temp_db_session, workspace_id=ws_id, user_id=user_id, run=run, challenge=challenge, timeout_sec=5),
        _confirm_soon(),
    )
    assert res1.status == "passed"
    assert res1.method == "manual"
    assert res1.cost_usd == 0.0

    # 2. Skip path via API
    async def _skip_soon():
        await asyncio.sleep(0.05)
        res = client.post(f"/api/captcha/{run.id}/skip", headers=headers, json={"reason": "cloudflare_hard"})
        assert res.status_code == 200

    res2, _ = await asyncio.gather(
        pause_for_manual_captcha(temp_db_session, workspace_id=ws_id, user_id=user_id, run=run, challenge=challenge, timeout_sec=5),
        _skip_soon(),
    )
    assert res2.status == "skipped"
    assert res2.blocked_reason == "cloudflare_hard"

    # 3. Timeout path
    res3 = await pause_for_manual_captcha(
        temp_db_session, workspace_id=ws_id, user_id=user_id, run=run, challenge=challenge, timeout_sec=0.05  # type: ignore[arg-type]
    )
    assert res3.status == "timeout"
    assert res3.blocked_reason == "manual_timeout"

    # 4. Real stats endpoint reflects exact counts
    stats_res = client.get("/api/captcha/stats", headers=headers)
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["solver_mode"] == "manual"
    assert stats["max_spend_usd"] == 0.0
    assert stats["total"] == 3
    assert stats["passed"] == 1
    assert stats["skipped"] == 1
    assert stats["timeout"] == 1
    assert temp_db_session.query(CaptchaAttempt).count() == 3
