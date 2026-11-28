"""Phase 5 Tests (§5.0 + §5A): FreeLLMAPI :31415 migration, editable Base URL, Keys URL, and fast SSE streaming."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy.orm import Session

import app.services.llm_router as router_mod
from app.models.core import User, Workspace
from app.models.providers import ApiKey, LlmCall, Provider
from app.security.crypto import decrypt_secret, encrypt_secret, mask_last4
from app.security.killswitch import activate_killswitch
from app.services.llm_router import LLMRouter, _LAST_ACTUAL_MODEL, _StreamFallbackError, seed_default_providers


def _parse_sse(raw_text: str) -> list[dict]:
    events: list[dict] = []
    cur_event = "message"
    cur_data: list[str] = []
    for line in raw_text.splitlines():
        if line.startswith("event:"):
            cur_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            cur_data.append(line.split(":", 1)[1].strip())
        elif not line.strip() and cur_data:
            raw_json = "\n".join(cur_data)
            events.append({"event": cur_event, "data": json.loads(raw_json)})
            cur_event = "message"
            cur_data = []
    if cur_data:
        events.append({"event": cur_event, "data": json.loads("\n".join(cur_data))})
    return events


def test_freellmapi_legacy_migration_and_custom_preservation(temp_db_session: Session):
    u = User(id="u-mig", email="mig@example.com", password_hash="x", role="owner")
    temp_db_session.add(u)
    temp_db_session.flush()
    ws = Workspace(id="ws-mig", name="Migration WS", owner_id=u.id)
    temp_db_session.add(ws)
    temp_db_session.flush()

    # Legacy :3001 + untouched auto models must migrate to :31415 + cheap: auto:fast
    legacy = Provider(
        id="freellmapi",
        workspace_id=ws.id,
        name="FreeLLMAPI (محلي)",
        kind="llm",
        connect_type="custom_endpoint",
        protocol="openai_compatible",
        api_base="http://localhost:3001/v1",
        models_json={"cheap": "auto", "mid": "auto", "strong": "auto"},
        enabled=False,
    )
    temp_db_session.add(legacy)
    temp_db_session.commit()

    seed_default_providers(temp_db_session, ws.id)
    temp_db_session.commit()
    temp_db_session.refresh(legacy)

    assert legacy.api_base == "http://127.0.0.1:31415/v1"
    assert legacy.models_json["cheap"] == "auto:fast"
    assert legacy.models_json["mid"] == "auto"
    assert legacy.models_json["strong"] == "auto"

    # Re-running must be idempotent
    seed_default_providers(temp_db_session, ws.id)
    temp_db_session.commit()
    assert legacy.api_base == "http://127.0.0.1:31415/v1"

    # Owner-customized api_base and models_json must NEVER be overwritten
    legacy.api_base = "http://127.0.0.1:9999/v1"
    legacy.models_json = {"cheap": "custom-fast-v2", "mid": "auto", "strong": "auto"}
    temp_db_session.commit()

    seed_default_providers(temp_db_session, ws.id)
    temp_db_session.commit()
    temp_db_session.refresh(legacy)

    assert legacy.api_base == "http://127.0.0.1:9999/v1"
    assert legacy.models_json["cheap"] == "custom-fast-v2"


def test_patch_provider_api_base_preserves_key_and_updates_keys_url(owner_session: dict, temp_db_session: Session):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    # Seed existing saved API key for freellmapi
    secret = "sk-owner-freellmapi-secret-1234"
    temp_db_session.add(
        ApiKey(
            workspace_id=ws_id,
            provider="freellmapi",
            secret_enc=encrypt_secret(secret),
            label="FreeLLMAPI",
            last4=mask_last4(secret),
        )
    )
    temp_db_session.commit()

    resp = client.patch(
        "/api/providers/freellmapi",
        json={"api_base": "http://127.0.0.1:45678/v1"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["api_base"] == "http://127.0.0.1:45678/v1"
    cat = body["catalogue"]
    assert "http://127.0.0.1:45678/Keys" in cat["empty_state_en"]
    assert "http://127.0.0.1:45678/Keys" in cat["empty_state_ar"]
    assert "localhost:3001" not in cat["empty_state_en"]

    # Saved API key must remain intact and undecryptably preserved
    key_row = temp_db_session.query(ApiKey).filter(ApiKey.workspace_id == ws_id, ApiKey.provider == "freellmapi").first()
    assert key_row is not None
    assert decrypt_secret(key_row.secret_enc) == secret


def test_chat_sse_stream_emits_tokens_actual_model_and_latency(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    prov = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "freellmapi").first()
    assert prov is not None
    prov.enabled = True
    temp_db_session.commit()

    async def fake_stream(provider, model, api_key, prompt, system_prompt):
        yield {"type": "token", "text": "مرحباً ", "model": "qwen-2.5-72b-actual", "is_mock": False}
        yield {"type": "token", "text": "بالنظام!", "model": "qwen-2.5-72b-actual", "is_mock": False}
        yield {"type": "meta", "model": "qwen-2.5-72b-actual", "tokens_in": 15, "tokens_out": 4, "is_mock": False}

    monkeypatch.setattr(router_mod, "_stream_provider_http", fake_stream)

    th_resp = client.post("/api/chat/threads", json={"title": "بث مباشر"}, headers=headers)
    assert th_resp.status_code == 200
    tid = th_resp.json()["thread"]["id"]

    stream_resp = client.post(
        f"/api/chat/threads/{tid}/messages/stream",
        json={"content": "اشرح لي حالة الخادم باختصار", "mock_mode": False},
        headers=headers,
    )
    assert stream_resp.status_code == 200
    events = _parse_sse(stream_resp.text)
    ev_names = [e["event"] for e in events]
    assert ev_names[0] == "start"
    token_events = [e for e in events if e["event"] == "token"]
    done_events = [e for e in events if e["event"] == "done"]
    assert len(token_events) == 2
    assert "".join(e["data"]["token"] for e in token_events) == "مرحباً بالنظام!"
    assert len(done_events) == 1
    assert done_events[0]["data"]["model_used"] == "qwen-2.5-72b-actual"
    assert isinstance(done_events[0]["data"]["latency_ms"], int)
    assert done_events[0]["data"]["latency_ms"] >= 0

    # Persisted thread message must also carry actual answering model and latency_ms
    get_resp = client.get(f"/api/chat/threads/{tid}", headers=headers)
    assert get_resp.status_code == 200
    asst_msgs = [m for m in get_resp.json()["messages"] if m["role"] == "assistant"]
    assert len(asst_msgs) == 1
    assert asst_msgs[0]["model_used"] == "qwen-2.5-72b-actual"
    assert isinstance(asst_msgs[0]["latency_ms"], int)
    assert not asst_msgs[0]["content"].startswith("محاكاة — ")


def test_stream_fallback_cancel_killswitch_budget_and_simulation(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]
    uid = owner_session["data"]["user"]["id"]

    prov = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "freellmapi").first()
    assert prov is not None
    prov.enabled = True
    temp_db_session.commit()

    # 1) Simulation mode SSE enforces "محاكاة — " prefix
    th_resp = client.post("/api/chat/threads", json={"title": "تجربة المحاكاة"}, headers=headers)
    tid = th_resp.json()["thread"]["id"]
    sim_resp = client.post(
        f"/api/chat/threads/{tid}/messages/stream",
        json={"content": "مرحبا", "mock_mode": True},
        headers=headers,
    )
    assert sim_resp.status_code == 200
    sim_events = _parse_sse(sim_resp.text)
    sim_text = "".join(e["data"]["token"] for e in sim_events if e["event"] == "token")
    assert sim_text.startswith("محاكاة — ")

    # Cancel endpoint responds cleanly even when stream already finished
    cancel_resp = client.post(f"/api/chat/threads/{tid}/cancel", headers=headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json() == {"thread_id": tid, "cancelled": False}

    # 2) Sync fallback on _StreamFallbackError
    async def failing_stream(*_args, **_kwargs):
        if False:
            yield {}
        raise _StreamFallbackError("http_400_no_stream")

    def fake_sync(provider, model, api_key, prompt, system_prompt, json_schema=None):
        _LAST_ACTUAL_MODEL.set("sync-fallback-model")
        return ("استجابة احتياطية كاملة", 9, 4)

    monkeypatch.setattr(router_mod, "_stream_provider_http", failing_stream)
    monkeypatch.setattr(router_mod, "_call_provider_http", fake_sync)

    async def _collect_fallback():
        llm = LLMRouter(temp_db_session, ws_id)
        out = []
        async for item in llm.route_stream(
            task_type="chat",
            tier="cheap",
            prompt="اختبار",
            preferred_provider="freellmapi",
            allow_mock_fallback=False,
        ):
            out.append(item)
        return out

    fb_events = asyncio.run(_collect_fallback())
    assert [e["type"] for e in fb_events] == ["token", "done"]
    assert fb_events[0]["token"] == "استجابة احتياطية كاملة"
    assert fb_events[1]["model"] == "sync-fallback-model"

    # 3) Mid-stream cancel_event logs aborted LlmCall
    ev_cancel = asyncio.Event()

    async def cancellable_stream(*_args, **_kwargs):
        yield {"type": "token", "text": "جزء1 ", "model": "qwen-cancel", "is_mock": False}
        ev_cancel.set()
        yield {"type": "token", "text": "جزء2 ", "model": "qwen-cancel", "is_mock": False}

    monkeypatch.setattr(router_mod, "_stream_provider_http", cancellable_stream)

    async def _collect_cancel():
        llm = LLMRouter(temp_db_session, ws_id)
        return [
            item
            async for item in llm.route_stream(
                task_type="chat",
                tier="cheap",
                prompt="الغاء",
                preferred_provider="freellmapi",
                allow_mock_fallback=False,
                cancel_event=ev_cancel,
            )
        ]

    c_events = asyncio.run(_collect_cancel())
    assert c_events[-1]["type"] == "aborted"
    assert c_events[-1]["reason"] == "cancelled"
    cancel_call = (
        temp_db_session.query(LlmCall)
        .filter(LlmCall.workspace_id == ws_id, LlmCall.error_code == "cancelled_by_user")
        .first()
    )
    assert cancel_call is not None
    assert cancel_call.ok is False

    # 4) Daily budget overrun mid-stream aborts
    prov.models_json = {"cheap": "claude-sonnet-4-5", "mid": "claude-sonnet-4-5", "strong": "claude-sonnet-4-5"}
    prov.budget_usd_daily = 0.00001
    temp_db_session.commit()

    async def expensive_stream(*_args, **_kwargs):
        yield {"type": "token", "text": "x" * 400, "model": "claude-sonnet-4-5", "is_mock": False}
        yield {"type": "meta", "model": "claude-sonnet-4-5", "tokens_in": 100, "tokens_out": 100, "is_mock": False}

    monkeypatch.setattr(router_mod, "_stream_provider_http", expensive_stream)

    async def _collect_budget():
        llm = LLMRouter(temp_db_session, ws_id)
        return [
            item
            async for item in llm.route_stream(
                task_type="chat",
                tier="cheap",
                prompt="تكلفة",
                preferred_provider="freellmapi",
                allow_mock_fallback=False,
            )
        ]

    b_events = asyncio.run(_collect_budget())
    assert b_events[-1]["type"] == "aborted"
    assert b_events[-1]["reason"] == "budget_exceeded"

    # 5) Active kill switch stops stream immediately
    prov.budget_usd_daily = 10.0
    temp_db_session.commit()
    activate_killswitch(temp_db_session, workspace_id=ws_id, user_id=uid, reason="emergency test")
    temp_db_session.commit()

    async def _collect_kill():
        llm = LLMRouter(temp_db_session, ws_id)
        return [
            item
            async for item in llm.route_stream(
                task_type="chat",
                tier="cheap",
                prompt="طوارئ",
                preferred_provider="freellmapi",
                allow_mock_fallback=False,
            )
        ]

    k_events = asyncio.run(_collect_kill())
    assert len(k_events) == 1
    assert k_events[0]["type"] == "aborted"
    assert k_events[0]["reason"] == "killswitch_active"
