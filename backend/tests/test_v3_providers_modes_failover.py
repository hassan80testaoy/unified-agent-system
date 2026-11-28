"""Tests for V3 Zadloop 3-Way Providers, Fetch Models, Multi-Account Failover, and Modes."""

from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.providers import Provider, ProviderAccount
from app.services.llm_router import LLMRouter, ProviderUnavailableError


def test_zadloop_3way_providers_and_prod_guard(owner_session, monkeypatch):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # Custom endpoint requires base_url + models
    bad_custom = client.post(
        "/api/providers",
        headers=headers,
        json={"id": "bad-ep", "name": "Bad EP", "connect_type": "custom_endpoint", "api_base": "", "models": {}},
    )
    assert bad_custom.status_code == 400

    good_custom = client.post(
        "/api/providers",
        headers=headers,
        json={
            "id": "my-vllm",
            "name": "Local vLLM",
            "connect_type": "custom_endpoint",
            "protocol": "openai-completions",
            "path_id": "vllm-main",
            "api_base": "http://127.0.0.1:8000/v1",
            "models": {"cheap": "qwen2.5-7b"},
            "api_key": "tok_local_123456",
        },
    )
    assert good_custom.status_code == 200
    assert good_custom.json()["connect_type"] == "custom_endpoint"

    # Sign-in allowed in development
    signin_dev = client.post(
        "/api/providers",
        headers=headers,
        json={"id": "codex-local", "name": "Codex Local Plan", "connect_type": "signin"},
    )
    assert signin_dev.status_code == 200

    # Sign-in FORBIDDEN (403) in production
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()
    try:
        signin_prod = client.post(
            "/api/providers",
            headers=headers,
            json={"id": "codex-prod", "name": "Codex Prod", "connect_type": "signin"},
        )
        assert signin_prod.status_code == 403
        assert signin_prod.json()["detail"]["code"] == "signin_forbidden_in_production"
    finally:
        monkeypatch.setenv("ENV", "development")
        get_settings.cache_clear()


def test_fetch_models_from_endpoint(owner_session, monkeypatch):
    client = owner_session["client"]
    headers = owner_session["headers"]

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}

    async def _fake_get(self, url, **kwargs):
        return _FakeResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    res = client.post(
        "/api/providers/fetch-models",
        headers=headers,
        json={"api_base": "https://api.deepseek.com/v1", "protocol": "openai-completions"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert [m["id"] for m in body["models"]] == ["deepseek-chat", "deepseek-reasoner"]


def test_multi_account_auto_failover_and_manual_rotate(owner_session, temp_db_session: Session, monkeypatch):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    p_res = client.post(
        "/api/providers",
        headers=headers,
        json={
            "id": "pool-llm",
            "name": "Pool LLM",
            "connect_type": "api_key",
            "api_base": "https://pool.example.com/v1",
            "models": {"cheap": "pool-mini"},
            "enabled": True,
        },
    )
    assert p_res.status_code == 200

    a1 = client.post(
        "/api/providers/pool-llm/accounts",
        headers=headers,
        json={"label": "Primary Acct", "auth_type": "api_key", "api_key": "sk-primary-1111", "priority": 0},
    ).json()["account"]
    a2 = client.post(
        "/api/providers/pool-llm/accounts",
        headers=headers,
        json={"label": "Backup Acct", "auth_type": "api_key", "api_key": "sk-backup-2222", "priority": 1},
    ).json()["account"]

    # Force primary-1 HTTP failure -> router automatically rests primary-1 and finishes via backup-2!
    def _fake_http(provider, model, api_key, prompt, system_prompt, json_schema=None):
        if api_key == "sk-primary-1111":
            raise ProviderUnavailableError("rate_limit_429")
        return f"answered_with_{api_key[-4:]}", 10, 12

    monkeypatch.setattr("app.services.llm_router._call_provider_http", _fake_http)

    router = LLMRouter(temp_db_session, ws_id)
    out = router.route(
        task_type="chat",
        tier="cheap",
        prompt="hello failover",
        preferred_provider="pool-llm",
        use_cache=False,
        allow_mock_fallback=False,
    )
    assert out["raw_text"] == "answered_with_2222"
    assert out["account_id"] == a2["id"]
    temp_db_session.commit()

    refreshed_a1 = temp_db_session.get(ProviderAccount, a1["id"])
    assert refreshed_a1 is not None and refreshed_a1.status == "resting"

    # Manual failover endpoint rests backup-2 as well
    rot = client.post("/api/providers/pool-llm/failover", headers=headers)
    assert rot.status_code == 200
    assert rot.json()["rested_account_id"] == a2["id"]


def test_modes_builtin_custom_and_project_override(owner_session):
    client = owner_session["client"]
    headers = owner_session["headers"]

    res = client.get("/api/modes", headers=headers)
    assert res.status_code == 200
    modes = res.json()["modes"]
    keys = {m["key"] for m in modes}
    assert {"delegate", "standard", "minimal", "build", "plan", "red-team"}.issubset(keys)
    defaults = [m["key"] for m in modes if m["is_default"]]
    assert defaults == ["delegate"]

    created = client.post(
        "/api/modes",
        headers=headers,
        json={"key": "seo-audit", "name_ar": "تدقيق السيو", "name_en": "SEO Audit", "sandbox_mode": "read-only"},
    )
    assert created.status_code == 200

    # Set project mode referencing an unknown/disabled provider -> degrades cleanly with notice
    proj = client.put(
        "/api/projects/client-a/mode",
        headers=headers,
        json={
            "mode_key": "seo-audit",
            "model_provider_id": "nonexistent-prov",
            "delegation_lanes": {"coder": "groq-free"},
        },
    )
    assert proj.status_code == 200
    p_body = proj.json()
    assert p_body["mode_key"] == "seo-audit"
    assert p_body["model_provider_id"] is None
    assert p_body["fallback_notice"] is not None
