"""Backlink & General Browser Session Replay (§1C — zero secrets, fuzzy selector matching, versioned playbooks)."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.models.v3_harness import BlSession

_SENSITIVE_FIELD_MARKERS = (
    "password",
    "pass",
    "secret",
    "token",
    "otp",
    "code",
    "api_key",
    "activation_link",
    "activation_url",
)


def _safe_domain_slug(domain: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", (domain or "site").strip().lower()).strip("._-")
    return cleaned or "site"


def sanitize_replay_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip any raw values, passwords, codes, or tokens from steps before persisting."""
    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            continue
        v_key = str(raw.get("value_key") or "").strip() or None
        if v_key:
            low_vk = v_key.lower()
            if any(marker in low_vk for marker in _SENSITIVE_FIELD_MARKERS) and low_vk not in {"site_password_vault", "google_signin", "bio_variant"}:
                v_key = "redacted_ref"
            if "@" in v_key or "http" in low_vk or "sk-" in low_vk:
                v_key = "redacted_ref"

        sel_css = raw.get("selector_css") or raw.get("selector")
        if isinstance(sel_css, str) and ("token=" in sel_css.lower() or "code=" in sel_css.lower()):
            sel_css = sel_css.split("?")[0]

        out.append(
            {
                "n": int(raw.get("n") or idx),
                "action": str(raw.get("action") or "click"),
                "selector_css": str(sel_css) if sel_css else None,
                "el_id": str(raw["el_id"]) if raw.get("el_id") else None,
                "el_class": str(raw["el_class"]) if raw.get("el_class") else None,
                "el_text": str(raw["el_text"]) if raw.get("el_text") else None,
                "value_key": v_key,
                "ok": int(raw.get("ok", 1)),
            }
        )
    return out


def save_backlink_session(
    db: Session,
    *,
    workspace_id: str,
    domain: str,
    steps: list[dict[str, Any]],
    site_id: str | None = None,
) -> BlSession:
    """Save a sanitized, versioned browser session replay + export playbook.json on disk."""
    norm_domain = (domain or "").strip().lower()
    clean_steps = sanitize_replay_steps(steps)

    latest = (
        db.query(BlSession)
        .filter(BlSession.workspace_id == workspace_id, BlSession.domain == norm_domain)
        .order_by(BlSession.version.desc())
        .first()
    )
    next_version = (latest.version + 1) if latest else 1

    playbook_dir = get_settings().data_dir / "playbooks" / _safe_domain_slug(norm_domain)
    playbook_dir.mkdir(parents=True, exist_ok=True)
    playbook_file = playbook_dir / "playbook.json"
    playbook_payload = {
        "domain": norm_domain,
        "version": next_version,
        "saved_at": utcnow().isoformat(),
        "steps": clean_steps,
    }
    playbook_file.write_text(json.dumps(playbook_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    row = BlSession(
        workspace_id=workspace_id,
        site_id=site_id,
        domain=norm_domain,
        version=next_version,
        steps_json=clean_steps,
        playbook_path=str(playbook_file),
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def match_step_selector(
    step: dict[str, Any],
    available_elements: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str | None]:
    """Fuzzy selector matching in priority order: selector_css -> el_id -> el_class -> el_text."""
    if available_elements is None:
        if step.get("selector_css"):
            return str(step["selector_css"]), "selector_css"
        if step.get("el_id"):
            return f"#{step['el_id']}", "el_id"
        if step.get("el_class"):
            return f".{str(step['el_class']).split()[0]}", "el_class"
        if step.get("el_text"):
            return f"text={step['el_text']}", "el_text"
        if step.get("action") == "goto":
            return step.get("value_key") or "goto", "action"
        return None, None

    if step.get("action") == "goto":
        return step.get("value_key") or "goto", "action"

    # Search live/provided DOM element descriptors in strict priority
    sel_css = step.get("selector_css")
    if sel_css:
        for el in available_elements:
            if el.get("selector_css") == sel_css:
                return str(sel_css), "selector_css"

    el_id = step.get("el_id")
    if el_id:
        for el in available_elements:
            if el.get("id") == el_id or el.get("el_id") == el_id:
                return f"#{el_id}", "el_id"

    el_cls = (step.get("el_class") or "").strip().lower()
    if el_cls:
        target_tokens = set(el_cls.split())
        for el in available_elements:
            cand = str(el.get("class") or el.get("el_class") or "").lower()
            if target_tokens & set(cand.split()):
                resolved = el.get("selector_css") or f".{list(target_tokens & set(cand.split()))[0]}"
                return str(resolved), "el_class"

    el_text = (step.get("el_text") or "").strip().lower()
    if el_text:
        for el in available_elements:
            cand_txt = str(el.get("text") or el.get("el_text") or "").strip().lower()
            if cand_txt and (el_text in cand_txt or cand_txt in el_text):
                resolved = el.get("selector_css") or f"text={el.get('text') or el.get('el_text')}"
                return str(resolved), "el_text"

    return None, None


def replay_backlink_session(
    db: Session,
    *,
    workspace_id: str,
    domain: str,
    available_elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Replay latest saved session for `domain`, updating saved-step & saved-LLM-call stats."""
    norm_domain = (domain or "").strip().lower()
    latest = (
        db.query(BlSession)
        .filter(BlSession.workspace_id == workspace_id, BlSession.domain == norm_domain)
        .order_by(BlSession.version.desc())
        .first()
    )
    if not latest:
        return {
            "found": False,
            "domain": norm_domain,
            "version": 0,
            "matched_steps": 0,
            "total_steps": 0,
            "saved_llm_calls": 0,
            "replay_count": 0,
            "resolved_steps": [],
        }

    steps = list(latest.steps_json or [])
    resolved: list[dict[str, Any]] = []
    matched = 0
    for step in steps:
        sel, via = match_step_selector(step, available_elements)
        if sel:
            matched += 1
        resolved.append({**step, "matched_selector": sel, "matched_by": via})

    saved_llm = max(1, (matched + 1) // 2) if matched > 0 else 0
    latest.replay_count = int(latest.replay_count or 0) + 1
    latest.saved_steps_total = int(latest.saved_steps_total or 0) + matched
    latest.saved_llm_calls_total = int(latest.saved_llm_calls_total or 0) + saved_llm
    latest.last_replay_at = utcnow()
    db.flush()

    return {
        "found": True,
        "session_id": latest.id,
        "domain": norm_domain,
        "version": latest.version,
        "matched_steps": matched,
        "total_steps": len(steps),
        "saved_llm_calls": saved_llm,
        "replay_count": latest.replay_count,
        "saved_steps_total": latest.saved_steps_total,
        "saved_llm_calls_total": latest.saved_llm_calls_total,
        "playbook_path": latest.playbook_path,
        "resolved_steps": resolved,
    }


def get_replay_stats_by_domain(db: Session, workspace_id: str) -> dict[str, dict[str, Any]]:
    rows = (
        db.query(BlSession)
        .filter(BlSession.workspace_id == workspace_id)
        .order_by(BlSession.version.desc())
        .all()
    )
    agg: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = agg.get(row.domain)
        if not entry:
            agg[row.domain] = {
                "domain": row.domain,
                "latest_version": row.version,
                "replay_count": int(row.replay_count or 0),
                "saved_steps_total": int(row.saved_steps_total or 0),
                "saved_llm_calls_total": int(row.saved_llm_calls_total or 0),
                "last_replay_at": row.last_replay_at.isoformat() if row.last_replay_at else None,
                "playbook_path": row.playbook_path,
            }
        else:
            entry["replay_count"] += int(row.replay_count or 0)
            entry["saved_steps_total"] += int(row.saved_steps_total or 0)
            entry["saved_llm_calls_total"] += int(row.saved_llm_calls_total or 0)
    return agg
