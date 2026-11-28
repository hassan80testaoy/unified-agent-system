"""Platform Regression & Capability Eval Harness (§8L.6 — measures quality & regressions per change)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.phase8 import EvalRun
from app.security.sanitizer import discard_vault_secret, register_vault_secret, scrub_text
from app.services.chat_output_guard import sanitize_chat_output
from app.services.intent_router import classify_intent
from app.services.loop_guard import LoopGuard, RunawayToolCallGuard
from app.services.self_dev_service import is_self_dev_path_forbidden
from app.skills.coded.catalog import CODED_SKILLS_SPECS


def serialize_eval_run(r: EvalRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "workspace_id": r.workspace_id,
        "suite_name": r.suite_name,
        "score": round(float(r.score), 2),
        "passed_count": int(r.passed_count),
        "total_count": int(r.total_count),
        "delta_vs_prev": round(float(r.delta_vs_prev), 2),
        "results": list(r.results_json or []),
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def run_platform_eval_suite(db: Session, *, workspace_id: str, suite_name: str = "platform_core") -> dict[str, Any]:
    """Run deterministic regression checks across core §8 guarantees and record score + delta (§8L.6)."""
    checks: list[dict[str, Any]] = []

    # 1. Output guard blocks raw JSON plans & <tool_call> tags
    cleaned_tc, meta_tc = sanitize_chat_output('قبل <tool_call>{"name":"x"}</tool_call> بعد')
    cleaned_json, meta_json = sanitize_chat_output('{"origin":"user","task":"Search Google","skills":["browsing"]}')
    checks.append(
        {
            "name": "output_guard_no_raw_json_or_tool_call",
            "passed": ("<tool_call>" not in cleaned_tc and meta_tc["had_tool_markup"] and "origin" not in cleaned_json and meta_json["had_internal_plan_json"]),
        }
    )

    # 2. Runaway tool call guard halts after 3 malformed tags
    rg = RunawayToolCallGuard(max_malformed=3)
    s1, _ = rg.observe("<tool_call>bad1</tool_call>")
    s2, _ = rg.observe("<tool_call>bad2</tool_call>")
    s3, _ = rg.observe("<tool_call>bad3</tool_call>")
    checks.append({"name": "runaway_tool_call_guard", "passed": (not s1 and not s2 and s3)})

    # 3. LoopGuard notes & stops repetitive tool calls
    lg = LoopGuard()
    decisions = [lg.record_step(tool_calls=[{"name": "browser_open", "args": {"url": "https://a.com"}}])[0] for _ in range(6)]
    checks.append({"name": "loop_guard_note_then_stop", "passed": ("note" in decisions and "stop" in decisions)})

    # 4. Intent router routes Arabic company search properly without backlink confusion
    srch = classify_intent("ابحثلي عن شركة مثال ورابط الموقع")
    checks.append({"name": "intent_router_arabic_search", "passed": srch["intent"] == "browser" and bool(srch["entities"].get("query"))})

    # 5. Blind-Fill secret scrubbing
    sentinel = "VAULT-SECRET-EVAL-991188"
    register_vault_secret(sentinel)
    try:
        scrubbed = scrub_text(f"token={sentinel}")
    finally:
        discard_vault_secret(sentinel)
    checks.append({"name": "blind_fill_vault_redaction", "passed": sentinel not in scrubbed and "[REDACTED]" in scrubbed})

    # 6. §0 Self-Dev guard rejects protected captcha/config files
    checks.append(
        {
            "name": "self_dev_protects_captcha_and_config",
            "passed": (
                is_self_dev_path_forbidden("backend/app/config.py")
                and is_self_dev_path_forbidden("browser-worker/worker.py")
                and is_self_dev_path_forbidden("backend/app/services/captcha_inject.py")
                and not is_self_dev_path_forbidden("web/src/app/page.tsx")
            ),
        }
    )

    # 7. >=10 coded platform skills available with schemas
    checks.append(
        {
            "name": "coded_platform_skills_coverage",
            "passed": len(CODED_SKILLS_SPECS) >= 13 and all("schema" in s for s in CODED_SKILLS_SPECS),
        }
    )

    passed_count = sum(1 for c in checks if c["passed"])
    total_count = len(checks)
    score = round((passed_count / total_count) * 100.0, 2) if total_count else 0.0

    prev = (
        db.query(EvalRun)
        .filter(EvalRun.workspace_id == workspace_id, EvalRun.suite_name == suite_name)
        .order_by(EvalRun.created_at.desc())
        .first()
    )
    delta = round(score - float(prev.score), 2) if prev else 0.0

    row = EvalRun(
        workspace_id=workspace_id,
        suite_name=suite_name,
        score=score,
        passed_count=passed_count,
        total_count=total_count,
        delta_vs_prev=delta,
        results_json=checks,
    )
    db.add(row)
    db.flush()
    return serialize_eval_run(row)
