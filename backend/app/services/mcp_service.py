"""Phase 5 MCP Servers, Discovery Probe, Namespaced Tools, and Exact Session Permission Engine (§5B)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models.core import SystemState
from app.models.phase5 import McpServer, McpSessionPermission
from app.security.crypto import decrypt_secret, encrypt_secret
from app.services.session_log import append_turn

_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_STRIP_ENV_PAT = re.compile(r"(^MCP_|^UAS_MCP_|_API_KEY$|_TOKEN$|_SECRET$|PASSWORD|CREDENTIAL)", re.I)

# In-process revocation epoch per permission ID so mid-run cancellation is instantaneous even across worker loops
_REVOKED_PERMISSION_IDS: set[str] = set()


class PermissionRevokedError(PermissionError):
    pass


def _mcp_readonly_state_key(workspace_id: str) -> str:
    return f"mcp_read_only_auto:{workspace_id}"


def get_read_only_auto_allow(db: Session, workspace_id: str) -> bool:
    row = db.get(SystemState, _mcp_readonly_state_key(workspace_id))
    return bool((row.value_json or {}).get("enabled")) if row else False


def set_read_only_auto_allow(db: Session, workspace_id: str, enabled: bool) -> bool:
    key = _mcp_readonly_state_key(workspace_id)
    row = db.get(SystemState, key)
    if row is None:
        row = SystemState(key=key, value_json={"enabled": bool(enabled)})
        db.add(row)
    else:
        row.value_json = {"enabled": bool(enabled)}
    db.flush()
    return bool(enabled)


def build_sanitized_worker_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Strip all MCP configs, API keys, tokens, and connection secrets for CLI child-process workers."""
    src = base_env if base_env is not None else os.environ
    return {k: v for k, v in src.items() if not _STRIP_ENV_PAT.search(k)}


def validate_namespace_prefix(raw: str | None, fallback_name: str = "mcp") -> str:
    candidate = (raw or "").strip()
    if not candidate:
        candidate = re.sub(r"[^A-Za-z0-9_-]+", "_", fallback_name.strip().lower()).strip("_-")[:32] or "mcp"
    if not _NAMESPACE_RE.match(candidate):
        raise ValueError("Namespace prefix must be 1-32 characters matching [A-Za-z0-9_-].")
    return candidate


def compute_launch_fingerprint(
    *,
    source_type: str,
    command: str,
    url: str,
    working_dir: str,
    env_keys: list[str],
) -> str:
    payload = {
        "source_type": source_type,
        "command": command.strip(),
        "url": url.strip(),
        "working_dir": working_dir.strip(),
        "env_keys": sorted(str(k) for k in env_keys),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def compute_tool_definition_hash(tool_def: dict[str, Any]) -> str:
    canonical = {
        "name": str(tool_def.get("name") or ""),
        "description": str(tool_def.get("description") or ""),
        "input_schema": tool_def.get("input_schema") or tool_def.get("inputSchema") or {},
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def parse_mcp_json_config(raw_json: str) -> list[dict[str, Any]]:
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise ValueError("JSON config must be an object.")
    if "mcpServers" in data and isinstance(data["mcpServers"], dict):
        out: list[dict[str, Any]] = []
        for s_name, cfg in data["mcpServers"].items():
            if not isinstance(cfg, dict):
                continue
            cmd = str(cfg.get("command") or "").strip()
            args = cfg.get("args") or []
            if cmd and isinstance(args, list) and args:
                cmd = " ".join([cmd] + [str(a) for a in args])
            out.append(
                {
                    "name": str(cfg.get("name") or s_name),
                    "command": cmd,
                    "url": str(cfg.get("url") or "").strip(),
                    "env": {str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
                    "working_dir": str(cfg.get("cwd") or cfg.get("working_dir") or "").strip(),
                    "namespace_prefix": cfg.get("namespace_prefix") or str(s_name),
                }
            )
        if not out:
            raise ValueError("No servers found inside mcpServers JSON.")
        return out

    cmd = str(data.get("command") or "").strip()
    args = data.get("args") or []
    if cmd and isinstance(args, list) and args:
        cmd = " ".join([cmd] + [str(a) for a in args])
    return [
        {
            "name": str(data.get("name") or "mcp-server"),
            "command": cmd,
            "url": str(data.get("url") or "").strip(),
            "env": {str(k): str(v) for k, v in (data.get("env") or {}).items()},
            "working_dir": str(data.get("cwd") or data.get("working_dir") or "").strip(),
            "namespace_prefix": data.get("namespace_prefix") or str(data.get("name") or "mcp-server"),
        }
    ]


def _normalize_tools(prefix: str, raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in raw_tools:
        if not isinstance(raw, dict):
            continue
        t_name = str(raw.get("name") or "").strip()
        if not t_name:
            continue
        desc = str(raw.get("description") or "").strip()
        schema = raw.get("inputSchema") or raw.get("input_schema") or {"type": "object", "properties": {}}
        annotations = raw.get("annotations") or {}
        read_only = bool(raw.get("read_only") or raw.get("readOnly") or raw.get("readOnlyHint") or annotations.get("readOnlyHint"))
        entry = {
            "name": t_name,
            "namespaced_name": f"{prefix}__{t_name}",
            "description": desc,
            "input_schema": schema,
            "read_only": read_only,
        }
        entry["definition_hash"] = compute_tool_definition_hash(entry)
        items.append(entry)
    return items


def _probe_link_sync(url: str) -> list[dict[str, Any]]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    with httpx.Client(timeout=4.0) as client:
        resp = client.post(url, json=payload, headers={"Accept": "application/json", "Content-Type": "application/json"})
        if resp.status_code >= 400:
            raise RuntimeError(f"http_{resp.status_code}")
        body = resp.json()
        if "error" in body and body["error"]:
            raise RuntimeError(str(body["error"]))
        result = body.get("result") or body
        return list(result.get("tools") or [])


def _split_command_args(command: str) -> list[str]:
    raw_args = shlex.split(command.strip(), posix=(os.name != "nt"))
    return [a[1:-1] if len(a) >= 2 and ((a[0] == '"' and a[-1] == '"') or (a[0] == "'" and a[-1] == "'")) else a for a in raw_args]


def _probe_command_sync(command: str, env_map: dict[str, str], working_dir: str) -> list[dict[str, Any]]:
    args = _split_command_args(command)
    if not args:
        raise RuntimeError("empty_command")
    proc_env = os.environ.copy()
    proc_env.update(env_map)
    cwd = working_dir if (working_dir and Path(working_dir).exists()) else None
    req_line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}) + "\n"
    proc = subprocess.run(
        args,
        input=req_line,
        cwd=cwd,
        env=proc_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=6.0,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"exit_{proc.returncode}:{(proc.stderr or proc.stdout).strip()[:160]}")
    for line in proc.stdout.splitlines():
        clean = line.strip()
        if not clean:
            continue
        try:
            body = json.loads(clean)
            if isinstance(body, dict):
                result = body.get("result") or body
                tools = result.get("tools")
                if isinstance(tools, list):
                    return tools
        except Exception:
            continue
    raise RuntimeError("no_jsonrpc_tools_response")


def probe_mcp_server(db: Session, server: McpServer, *, session_id: str | None = None) -> McpServer:
    """Safely probe an MCP server; failures degrade gracefully to status='unavailable' without crashing."""
    env_map: dict[str, str] = {}
    if server.env_enc:
        try:
            env_map = json.loads(decrypt_secret(server.env_enc))
        except Exception:
            env_map = {}

    try:
        if server.url.strip():
            raw_tools = _probe_link_sync(server.url.strip())
        elif server.command.strip():
            raw_tools = _probe_command_sync(server.command.strip(), env_map, server.working_dir)
        else:
            raise RuntimeError("missing_command_or_url")
        server.tools_json = _normalize_tools(server.namespace_prefix, raw_tools)
        server.status = "active"
        server.last_error = None
        if session_id:
            append_turn(
                session_id,
                {
                    "type": "mcp_tools_loaded",
                    "server_id": server.id,
                    "namespace_prefix": server.namespace_prefix,
                    "tools": [t["namespaced_name"] for t in server.tools_json],
                },
            )
    except Exception as exc:
        server.status = "unavailable"
        server.last_error = str(exc)[:300]
        server.tools_json = []
    db.flush()
    return server


def add_mcp_server(
    db: Session,
    *,
    workspace_id: str,
    name: str,
    source_type: str,
    command: str = "",
    url: str = "",
    env: dict[str, str] | None = None,
    scope: str = "all_sessions",
    project_key: str | None = None,
    namespace_prefix: str | None = None,
    working_dir: str = "",
    session_id: str | None = None,
) -> McpServer:
    prefix = validate_namespace_prefix(namespace_prefix, fallback_name=name)
    env_clean = {str(k).strip(): str(v) for k, v in (env or {}).items() if str(k).strip()}
    env_keys = sorted(env_clean.keys())
    is_remote = bool(url.strip()) and not url.strip().startswith(("http://127.0.0.1", "http://localhost"))
    fp = compute_launch_fingerprint(
        source_type=source_type,
        command=command,
        url=url,
        working_dir=working_dir,
        env_keys=env_keys,
    )
    existing = (
        db.query(McpServer)
        .filter(McpServer.workspace_id == workspace_id, McpServer.namespace_prefix == prefix)
        .first()
    )
    if existing:
        raise ValueError(f"Namespace prefix '{prefix}' already exists in workspace.")

    srv = McpServer(
        workspace_id=workspace_id,
        name=name.strip() or prefix,
        source_type=source_type,
        command=command.strip(),
        url=url.strip(),
        env_enc=encrypt_secret(json.dumps(env_clean)) if env_clean else None,
        env_keys_json=env_keys,
        scope=scope if scope in {"all_sessions", "project"} else "all_sessions",
        project_key=project_key.strip() if project_key else None,
        namespace_prefix=prefix,
        working_dir=working_dir.strip(),
        is_remote=is_remote,
        status="active",
        launch_fingerprint=fp,
    )
    db.add(srv)
    db.flush()
    probe_mcp_server(db, srv, session_id=session_id)
    db.flush()
    return srv


def list_namespaced_tools(db: Session, workspace_id: str, *, project_key: str | None = None) -> list[dict[str, Any]]:
    rows = db.query(McpServer).filter(McpServer.workspace_id == workspace_id).all()
    tools: list[dict[str, Any]] = []
    for srv in rows:
        if srv.status != "active":
            continue
        if srv.scope == "project" and project_key and srv.project_key and srv.project_key != project_key:
            continue
        for t in list(srv.tools_json or []):
            tools.append({**t, "server_id": srv.id, "server_name": srv.name, "namespace_prefix": srv.namespace_prefix})
    return tools


def _find_tool(server: McpServer, tool_name: str) -> dict[str, Any] | None:
    for t in list(server.tools_json or []):
        if t.get("namespaced_name") == tool_name or t.get("name") == tool_name:
            return t
    return None


def evaluate_mcp_permission(
    db: Session,
    *,
    workspace_id: str,
    session_id: str,
    server: McpServer,
    tool_name: str,
    account_id: str = "default",
    is_delegated_worker: bool = False,
    delegation_filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tool_def = _find_tool(server, tool_name)
    if tool_def is None:
        return {"allowed": False, "requires_approval": False, "reason": "tool_not_found"}
    if server.status != "active":
        return {"allowed": False, "requires_approval": False, "reason": "server_unavailable"}

    # Narrowed delegator tool filter check: an allow-list admits NO MCP tool
    for flt in delegation_filters or []:
        allow_list = flt.get("allow_list") or flt.get("tool_allowlist") or []
        if allow_list:
            return {"allowed": False, "requires_approval": False, "reason": "allowlist_admits_no_mcp_tool"}
        deny_list = set(flt.get("deny_list") or [])
        if tool_def["namespaced_name"] in deny_list or tool_def["name"] in deny_list:
            return {"allowed": False, "requires_approval": False, "reason": "denied_by_delegator_filter"}

    perm = (
        db.query(McpSessionPermission)
        .filter(
            McpSessionPermission.workspace_id == workspace_id,
            McpSessionPermission.session_id == session_id,
            McpSessionPermission.server_id == server.id,
            McpSessionPermission.account_id == account_id,
            McpSessionPermission.scope == server.scope,
            McpSessionPermission.tool_name == tool_def["namespaced_name"],
            McpSessionPermission.definition_hash == tool_def["definition_hash"],
            McpSessionPermission.launch_fingerprint == server.launch_fingerprint,
            McpSessionPermission.revoked.is_(False),
        )
        .order_by(McpSessionPermission.created_at.desc())
        .first()
    )

    if perm and perm.id not in _REVOKED_PERMISSION_IDS:
        # Critical rule: delegated workers NEVER receive `allow_once` permissions
        if is_delegated_worker and perm.grant_mode != "remembered":
            return {"allowed": False, "requires_approval": False, "reason": "allow_once_excluded_for_worker"}
        return {
            "allowed": True,
            "requires_approval": False,
            "permission_id": perm.id,
            "grant_mode": perm.grant_mode,
            "reason": "permitted",
        }

    # Read-only auto-run mode applies across sessions unless restricted by allow-list above
    if tool_def.get("read_only") and get_read_only_auto_allow(db, workspace_id):
        return {
            "allowed": True,
            "requires_approval": False,
            "permission_id": None,
            "grant_mode": "read_only_auto",
            "reason": "read_only_auto_allowed",
        }

    # Delegated workers NEVER ask for approval
    if is_delegated_worker:
        return {"allowed": False, "requires_approval": False, "reason": "worker_never_asks_approval"}

    return {"allowed": False, "requires_approval": True, "reason": "approval_required"}


def grant_mcp_permission(
    db: Session,
    *,
    workspace_id: str,
    session_id: str,
    server: McpServer,
    tool_name: str,
    grant_mode: str = "remembered",
    account_id: str = "default",
) -> McpSessionPermission:
    tool_def = _find_tool(server, tool_name)
    if tool_def is None:
        raise ValueError(f"Tool '{tool_name}' not found on server '{server.name}'.")
    mode = "allow_once" if grant_mode == "allow_once" else "remembered"
    row = McpSessionPermission(
        workspace_id=workspace_id,
        session_id=session_id,
        server_id=server.id,
        account_id=account_id,
        scope=server.scope,
        tool_name=tool_def["namespaced_name"],
        definition_hash=tool_def["definition_hash"],
        launch_fingerprint=server.launch_fingerprint,
        grant_mode=mode,
        revoked=False,
    )
    db.add(row)
    db.flush()
    return row


def revoke_mcp_permission(db: Session, workspace_id: str, permission_id: str) -> McpSessionPermission:
    row = db.get(McpSessionPermission, permission_id)
    if not row or row.workspace_id != workspace_id:
        raise ValueError("Permission not found.")
    row.revoked = True
    row.revoked_at = utcnow()
    _REVOKED_PERMISSION_IDS.add(row.id)
    db.flush()
    return row


def configure_mcp_oauth(
    db: Session,
    server: McpServer,
    *,
    token_url: str,
    client_id: str,
    client_secret: str = "",
    refresh_token: str = "",
    access_token: str = "",
    expires_in_sec: int = 3600,
    auto_refresh: bool = True,
) -> McpServer:
    """Store Fernet-encrypted OAuth credentials for an MCP server with optional auto-refresh."""
    payload = {
        "token_url": token_url.strip(),
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "refresh_token": refresh_token.strip(),
        "access_token": access_token.strip(),
    }
    server.oauth_config_enc = encrypt_secret(json.dumps(payload))
    server.oauth_auto_refresh = bool(auto_refresh)
    server.oauth_expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(0, int(expires_in_sec)))
    db.flush()
    return server


def ensure_mcp_oauth_fresh(db: Session, server: McpServer, *, force: bool = False) -> dict[str, Any]:
    """Auto-refresh MCP OAuth token when expired or expiring within 120s (NEVER leaks token)."""
    if not server.oauth_config_enc:
        return {"configured": False, "refreshed": False}
    try:
        cfg = json.loads(decrypt_secret(server.oauth_config_enc))
    except Exception:
        return {"configured": False, "refreshed": False, "error": "decrypt_failed"}

    now = datetime.now(timezone.utc)
    exp = server.oauth_expires_at
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    needs = bool(force) or (exp is None) or (exp <= now + timedelta(seconds=120))
    if not needs or (not force and not server.oauth_auto_refresh):
        return {
            "configured": True,
            "refreshed": False,
            "expires_at": exp.isoformat() if exp else None,
            "has_access_token": bool(cfg.get("access_token")),
        }

    token_url = str(cfg.get("token_url") or "").strip()
    refresh_tok = str(cfg.get("refresh_token") or "").strip()
    if not token_url or not refresh_tok:
        return {"configured": True, "refreshed": False, "error": "missing_refresh_token_or_url"}

    resp = httpx.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
            "client_id": str(cfg.get("client_id") or ""),
            "client_secret": str(cfg.get("client_secret") or ""),
        },
        timeout=12.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"mcp_oauth_refresh_http_{resp.status_code}")

    body = resp.json() or {}
    new_access = str(body.get("access_token") or "").strip()
    if not new_access:
        raise RuntimeError("mcp_oauth_refresh_missing_access_token")
    cfg["access_token"] = new_access
    if body.get("refresh_token"):
        cfg["refresh_token"] = str(body["refresh_token"]).strip()
    expires_in = int(body.get("expires_in") or 3600)
    new_exp = now + timedelta(seconds=max(60, expires_in))

    server.oauth_config_enc = encrypt_secret(json.dumps(cfg))
    server.oauth_expires_at = new_exp
    db.flush()
    return {
        "configured": True,
        "refreshed": True,
        "expires_at": new_exp.isoformat(),
        "has_access_token": True,
    }


def _call_mcp_tool_real(server: McpServer, raw_tool_name: str, arguments: dict[str, Any]) -> Any:
    env_map: dict[str, str] = {}
    if server.env_enc:
        try:
            env_map = json.loads(decrypt_secret(server.env_enc))
        except Exception:
            env_map = {}

    rpc_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": raw_tool_name, "arguments": arguments}}
    if server.url.strip():
        headers = {"Content-Type": "application/json"}
        if server.oauth_config_enc:
            try:
                ocfg = json.loads(decrypt_secret(server.oauth_config_enc))
                tok = str(ocfg.get("access_token") or "").strip()
                if tok:
                    headers["Authorization"] = f"Bearer {tok}"
            except Exception:
                pass
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(server.url.strip(), json=rpc_req, headers=headers)
            if resp.status_code >= 400:
                raise RuntimeError(f"mcp_call_http_{resp.status_code}")
            body = resp.json()
            return body.get("result") if "result" in body else body

    if server.command.strip():
        args = _split_command_args(server.command)
        proc_env = os.environ.copy()
        proc_env.update(env_map)
        cwd = server.working_dir if (server.working_dir and Path(server.working_dir).exists()) else None
        init_and_call = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
            + "\n"
            + json.dumps(rpc_req)
            + "\n"
        )
        proc = subprocess.run(
            args,
            input=init_and_call,
            cwd=cwd,
            env=proc_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8.0,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"mcp_cmd_exit_{proc.returncode}")
        last_obj: Any = None
        for line in proc.stdout.splitlines():
            clean = line.strip()
            if not clean:
                continue
            try:
                obj = json.loads(clean)
                if isinstance(obj, dict) and obj.get("id") == 2:
                    return obj.get("result") if "result" in obj else obj
                last_obj = obj
            except Exception:
                continue
        return last_obj

    raise RuntimeError("no_mcp_transport")


def execute_mcp_tool(
    db: Session,
    *,
    workspace_id: str,
    session_id: str,
    server: McpServer,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    account_id: str = "default",
    is_delegated_worker: bool = False,
    delegation_filters: list[dict[str, Any]] | None = None,
    on_before_dispatch: Any = None,
) -> dict[str, Any]:
    eval_res = evaluate_mcp_permission(
        db,
        workspace_id=workspace_id,
        session_id=session_id,
        server=server,
        tool_name=tool_name,
        account_id=account_id,
        is_delegated_worker=is_delegated_worker,
        delegation_filters=delegation_filters,
    )
    if not eval_res["allowed"]:
        return {"ok": False, **eval_res}

    perm_id = eval_res.get("permission_id")
    if on_before_dispatch:
        on_before_dispatch()

    # Live re-check so a revoked permission stops an active worker immediately before tool execution
    if perm_id:
        db.expire_all()
        fresh = db.get(McpSessionPermission, perm_id)
        if perm_id in _REVOKED_PERMISSION_IDS or (fresh and fresh.revoked):
            raise PermissionRevokedError("Permission revoked while worker was running.")

    tool_def = _find_tool(server, tool_name)
    assert tool_def is not None
    if server.oauth_config_enc and server.oauth_auto_refresh:
        ensure_mcp_oauth_fresh(db, server)
    result = _call_mcp_tool_real(server, tool_def["name"], dict(arguments or {}))

    if perm_id and eval_res.get("grant_mode") == "allow_once":
        row = db.get(McpSessionPermission, perm_id)
        if row:
            row.revoked = True
            row.revoked_at = utcnow()
            _REVOKED_PERMISSION_IDS.add(row.id)

    append_turn(
        session_id,
        {
            "type": "mcp_tool_call",
            "server_id": server.id,
            "tool": tool_def["namespaced_name"],
            "is_delegated_worker": is_delegated_worker,
        },
    )
    db.flush()
    return {"ok": True, "tool": tool_def["namespaced_name"], "result": result, "grant_mode": eval_res.get("grant_mode")}


def serialize_mcp_server(s: McpServer) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "source_type": s.source_type,
        "command": s.command,
        "url": s.url,
        "env_keys": list(s.env_keys_json or []),
        "scope": s.scope,
        "project_key": s.project_key,
        "namespace_prefix": s.namespace_prefix,
        "working_dir": s.working_dir,
        "is_remote": bool(s.is_remote),
        "status": s.status,
        "last_error": s.last_error,
        "tools": list(s.tools_json or []),
        "launch_fingerprint": s.launch_fingerprint,
        "oauth_configured": bool(s.oauth_config_enc),
        "oauth_auto_refresh": bool(s.oauth_auto_refresh),
        "oauth_expires_at": s.oauth_expires_at.isoformat() if s.oauth_expires_at else None,
        "created_at": s.created_at.isoformat(),
    }


def serialize_mcp_permission(p: McpSessionPermission) -> dict[str, Any]:
    return {
        "id": p.id,
        "session_id": p.session_id,
        "server_id": p.server_id,
        "account_id": p.account_id,
        "scope": p.scope,
        "tool_name": p.tool_name,
        "definition_hash": p.definition_hash,
        "launch_fingerprint": p.launch_fingerprint,
        "grant_mode": p.grant_mode,
        "revoked": bool(p.revoked),
        "revoked_at": p.revoked_at.isoformat() if p.revoked_at else None,
        "created_at": p.created_at.isoformat(),
    }
