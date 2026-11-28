"""Tests for Phase 3 §0A Mock Honesty, §0B Delegation/Local Providers, §0C General Scope & data/accounts.xlsx."""

from __future__ import annotations

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.core import AuditLog, WorkspaceMember
from app.models.providers import Provider
from app.models.v3_harness import ChatMessage
from app.services.accounts_sheet import get_accounts_spreadsheet_path
from app.services.intent_router import classify_intent
from app.services.llm_router import LLMRouter
from app.services.runtime_checks import is_local_endpoint, validate_real_email


def test_mock_honesty_placeholder_email_and_real_mode_guard(owner_session, temp_db_session: Session):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # Runtime status reflects missing real requirements
    rs = client.get("/api/system/runtime-status", headers=headers)
    assert rs.status_code == 200
    assert rs.json()["default_mock_mode"] is True
    assert "browser_worker" in rs.json()["missing_keys"]

    # Placeholder emails rejected cleanly before run
    for bad in ("name@gmail.com", "test@domain.com", "seo@example.com", "user@yahoo.com"):
        ok, err = validate_real_email(bad)
        assert ok is False and err

    th = client.post("/api/chat/threads", headers=headers, json={"title": "Honesty Test"}).json()["thread"]
    tid = th["id"]

    # Reject placeholder email in chat backlink request
    bad_msg = client.post(
        f"/api/chat/threads/{tid}/messages",
        headers=headers,
        json={"content": "سجل لي حساب في موقع medium.com بالإيميل name@gmail.com واعمل باك لينك", "mock_mode": True},
    )
    assert bad_msg.status_code == 200
    assert "Placeholder" in bad_msg.json()["assistant_message"]["content"] or "وهمي" in bad_msg.json()["assistant_message"]["content"]

    # Mock mode explicitly labels output as simulation and NEVER presents mock:// as a live link
    sim_msg = client.post(
        f"/api/chat/threads/{tid}/messages",
        headers=headers,
        json={"content": "سجل لي حساب في موقع medium.com بالإيميل moaaz.real@mydomain.io واعمل باك لينك", "mock_mode": True},
    )
    assert sim_msg.status_code == 200
    sim_body = sim_msg.json()
    assert sim_body["mock_mode"] is True
    assert sim_body["assistant_message"]["content"].startswith("محاكاة — ")
    assert "محاكاة، ليس رابطاً حياً" in sim_body["assistant_message"]["content"]
    assert sim_body["assistant_message"]["card_payload"]["is_mock"] is True

    # Real toggle (mock_mode: false) sends mock_mode=False and stops honestly on missing pieces
    real_msg = client.post(
        f"/api/chat/threads/{tid}/messages",
        headers=headers,
        json={"content": "سجل لي حساب في موقع about.me بالإيميل moaaz.real@mydomain.io واعمل باك لينك", "mock_mode": False},
    )
    assert real_msg.status_code == 200
    real_body = real_msg.json()
    assert real_body["mock_mode"] is False
    assert real_body["assistant_message"]["card_payload"]["status"] == "missing_requirements"


def test_orchestrator_equals_worker_allowed_reviewer_separated(owner_session):
    client = owner_session["client"]
    headers = owner_session["headers"]

    client.post("/api/agents/discover", headers=headers)
    agents = client.get("/api/agents", headers=headers).json()["agents"]
    backlink_id = next(a["id"] for a in agents if a["key"] == "backlink")

    # Orchestrator == worker is ALLOWED (§0B.1); Reviewer must still differ
    ok_res = client.put(
        f"/api/agents/{backlink_id}/providers",
        headers=headers,
        json={
            "orchestrator": {"provider": "groq-free", "tier": "strong"},
            "workers": {"bio": {"provider": "groq-free", "tier": "mid"}},
            "reviewer": {"provider": "openrouter-free", "tier": "mid"},
        },
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["orchestrator"]["provider"] == "groq-free"
    assert ok_res.json()["workers"]["bio"]["provider"] == "groq-free"

    # Reviewer == worker is STILL rejected
    bad_res = client.put(
        f"/api/agents/{backlink_id}/providers",
        headers=headers,
        json={
            "orchestrator": {"provider": "groq-free", "tier": "strong"},
            "workers": {"bio": {"provider": "openrouter-free", "tier": "mid"}},
            "reviewer": {"provider": "openrouter-free", "tier": "mid"},
        },
    )
    assert bad_res.status_code == 400


def test_local_aggregated_providers_and_vps_isolation(owner_session, temp_db_session: Session, monkeypatch):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    provs = client.get("/api/providers", headers=headers).json()["providers"]
    by_id = {p["id"]: p for p in provs}
    assert "freellmapi" in by_id
    assert "free-claude-code" in by_id
    assert by_id["freellmapi"]["local_only"] is True
    assert by_id["free-claude-code"]["local_only"] is True
    assert is_local_endpoint("http://localhost:3001/v1") is True
    assert is_local_endpoint("https://api.openai.com/v1") is False

    # Custom local endpoint answers chat in local development mode
    add_res = client.post(
        "/api/providers",
        headers=headers,
        json={
            "id": "local-fcc-test",
            "name": "Local FCC Test",
            "connect_type": "custom_endpoint",
            "protocol": "openai-completions",
            "api_base": "http://127.0.0.1:8082/v1",
            "models": {"cheap": "openai/gpt-5.1"},
            "api_key": "fcc-test-token-1234",
            "enabled": True,
        },
    )
    assert add_res.status_code == 200

    monkeypatch.setattr(
        "app.services.llm_router._call_provider_http",
        lambda prov, model, key, p, s, j=None: (f"replied_via_{prov.id}", 5, 6),
    )
    router = LLMRouter(temp_db_session, ws_id)
    dev_out = router.route(
        task_type="chat",
        tier="cheap",
        prompt="hello",
        preferred_provider="local-fcc-test",
        use_cache=False,
        allow_mock_fallback=False,
    )
    assert dev_out["provider_id"] == "local-fcc-test"

    # In production/VPS mode, localhost/local providers are ignored by router
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()
    try:
        cands = router._candidate_providers(tier="cheap", preferred_provider_id="local-fcc-test")
        assert all(not is_local_endpoint(c.api_base) and c.kind != "llm-local" for c in cands)
    finally:
        monkeypatch.setenv("ENV", "development")
        get_settings.cache_clear()


def test_general_scope_non_seo_and_accounts_spreadsheet_dedup(owner_session, temp_db_session: Session):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # Non-SEO requests route cleanly and never reply "SEO only"
    assert classify_intent("build me a small todo app")["intent"] == "coding"
    th_id = client.post("/api/chat/threads", headers=headers, json={"title": "General"}).json()["thread"]["id"]

    non_seo = client.post(
        f"/api/chat/threads/{th_id}/messages",
        headers=headers,
        json={"content": "build me a small todo app", "mock_mode": True},
    )
    assert non_seo.status_code == 200
    assert non_seo.json()["intent"] == "coding"
    assert "only does SEO" not in non_seo.json()["assistant_message"]["content"]

    # Accounts spreadsheet recorded & deduplicated on re-run
    for _ in range(2):
        res = client.post(
            f"/api/chat/threads/{th_id}/messages",
            headers=headers,
            json={"content": "سجل في موقع carrd.co بالإيميل moaaz.sheet@mydomain.io واعمل باك لينك", "mock_mode": True},
        )
        assert res.status_code == 200

    xlsx_path = get_accounts_spreadsheet_path()
    assert xlsx_path.exists()
    wb = load_workbook(xlsx_path)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("site", "email_username", "password", "profile_url", "created_at", "notes")
    carrd_rows = [r for r in rows[1:] if r[0] == "carrd.co" and r[1] == "moaaz.sheet@mydomain.io"]
    assert len(carrd_rows) == 1  # No duplicate rows
    recorded_pw = str(carrd_rows[0][2])
    assert len(recorded_pw) >= 8

    # Password NEVER leaked into ChatMessage or AuditLog
    all_msgs = " ".join(m.content for m in temp_db_session.query(ChatMessage).all())
    assert recorded_pw not in all_msgs

    # Owner can download spreadsheet and action is audited
    dl = client.get("/api/backlinks/accounts/spreadsheet", headers=headers)
    assert dl.status_code == 200
    assert len(dl.content) > 100
    assert (
        temp_db_session.query(AuditLog)
        .filter(AuditLog.action == "backlinks.accounts_spreadsheet.download")
        .count()
        == 1
    )
