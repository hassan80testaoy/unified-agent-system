"""Stdlib-only Python SDK for Unified Agent System (Coding Agent + Platform API).

Zero external dependencies — works out-of-the-box with Python 3.10+ targeting localhost:8010 or VPS.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class UasApiError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class UnifiedAgentSDK:
    """Lightweight Python stdlib client for Unified Agent System."""

    def __init__(self, base_url: str = "http://127.0.0.1:8010", token: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        data: bytes | None = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            err_raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(err_raw)
            except Exception:
                parsed = err_raw
            raise UasApiError(exc.code, parsed) from exc

    def login(self, email: str, password: str) -> str:
        res = self._request("POST", "/api/auth/login", {"email": email, "password": password})
        token = str(res.get("token") or res.get("access_token") or "")
        self.token = token
        return token

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def runtime_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/system/runtime-status")

    def list_threads(self) -> dict[str, Any]:
        return self._request("GET", "/api/threads")

    def create_thread(
        self,
        *,
        title: str = "مهمة برمجة جديدة",
        goal: str = "",
        workspace_dir: str | None = None,
        mode_key: str = "delegate",
        use_worktree: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "title": title,
            "goal": goal,
            "mode_key": mode_key,
            "use_worktree": use_worktree,
        }
        if workspace_dir:
            body["workspace_dir"] = workspace_dir
        return self._request("POST", "/api/threads", body)

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/threads/{thread_id}")

    def send_message(self, thread_id: str, content: str, *, skip_browser_verify: bool = False) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/threads/{thread_id}/messages",
            {"content": content, "skip_browser_verify": skip_browser_verify},
        )

    def approve_plan(self, thread_id: str, design_index: int = 0) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/threads/{thread_id}/approve-plan",
            {"design_index": design_index},
        )

    def update_todo(self, thread_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("PUT", f"/api/threads/{thread_id}/todo", {"items": items})

    def undo(self, thread_id: str, to_seq: int) -> dict[str, Any]:
        return self._request("POST", f"/api/threads/{thread_id}/undo", {"to_seq": to_seq})

    def fork(self, thread_id: str, from_seq: int | None = None, new_title: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/threads/{thread_id}/fork",
            {"from_seq": from_seq, "new_title": new_title, "use_worktree": True},
        )

    def list_artifacts(self, thread_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/threads/{thread_id}/artifacts")

    def get_changes(self, thread_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/threads/{thread_id}/changes")

    def create_worktree(self, thread_id: str, branch_name: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/api/threads/{thread_id}/worktree", {"branch_name": branch_name})

    def merge_worktree(self, thread_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/threads/{thread_id}/worktree/merge", {})

    def run_race(
        self,
        thread_id: str,
        *,
        prompt: str,
        test_command: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/threads/{thread_id}/race",
            {"prompt": prompt, "test_command": test_command, "candidates": candidates},
        )

    def spawn_subagent(self, thread_id: str, role: str, prompt: str, *, use_worktree: bool = False) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/threads/{thread_id}/subagents",
            {"role": role, "prompt": prompt, "use_worktree": use_worktree},
        )

    def list_goals(self) -> dict[str, Any]:
        return self._request("GET", "/api/goals")

    def create_goal(self, title: str, outcome: str, thread_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/goals",
            {"title": title, "outcome": outcome, "thread_id": thread_id, "autopilot": True},
        )

    def step_goal(self, goal_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/goals/{goal_id}/step", {})

    def list_hooks(self) -> dict[str, Any]:
        return self._request("GET", "/api/hooks")

    def create_hook(
        self,
        *,
        name: str,
        event: str,
        command: str,
        tool_pattern: str = "*",
        block_on_fail: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/hooks",
            {
                "name": name,
                "event": event,
                "command": command,
                "tool_pattern": tool_pattern,
                "enabled": True,
                "block_on_fail": block_on_fail,
            },
        )

    def delete_hook(self, hook_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/api/hooks/{hook_id}")
