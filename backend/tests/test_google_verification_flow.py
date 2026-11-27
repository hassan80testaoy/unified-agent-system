"""Tests for Owner Google Account (§2.3), verification-code ask flow (never stored/logged, max 2 asks, timeout), and disconnect wipe."""

from __future__ import annotations

import asyncio
import logging

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.agents import AgentModel, Run, RunEvent, Task
from app.models.core import AuditLog
from app.models.google_account import GoogleAccount, VerificationRequest
from app.services.google_account import ask_user_for_verification_code, verification_coordinator


@pytest.mark.asyncio
async def test_google_verification_code_never_logged_or_stored(owner_session, temp_db_session: Session, caplog):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]
    user_id = owner_session["data"]["user"]["id"]

    # 1. Add Google account securely
    res = client.post(
        "/api/google-accounts",
        headers=headers,
        json={
            "label": "حساب الباك لينك",
            "email": "moaaz.backlinks@gmail.com",
            "password": "MyGooglePassword!99",
            "allow_gmail_send": False,
        },
    )
    assert res.status_code == 200
    ga_id = res.json()["id"]
    assert res.json()["email_masked"] == "m****s@gmail.com"
    assert "MyGooglePassword!99" not in res.text

    ga = temp_db_session.get(GoogleAccount, ga_id)
    assert ga is not None
    profile_dir = get_settings().browser_profiles_dir / f"google_{ga_id}"
    assert profile_dir.exists()

    # Create a run to bind verification requests to
    agent = temp_db_session.query(AgentModel).filter(AgentModel.workspace_id == ws_id, AgentModel.key == "backlink").first()
    task = Task(workspace_id=ws_id, agent_id=agent.id, request_text="test", input_json={}, created_by=user_id)
    temp_db_session.add(task)
    temp_db_session.flush()
    run = Run(workspace_id=ws_id, task_id=task.id, agent_id=agent.id, status="running")
    temp_db_session.add(run)
    temp_db_session.commit()

    # 2. Successful one-time verification code exchange
    secret_code = "847291"
    with caplog.at_level(logging.DEBUG):
        async def _submit_soon():
            await asyncio.sleep(0.05)
            # Submit via API endpoint to test HTTP path too
            post_res = client.post(
                f"/api/verification-codes/{run.id}",
                headers=headers,
                json={"code": secret_code},
            )
            assert post_res.status_code == 200
            assert post_res.json()["delivered"] is True

        ask_coro = ask_user_for_verification_code(
            temp_db_session,
            workspace_id=ws_id,
            user_id=user_id,
            run=run,
            site="hashnode.com",
            google_account=ga,
            code_type="google_2fa",
            verifier_fn=lambda c: c == secret_code,
            timeout_sec=5,
        )
        outcome, _ = await asyncio.gather(ask_coro, _submit_soon())

    assert outcome["status"] == "ok"
    assert outcome["asked_count"] == 1

    # CRITICAL ASSERTION: the verification code MUST NEVER exist in DB tables or logs
    assert secret_code not in caplog.text
    for vr in temp_db_session.query(VerificationRequest).all():
        assert vr.result == "ok"
        assert not hasattr(vr, "code")
    for ev in temp_db_session.query(RunEvent).all():
        assert secret_code not in ev.message
        assert secret_code not in str(ev.data or "")
    for al in temp_db_session.query(AuditLog).all():
        assert secret_code not in str(al.details or "")

    # 3. Max 2 asks on wrong codes -> needs_manual
    async def _submit_two_wrong_codes():
        await asyncio.sleep(0.05)
        verification_coordinator.deliver_code(run.id, "111111")
        await asyncio.sleep(0.05)
        verification_coordinator.deliver_code(run.id, "222222")

    ask_wrong = ask_user_for_verification_code(
        temp_db_session,
        workspace_id=ws_id,
        user_id=user_id,
        run=run,
        site="medium.com",
        google_account=ga,
        code_type="google_2fa",
        verifier_fn=lambda c: False,
        timeout_sec=5,
    )
    outcome_wrong, _ = await asyncio.gather(ask_wrong, _submit_two_wrong_codes())
    assert outcome_wrong["status"] == "needs_manual"
    assert outcome_wrong["asked_count"] == 2

    # 4. Timeout handling
    outcome_to = await ask_user_for_verification_code(
        temp_db_session,
        workspace_id=ws_id,
        user_id=user_id,
        run=run,
        site="dev.to",
        google_account=ga,
        code_type="google_2fa",
        timeout_sec=0.05,  # type: ignore[arg-type]
    )
    assert outcome_to["status"] == "timeout"

    # 5. Disconnect wipes credentials & deletes persistent browser profile dir
    disc_res = client.post(f"/api/google-accounts/{ga_id}/disconnect", headers=headers)
    assert disc_res.status_code == 200
    temp_db_session.refresh(ga)
    assert ga.password_enc is None
    assert ga.oauth_refresh_token_enc is None
    assert ga.status == "disconnected"
    assert not profile_dir.exists()
