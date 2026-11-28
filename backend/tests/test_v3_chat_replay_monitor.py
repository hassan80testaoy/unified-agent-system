"""Tests for V3 Chat-First General Agent, Session Replay, Flagship Loop, and Link Monitor Alerts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.base import AgentContext
from app.agents.link_monitor.agent import LinkMonitorAgent
from app.agents.registry import discover_agents
from app.models.agents import AgentModel, Run, RunEvent, Task
from app.models.backlinks import BlAccount, BlLink, BlSite, BlTarget
from app.models.core import Notification
from app.models.v3_harness import BlSession
from app.services.browser_loop import run_flagship_backlink_loop
from app.services.intent_router import classify_intent
from app.services.llm_router import LLMRouter
from app.services.session_log import read_log
from app.services.session_replay import replay_backlink_session, save_backlink_session


def test_intent_routing_and_chat_ndjson_compact_undo(owner_session):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # Pure unit checks first
    assert classify_intent("أرخص طيران للقاهرة بكرة")["intent"] == "flights"
    assert classify_intent("شوف إيميلي")["intent"] == "agent-mail"
    backlink_route = classify_intent("سجل لي حساب في موقع medium.com بالإيميل test@gmail.com واعمل باك لينك")
    assert backlink_route["intent"] == "backlink"
    assert backlink_route["entities"]["domain"] == "medium.com"
    assert backlink_route["entities"]["email"] == "test@gmail.com"

    # End-to-end Chat thread lifecycle
    th_res = client.post("/api/chat/threads", headers=headers, json={"title": "تجربة V3"})
    assert th_res.status_code == 200
    tid = th_res.json()["thread"]["id"]
    ndjson_path = Path(th_res.json()["thread"]["ndjson_path"])

    # 1. Flights query never triggers backlink
    r1 = client.post(f"/api/chat/threads/{tid}/messages", headers=headers, json={"content": "أرخص طيران لدبي"})
    assert r1.status_code == 200
    assert r1.json()["intent"] == "flights"
    assert r1.json()["assistant_message"]["card_type"] == "approval"

    # 2. Mail query routes to agent-mail
    r2 = client.post(f"/api/chat/threads/{tid}/messages", headers=headers, json={"content": "شوف إيميلي"})
    assert r2.status_code == 200
    assert r2.json()["intent"] == "agent-mail"

    # 3. Backlink query runs Flagship Loop cleanly in explicit simulation mode
    r3 = client.post(
        f"/api/chat/threads/{tid}/messages",
        headers=headers,
        json={"content": "سجل لي حساب في موقع hubpages.com بالإيميل moaaz.seo.real@mydomain.io واعمل باك لينك"},
    )
    assert r3.status_code == 200
    assert r3.json()["intent"] == "backlink"
    assert "محاكاة — " in r3.json()["assistant_message"]["content"]
    assert r3.json()["assistant_message"]["card_payload"]["is_mock"] is True

    assert ndjson_path.exists()
    active_before_undo = read_log(tid)
    assert len(active_before_undo) >= 6

    # Compact + Undo
    cmp_res = client.post(f"/api/chat/threads/{tid}/compact", headers=headers)
    assert cmp_res.status_code == 200
    assert cmp_res.json()["compacted_summary"]

    undo_res = client.post(f"/api/chat/threads/{tid}/undo", headers=headers, json={})
    assert undo_res.status_code == 200
    assert undo_res.json()["rewound_count"] == 2

    detail = client.get(f"/api/chat/threads/{tid}", headers=headers).json()
    assert len(detail["messages"]) == 4  # 6 -> 4 active messages after rewinding last 2-msg turn


def test_session_replay_secret_stripping_fuzzy_match_and_flagship_loop(owner_session, temp_db_session: Session):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]
    user_id = owner_session["data"]["user"]["id"]

    # Save session with a forbidden raw secret key attempt -> stripped on save
    s1 = save_backlink_session(
        temp_db_session,
        workspace_id=ws_id,
        domain="replay-site.example",
        steps=[
            {"n": 1, "action": "goto", "value_key": "signup_url"},
            {"n": 2, "action": "type", "selector_css": "input.old-email", "el_id": "email_field", "value_key": "secret_pass_token"},
            {"n": 3, "action": "submit", "selector_css": "button.old-btn", "el_text": "Create Account"},
        ],
    )
    temp_db_session.commit()
    assert s1.version == 1
    pb_text = Path(s1.playbook_path).read_text(encoding="utf-8")
    assert "secret_pass_token" not in pb_text

    # Replay with changed CSS selectors matching via el_id and el_text fallback
    rep = replay_backlink_session(
        temp_db_session,
        workspace_id=ws_id,
        domain="replay-site.example",
        available_elements=[
            {"selector_css": "input#email_field", "id": "email_field"},
            {"selector_css": "button#new-submit", "text": "Create Account Now"},
        ],
    )
    temp_db_session.commit()
    assert rep["found"] is True
    assert rep["matched_steps"] == 3
    assert rep["saved_llm_calls"] >= 1
    assert rep["replay_count"] == 1

    # Flagship loop directly with simulated 2FA returns verification_code card cleanly
    loop_2fa = asyncio.run(
        run_flagship_backlink_loop(
            temp_db_session,
            workspace_id=ws_id,
            user_id=user_id,
            domain="twofa-site.example",
            email="moaaz.twofa@mydomain.io",
            mock_mode=True,
            simulate_2fa=True,
        )
    )
    assert loop_2fa["status"] == "awaiting_verification_code"
    assert loop_2fa["card_type"] == "verification_code"

    sess_res = client.get("/api/backlinks/sessions", headers=headers)
    assert sess_res.status_code == 200
    assert any(row["domain"] == "replay-site.example" for row in sess_res.json()["stats"])


def test_link_monitor_notifications_on_lost_and_downgraded(owner_session, temp_db_session: Session):
    ws_id = owner_session["data"]["workspace"]["id"]
    user_id = owner_session["data"]["user"]["id"]

    discover_agents(temp_db_session, ws_id)
    agent_row = temp_db_session.query(AgentModel).filter(AgentModel.workspace_id == ws_id, AgentModel.key == "link_monitor").first()
    assert agent_row is not None

    target = BlTarget(workspace_id=ws_id, kind="homepage", url="https://moaazhassan.com")
    s_lost = BlSite(workspace_id=ws_id, domain="lost-site.example", status="live")
    s_down = BlSite(workspace_id=ws_id, domain="downgrade-site.example", status="live")
    temp_db_session.add_all([target, s_lost, s_down])
    temp_db_session.flush()

    a_lost = BlAccount(workspace_id=ws_id, site_id=s_lost.id, profile_url="mock://lost-site.example/u/moaaz")
    a_down = BlAccount(workspace_id=ws_id, site_id=s_down.id, profile_url="mock://downgrade-site.example/u/moaaz")
    temp_db_session.add_all([a_lost, a_down])
    temp_db_session.flush()

    l_lost = BlLink(workspace_id=ws_id, account_id=a_lost.id, target_id=target.id, link_type="dofollow", live=1)
    l_down = BlLink(workspace_id=ws_id, account_id=a_down.id, target_id=target.id, link_type="dofollow", live=1)
    task = Task(workspace_id=ws_id, agent_id=agent_row.id, request_text="monitor links", created_by=user_id)
    temp_db_session.add(task)
    temp_db_session.flush()
    run = Run(workspace_id=ws_id, task_id=task.id, agent_id=agent_row.id, status="queued")
    temp_db_session.add_all([l_lost, l_down, run])
    temp_db_session.commit()

    ctx = AgentContext(
        db=temp_db_session,
        workspace_id=ws_id,
        user_id=user_id,
        run=run,
        agent_id=agent_row.id,
        allowed_tools=["bl_tables", "browser"],
        router=LLMRouter(temp_db_session, ws_id),
    )
    summary = asyncio.run(
        LinkMonitorAgent().run(
            ctx,
            {
                "mock_mode": True,
                "simulate_lost_domains": ["lost-site.example"],
                "simulate_downgrade_domains": ["downgrade-site.example"],
            },
        )
    )
    assert summary["lost"] == 1
    assert summary["downgraded"] == 1

    notes = temp_db_session.query(Notification).filter(Notification.workspace_id == ws_id).all()
    kinds = {n.kind for n in notes}
    assert "link_lost" in kinds
    assert "link_downgraded" in kinds

    ev_kinds = {
        e.kind
        for e in temp_db_session.query(RunEvent).filter(RunEvent.run_id == run.id).all()
    }
    assert "link_lost" in ev_kinds
    assert "link_downgraded" in ev_kinds
