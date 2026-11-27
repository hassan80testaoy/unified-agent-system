"""Agent Protocol & secure AgentContext enforcing allowed_tools, budgets, and kill switch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.captcha.solver import CaptchaSolver, get_captcha_solver
from app.models.agents import Approval, Run
from app.security.killswitch import is_killswitch_active
from app.services.events import emit_and_broadcast_run_event
from app.services.llm_router import LLMRouter


class ToolPermissionError(PermissionError):
    pass


class KillSwitchTriggeredError(RuntimeError):
    pass


@dataclass
class AgentContext:
    db: Session
    workspace_id: str
    user_id: str
    run: Run
    agent_id: str
    allowed_tools: list[str]
    router: LLMRouter
    run_max_budget_usd: float | None = None

    def require_tool(self, tool_name: str) -> None:
        if tool_name not in self.allowed_tools:
            raise ToolPermissionError(
                f"Agent '{self.agent_id}' is not permitted to use tool '{tool_name}'. Allowed: {self.allowed_tools}"
            )

    def check_killswitch(self) -> None:
        if is_killswitch_active(self.db):
            raise KillSwitchTriggeredError("Global kill switch is active.")
        self.db.refresh(self.run)
        if self.run.status == "canceled":
            raise KillSwitchTriggeredError("Run was canceled.")

    @property
    def captcha_solver(self) -> CaptchaSolver:
        self.require_tool("captcha")
        return get_captcha_solver()

    def llm(
        self,
        task_type: str,
        tier: str,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.require_tool("llm")
        self.check_killswitch()
        return self.router.route(
            task_type=task_type,
            tier=tier,
            prompt=prompt,
            json_schema=json_schema,
            agent_id=self.agent_id,
            run_id=self.run.id,
            run_max_budget_usd=self.run_max_budget_usd,
        )

    def review(self, producer_provider: str, content: str, criteria: str) -> dict[str, Any]:
        self.require_tool("llm")
        return self.router.review_with_different_provider(
            producer_provider=producer_provider,
            content_to_review=content,
            criteria=criteria,
            agent_id=self.agent_id,
            run_id=self.run.id,
        )

    async def emit(self, level: str, kind: str, message: str, data: dict[str, Any] | None = None) -> None:
        await emit_and_broadcast_run_event(
            self.db,
            workspace_id=self.workspace_id,
            run_id=self.run.id,
            level=level,
            kind=kind,
            message=message,
            data=data,
        )

    def ask_approval(self, kind: str, payload: dict[str, Any]) -> Approval:
        approval = Approval(
            workspace_id=self.workspace_id,
            run_id=self.run.id,
            kind=kind,
            payload=payload,
            status="pending",
        )
        self.db.add(approval)
        self.db.flush()
        return approval


class BaseAgent(Protocol):
    key: str

    def plan(self, task_input: dict[str, Any], ctx_info: dict[str, Any]) -> list[dict[str, Any]]:
        ...

    async def run(self, ctx: AgentContext, task_input: dict[str, Any]) -> dict[str, Any]:
        ...

    def verify(self, item: dict[str, Any]) -> dict[str, Any]:
        ...
