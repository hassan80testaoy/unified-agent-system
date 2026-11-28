"""Phase 5 MCP Servers, Permissions, Skills Store & StreamableHTTP MCP Bridge Router (§5B)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.phase5 import McpServer, McpSessionPermission
from app.models.providers import Provider
from app.models.v3_harness import ChatThread, ModePreset
from app.security.auth import AuthContext, get_current_auth, require_roles
from app.security.killswitch import get_killswitch_status
from app.services.chat_agent import create_thread, process_user_message
from app.services.mcp_service import (
    PermissionRevokedError,
    add_mcp_server,
    execute_mcp_tool,
    get_read_only_auto_allow,
    grant_mcp_permission,
    list_namespaced_tools,
    parse_mcp_json_config,
    probe_mcp_server,
    revoke_mcp_permission,
    serialize_mcp_permission,
    serialize_mcp_server,
    set_read_only_auto_allow,
)
from app.services.modes import seed_default_modes
from app.services.session_log import read_log
from app.services.skills_store import (
    SKILLS_SH_STORE,
    discover_compatible_skills,
    install_skill_one_click,
    read_skill_reference,
    run_installed_skill,
    serialize_skill,
)

router = APIRouter(tags=["mcp_skills"])


class McpServerCreateRequest(BaseModel):
    source_type: str = "command"  # command | link | json
    name: str = ""
    command: str = ""
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    raw_json: str | None = None
    scope: str = "all_sessions"
    project_key: str | None = None
    namespace_prefix: str | None = None
    working_dir: str = ""
    session_id: str | None = None


class McpPermissionGrantRequest(BaseModel):
    session_id: str
    server_id: str
    tool_name: str
    grant_mode: str = "remembered"  # remembered | allow_once
    account_id: str = "default"


class McpToolExecuteRequest(BaseModel):
    session_id: str
    server_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    account_id: str = "default"
    is_delegated_worker: bool = False
    delegation_filters: list[dict[str, Any]] = Field(default_factory=list)


class McpSettingsUpdateRequest(BaseModel):
    read_only_auto_allow: bool


class SkillInstallRequest(BaseModel):
    slug: str
    custom_contract_md: str | None = None
    custom_script_py: str | None = None


class SkillRunRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get("/api/mcp/servers")
def list_mcp_servers(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(McpServer)
        .filter(McpServer.workspace_id == auth.workspace.id)
        .order_by(McpServer.created_at.desc())
        .all()
    )
    return {
        "servers": [serialize_mcp_server(s) for s in rows],
        "tools": list_namespaced_tools(db, auth.workspace.id),
        "read_only_auto_allow": get_read_only_auto_allow(db, auth.workspace.id),
    }


@router.post("/api/mcp/servers")
def create_mcp_server(
    req: McpServerCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    created: list[McpServer] = []
    try:
        if req.source_type == "json" or (req.raw_json and req.raw_json.strip()):
            specs = parse_mcp_json_config(req.raw_json or "")
            for spec in specs:
                srv = add_mcp_server(
                    db,
                    workspace_id=auth.workspace.id,
                    name=spec["name"],
                    source_type="json",
                    command=spec["command"],
                    url=spec["url"],
                    env=spec["env"],
                    scope=req.scope,
                    project_key=req.project_key,
                    namespace_prefix=req.namespace_prefix or spec.get("namespace_prefix"),
                    working_dir=req.working_dir or spec.get("working_dir", ""),
                    session_id=req.session_id,
                )
                created.append(srv)
        else:
            srv = add_mcp_server(
                db,
                workspace_id=auth.workspace.id,
                name=req.name or req.namespace_prefix or "mcp-server",
                source_type=req.source_type,
                command=req.command,
                url=req.url,
                env=req.env,
                scope=req.scope,
                project_key=req.project_key,
                namespace_prefix=req.namespace_prefix,
                working_dir=req.working_dir,
                session_id=req.session_id,
            )
            created.append(srv)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "mcp_invalid", "message_ar": str(exc), "message_en": str(exc)}) from exc

    return {"servers": [serialize_mcp_server(s) for s in created], "server": serialize_mcp_server(created[0])}


@router.post("/api/mcp/servers/{server_id}/probe")
def probe_mcp_server_endpoint(
    server_id: str,
    session_id: str | None = None,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    srv = db.get(McpServer, server_id)
    if not srv or srv.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "خادم MCP غير موجود", "message_en": "MCP server not found"})
    probe_mcp_server(db, srv, session_id=session_id)
    db.commit()
    return {"server": serialize_mcp_server(srv)}


@router.delete("/api/mcp/servers/{server_id}")
def delete_mcp_server(
    server_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    srv = db.get(McpServer, server_id)
    if not srv or srv.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "خادم MCP غير موجود", "message_en": "MCP server not found"})
    db.query(McpSessionPermission).filter(McpSessionPermission.server_id == srv.id).delete()
    db.delete(srv)
    db.commit()
    return {"deleted": True, "id": server_id}


@router.get("/api/mcp/tools")
def list_mcp_tools_endpoint(
    project_key: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    return {"tools": list_namespaced_tools(db, auth.workspace.id, project_key=project_key)}


@router.get("/api/mcp/settings")
def get_mcp_settings_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return {"read_only_auto_allow": get_read_only_auto_allow(db, auth.workspace.id)}


@router.put("/api/mcp/settings")
def update_mcp_settings_endpoint(
    req: McpSettingsUpdateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    val = set_read_only_auto_allow(db, auth.workspace.id, req.read_only_auto_allow)
    db.commit()
    return {"read_only_auto_allow": val}


@router.get("/api/mcp/permissions")
def list_mcp_permissions(
    session_id: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    q = db.query(McpSessionPermission).filter(McpSessionPermission.workspace_id == auth.workspace.id)
    if session_id:
        q = q.filter(McpSessionPermission.session_id == session_id)
    rows = q.order_by(McpSessionPermission.created_at.desc()).all()
    return {"permissions": [serialize_mcp_permission(r) for r in rows]}


@router.post("/api/mcp/permissions")
def create_mcp_permission(
    req: McpPermissionGrantRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    srv = db.get(McpServer, req.server_id)
    if not srv or srv.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "خادم MCP غير موجود", "message_en": "MCP server not found"})
    try:
        perm = grant_mcp_permission(
            db,
            workspace_id=auth.workspace.id,
            session_id=req.session_id,
            server=srv,
            tool_name=req.tool_name,
            grant_mode=req.grant_mode,
            account_id=req.account_id,
        )
        db.commit()
        return {"permission": serialize_mcp_permission(perm)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_permission", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/mcp/permissions/{permission_id}/revoke")
def revoke_mcp_permission_endpoint(
    permission_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        row = revoke_mcp_permission(db, auth.workspace.id, permission_id)
        db.commit()
        return {"permission": serialize_mcp_permission(row)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/mcp/tools/execute")
def execute_mcp_tool_endpoint(
    req: McpToolExecuteRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    srv = db.get(McpServer, req.server_id)
    if not srv or srv.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "خادم MCP غير موجود", "message_en": "MCP server not found"})
    try:
        res = execute_mcp_tool(
            db,
            workspace_id=auth.workspace.id,
            session_id=req.session_id,
            server=srv,
            tool_name=req.tool_name,
            arguments=req.arguments,
            account_id=req.account_id,
            is_delegated_worker=req.is_delegated_worker,
            delegation_filters=req.delegation_filters,
        )
        db.commit()
        return res
    except PermissionRevokedError as exc:
        db.commit()
        raise HTTPException(status_code=403, detail={"code": "permission_revoked", "message_ar": str(exc), "message_en": str(exc)}) from exc


# --- Skills Store (skills.sh + ~/.agent/skills + progressive disclosure) ---

@router.get("/api/skills/store")
def list_skills_store(auth: AuthContext = Depends(get_current_auth)):
    return {
        "store": [
            {
                "slug": s["slug"],
                "name": s["name"],
                "description": s["description"],
                "references": list((s.get("references") or {}).keys()),
            }
            for s in SKILLS_SH_STORE
        ]
    }


@router.post("/api/skills/store/install")
def install_skill_endpoint(
    req: SkillInstallRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        row = install_skill_one_click(
            db,
            workspace_id=auth.workspace.id,
            slug=req.slug,
            custom_contract_md=req.custom_contract_md,
            custom_script_py=req.custom_script_py,
        )
        db.commit()
        return {"skill": serialize_skill(row)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "skill_install_error", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.get("/api/skills/installed")
def list_installed_skills(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = discover_compatible_skills(db, auth.workspace.id)
    db.commit()
    return {"skills": [serialize_skill(r) for r in rows]}


@router.get("/api/skills/installed/{slug}/references/{ref_name}")
def read_installed_skill_ref(
    slug: str,
    ref_name: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    try:
        content = read_skill_reference(db, auth.workspace.id, slug, ref_name)
        return {"slug": slug, "ref_name": ref_name, "content": content}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "ref_not_found", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/skills/installed/{slug}/run")
def run_installed_skill_endpoint(
    slug: str,
    req: SkillRunRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        return run_installed_skill(
            db,
            workspace_id=auth.workspace.id,
            slug=slug,
            tool_name=req.tool_name,
            arguments=req.arguments,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"code": "skill_run_error", "message_ar": str(exc), "message_en": str(exc)}) from exc


# --- StreamableHTTP MCP Bridge (dsh-mcp-server pattern) ---

_BRIDGE_ALLOWED_TOOLS: list[dict[str, Any]] = [
    {"name": "session_send", "description": "Send a message/task to an agent session (creates thread if omitted).", "inputSchema": {"type": "object", "required": ["content"], "properties": {"session_id": {"type": "string"}, "content": {"type": "string"}}}},
    {"name": "session_status", "description": "Inspect session status and mode.", "inputSchema": {"type": "object", "required": ["session_id"], "properties": {"session_id": {"type": "string"}}}},
    {"name": "session_tail", "description": "Read latest session NDJSON log entries.", "inputSchema": {"type": "object", "required": ["session_id"], "properties": {"session_id": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "model_list", "description": "List enabled LLM providers and configured models.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "preset_list", "description": "List available harness modes/presets.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "harness_status", "description": "Get overall harness kill-switch and active thread summary.", "inputSchema": {"type": "object", "properties": {}}},
]
_BRIDGE_ALLOWED_NAMES = {t["name"] for t in _BRIDGE_ALLOWED_TOOLS}


def _rpc_err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


@router.post("/api/mcp/bridge")
async def mcp_bridge_streamable_http(
    request: Request,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    """StreamableHTTP JSON-RPC 2.0 endpoint exposing ONLY safe delegation & inspection arms (zero grants/deletion/credentials)."""
    body = await request.json()
    req_id = body.get("id")
    method = str(body.get("method") or "").strip()
    params = dict(body.get("params") or {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "unified-agent-system-bridge", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _BRIDGE_ALLOWED_TOOLS}}

    if method == "tools/call":
        t_name = str(params.get("name") or "").strip()
        args = dict(params.get("arguments") or {})
        if t_name not in _BRIDGE_ALLOWED_NAMES:
            return _rpc_err(
                req_id,
                -32003,
                f"Forbidden: bridge refuses '{t_name}'. Approval grants, deletion, credentials, and computer lifecycle are never exposed.",
            )

        if t_name == "harness_status":
            threads_cnt = db.query(ChatThread).filter(ChatThread.workspace_id == auth.workspace.id).count()
            return {"jsonrpc": "2.0", "id": req_id, "result": {"killswitch": get_killswitch_status(db), "threads_count": threads_cnt}}

        if t_name == "preset_list":
            seed_default_modes(db, auth.workspace.id)
            modes = db.query(ModePreset).filter(ModePreset.workspace_id == auth.workspace.id).all()
            return {"jsonrpc": "2.0", "id": req_id, "result": {"presets": [{"key": m.key, "name_en": m.name_en, "is_default": m.is_default} for m in modes]}}

        if t_name == "model_list":
            provs = db.query(Provider).filter(Provider.workspace_id == auth.workspace.id, Provider.kind != "captcha").all()
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "models": [
                        {"provider_id": p.id, "enabled": p.enabled, "models": p.models_json}
                        for p in provs
                    ]
                },
            }

        if t_name == "session_status":
            sid = str(args.get("session_id") or "")
            th = db.get(ChatThread, sid)
            if not th or th.workspace_id != auth.workspace.id:
                return _rpc_err(req_id, -32004, "Session not found")
            return {"jsonrpc": "2.0", "id": req_id, "result": {"id": th.id, "title": th.title, "mode_key": th.mode_key, "status": th.status}}

        if t_name == "session_tail":
            sid = str(args.get("session_id") or "")
            lim = max(1, min(int(args.get("limit") or 10), 100))
            th = db.get(ChatThread, sid)
            if not th or th.workspace_id != auth.workspace.id:
                return _rpc_err(req_id, -32004, "Session not found")
            entries = read_log(th.id)[-lim:]
            return {"jsonrpc": "2.0", "id": req_id, "result": {"session_id": th.id, "entries": entries}}

        if t_name == "session_send":
            content = str(args.get("content") or "").strip()
            if not content:
                return _rpc_err(req_id, -32602, "content is required")
            sid = str(args.get("session_id") or "").strip()
            th = db.get(ChatThread, sid) if sid else None
            if sid and (not th or th.workspace_id != auth.workspace.id):
                return _rpc_err(req_id, -32004, "Session not found")
            if th is None:
                th = create_thread(db, workspace_id=auth.workspace.id, user_id=auth.user.id, title=content[:60])
                db.commit()
            res = await process_user_message(db, thread=th, user_id=auth.user.id, content=content, mock_mode=False)
            return {"jsonrpc": "2.0", "id": req_id, "result": {"session_id": th.id, "assistant_message": res["assistant_message"]}}

    return _rpc_err(req_id, -32601, f"Method '{method}' not allowed on bridge.")
