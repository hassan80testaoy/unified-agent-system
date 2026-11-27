"""Tests for multi-provider registry, per-agent delegation, reviewer separation, budget hard stops, and caching."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.agents import AgentModel
from app.models.providers import LlmCall, Provider
from app.services.llm_router import (
    BudgetExceededError,
    LLMRouter,
    ReviewerProviderConflictError,
)


def test_provider_crud_delegation_and_reviewer_separation(owner_session, temp_db_session: Session):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # Add a custom provider via API without code deploy
    add_res = client.post(
        "/api/providers",
        headers=headers,
        json={
            "id": "custom-mock-2",
            "name": "Second Mock Provider",
            "kind": "llm-mock",
            "api_base": "local://mock2",
            "models": {"strong": "mock-strong-v1", "mid": "mock-mid-v1", "cheap": "mock-cheap-v1"},
            "budget_usd_daily": 1.0,
            "enabled": True,
        },
    )
    assert add_res.status_code == 200

    agents_res = client.get("/api/agents", headers=headers).json()
    backlink_agent = next(a for a in agents_res["agents"] if a["key"] == "backlink")

    # Setting reviewer to the SAME provider as orchestrator must fail
    bad_assign = client.put(
        f"/api/agents/{backlink_agent['id']}/providers",
        headers=headers,
        json={
            "orchestrator": {"provider": "mock-local", "tier": "strong"},
            "workers": {"bio": {"provider": "mock-local", "tier": "mid"}},
            "reviewer": {"provider": "mock-local", "tier": "mid"},
        },
    )
    assert bad_assign.status_code == 400
    assert bad_assign.json()["detail"]["code"] == "reviewer_provider_conflict"

    # Setting reviewer to a DIFFERENT provider succeeds
    good_assign = client.put(
        f"/api/agents/{backlink_agent['id']}/providers",
        headers=headers,
        json={
            "orchestrator": {"provider": "mock-local", "tier": "strong"},
            "workers": {"bio": {"provider": "mock-local", "tier": "mid"}},
            "reviewer": {"provider": "custom-mock-2", "tier": "mid"},
        },
    )
    assert good_assign.status_code == 200
    assert good_assign.json()["reviewer"]["provider"] == "custom-mock-2"

    # Runtime review_with_different_provider uses a different provider than producer
    ws_id = owner_session["data"]["workspace"]["id"]
    router = LLMRouter(temp_db_session, ws_id)
    review_res = router.review_with_different_provider(
        producer_provider="mock-local",
        content_to_review="خبير سيو معاذ حسن — دكتور السيو",
        criteria="Check Arabic SEO bio quality",
        agent_id=backlink_agent["id"],
    )
    assert review_res["provider_id"] != "mock-local"
    assert review_res["output"]["approved"] is True


def test_router_budget_hard_stop_and_cheap_cache(owner_session, temp_db_session: Session):
    ws_id = owner_session["data"]["workspace"]["id"]
    router = LLMRouter(temp_db_session, ws_id)

    # First cheap call + second identical call hits cache
    res1 = router.route(task_type="classify", tier="cheap", prompt="Check noindex for example.org", use_cache=True)
    res2 = router.route(task_type="classify", tier="cheap", prompt="Check noindex for example.org", use_cache=True)
    assert res1["cached"] is False
    assert res2["cached"] is True

    # Simulate a paid LLM provider that already hit its daily budget
    paid = Provider(
        id="paid-test-llm",
        workspace_id=ws_id,
        name="Paid Test LLM",
        kind="llm",
        api_base="https://api.example.com/v1",
        auth_ref="env:NONEXISTENT_KEY",
        models_json={"strong": "gpt-4.1", "mid": "gpt-4.1-mini", "cheap": "gpt-4.1-nano"},
        budget_usd_daily=1.0,
        budget_usd_monthly=10.0,
        enabled=True,
    )
    temp_db_session.add(paid)
    # Disable mock-local & ollama-local so only paid-test-llm is active for this test
    for p in temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id != "paid-test-llm").all():
        p.enabled = False
    temp_db_session.add(
        LlmCall(
            workspace_id=ws_id,
            provider_id="paid-test-llm",
            model="gpt-4.1",
            tier="strong",
            task_type="test",
            tokens_in=100,
            tokens_out=100,
            cost_usd=1.25,
            latency_ms=10,
            ok=True,
        )
    )
    temp_db_session.commit()

    with pytest.raises(BudgetExceededError):
        router.route(
            task_type="test",
            tier="strong",
            prompt="Should stop immediately",
            preferred_provider="paid-test-llm",
            use_cache=False,
            allow_mock_fallback=False,
        )
