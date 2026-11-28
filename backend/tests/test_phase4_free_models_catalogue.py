"""Phase 4 tests: FreeLLMAPI + FCC Full Free-Model Catalogues, Honest Empty States & Per-Role Routing (§4A-§4C)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.providers import Provider, ProviderModel
from app.services.llm_router import LLMRouter
from app.services.model_catalogue import FREELLMAPI_EMPTY_AR, refresh_stale_catalogues_once
from app.services.runtime_checks import is_local_endpoint


def _fake_freellmapi_models(count: int = 120) -> list[dict]:
    items = [
        {"id": "auto", "owned_by": "freellmapi"},
        {"id": "groq/llama-3.3-70b-versatile", "owned_by": "groq"},
        {"id": "openrouter/deepseek/deepseek-r1:free", "owned_by": "openrouter"},
        {"id": "mistral/mistral-small", "owned_by": "mistral"},
    ]
    for i in range(count - len(items)):
        items.append({"id": f"family{i % 6}/free-model-{i:04d}", "owned_by": f"family{i % 6}"})
    return items


def test_freellmapi_auto_fetch_on_key_enable_pagination_and_refresh(owner_session, temp_db_session: Session, monkeypatch):
    client = owner_session["client"]
    headers = owner_session["headers"]

    seen_calls: list[dict] = []
    batch = _fake_freellmapi_models(120)

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"data": batch}

    async def _fake_get(self, url, **kwargs):
        seen_calls.append({"url": str(url), "headers": dict(kwargs.get("headers") or {})})
        return _FakeResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    # 1. Saving key automatically syncs full catalogue via /v1/models
    key_res = client.put(
        "/api/settings/keys/freellmapi",
        headers=headers,
        json={"secret": "flm_test_key_9999"},
    )
    assert key_res.status_code == 200, key_res.text
    body = key_res.json()
    assert body["catalogue"]["fetched_count"] == 120
    assert body["catalogue"]["total_count"] == 120
    assert any(c["url"] == "http://127.0.0.1:31415/v1/models" and "Bearer flm_test_key_9999" in c["headers"].get("Authorization", "") for c in seen_calls)

    # Tier defaults stay `auto:fast` / `auto`
    provs = {p["id"]: p for p in client.get("/api/providers", headers=headers).json()["providers"]}
    assert provs["freellmapi"]["models"]["cheap"] == "auto:fast"
    assert provs["freellmapi"]["models"]["mid"] == "auto"
    assert provs["freellmapi"]["models"]["strong"] == "auto"
    assert provs["freellmapi"]["catalogue"]["last_fetched_at"]

    # 2. Pagination + search returns slices cleanly (never all rows at once)
    p1 = client.get("/api/providers/freellmapi/catalogue?page=1&page_size=25", headers=headers)
    assert p1.status_code == 200
    d1 = p1.json()
    assert d1["total"] == 120
    assert len(d1["items"]) == 25
    assert d1["total_pages"] == 5

    search_res = client.get("/api/providers/freellmapi/catalogue?q=groq&page=1&page_size=25", headers=headers).json()
    assert search_res["total"] == 1
    assert search_res["items"][0]["id"] == "groq/llama-3.3-70b-versatile"

    # 3. Stale detection (>24h) + manual refresh updates timestamp & catalogue
    prov_row = temp_db_session.get(Provider, "freellmapi")
    assert prov_row is not None
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    cfg = dict(prov_row.config_json or {})
    cfg["catalogue"] = {**dict(cfg.get("catalogue") or {}), "last_fetched_at": old_ts}
    prov_row.config_json = cfg
    temp_db_session.commit()

    stale_page = client.get("/api/providers/freellmapi/catalogue", headers=headers).json()
    assert stale_page["summary"]["stale"] is True

    batch.append({"id": "cohere/command-a-free", "owned_by": "cohere"})
    ref_res = client.post("/api/providers/freellmapi/catalogue/refresh", headers=headers)
    assert ref_res.status_code == 200
    assert ref_res.json()["catalogue"]["fetched_count"] == 121
    assert ref_res.json()["catalogue"]["stale"] is False
    assert ref_res.json()["catalogue"]["last_fetched_at"] != old_ts

    # 4. Enabling also triggers auto-fetch cleanly
    seen_calls.clear()
    client.patch("/api/providers/freellmapi", headers=headers, json={"enabled": False})
    en_res = client.patch("/api/providers/freellmapi", headers=headers, json={"enabled": True})
    assert en_res.status_code == 200
    assert len(seen_calls) == 1


def test_freellmapi_honest_empty_state_when_no_keys(owner_session, temp_db_session: Session, monkeypatch):
    client = owner_session["client"]
    headers = owner_session["headers"]

    class _EmptyResp:
        status_code = 200

        def json(self):
            return {"data": []}

    async def _fake_get(self, url, **kwargs):
        return _EmptyResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    client.get("/api/providers", headers=headers)
    client.patch("/api/providers/freellmapi", headers=headers, json={"enabled": True})

    cat = client.get("/api/providers/freellmapi/catalogue", headers=headers).json()
    assert cat["total"] == 0
    assert cat["items"] == []
    assert cat["summary"]["empty_state_ar"] == FREELLMAPI_EMPTY_AR
    assert temp_db_session.query(ProviderModel).filter(ProviderModel.provider_id == "freellmapi").count() == 0


def test_fcc_fallback_manual_slugs_and_catalogue_fetch(owner_session, monkeypatch):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # Path A: FCC /v1/models returns 404 -> preserves 3 default tier models + manual slug fallback
    class _Resp404:
        status_code = 404

        def json(self):
            return {"detail": "Not Found"}

    async def _get_404(self, url, **kwargs):
        return _Resp404()

    monkeypatch.setattr(httpx.AsyncClient, "get", _get_404)
    res_404 = client.patch(
        "/api/providers/free-claude-code",
        headers=headers,
        json={"enabled": True, "api_key": "fcc-proxy-secret-1111"},
    )
    assert res_404.status_code == 200
    assert res_404.json()["catalogue"]["supports_models_endpoint"] is False

    provs = {p["id"]: p for p in client.get("/api/providers", headers=headers).json()["providers"]}
    assert provs["free-claude-code"]["models"] == {
        "cheap": "open_router/openrouter/free",
        "mid": "openai/gpt-5.1",
        "strong": "openai/gpt-5.1",
    }

    manual_res = client.put(
        "/api/providers/free-claude-code/catalogue/manual-slugs",
        headers=headers,
        json={"slugs_text": "groq/llama-3.3-70b-versatile\nopen_router/openrouter/free\nopenai/gpt-5.1"},
    )
    assert manual_res.status_code == 200
    assert manual_res.json()["catalogue"]["manual_count"] == 3

    cat_manual = client.get("/api/providers/free-claude-code/catalogue?q=llama", headers=headers).json()
    assert cat_manual["total"] == 1
    assert cat_manual["items"][0]["id"] == "groq/llama-3.3-70b-versatile"
    assert cat_manual["summary"]["fallback_note_ar"]

    # Path B: When FCC /v1/models returns 200, fetched models sync alongside manual entries
    class _Resp200:
        status_code = 200

        def json(self):
            return {"data": [{"id": "openai/gpt-5.1"}, {"id": "nvidia/deepseek-v3"}]}

    async def _get_200(self, url, **kwargs):
        return _Resp200()

    monkeypatch.setattr(httpx.AsyncClient, "get", _get_200)
    ref_ok = client.post("/api/providers/free-claude-code/catalogue/refresh", headers=headers)
    assert ref_ok.status_code == 200
    assert ref_ok.json()["ok"] is True
    assert ref_ok.json()["catalogue"]["supports_models_endpoint"] is True
    assert ref_ok.json()["catalogue"]["total_count"] >= 3


def test_tier_and_role_model_routing_sends_exact_model_on_wire(owner_session, temp_db_session: Session, monkeypatch):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    class _EmptyGet:
        status_code = 200

        def json(self):
            return {"data": [{"id": "auto"}, {"id": "groq/llama-3.3-70b-versatile"}, {"id": "mistral/mistral-small"}]}

    async def _fake_get(self, url, **kwargs):
        return _EmptyGet()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    client.put("/api/settings/keys/freellmapi", headers=headers, json={"secret": "flm_wire_key"})
    client.put("/api/settings/keys/openrouter-free", headers=headers, json={"secret": "or_wire_key"})

    # Reviewer conflict rejected (orchestrator == reviewer on freellmapi)
    bad_roles = client.put(
        "/api/settings/role-models",
        headers=headers,
        json={
            "roles": {
                "orchestrator": {"provider_id": "freellmapi", "tier": "strong", "model": "auto"},
                "worker": {"provider_id": "freellmapi", "tier": "mid", "model": "auto"},
                "reviewer": {"provider_id": "freellmapi", "tier": "mid", "model": "auto"},
            }
        },
    )
    assert bad_roles.status_code == 400
    assert bad_roles.json()["detail"]["code"] == "reviewer_provider_conflict"

    # Orchestrator == worker allowed; coding_planner uses specific Groq model; reviewer uses openrouter-free
    ok_roles = client.put(
        "/api/settings/role-models",
        headers=headers,
        json={
            "roles": {
                "orchestrator": {"provider_id": "freellmapi", "tier": "strong", "model": "auto"},
                "worker": {"provider_id": "freellmapi", "tier": "cheap", "model": "auto"},
                "reviewer": {"provider_id": "openrouter-free", "tier": "mid", "model": "openrouter/free"},
                "coding_planner": {"provider_id": "freellmapi", "tier": "strong", "model": "groq/llama-3.3-70b-versatile"},
            }
        },
    )
    assert ok_roles.status_code == 200

    # Mock wire HTTP POST to capture exact URL, headers, and model sent over the wire
    wire_calls: list[dict] = []

    class _FakePostResp:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "real-wire-reply"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            }

    def _fake_post(self, url, **kwargs):
        wire_calls.append({"url": str(url), "headers": dict(kwargs.get("headers") or {}), "json": dict(kwargs.get("json") or {})})
        return _FakePostResp()

    monkeypatch.setattr(httpx.Client, "post", _fake_post)
    router = LLMRouter(temp_db_session, ws_id)

    # 1. coding_planner role -> exact selected Groq model via FreeLLMAPI
    res_planner = router.route(
        task_type="coding_planner",
        tier="strong",
        prompt="plan architecture",
        use_cache=False,
        allow_mock_fallback=False,
    )
    assert res_planner["provider_id"] == "freellmapi"
    assert res_planner["model"] == "groq/llama-3.3-70b-versatile"
    assert res_planner["output"] == "real-wire-reply"
    assert wire_calls[-1]["url"] == "http://127.0.0.1:31415/v1/chat/completions"
    assert wire_calls[-1]["json"]["model"] == "groq/llama-3.3-70b-versatile"

    # 2. worker/chat role -> stays `auto` default
    res_worker = router.route(
        task_type="chat",
        tier="cheap",
        prompt="hello worker",
        use_cache=False,
        allow_mock_fallback=False,
    )
    assert res_worker["provider_id"] == "freellmapi"
    assert res_worker["model"] == "auto"
    assert wire_calls[-1]["json"]["model"] == "auto"
    temp_db_session.commit()

    # 3. Direct tier update on freellmapi works when calling with explicit provider & default model lookup
    tier_upd = client.put(
        "/api/providers/freellmapi/tiers",
        headers=headers,
        json={"cheap": "mistral/mistral-small"},
    )
    assert tier_upd.status_code == 200
    assert tier_upd.json()["models"]["cheap"] == "mistral/mistral-small"
    res_tier = router.route(
        task_type="healthcheck",
        tier="cheap",
        prompt="ping",
        preferred_provider="freellmapi",
        use_cache=False,
        allow_mock_fallback=False,
    )
    assert res_tier["model"] == "mistral/mistral-small"
    assert wire_calls[-1]["json"]["model"] == "mistral/mistral-small"


def test_local_only_badge_and_production_filter_intact(owner_session, temp_db_session: Session, monkeypatch):
    import asyncio
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    provs = {p["id"]: p for p in client.get("/api/providers", headers=headers).json()["providers"]}
    assert provs["freellmapi"]["local_only"] is True
    assert provs["free-claude-code"]["local_only"] is True

    client.patch("/api/providers/freellmapi", headers=headers, json={"enabled": True})
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()
    try:
        router = LLMRouter(temp_db_session, ws_id)
        cands = router._candidate_providers(tier="cheap")
        assert all(c.id not in {"freellmapi", "free-claude-code"} for c in cands)
        assert all(not is_local_endpoint(c.api_base) for c in cands)
        refreshed = asyncio.run(refresh_stale_catalogues_once(temp_db_session))
        assert "freellmapi" not in refreshed
        assert "free-claude-code" not in refreshed
    finally:
        monkeypatch.setenv("ENV", "development")
        get_settings.cache_clear()
