"""Inject a solved captcha token into the live browser-worker session.

A token obtained by any solver plugin (PassXAPI / 2Captcha / Anti-Captcha /
CapSolver / CapMonster) is worthless unless it is submitted from the SAME
browser session and IP that will complete the form. This helper POSTs the token
to the browser-worker endpoint POST /sessions/inject-captcha-token, which
re-opens the SAME persistent profile, injects the token into the response
field, and reports whether the challenge is gone.

Same conflict warning as the solver plugins: this is part of the opt-in,
owner-installed captcha pack and conflicts with repo AGENTS.md rule 2
(Human-in-the-Loop only). The token is passed in the request body (never in
a URL, never logged) and the caller must wipe its copy immediately after.

Returns one of: "completed" | "still_challenged" | "unavailable" | "no_token" | "error"
"""

from __future__ import annotations

from typing import Any

import httpx


async def inject_token_via_worker(
    *,
    worker_url: str,
    run_id: str,
    profile_id: str = "default",
    url: str,
    token: str,
    token_field: str = "g-recaptcha-response",
    challenge_type: str = "recaptcha",
    proxy: str | None = None,
    submit_selector: str | None = None,
    timeout_sec: float = 60.0,
) -> str:
    if not token:
        return "no_token"
    if not (worker_url or "").strip():
        return "unavailable"
    payload: dict[str, Any] = {
        "run_id": run_id,
        "profile_id": profile_id,
        "url": url,
        "token": token,
        "token_field": token_field,
        "challenge_type": challenge_type,
    }
    if proxy:
        payload["proxy"] = proxy
    if submit_selector:
        payload["submit_selector"] = submit_selector
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(
                f"{worker_url.rstrip('/')}/sessions/inject-captcha-token",
                json=payload,
            )
    except Exception:
        return "error"
    if resp.status_code != 200:
        return "error"
    try:
        data = resp.json()
    except Exception:
        return "error"
    status = str((data or {}).get("status") or "")
    return status if status in ("completed", "still_challenged") else "error"
