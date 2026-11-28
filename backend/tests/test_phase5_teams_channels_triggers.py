"""Phase 5 Tests (§5C): Bots Roster, Model Picker, Channels, Jev Routing, Team Install/Share & Triggers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.core import Notification
from app.models.phase5 import AutomationTrigger, BotContact, TeamChannel, TriggerRunLog
from app.services.llm_router import LLMRouter
from app.services.triggers_service import cron_matches, tick_due_schedule_triggers


def test_bots_roster_actions_model_picker_and_mid_conversation_switch(
    owner_session: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]

    bots_resp = client.get("/api/bots", headers=headers)
    assert bots_resp.status_code == 200
    bots = bots_resp.json()["bots"]
    assert len(bots) >= 3
    coder = next(b for b in bots if b["handle"] == "coder")
    conv_id = coder["conversation_id"]
    assert conv_id

    # Context menu actions: pin, mark_unread, mark_read, edit profile, duplicate, hide, delete
    assert client.post(f"/api/bots/{coder['id']}/action", json={"action": "pin"}, headers=headers).json()["bot"]["pinned"] is True
    assert client.post(f"/api/bots/{coder['id']}/action", json={"action": "mark_unread"}, headers=headers).json()["bot"]["unread_count"] >= 1
    assert client.post(f"/api/bots/{coder['id']}/action", json={"action": "mark_read"}, headers=headers).json()["bot"]["unread_count"] == 0

    patched = client.patch(
        f"/api/bots/{coder['id']}",
        json={"name": "مهندس النظم الأول", "role_title": "Principal Engineer"},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["bot"]["name"] == "مهندس النظم الأول"

    dup_resp = client.post(f"/api/bots/{coder['id']}/duplicate", headers=headers)
    assert dup_resp.status_code == 200
    dup = dup_resp.json()["bot"]
    assert dup["id"] != coder["id"]
    assert dup["conversation_id"] != conv_id

    # Hide excludes duplicate from default roster, include_hidden reveals it, then delete removes it
    client.post(f"/api/bots/{dup['id']}/action", json={"action": "hide"}, headers=headers)
    visible_ids = {b["id"] for b in client.get("/api/bots", headers=headers).json()["bots"]}
    assert dup["id"] not in visible_ids
    all_ids = {b["id"] for b in client.get("/api/bots?include_hidden=true", headers=headers).json()["bots"]}
    assert dup["id"] in all_ids
    assert client.delete(f"/api/bots/{dup['id']}", headers=headers).json()["deleted"] is True

    # Model picker rail shows defaults and dimmed unavailable providers with reasons
    rail = client.get("/api/bots/model-picker", headers=headers).json()["rail"]
    assert len(rail) >= 3
    unavail = [r for r in rail if not r["available"]]
    assert len(unavail) >= 1
    assert all(r["unavailable_reason"] for r in unavail)
    assert any(m["is_default"] for r in rail for m in r["models"])

    # Mid-conversation model switch affects subsequent bot messages cleanly
    seen_models: list[str] = []

    def fake_route(self, **kwargs):
        chosen = kwargs.get("preferred_model") or "auto:fast"
        seen_models.append(chosen)
        return {
            "provider_id": kwargs.get("preferred_provider") or "freellmapi",
            "model": chosen,
            "requested_model": chosen,
            "tier": "cheap",
            "task_type": kwargs.get("task_type"),
            "output": None,
            "raw_text": f"رد باستخدام {chosen}",
            "tokens_in": 5,
            "tokens_out": 5,
            "cost_usd": 0.0,
            "latency_ms": 12,
            "cached": False,
            "is_mock": False,
        }

    monkeypatch.setattr(LLMRouter, "route", fake_route)

    c1 = client.post(f"/api/bots/{coder['id']}/chat", json={"content": "أول رسالة"}, headers=headers)
    assert c1.status_code == 200
    assert c1.json()["model_used"] == "auto:fast"

    sw = client.patch(
        f"/api/bots/{coder['id']}/model",
        json={"provider_id": "openrouter", "model_name": "qwen/qwen3-coder:free"},
        headers=headers,
    )
    assert sw.status_code == 200
    assert sw.json()["bot"]["provider_id"] == "openrouter"
    assert sw.json()["bot"]["model_name"] == "qwen/qwen3-coder:free"

    c2 = client.post(f"/api/bots/{coder['id']}/chat", json={"content": "كمل بنفس المحادثة"}, headers=headers)
    assert c2.status_code == 200
    assert c2.json()["model_used"] == "qwen/qwen3-coder:free"
    assert seen_models == ["auto:fast", "qwen/qwen3-coder:free"]


def test_channels_isolated_transcripts_and_jev_routing(owner_session: dict):
    client = owner_session["client"]
    headers = owner_session["headers"]

    bots = client.get("/api/bots", headers=headers).json()["bots"]
    chief = next(b for b in bots if b["handle"] == "chief")
    coder = next(b for b in bots if b["handle"] == "coder")
    research = next(b for b in bots if b["handle"] == "research")

    # Save Jev settings
    put_cfg = client.put(
        "/api/settings/routing",
        json={"enabled": True, "decision_model": "auto:fast", "confidence_threshold": 0.7, "fallback_to_lead": True},
        headers=headers,
    )
    assert put_cfg.status_code == 200
    assert client.get("/api/settings/routing", headers=headers).json()["routing"]["decision_model"] == "auto:fast"

    # Two separate contextual channels sharing the SAME bots
    ch_a = client.post(
        "/api/channels",
        json={
            "name": "Alpha Code",
            "context_type": "project",
            "shared_instructions": "Python backend focus",
            "lead_bot_id": chief["id"],
            "bot_ids": [chief["id"], coder["id"], research["id"]],
        },
        headers=headers,
    ).json()["channel"]
    ch_b = client.post(
        "/api/channels",
        json={
            "name": "Beta Personal",
            "context_type": "personal",
            "shared_instructions": "Personal research notes",
            "lead_bot_id": chief["id"],
            "bot_ids": [chief["id"], coder["id"], research["id"]],
        },
        headers=headers,
    ).json()["channel"]

    # 1) Coding signal routes to coder
    r1 = client.post(
        f"/api/channels/{ch_a['id']}/messages",
        json={"content": "Please fix this python bug in our api endpoint", "mock_mode": True},
        headers=headers,
    ).json()
    assert r1["routed_bot"]["id"] == coder["id"]
    assert r1["routing"]["reason"] == "jev_coding_signal"

    # 2) Explicit @mention overrides Jev
    r2 = client.post(
        f"/api/channels/{ch_a['id']}/messages",
        json={"content": "@research check SEO backlinks for python api", "mock_mode": True},
        headers=headers,
    ).json()
    assert r2["routed_bot"]["id"] == research["id"]
    assert r2["routing"]["reason"] == "mention:@research"

    # 3) Ambiguous/unsure message falls back to lead bot
    r3 = client.post(
        f"/api/channels/{ch_b['id']}/messages",
        json={"content": "صباح الخير، ما هي خطة اليوم؟", "mock_mode": True},
        headers=headers,
    ).json()
    assert r3["routed_bot"]["id"] == chief["id"]
    assert r3["routing"]["reason"] == "jev_unsure_fallback_to_lead"

    # Transcripts remain strictly isolated per channel
    channels = {c["id"]: c for c in client.get("/api/channels", headers=headers).json()["channels"]}
    assert len(channels[ch_a["id"]]["transcript"]) == 4  # 2 user + 2 assistant
    assert len(channels[ch_b["id"]]["transcript"]) == 2  # 1 user + 1 assistant


def test_team_markdown_review_install_share_scrubbing_and_triggers(
    owner_session: dict,
    temp_db_session: Session,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    team_md = """---
name: Growth Squad
version: "2.1"
credentials:
  openai_key: sk-should-never-import-123456
conversations:
  - secret private chat
memory:
  - hidden user note
permissions:
  - admin_all
computer_access: full
chief_of_staff:
  name: Growth Lead
  handle: growth_lead
  instructions: Manage launch at https://secret.internal.local/api
bots:
  - name: Growth Writer
    handle: growth_writer
    instructions: Draft RTL landing pages with key sk-1234567890abcdef1234
    skills:
      - slug-formatter
channels:
  - name: LaunchOps
    context_type: project
    shared_instructions: Coordinate launch
connectors:
  - slack
  - notion
routines:
  - name: Daily Digest
    cron: "30 9 * * *"
    prompt: Summarize metrics
---
Portable team description.
"""

    # Preview MUST NOT write any DB rows and MUST list stripped forbidden fields
    bots_before = temp_db_session.query(BotContact).filter(BotContact.workspace_id == ws_id).count()
    channels_before = temp_db_session.query(TeamChannel).filter(TeamChannel.workspace_id == ws_id).count()
    trigs_before = temp_db_session.query(AutomationTrigger).filter(AutomationTrigger.workspace_id == ws_id).count()

    prev = client.post("/api/teams/preview", json={"raw_md": team_md}, headers=headers)
    assert prev.status_code == 200, prev.text
    pv = prev.json()["preview"]
    assert set(pv["stripped_forbidden_fields"]) == {"computer_access", "conversations", "credentials", "memory", "permissions"}
    assert temp_db_session.query(BotContact).filter(BotContact.workspace_id == ws_id).count() == bots_before
    assert temp_db_session.query(TeamChannel).filter(TeamChannel.workspace_id == ws_id).count() == channels_before
    assert temp_db_session.query(AutomationTrigger).filter(AutomationTrigger.workspace_id == ws_id).count() == trigs_before

    # Installing without confirmation is rejected (400)
    unconf = client.post("/api/teams/install", json={"raw_md": team_md, "confirmed": False}, headers=headers)
    assert unconf.status_code == 400

    # Confirmed install sets all connectors OFF and all routines PAUSED/disabled
    inst = client.post("/api/teams/install", json={"raw_md": team_md, "confirmed": True}, headers=headers)
    assert inst.status_code == 200, inst.text
    inst_data = inst.json()
    assert inst_data["routines_paused_count"] == 1
    assert set(inst_data["connectors_disabled"]) == {"slack", "notion"}
    for b in inst_data["bots"]:
        assert all(app["enabled"] is False for app in b["connected_apps"])

    trigs = client.get("/api/triggers", headers=headers).json()["triggers"]
    imported_trig = next(t for t in trigs if t["name"] == "Daily Digest")
    assert imported_trig["paused"] is True
    assert imported_trig["enabled"] is False

    # Share export (folder + github) scrubs API keys, private chats, memory, and URLs
    share_folder = client.post("/api/teams/share", json={"title": "Clean Team", "target": "folder"}, headers=headers)
    assert share_folder.status_code == 200
    sf = share_folder.json()
    assert "sk-1234567890abcdef1234" not in sf["markdown"]
    assert "https://secret.internal.local" not in sf["markdown"]
    assert "secret private chat" not in sf["markdown"]
    assert sf["saved_path"] is not None

    share_gh = client.post("/api/teams/share", json={"title": "Clean Team", "target": "github"}, headers=headers)
    assert share_gh.status_code == 200
    assert share_gh.json()["github_bundle"]["filename"] == "clean-team/TEAM.md"
    assert "sk-1234567890abcdef1234" not in share_gh.json()["github_bundle"]["content"]

    # Triggers: schedule cron + manual fire + event emission + visible Notifications (never silent)
    assert cron_matches("30 9 * * *", datetime(2026, 11, 28, 9, 30, tzinfo=timezone.utc)) is True
    assert cron_matches("30 9 * * *", datetime(2026, 11, 28, 9, 31, tzinfo=timezone.utc)) is False

    t_sched = client.post(
        "/api/triggers",
        json={
            "name": "Morning Ping",
            "trigger_type": "schedule",
            "cron_expr": "0 8 * * *",
            "target_bot_id": inst_data["bots"][0]["id"],
            "action_prompt": "صباح الخير",
            "enabled": True,
            "paused": False,
        },
        headers=headers,
    ).json()["trigger"]

    # Tick cron scheduler at exact matching time
    trig_row = temp_db_session.get(AutomationTrigger, t_sched["id"])
    assert trig_row is not None
    due_logs = tick_due_schedule_triggers(temp_db_session, now=datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc))
    temp_db_session.commit()
    assert len(due_logs) == 1
    assert due_logs[0].status == "success"

    # Manual fire + history
    fire_resp = client.post(f"/api/triggers/{t_sched['id']}/fire", headers=headers)
    assert fire_resp.status_code == 200
    assert fire_resp.json()["trigger"]["fire_count"] >= 2

    hist = client.get(f"/api/triggers/{t_sched['id']}/history", headers=headers).json()["history"]
    assert len(hist) >= 2

    # Event trigger
    ev_trig = client.post(
        "/api/triggers",
        json={
            "name": "On Repo Push",
            "trigger_type": "event",
            "event_name": "repo.pushed",
            "target_bot_id": inst_data["bots"][0]["id"],
            "action_prompt": "تحقق من الكود الجديد",
            "enabled": True,
            "paused": False,
        },
        headers=headers,
    ).json()["trigger"]

    emitted = client.post(
        "/api/triggers/emit-event",
        json={"event_name": "repo.pushed", "payload": {"branch": "main"}},
        headers=headers,
    )
    assert emitted.status_code == 200
    assert emitted.json()["fired_count"] == 1

    # Notifications MUST have been created (triggers never run silently)
    notifs = (
        temp_db_session.query(Notification)
        .filter(Notification.workspace_id == ws_id, Notification.kind == "trigger_success")
        .all()
    )
    assert len(notifs) >= 3

    # Patch & delete cleanup works cleanly
    assert client.patch(f"/api/triggers/{ev_trig['id']}", json={"paused": True}, headers=headers).json()["trigger"]["paused"] is True
    assert client.delete(f"/api/triggers/{ev_trig['id']}", headers=headers).json()["deleted"] is True
