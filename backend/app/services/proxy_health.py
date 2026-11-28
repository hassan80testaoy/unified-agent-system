"""Proxy health detection, one-time direct fallback tracking, and runtime-status reporting (§7.0)."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.core import SystemState
from app.security.sanitizer import scrub_text

_STATE_KEY = "proxy_health_state"
_LOCK = threading.Lock()
_IN_MEMORY_STATE: dict[str, Any] = {}


def is_proxy_limit_or_auth_failure(snap: dict[str, Any] | None = None, error_text: str | None = None) -> str | None:
    """Return a concise reason string if browser response/error indicates an exhausted or failing proxy."""
    err = (error_text or "").strip()
    low_err = err.lower()
    if any(
        tok in low_err
        for tok in (
            "monthly request limit",
            "limit exceeded",
            "quota exceeded",
            "dashboard.scrape.do",
            "err_proxy_connection_failed",
            "err_tunnel_connection_failed",
            "err_connection_reset",
            "proxy authentication required",
            "http 401",
            "status_code=401",
            "status=401",
            "http 403",
            "http 407",
            "http 429",
        )
    ):
        return scrub_text(err)[:180] or "proxy_error_or_limit"

    if snap:
        http_status = snap.get("http_status")
        vis = str(snap.get("visible_text") or "")
        title = str(snap.get("title") or "")
        combined = f"{title}\n{vis}".lower()
        if any(
            tok in combined
            for tok in (
                "dashboard.scrape.do",
                "monthly request limit exceeded",
                "request limit exceeded",
                "quota exceeded",
                "proxy authentication required",
                '"statuscode":401',
                '"statuscode": 401',
                '"statuscode":403',
                '"statuscode":429',
            )
        ):
            return f"proxy_http_{http_status or 401}_limit_exceeded"
        if isinstance(http_status, int) and http_status in (401, 403, 407, 429):
            return f"proxy_http_{http_status}"

    return None


def record_proxy_outcome(
    db: Session | None,
    *,
    configured: bool,
    path: str,
    fell_back_to_direct: bool,
    error: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Persist sanitized proxy health telemetry without exposing proxy credentials."""
    now_iso = datetime.now(timezone.utc).isoformat()
    safe_err = scrub_text(error)[:200] if error else None
    safe_url = scrub_text(url)[:200] if url else None

    with _LOCK:
        prev = dict(_IN_MEMORY_STATE)
        state = {
            "configured": bool(configured),
            "status": (
                "unconfigured"
                if not configured
                else ("degraded_limit" if (fell_back_to_direct or safe_err) else "ok")
            ),
            "last_path": path,
            "last_fell_back_to_direct": bool(fell_back_to_direct),
            "last_error": safe_err if (fell_back_to_direct or safe_err) else prev.get("last_error"),
            "last_error_at": now_iso if (fell_back_to_direct or safe_err) else prev.get("last_error_at"),
            "last_checked_at": now_iso,
            "last_target_url": safe_url,
        }
        _IN_MEMORY_STATE.clear()
        _IN_MEMORY_STATE.update(state)

    if db is not None:
        try:
            row = db.get(SystemState, _STATE_KEY)
            if row is None:
                db.add(SystemState(key=_STATE_KEY, value_json=state))
            else:
                row.value_json = state
            db.flush()
        except Exception:
            pass
    return dict(state)


def get_proxy_health(db: Session | None = None) -> dict[str, Any]:
    """Return proxy status summary for /api/system/runtime-status (no secret URL/token leak)."""
    settings = get_settings()
    configured = bool((settings.residential_proxy or "").strip())

    state: dict[str, Any] = {}
    with _LOCK:
        if _IN_MEMORY_STATE:
            state = dict(_IN_MEMORY_STATE)

    if not state and db is not None:
        try:
            row = db.get(SystemState, _STATE_KEY)
            if row and isinstance(row.value_json, dict):
                state = dict(row.value_json)
        except Exception:
            pass

    default_status = "ok" if configured else "unconfigured"
    if not state:
        return {
            "configured": configured,
            "status": default_status,
            "last_path": "proxy" if configured else "direct",
            "last_fell_back_to_direct": False,
            "last_error": None,
            "last_error_at": None,
            "last_checked_at": None,
            "last_target_url": None,
        }

    # Always reflect live `.env` configured boolean cleanly
    out = dict(state)
    out["configured"] = configured
    if not configured:
        out["status"] = "unconfigured"
    return out


def reset_proxy_health_cache() -> None:
    """Clear in-memory cache (used by tests to isolate runs)."""
    with _LOCK:
        _IN_MEMORY_STATE.clear()
