"""Phase 8 Tests — §8.0 Output/Loop Guards, Native Tool Calling, Real Search, Parallel Chat/Steer, §8K Memory, §8L.2 Blind-Fill Scrubber."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models.coding import CodingThread
from app.models.providers import Provider
from app.models.v3_harness import ChatThread
from app.security.sanitizer import discard_vault_secret, scrub_text
from app.services.browser_agent import BrowserWorkerClient, run_general_browser_agent
from app.services.chat_output_guard import StreamingOutputFilter, sanitize_chat_output
from app.services.coding_agent import send_coding_message
from app.services.llm_router import LLMRouter
from app.services.loop_guard import LoopGuard, RunawayToolCallGuard
from app.services.memory_service import build_memory_context_block, create_memory
from app.services.vault import create_vault_request, submit_vault_request


def test_output_guard_strips_tool_call_and_internal_plan_json():
    raw_tc = 'مرحباً <tool_call>{"name":"search","arguments":{"q":"ai"}}</tool_call> تم التنفيذ'
    cleaned, meta = sanitize_chat_output(raw_tc)
    assert "<tool_call>" not in cleaned
    assert "مرحباً" in cleaned and "تم التنفيذ" in cleaned
    assert meta["had_tool_markup"] is True
    assert len(meta["extracted_calls"]) == 1

    raw_plan = '{"origin":"user","task":"Search Google","skills":["browser-use"]}'
    cleaned_p, meta_p = sanitize_chat_output(raw_plan)
    assert meta_p["had_internal_plan_json"] is True
    assert '"origin"' not in cleaned_p
    assert "Search Google" in cleaned_p


def test_streaming_filter_preserves_spaces_and_strips_split_tool_call():
    flt = StreamingOutputFilter()
    chunks = ["مرحباً ", "بك <tool_", 'call>{"name":"x"}</tool_call>', " هنا!"]
    emitted = "".join(flt.feed(c) for c in chunks) + flt.flush()
    assert "<tool_call" not in emitted
    assert "مرحباً بك" in emitted
    assert "هنا!" in emitted


def test_loop_guard_and_runaway_tool_call_guard():
    lg = LoopGuard()
    same_call = [{"name": "browser_open", "arguments": {"url": "https://example.com"}}]
    outcomes = [lg.record_step(tool_calls=same_call)[0] for _ in range(7)]
    assert "note" in outcomes
    assert outcomes[-1] == "stop"

    # Mutating tool resets loop counter cleanly
    d_reset, _ = lg.record_step(tool_calls=[{"name": "write", "arguments": {"path": "a.py"}}])
    assert d_reset == "ok"

    rg = RunawayToolCallGuard(max_malformed=3)
    assert rg.observe("<tool_call>bad1</tool_call>")[0] is False
    assert rg.observe("<tool_call>bad2</tool_call>")[0] is False
    halted, reason = rg.observe("<tool_call>bad3</tool_call>")
    assert halted is True and reason and "<tool_call>" in reason


def test_native_tool_calling_route_with_tools(owner_session: dict, temp_db_session: Session, monkeypatch: pytest.MonkeyPatch):
    ws_id = owner_session["data"]["workspace"]["id"]
    prov = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "freellmapi").first()
    assert prov is not None
    prov.enabled = True
    temp_db_session.commit()

    captured_payloads: list[dict[str, Any]] = []

    class FakeResp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "model": "qwen-2.5-72b",
                "choices": [
                    {
                        "message": {
                            "content": "جارٍ الاستدعاء",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "web_search", "arguments": json.dumps({"query": "FastAPI"})},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 22, "completion_tokens": 11},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url: str, headers=None, json=None):
            captured_payloads.append({"url": url, "json": json})
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    res = LLMRouter(temp_db_session, ws_id).route_with_tools(
        task_type="chat",
        tier="cheap",
        messages=[{"role": "user", "content": "ابحث عن FastAPI"}],
        tools=[{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}],
        preferred_provider="freellmapi",
        allow_mock_fallback=False,
    )
    assert len(captured_payloads) == 1
    assert "tools" in captured_payloads[0]["json"]
    assert res["tool_calls"][0]["name"] == "web_search"
    assert res["tool_calls"][0]["arguments"]["query"] == "FastAPI"
    assert "<tool_call>" not in res["content"]


def test_real_arabic_search_returns_links_never_bare_ack(owner_session: dict, monkeypatch: pytest.MonkeyPatch):
    client = owner_session["client"]
    headers = owner_session["headers"]

    class FakeDDGResp:
        status_code = 200
        text = (
            '<html><body>'
            '<a class="result__a" href="https://fastapi.tiangolo.com/">FastAPI Docs</a>'
            '<a class="result__a" href="https://docs.python.org/">Python Official</a>'
            "</body></html>"
        )

    class FakeDDGClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url: str):
            return FakeDDGResp()

    monkeypatch.setattr(httpx, "Client", FakeDDGClient)

    th = client.post("/api/chat/threads", json={"title": "بحث حقيقي"}, headers=headers).json()["thread"]
    resp = client.post(
        f"/api/chat/threads/{th['id']}/messages",
        json={"content": "ابحثلي عن أفضل توثيق FastAPI", "mock_mode": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    content = body["assistant_message"]["content"]
    assert "تم استلام رسالتك" not in content
    assert "<tool_call>" not in content
    assert "FastAPI Docs" in content
    assert "https://fastapi.tiangolo.com/" in content


def test_parallel_threads_live_status_pause_resume_and_steer(owner_session: dict):
    client = owner_session["client"]
    headers = owner_session["headers"]

    tids = []
    for i in range(3):
        res = client.post("/api/chat/threads", json={"title": f"مهمة متوازية {i+1}"}, headers=headers)
        assert res.status_code == 200
        tids.append(res.json()["thread"]["id"])

    live = client.get("/api/chat/threads/live-status", headers=headers)
    assert live.status_code == 200
    live_ids = {t["thread_id"] for t in live.json()["threads"]}
    assert set(tids).issubset(live_ids)

    pause_res = client.post(f"/api/chat/threads/{tids[0]}/pause", headers=headers)
    assert pause_res.status_code == 200 and pause_res.json()["paused"] is True

    steer_res = client.post(
        f"/api/chat/threads/{tids[0]}/steer",
        json={"instruction": "استخدم الإخراج العربي المختصر فقط"},
        headers=headers,
    )
    assert steer_res.status_code == 200 and steer_res.json()["queued_count"] == 1

    resume_res = client.post(f"/api/chat/threads/{tids[0]}/resume", headers=headers)
    assert resume_res.status_code == 200 and resume_res.json()["paused"] is False

    msg_res = client.post(
        f"/api/chat/threads/{tids[0]}/messages",
        json={"content": "لخص الحالة الحالية", "mock_mode": True},
        headers=headers,
    )
    assert msg_res.status_code == 200
    # Queued steer should have been consumed cleanly
    th_after = client.get(f"/api/chat/threads/{tids[0]}", headers=headers).json()["thread"]
    assert th_after["pending_steering"] == []


@pytest.mark.asyncio
async def test_memory_crud_fts_compaction_export_delete_and_all_agent_injection(
    owner_session: dict,
    temp_db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]
    uid = owner_session["data"]["user"]["id"]

    # 1. Create memory -> check MEMORY.md & USER.md mirrors
    m1 = client.post(
        "/api/memory",
        json={"category": "preference", "content": "أفضل ملخصات برمجية باللغة العربية RTL"},
        headers=headers,
    )
    assert m1.status_code == 200
    mem_id = m1.json()["id"]

    list_res = client.get("/api/memory", headers=headers).json()
    assert any(item["id"] == mem_id for item in list_res["memories"])
    assert "أفضل ملخصات برمجية" in list_res["user_md"]

    # 2. Update memory
    upd = client.patch(
        f"/api/memory/{mem_id}",
        json={"content": "أفضل ملخصات بايثون مع الكود أولاً"},
        headers=headers,
    )
    assert upd.status_code == 200
    assert "مع الكود أولاً" in upd.json()["memory"]["content"]

    # 3. Cross-session FTS5 recall + vector memory search
    th1 = client.post("/api/chat/threads", json={"title": "جلسة سابقة"}, headers=headers).json()["thread"]
    client.post(
        f"/api/chat/threads/{th1['id']}/messages",
        json={"content": "تذكر رمز المشروع المعماري OrionX-2026", "mock_mode": True},
        headers=headers,
    )

    search_res = client.post("/api/memory/search", json={"query": "OrionX-2026 بايثون"}, headers=headers)
    assert search_res.status_code == 200
    sdata = search_res.json()
    assert len(sdata["memory_hits"]) >= 1
    assert any("OrionX" in h["content"] for h in sdata["session_hits"])

    # 4. Compaction preserves facts into persistent memory
    comp = client.post(f"/api/chat/threads/{th1['id']}/compact", headers=headers)
    assert comp.status_code == 200
    assert comp.json()["compacted_summary"]

    # 5. Memory loads into ALL main agent paths (chat, coding, browser)
    mem_ctx, loaded_list = build_memory_context_block(
        temp_db_session, workspace_id=ws_id, user_id=uid, query="بايثون OrionX-2026"
    )
    assert "مع الكود أولاً" in mem_ctx
    assert len(loaded_list) >= 1

    captured_coding_prompts: list[str] = []

    def fake_route(self, *, task_type: str, tier: str = "cheap", prompt: str = "", system_prompt: str = "", **kwargs):
        captured_coding_prompts.append(f"{system_prompt}\n{prompt}")
        return {"provider_id": "llm-mock", "model": "mock", "content": "خطة جاهزة", "is_mock": True}

    monkeypatch.setattr(LLMRouter, "route", fake_route)

    prov = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "freellmapi").first()
    assert prov is not None
    prov.enabled = True
    coding_ws = tmp_path / "coding_ws"
    coding_ws.mkdir(parents=True, exist_ok=True)
    ct = CodingThread(
        workspace_id=ws_id,
        user_id=uid,
        title="coding mem test",
        workspace_dir=str(coding_ws),
        model_provider_id="freellmapi",
    )
    temp_db_session.add(ct)
    temp_db_session.commit()
    send_coding_message(temp_db_session, ct, "اكتب دالة بايثون بسيطة", skip_browser_verify=True)
    assert any("مع الكود أولاً" in p for p in captured_coding_prompts)

    def fake_route_browser(self, *, task_type: str, tier: str = "cheap", prompt: str = "", system_prompt: str = "", **kwargs):
        captured_coding_prompts.append(f"{system_prompt}\n{prompt}")
        return {
            "provider_id": "llm-mock",
            "model": "mock",
            "content": json.dumps({"tool": "done", "args": {"summary_ar": "تمت القراءة"}}),
            "is_mock": False,
        }

    monkeypatch.setattr(LLMRouter, "route", fake_route_browser)
    monkeypatch.setattr("app.services.browser_agent.get_runtime_readiness", lambda db, w: {"browser_worker": True})
    monkeypatch.setattr("app.services.browser_agent.resolve_browser_worker_url", lambda: "http://127.0.0.1:7080")

    async def fake_worker_call(self, path: str, payload: dict[str, Any]):
        if path == "/interactive/close":
            return {"ok": True}
        return {
            "url": payload.get("url") or "https://example.com",
            "title": "Example Page",
            "links": [{"href": "https://example.com/docs", "text": "Docs"}],
            "inputs": [],
            "text": "Example domain page",
            "screenshot_url": None,
        }

    monkeypatch.setattr(BrowserWorkerClient, "call", fake_worker_call)
    captured_coding_prompts.clear()

    b_res = await run_general_browser_agent(
        temp_db_session,
        thread_id=th1["id"],
        workspace_id=ws_id,
        user_id=uid,
        prompt="افتح موقع example.com واقرأ ما فيه عن بايثون",
        entities={"url": "https://example.com"},
        mock_mode=False,
    )
    assert b_res["status"] == "completed"
    assert any("مع الكود أولاً" in p for p in captured_coding_prompts)
    temp_db_session.commit()

    # 6. Export + full purge
    exp = client.get("/api/memory/export", headers=headers)
    assert exp.status_code == 200
    assert len(exp.json()["memories"]) >= 1

    purge = client.delete("/api/memory/purge/all", headers=headers)
    assert purge.status_code == 200 and purge.json()["deleted_all"] is True
    after_list = client.get("/api/memory", headers=headers).json()
    assert after_list["memories"] == []
    assert "مع الكود أولاً" not in after_list["user_md"]


def test_blind_fill_vault_secret_scrubbed_from_llm_http_payload(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    ws_id = owner_session["data"]["workspace"]["id"]
    uid = owner_session["data"]["user"]["id"]
    secret_val = "UltraSecretVaultPass!778899"

    try:
        vreq = create_vault_request(
            temp_db_session,
            workspace_id=ws_id,
            user_id=uid,
            kind="login",
            label="Test Site Login",
            allowed_sites=["example.org"],
        )
        submit_vault_request(
            temp_db_session,
            raw_token=vreq["token"],
            username="owner@example.org",
            secret_value=secret_val,
        )

        # Direct scrubber strips raw vault secret
        assert secret_val not in scrub_text(f"login password is {secret_val}")

        prov = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "freellmapi").first()
        assert prov is not None
        prov.enabled = True
        temp_db_session.commit()

        sent_bodies: list[dict[str, Any]] = []

        class FakeResp:
            status_code = 200

            def json(self):
                return {"model": "qwen", "choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url: str, headers=None, json=None):
                sent_bodies.append(json)
                return FakeResp()

        monkeypatch.setattr(httpx, "Client", FakeClient)

        LLMRouter(temp_db_session, ws_id).route(
            task_type="chat",
            tier="cheap",
            prompt=f"Never send {secret_val} upstream",
            system_prompt=f"Sys {secret_val}",
            preferred_provider="freellmapi",
            allow_mock_fallback=False,
        )
        assert len(sent_bodies) == 1
        dumped = json.dumps(sent_bodies[0])
        assert secret_val not in dumped
        assert "[REDACTED]" in dumped
    finally:
        discard_vault_secret(secret_val)
