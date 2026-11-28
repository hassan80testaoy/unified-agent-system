"""Planner-Brain pattern (§7C.2): Strong model plans & judges only; Cheaper model executes + optional different-provider reviewer (§7C.3)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.core import SystemState
from app.services.llm_router import LLMRouter, _estimate_cost

DEFAULT_EXPENSIVE_TASKS = ("goal_planner", "site_audit", "coding_plan", "research")


def get_planner_brain_config(db: Session, workspace_id: str) -> dict[str, Any]:
    key = f"planner_brain:{workspace_id}"
    row = db.get(SystemState, key)
    if row and isinstance(row.value_json, dict):
        data = dict(row.value_json)
        data.setdefault("enabled", True)
        data.setdefault("tasks", list(DEFAULT_EXPENSIVE_TASKS))
        return data
    return {
        "enabled": True,
        "tasks": list(DEFAULT_EXPENSIVE_TASKS),
        "planner_tier": "strong",
        "executor_tier": "cheap",
        "judge_tier": "strong",
    }


def set_planner_brain_config(
    db: Session,
    workspace_id: str,
    *,
    enabled: bool,
    tasks: list[str] | None = None,
) -> dict[str, Any]:
    cfg = get_planner_brain_config(db, workspace_id)
    cfg["enabled"] = bool(enabled)
    if tasks is not None:
        cfg["tasks"] = [str(t).strip() for t in tasks if str(t).strip()]
    key = f"planner_brain:{workspace_id}"
    row = db.get(SystemState, key)
    if row is None:
        db.add(SystemState(key=key, value_json=cfg))
    else:
        row.value_json = cfg
    db.flush()
    return cfg


def get_planner_brain_stats(db: Session, workspace_id: str) -> dict[str, Any]:
    key = f"planner_brain_stats:{workspace_id}"
    row = db.get(SystemState, key)
    base = dict(row.value_json or {}) if (row and isinstance(row.value_json, dict)) else {}
    runs = int(base.get("runs", 0))
    actual_cost = round(float(base.get("actual_cost_usd", 0.0)), 6)
    all_strong_est = round(float(base.get("all_strong_baseline_usd", 0.0)), 6)
    saved = round(max(0.0, all_strong_est - actual_cost), 6)
    pct = round((saved / all_strong_est) * 100.0, 2) if all_strong_est > 0 else 0.0
    return {
        "runs": runs,
        "actual_cost_usd": actual_cost,
        "all_strong_baseline_usd": all_strong_est,
        "estimated_savings_usd": saved,
        "estimated_savings_pct": pct,
        "last_split": base.get("last_split"),
        "updated_at": base.get("updated_at"),
    }


def _record_split_stats(db: Session, workspace_id: str, *, actual: float, all_strong: float, split: dict[str, Any]) -> None:
    key = f"planner_brain_stats:{workspace_id}"
    row = db.get(SystemState, key)
    cur = dict(row.value_json or {}) if (row and isinstance(row.value_json, dict)) else {}
    cur["runs"] = int(cur.get("runs", 0)) + 1
    cur["actual_cost_usd"] = round(float(cur.get("actual_cost_usd", 0.0)) + actual, 6)
    cur["all_strong_baseline_usd"] = round(float(cur.get("all_strong_baseline_usd", 0.0)) + all_strong, 6)
    cur["last_split"] = split
    cur["updated_at"] = datetime.now(timezone.utc).isoformat()
    if row is None:
        db.add(SystemState(key=key, value_json=cur))
    else:
        row.value_json = cur
    db.flush()


def run_planner_brain_workflow(
    db: Session,
    *,
    workspace_id: str,
    task_name: str,
    goal_prompt: str,
    executor_prompt_builder: Callable[[str], str] | None = None,
    require_review: bool = False,
    agent_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run Strong Plan -> Cheap Execute -> Strong Judge -> Optional Separate Reviewer."""
    cfg = get_planner_brain_config(db, workspace_id)
    use_split = bool(cfg.get("enabled", True)) and (
        not cfg.get("tasks") or task_name in cfg["tasks"]
    )
    router = LLMRouter(db, workspace_id)

    plan_tier = "strong" if use_split else "mid"
    exec_tier = "cheap" if use_split else "mid"
    judge_tier = "strong" if use_split else "mid"

    # 1. Strong model plans only
    plan_res = router.route(
        task_type="planning",
        tier=plan_tier,
        profile_override="planning",
        goal_text=goal_prompt,
        prompt=f"Create a concise step-by-step execution plan for:\n{goal_prompt}",
        system_prompt="You are a Principal Planner. Produce only the minimal structured execution plan.",
        agent_id=agent_id,
        run_id=run_id,
        use_cache=False,
    )

    # 2. Cheaper model executes
    exec_prompt = (
        executor_prompt_builder(plan_res["raw_text"])
        if executor_prompt_builder
        else f"Execute this plan concisely in Arabic:\n\nPlan:\n{plan_res['raw_text']}\n\nRequest:\n{goal_prompt}"
    )
    exec_profile = "coding" if "code" in task_name else ("research" if "audit" in task_name or "research" in task_name else "chat")
    exec_res = router.route(
        task_type=task_name,
        tier=exec_tier,
        profile_override=exec_profile,
        prompt=exec_prompt,
        system_prompt="You are a fast, cost-efficient Executor. Follow the plan strictly without inventing facts.",
        agent_id=agent_id,
        run_id=run_id,
        use_cache=False,
    )

    # 3. Strong model judges quality
    judge_res = router.route(
        task_type="judge",
        tier=judge_tier,
        profile_override="planning",
        prompt=(
            f"Verify if the executor output satisfies the plan completely.\n"
            f"Plan:\n{plan_res['raw_text'][:800]}\n\nOutput:\n{exec_res['raw_text'][:1200]}"
        ),
        system_prompt="You are a strict Quality Judge. Summarize pass/fail in 2 lines.",
        agent_id=agent_id,
        run_id=run_id,
        use_cache=False,
    )

    review_res: dict[str, Any] | None = None
    if require_review:
        review_res = router.review_with_different_provider(
            producer_provider=str(exec_res["provider_id"]),
            content_to_review=str(exec_res["raw_text"]),
            criteria=f"Accuracy and completeness for {task_name}",
            agent_id=agent_id,
            run_id=run_id,
        )

    actual_cost = (
        float(plan_res.get("cost_usd", 0.0))
        + float(exec_res.get("cost_usd", 0.0))
        + float(judge_res.get("cost_usd", 0.0))
    )
    # Hypothetical cost if executor ALSO used the strong planner model
    strong_exec_est = _estimate_cost(
        str(plan_res.get("model") or ""),
        int(exec_res.get("tokens_in", 0)),
        int(exec_res.get("tokens_out", 0)),
    )
    all_strong = float(plan_res.get("cost_usd", 0.0)) + strong_exec_est + float(judge_res.get("cost_usd", 0.0))
    if all_strong < actual_cost:
        all_strong = actual_cost

    split_meta = {
        "planner": {"provider": plan_res["provider_id"], "model": plan_res["model"], "tier": plan_res["tier"]},
        "executor": {"provider": exec_res["provider_id"], "model": exec_res["model"], "tier": exec_res["tier"]},
        "judge": {"provider": judge_res["provider_id"], "model": judge_res["model"], "tier": judge_res["tier"]},
        "reviewer": (
            {"provider": review_res["provider_id"], "model": review_res["model"]}
            if review_res
            else None
        ),
        "actual_cost_usd": round(actual_cost, 6),
        "all_strong_baseline_usd": round(all_strong, 6),
        "savings_usd": round(max(0.0, all_strong - actual_cost), 6),
        "used_split": use_split,
    }
    _record_split_stats(db, workspace_id, actual=actual_cost, all_strong=all_strong, split=split_meta)

    return {
        "plan_text": plan_res["raw_text"],
        "execution_text": exec_res["raw_text"],
        "judge_text": judge_res["raw_text"],
        "review": review_res,
        "split": split_meta,
    }
