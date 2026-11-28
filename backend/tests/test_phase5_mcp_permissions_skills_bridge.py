"""Phase 5 Tests (§5B): MCP 3-way add, Exact Session Permissions, Skills Store, and StreamableHTTP Bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.phase5 import McpServer
from app.models.v3_harness import ChatThread
from app.services.chat_agent import undo_thread
from app.services.coding_agent import resume_interrupted_threads
from app.services.mcp_service import (
    PermissionRevokedError,
    build_sanitized_worker_env,
    execute_mcp_tool,
    revoke_mcp_permission,
)
from app.services.session_log import read_log


def _write_stdio_mcp_script(tmp_path: Path) -> Path:
    script = tmp_path / "echo_mcp_server.py"
    script.write_text(
        """import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    mid = req.get("id")
    m = req.get("method")
    if m == "tools/list":
        tools = [
            {"name": "echo", "description": "Echo input safely", "read_only": False, "inputSchema": {"type": "object"}},
            {"name": "read_info", "description": "Read-only info tool", "readOnly": True, "inputSchema": {"type": "object"}},
        ]
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}) + "\\n")
        sys.stdout.flush()
    elif m == "tools/call":
        args = (req.get("params") or {}).get("arguments") or {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"echoed": args}}) + "\\n")
        sys.stdout.flush()
""",
        encoding="utf-8",
    )
    return script


def test_mcp_3way_add_namespace_graceful_failure_and_session_log(
    owner_session: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # Create chat thread for session log verification
    th = client.post("/api/chat/threads", json={"title": "اختبار MCP"}, headers=headers).json()["thread"]
    sid = th["id"]

    # Invalid namespace prefix rejected (400)
    bad_ns = client.post(
        "/api/mcp/servers",
        json={
            "source_type": "command",
            "name": "bad",
            "command": f'"{sys.executable}" -V',
            "namespace_prefix": "invalid namespace!",
        },
        headers=headers,
    )
    assert bad_ns.status_code == 400

    # 1) Add by Command (real stdio JSON-RPC server)
    script = _write_stdio_mcp_script(tmp_path)
    cmd_str = f'"{sys.executable}" "{script.as_posix()}"'
    cmd_resp = client.post(
        "/api/mcp/servers",
        json={
            "source_type": "command",
            "name": "Echo Local",
            "command": cmd_str,
            "env": {"DEMO_ENV": "val"},
            "namespace_prefix": "echo_srv",
            "working_dir": str(tmp_path),
            "session_id": sid,
        },
        headers=headers,
    )
    assert cmd_resp.status_code == 200, cmd_resp.text
    srv_cmd = cmd_resp.json()["server"]
    assert srv_cmd["status"] == "active"
    assert srv_cmd["env_keys"] == ["DEMO_ENV"]
    namespaced = [t["namespaced_name"] for t in srv_cmd["tools"]]
    assert "echo_srv__echo" in namespaced
    assert "echo_srv__read_info" in namespaced

    # Session log must contain mcp_tools_loaded
    entries = read_log(sid)
    assert any(e.get("type") == "mcp_tools_loaded" and "echo_srv__echo" in (e.get("tools") or []) for e in entries)

    # 2) Add by Link (mocked httpx.Client.post only for external example.invalid URL)
    import httpx
    orig_post = httpx.Client.post

    class _FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "ping", "description": "Remote ping", "read_only": True}]}}

    def _wrapped_post(self_client, url, *a, **kw):
        if "example.invalid" in str(url):
            return _FakeResp()
        return orig_post(self_client, url, *a, **kw)

    monkeypatch.setattr(httpx.Client, "post", _wrapped_post)
    link_resp = client.post(
        "/api/mcp/servers",
        json={
            "source_type": "link",
            "name": "Remote Ping",
            "url": "https://example.invalid/mcp",
            "namespace_prefix": "remote_mcp",
            "scope": "project",
            "project_key": "proj-alpha",
        },
        headers=headers,
    )
    assert link_resp.status_code == 200, link_resp.text
    srv_link = link_resp.json()["server"]
    assert srv_link["status"] == "active"
    assert srv_link["is_remote"] is True
    assert srv_link["tools"][0]["namespaced_name"] == "remote_mcp__ping"

    # 3) Add by Paste JSON (mcpServers) including a failing command -> graceful "unavailable" without breaking chat
    raw_json = json.dumps(
        {
            "mcpServers": {
                "broken_tool": {
                    "command": f'"{sys.executable}" -c "import sys; sys.exit(1)"',
                    "env": {"FOO": "bar"},
                }
            }
        }
    )
    json_resp = client.post(
        "/api/mcp/servers",
        json={"source_type": "json", "raw_json": raw_json},
        headers=headers,
    )
    assert json_resp.status_code == 200, json_resp.text
    created = json_resp.json()["servers"]
    assert len(created) == 1
    assert created[0]["status"] == "unavailable"
    assert created[0]["tools"] == []

    # Chat endpoint keeps working with HTTP 200 despite broken MCP server
    chat_resp = client.post(f"/api/chat/threads/{sid}/messages", json={"content": "مرحبا", "mock_mode": True}, headers=headers)
    assert chat_resp.status_code == 200

    # /api/mcp/tools exposes active namespaced tools
    tools_resp = client.get("/api/mcp/tools", headers=headers)
    assert tools_resp.status_code == 200
    all_tool_names = {t["namespaced_name"] for t in tools_resp.json()["tools"]}
    assert {"echo_srv__echo", "echo_srv__read_info", "remote_mcp__ping"}.issubset(all_tool_names)


def test_exact_session_permissions_revocation_worker_isolation_and_sanitized_env(
    owner_session: dict,
    temp_db_session: Session,
    tmp_path: Path,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    th1 = client.post("/api/chat/threads", json={"title": "جلسة رئيسية"}, headers=headers).json()["thread"]
    th2 = client.post("/api/chat/threads", json={"title": "جلسة متفرعة"}, headers=headers).json()["thread"]
    sid1, sid2 = th1["id"], th2["id"]

    script = _write_stdio_mcp_script(tmp_path)
    srv = client.post(
        "/api/mcp/servers",
        json={"source_type": "command", "name": "PermSrv", "command": f'"{sys.executable}" "{script.as_posix()}"', "namespace_prefix": "perm_srv"},
        headers=headers,
    ).json()["server"]
    srv_id = srv["id"]

    # Unpermitted main agent gets requires_approval=True (allowed=False)
    unperm = client.post(
        "/api/mcp/tools/execute",
        json={"session_id": sid1, "server_id": srv_id, "tool_name": "perm_srv__echo", "arguments": {"hi": 1}},
        headers=headers,
    )
    assert unperm.status_code == 200
    assert unperm.json()["allowed"] is False
    assert unperm.json()["requires_approval"] is True

    # Delegated worker NEVER asks approval & ignores allow_once
    once_grant = client.post(
        "/api/mcp/permissions",
        json={"session_id": sid1, "server_id": srv_id, "tool_name": "perm_srv__echo", "grant_mode": "allow_once"},
        headers=headers,
    )
    assert once_grant.status_code == 200
    worker_once = client.post(
        "/api/mcp/tools/execute",
        json={
            "session_id": sid1,
            "server_id": srv_id,
            "tool_name": "perm_srv__echo",
            "arguments": {"w": 1},
            "is_delegated_worker": True,
        },
        headers=headers,
    )
    assert worker_once.status_code == 200
    assert worker_once.json()["allowed"] is False
    assert worker_once.json()["requires_approval"] is False
    assert worker_once.json()["reason"] == "allow_once_excluded_for_worker"

    # Main agent uses allow_once and consumes it cleanly
    main_once = client.post(
        "/api/mcp/tools/execute",
        json={"session_id": sid1, "server_id": srv_id, "tool_name": "perm_srv__echo", "arguments": {"x": 42}},
        headers=headers,
    )
    assert main_once.status_code == 200
    assert main_once.json()["ok"] is True
    assert main_once.json()["result"] == {"echoed": {"x": 42}}

    # Second call needs approval again
    after_once = client.post(
        "/api/mcp/tools/execute",
        json={"session_id": sid1, "server_id": srv_id, "tool_name": "perm_srv__echo", "arguments": {}},
        headers=headers,
    )
    assert after_once.json()["allowed"] is False
    assert after_once.json()["requires_approval"] is True

    # Grant remembered permission on sid1
    rem_grant = client.post(
        "/api/mcp/permissions",
        json={"session_id": sid1, "server_id": srv_id, "tool_name": "perm_srv__echo", "grant_mode": "remembered"},
        headers=headers,
    ).json()["permission"]
    perm_id = rem_grant["id"]

    # New / forked session sid2 starts with ZERO grants!
    forked_try = client.post(
        "/api/mcp/tools/execute",
        json={"session_id": sid2, "server_id": srv_id, "tool_name": "perm_srv__echo", "arguments": {}},
        headers=headers,
    )
    assert forked_try.json()["allowed"] is False
    assert forked_try.json()["requires_approval"] is True

    # Remembered permission survives undo_thread and resume_interrupted_threads on sid1
    thread_row = temp_db_session.get(ChatThread, sid1)
    assert thread_row is not None
    undo_thread(temp_db_session, thread_row)
    resume_interrupted_threads(temp_db_session)
    temp_db_session.commit()

    # Delegator allow_list filter admits NO MCP tool
    allowlist_try = client.post(
        "/api/mcp/tools/execute",
        json={
            "session_id": sid1,
            "server_id": srv_id,
            "tool_name": "perm_srv__echo",
            "is_delegated_worker": True,
            "delegation_filters": [{"allow_list": ["*"]}],
        },
        headers=headers,
    )
    assert allowlist_try.json()["allowed"] is False
    assert allowlist_try.json()["reason"] == "allowlist_admits_no_mcp_tool"

    # Delegated worker succeeds with remembered permission & logs to session NDJSON
    worker_ok = client.post(
        "/api/mcp/tools/execute",
        json={
            "session_id": sid1,
            "server_id": srv_id,
            "tool_name": "perm_srv__echo",
            "arguments": {"worker": True},
            "is_delegated_worker": True,
        },
        headers=headers,
    )
    assert worker_ok.status_code == 200
    assert worker_ok.json()["ok"] is True
    assert any(e.get("type") == "mcp_tool_call" and e.get("tool") == "perm_srv__echo" for e in read_log(sid1))

    # Mutating tool definition_hash invalidates remembered grant -> requires_approval=True
    srv_row = temp_db_session.get(McpServer, srv_id)
    assert srv_row is not None
    orig_tools = list(srv_row.tools_json)
    mutated = [dict(t) for t in orig_tools]
    mutated[0]["definition_hash"] = "mutated_hash_000"
    srv_row.tools_json = mutated
    temp_db_session.commit()

    mutated_try = client.post(
        "/api/mcp/tools/execute",
        json={"session_id": sid1, "server_id": srv_id, "tool_name": "perm_srv__echo", "arguments": {}},
        headers=headers,
    )
    assert mutated_try.json()["allowed"] is False
    assert mutated_try.json()["requires_approval"] is True

    # Restore hash, test live revocation stops active worker immediately via PermissionRevokedError
    srv_row.tools_json = orig_tools
    temp_db_session.commit()

    def _revoke_midflight():
        revoke_mcp_permission(temp_db_session, ws_id, perm_id)
        temp_db_session.commit()

    with pytest.raises(PermissionRevokedError):
        execute_mcp_tool(
            temp_db_session,
            workspace_id=ws_id,
            session_id=sid1,
            server=srv_row,
            tool_name="perm_srv__echo",
            arguments={"should": "abort"},
            is_delegated_worker=True,
            on_before_dispatch=_revoke_midflight,
        )

    # Read-only auto-run mode permits read_info without explicit session grant
    set_ro = client.put("/api/mcp/settings", json={"read_only_auto_allow": True}, headers=headers)
    assert set_ro.status_code == 200
    assert set_ro.json()["read_only_auto_allow"] is True

    ro_exec = client.post(
        "/api/mcp/tools/execute",
        json={"session_id": sid2, "server_id": srv_id, "tool_name": "perm_srv__read_info", "arguments": {"q": "status"}},
        headers=headers,
    )
    assert ro_exec.status_code == 200
    assert ro_exec.json()["ok"] is True
    assert ro_exec.json()["grant_mode"] == "read_only_auto"

    # Worker env sanitizer strips MCP configs, keys, tokens, secrets
    clean_env = build_sanitized_worker_env(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "MCP_SERVER_TOKEN": "secret-mcp",
            "UAS_MCP_ENDPOINT": "http://internal",
            "OPENAI_API_KEY": "sk-1234",
            "AUTH_TOKEN": "tok-567",
            "DB_SECRET": "shh",
        }
    )
    assert clean_env == {"PATH": "/usr/bin", "HOME": "/home/user"}


def test_skills_store_progressive_disclosure_and_mcp_bridge(owner_session: dict):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # 1) Skills Store & Progressive Disclosure & Typed Run
    store = client.get("/api/skills/store", headers=headers)
    assert store.status_code == 200
    assert any(s["slug"] == "slug-formatter" for s in store.json()["store"])

    inst = client.post("/api/skills/store/install", json={"slug": "slug-formatter"}, headers=headers)
    assert inst.status_code == 200, inst.text
    skill = inst.json()["skill"]
    assert skill["slug"] == "slug-formatter"
    assert len(skill["contract_md"]) < 1000

    ref = client.get("/api/skills/installed/slug-formatter/references/guide.md", headers=headers)
    assert ref.status_code == 200
    assert "SEO Slug Guide" in ref.json()["content"]

    run_ok = client.post(
        "/api/skills/installed/slug-formatter/run",
        json={"tool_name": "format_slug", "arguments": {"text": "Unified Agent System Phase 5"}},
        headers=headers,
    )
    assert run_ok.status_code == 200, run_ok.text
    assert run_ok.json()["result"]["slug"] == "unified-agent-system-phase-5"

    # Schema rejection when required text missing
    run_bad = client.post(
        "/api/skills/installed/slug-formatter/run",
        json={"tool_name": "format_slug", "arguments": {}},
        headers=headers,
    )
    assert run_bad.status_code == 400

    # 2) StreamableHTTP MCP Bridge
    init = client.post("/api/mcp/bridge", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, headers=headers)
    assert init.status_code == 200
    assert init.json()["result"]["serverInfo"]["name"] == "unified-agent-system-bridge"

    listed = client.post("/api/mcp/bridge", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers=headers)
    assert listed.status_code == 200
    tool_names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert tool_names == {"session_send", "session_status", "session_tail", "model_list", "preset_list", "harness_status"}

    # Allowed bridge calls work
    st = client.post(
        "/api/mcp/bridge",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "harness_status", "arguments": {}}},
        headers=headers,
    )
    assert "killswitch" in st.json()["result"]

    ml = client.post(
        "/api/mcp/bridge",
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "model_list", "arguments": {}}},
        headers=headers,
    )
    assert isinstance(ml.json()["result"]["models"], list)

    pl = client.post(
        "/api/mcp/bridge",
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "preset_list", "arguments": {}}},
        headers=headers,
    )
    assert len(pl.json()["result"]["presets"]) >= 1

    sent = client.post(
        "/api/mcp/bridge",
        json={"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "session_send", "arguments": {"content": "مرحبا"}}},
        headers=headers,
    )
    sid = sent.json()["result"]["session_id"]

    s_stat = client.post(
        "/api/mcp/bridge",
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "session_status", "arguments": {"session_id": sid}}},
        headers=headers,
    )
    assert s_stat.json()["result"]["id"] == sid

    s_tail = client.post(
        "/api/mcp/bridge",
        json={"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "session_tail", "arguments": {"session_id": sid, "limit": 5}}},
        headers=headers,
    )
    assert len(s_tail.json()["result"]["entries"]) >= 1

    # Forbidden calls MUST be refused with -32003
    for forbidden in ("grant_approval", "delete_workspace", "vault_read", "computer_exec"):
        refused = client.post(
            "/api/mcp/bridge",
            json={"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {"name": forbidden, "arguments": {}}},
            headers=headers,
        )
        assert refused.json()["error"]["code"] == -32003
