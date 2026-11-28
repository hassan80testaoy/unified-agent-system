"""Phase 7 tests: §7C Hermes Routing + Wizard Backup/Restore + Planner-Brain, §7D Hypnos (OFF default + Opt-in + Exam + Unlearn), §7E Harness Patterns."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models.phase7 import HypnosLoraVersion, RoutingConfigBackup
from app.models.providers import LlmCall, Provider
from app.services.llm_router import LLMRouter
from app.services.mcp_service import configure_mcp_oauth


def test_hermes_routing_profiles_coding_and_research_verified_in_llm_calls_and_wizard_backup_restore(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    # Enable openrouter-free & openai providers for realistic multi-provider profile assignment
    p_code = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "openai").first()
    p_res = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "openrouter-free").first()
    assert p_code is not None and p_res is not None
    p_code.enabled = True
    p_res.enabled = True
    temp_db_session.commit()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-coding-only-placeholder")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-research-only-placeholder")

    # Configure profiles via wizard -> MUST create a config backup BEFORE editing
    wiz = client.post(
        "/api/routing/wizard/configure-model-routing",
        json={
            "fetch_live_models": False,
            "overrides": {
                "coding": {"provider_id": "openai", "model": "gpt-4o-mini", "tier": "mid"},
                "research": {"provider_id": "openrouter-free", "model": "nvidia/nemotron-3.5-lightning:free", "tier": "mid"},
            },
        },
        headers=headers,
    )
    assert wiz.status_code == 200
    wb = wiz.json()
    backup_id = wb["backup_id"]
    assert Path(wb["backup_file"]).exists()
    bk_row = temp_db_session.get(RoutingConfigBackup, backup_id)
    assert bk_row is not None
    # Backup snapshot captured BEFORE wizard overrides were applied ("auto" model originally)
    snap_coding = next(p for p in bk_row.snapshot_json["profiles"] if p["context_key"] == "coding")
    assert snap_coding["model"] == "auto"

    wire_calls: list[tuple[str, str]] = []

    def fake_http(provider, model, api_key, prompt, system_prompt, json_schema=None):
        wire_calls.append((provider.id, model))
        return (f"ok:{provider.id}:{model}", 18, 9)

    monkeypatch.setattr("app.services.llm_router._call_provider_http", fake_http)

    router = LLMRouter(temp_db_session, ws_id)
    res_coding = router.route(task_type="general_work", toolsets=["terminal", "file"], prompt="Refactor module", use_cache=False)
    res_research = router.route(task_type="general_work", toolsets=["web", "browser"], prompt="Browse docs", use_cache=False)
    temp_db_session.commit()

    assert res_coding["routing_profile"] == "coding"
    assert res_coding["provider_id"] == "openai"
    assert res_coding["model"] == "gpt-4o-mini"
    assert res_research["routing_profile"] == "research"
    assert res_research["provider_id"] == "openrouter-free"
    assert res_research["model"] == "nvidia/nemotron-3.5-lightning:free"

    # Verified directly in llm_calls table!
    db_calls = temp_db_session.query(LlmCall).filter(LlmCall.workspace_id == ws_id).order_by(LlmCall.created_at.asc()).all()
    assert any(c.route_id == "profile:coding" and c.provider_id == "openai" and c.model == "gpt-4o-mini" for c in db_calls)
    assert any(c.route_id == "profile:research" and c.provider_id == "openrouter-free" and c.model == "nvidia/nemotron-3.5-lightning:free" for c in db_calls)

    # Restore backup rolls profiles cleanly back to pre-wizard snapshot
    rest = client.post(f"/api/routing/backups/{backup_id}/restore", headers=headers)
    assert rest.status_code == 200
    rest_coding = next(p for p in rest.json()["profiles"] if p["context_key"] == "coding")
    assert rest_coding["model"] == "auto"


def test_planner_brain_strong_cheap_split_and_different_provider_reviewer(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    # Configure OpenAI (executor) and Anthropic (reviewer/strong fallback)
    for pid, env_k, val in (
        ("openai", "OPENAI_API_KEY", "sk-openai-placeholder"),
        ("anthropic", "ANTHROPIC_API_KEY", "sk-ant-placeholder"),
    ):
        prov = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == pid).first()
        if prov:
            prov.enabled = True
        monkeypatch.setenv(env_k, val)
    temp_db_session.commit()

    client.put("/api/routing/profiles/planning", json={"provider_id": "anthropic", "model": "claude-sonnet-4-5", "tier": "strong"}, headers=headers)
    client.put("/api/routing/profiles/chat", json={"provider_id": "openai", "model": "gpt-4o-mini", "tier": "cheap"}, headers=headers)

    def fake_http(provider, model, api_key, prompt, system_prompt, json_schema=None):
        if json_schema and "approved" in (json_schema.get("properties") or {}):
            return ('{"approved": true, "notes": "reviewed independently"}', 20, 12)
        return (f"result from {provider.id}/{model}", 120, 45)

    monkeypatch.setattr("app.services.llm_router._call_provider_http", fake_http)

    pb_res = client.post(
        "/api/routing/planner-brain/run",
        json={"task_name": "goal_planner", "goal_prompt": "Plan & draft SEO roadmap", "require_review": True},
        headers=headers,
    )
    assert pb_res.status_code == 200
    sp = pb_res.json()["split"]
    assert sp["used_split"] is True
    assert sp["planner"]["tier"] == "strong"
    assert sp["executor"]["tier"] == "cheap"
    assert sp["judge"]["tier"] == "strong"
    assert sp["reviewer"] is not None
    assert sp["reviewer"]["provider"] != sp["executor"]["provider"]

    stats_res = client.get("/api/routing/planner-brain/stats", headers=headers)
    assert stats_res.status_code == 200
    assert stats_res.json()["runs"] >= 1


def test_hypnos_off_by_default_blocks_training_and_opt_in_produces_lora_exam_and_exact_unlearn(
    owner_session: dict,
    temp_db_session: Session,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    # 1. OFF by default + refuses training while off
    st0 = client.get("/api/hypnos/status", headers=headers).json()
    assert st0["config"]["enabled"] is False
    assert st0["config"]["opt_in_required"] is True

    # Record daytime feedback while OFF
    ev_keep = client.post(
        "/api/hypnos/feedback",
        json={"kind": "approved", "context_summary": "Verified canonical rule", "correction_text": "Always check canonical tag"},
        headers=headers,
    ).json()["id"]
    ev_forget = client.post(
        "/api/hypnos/feedback",
        json={"kind": "corrected", "context_summary": "Sensitive mistake X", "correction_text": "Avoid sensitive habit X"},
        headers=headers,
    ).json()["id"]
    ev_cmd = client.post(
        "/api/hypnos/feedback",
        json={"kind": "cmd_passed", "context_summary": "Pytest check", "command_name": "pytest -q"},
        headers=headers,
    ).json()["id"]

    # Night run MUST refuse to train while OFF
    no_train = client.post("/api/hypnos/night-run", headers=headers)
    assert no_train.status_code == 200
    assert no_train.json()["trained"] is False
    assert temp_db_session.query(HypnosLoraVersion).filter(HypnosLoraVersion.workspace_id == ws_id).count() == 0

    # 2. Explicit Opt-In -> night cycle produces LoRA + held-out exam WITHOUT auto-replacing active brain
    client.patch("/api/hypnos/config", json={"enabled": True, "schedule_hour": 3}, headers=headers)
    night = client.post("/api/hypnos/night-run", headers=headers)
    assert night.status_code == 200
    nb = night.json()
    assert nb["trained"] is True
    assert nb["auto_replaced"] is False
    ver = nb["version"]
    assert ver["version_tag"] == "lora-v1"
    assert ver["exam_score"] > ver["baseline_score"]
    assert ver["exam_passed"] is True
    assert Path(ver["adapter_path"]).exists()
    # Active brain still None until promoted!
    assert client.get("/api/hypnos/status", headers=headers).json()["config"]["active_lora_version_id"] is None

    # 3. Promote shrinks prompt size cleanly
    prom = client.post(f"/api/hypnos/versions/{ver['id']}/promote", headers=headers)
    assert prom.status_code == 200
    cfg_after = prom.json()["config"]
    assert cfg_after["active_lora_version_id"] == ver["id"]
    assert cfg_after["prompt_tokens_after"] < cfg_after["prompt_tokens_before"]

    # 4. Exact Unlearning removes excluded event and retrains clean ancestor LoRA
    unl = client.post(
        "/api/hypnos/unlearn",
        json={"exclude_event_ids": [ev_forget]},
        headers=headers,
    )
    assert unl.status_code == 200
    ub = unl.json()
    assert ver["id"] in ub["invalidated_version_ids"]
    retrained = ub["retrained_version"]
    assert retrained is not None
    assert ev_forget not in retrained["training_event_ids"]
    assert ev_keep in retrained["training_event_ids"]
    assert ev_cmd in retrained["training_event_ids"]


def test_stats_detail_cli_delegation_fallback_mcp_oauth_refresh_and_unified_reviews(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    # §7E.1 Stats detail on replies + usage endpoint
    tid = client.post("/api/chat/threads", json={"title": "Stats test"}, headers=headers).json()["thread"]["id"]
    m_res = client.post(f"/api/chat/threads/{tid}/messages", json={"content": "مرحبا", "mock_mode": True}, headers=headers)
    assert m_res.status_code == 200
    st_obj = m_res.json()["assistant_message"]["card_payload"]["stats"]
    assert "tokens_in" in st_obj and "tokens_out" in st_obj and "context_usage_pct" in st_obj
    assert "model_latency_ms" in st_obj and "tool_latency_ms" in st_obj
    usage = client.get("/api/stats/usage", headers=headers)
    assert usage.status_code == 200 and usage.json()["total_calls"] >= 1

    # §7E.2 Coding Local-CLI delegation when logged in vs clean API fallback when absent
    cth = client.post("/api/threads", json={"title": "CLI Test", "workspace_dir": str(tmp_path)}, headers=headers).json()["thread"]

    monkeypatch.setattr(
        "app.services.coding_cli_delegation._probe_single_cli",
        lambda name: {"installed": True, "logged_in": True, "path": f"/usr/bin/{name}", "reason": "ready"}
        if name == "codex"
        else {"installed": False, "logged_in": False, "path": None, "reason": "not_in_path"},
    )
    monkeypatch.setattr(
        "app.services.coding_cli_delegation._run_local_cli_task",
        lambda tool_name, exe_path, cwd, prompt: f"CLI output by {tool_name}",
    )
    cli_sub = client.post(
        f"/api/threads/{cth['id']}/subagents",
        json={"role": "coder", "prompt": "Write helper", "delegate_cli": True, "preferred_cli": "codex"},
        headers=headers,
    )
    assert cli_sub.status_code == 200
    assert cli_sub.json()["delegation_mode"] == "local_cli"
    assert cli_sub.json()["cli_tool"] == "codex"

    # Now CLI unavailable -> clean API fallback (no crash)
    monkeypatch.setattr(
        "app.services.coding_cli_delegation._probe_single_cli",
        lambda name: {"installed": False, "logged_in": False, "path": None, "reason": "not_in_path"},
    )
    fb_sub = client.post(
        f"/api/threads/{cth['id']}/subagents",
        json={"role": "coder", "prompt": "Write helper 2", "delegate_cli": True},
        headers=headers,
    )
    assert fb_sub.status_code == 200
    assert fb_sub.json()["delegation_mode"] == "api_fallback"

    # §7E.3 MCP OAuth auto-refresh + Connectors summary
    from app.models.phase5 import McpServer

    srv = McpServer(
        workspace_id=ws_id,
        name="Remote OAuth MCP",
        source_type="link",
        url="https://mcp.example.com/rpc",
        namespace_prefix="remote_oauth",
        is_remote=True,
        tools_json=[{"name": "ping", "namespaced_name": "remote_oauth.ping", "description": "ping"}],
    )
    temp_db_session.add(srv)
    temp_db_session.flush()
    configure_mcp_oauth(
        temp_db_session,
        srv,
        token_url="https://auth.example.com/oauth/token",
        client_id="cid-1",
        refresh_token="rtok-old",
        access_token="expired-tok",
        expires_in_sec=0,
        auto_refresh=True,
    )
    temp_db_session.commit()

    seen_auth_headers: list[str] = []

    def fake_mcp_post(url: str, data=None, json=None, headers=None, timeout=None):
        req = httpx.Request("POST", url)
        if "oauth/token" in url:
            return httpx.Response(200, request=req, json={"access_token": "fresh-tok-777", "expires_in": 3600})
        seen_auth_headers.append((headers or {}).get("Authorization", ""))
        return httpx.Response(200, request=req, json={"jsonrpc": "2.0", "id": 2, "result": {"ok": True}})

    orig_client_post = httpx.Client.post

    def wrapped_client_post(self, u, **kw):
        if type(self) is not httpx.Client:
            return orig_client_post(self, u, **kw)
        return fake_mcp_post(str(u), **kw)

    monkeypatch.setattr("app.services.mcp_service.httpx.post", fake_mcp_post)
    monkeypatch.setattr("app.services.mcp_service.httpx.Client.post", wrapped_client_post)
    ref = client.post(f"/api/mcp/servers/{srv.id}/oauth/refresh", headers=headers)
    assert ref.status_code == 200
    assert ref.json()["oauth"]["refreshed"] is True
    assert client.get("/api/connectors/summary", headers=headers).json()["mcp_servers"][0]["oauth_auto_refresh"] is True

    # §7E.4 Unified Review Page (GitHub PRs / GitLab MRs + comment + approve)
    rev = client.post(
        "/api/reviews",
        json={
            "provider": "github",
            "repo": "hassan80testaoy/unified-agent-system",
            "external_number": 7,
            "title": "feat: phase 7",
            "diff_text": "+ print('phase7')",
            "checks": [{"name": "pytest", "status": "success"}],
        },
        headers=headers,
    ).json()["review"]
    cm = client.post(f"/api/reviews/{rev['id']}/comment", json={"body": "Looks solid"}, headers=headers)
    assert cm.status_code == 200 and len(cm.json()["review"]["comments"]) == 1
    ap = client.post(f"/api/reviews/{rev['id']}/approve", json={"note": "LGTM"}, headers=headers)
    assert ap.status_code == 200 and ap.json()["item"]["status"] == "approved"
