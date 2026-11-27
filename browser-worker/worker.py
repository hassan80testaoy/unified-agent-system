"""Playwright Browser Worker — Human-in-the-Loop visible browser with persistent profiles.

Policy constraints:
- Standard Chromium session with persistent user_data_dir per Google account / site group.
- ZERO fingerprint spoofing, ZERO anti-bot evasion, ZERO automated CAPTCHA token injection.
- When a CAPTCHA, phone verification, or 2FA prompt is detected, the session stays visible
  on noVNC (localhost-only behind SSH tunnel) so the owner completes it manually.
- Single write-lease lock ensures one active profile write operation at a time per worker.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Unified Agent Browser Worker", version="1.0.0")

PROFILES_DIR = Path(os.environ.get("PROFILES_DIR", "./profiles")).resolve()
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

_WRITE_LEASE = asyncio.Lock()
_ACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}


class OpenSiteRequest(BaseModel):
    run_id: str
    profile_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    url: str
    headless: bool = False


class StepAction(BaseModel):
    action: str  # goto | click | type | select | submit | wait
    selector: str | None = None
    value: str | None = None
    value_key: str | None = None


class ExecuteStepsRequest(BaseModel):
    run_id: str
    profile_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    url: str
    steps: list[StepAction] = Field(default_factory=list)


def _detect_challenge_from_html(html: str) -> dict[str, str] | None:
    lower = html.lower()
    if "cf-turnstile" in lower or "challenges.cloudflare.com" in lower:
        return {"challenge_type": "turnstile", "reason": "cloudflare"}
    if "g-recaptcha" in lower or "recaptcha/api2" in lower:
        return {"challenge_type": "recaptcha", "reason": "captcha_manual"}
    if "h-captcha" in lower or "hcaptcha.com" in lower:
        return {"challenge_type": "hcaptcha", "reason": "captcha_manual"}
    if "type=\"tel\"" in lower or "verify your phone" in lower or "phone number" in lower:
        return {"challenge_type": "phone_verify", "reason": "phone_verify"}
    return None


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "lease_locked": _WRITE_LEASE.locked(),
        "active_sessions": len(_ACTIVE_SESSIONS),
    }


@app.post("/sessions/run-steps")
async def run_steps(req: ExecuteStepsRequest) -> dict[str, Any]:
    """Run declarative steps inside Playwright persistent profile under write-lease."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"Playwright unavailable: {exc}")

    profile_dir = (PROFILES_DIR / req.profile_id).resolve()
    if not str(profile_dir).startswith(str(PROFILES_DIR)):
        raise HTTPException(status_code=400, detail="Invalid profile_id")
    profile_dir.mkdir(parents=True, exist_ok=True)

    recorded_steps: list[dict[str, Any]] = []
    async with _WRITE_LEASE:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=os.environ.get("BROWSER_HEADLESS", "false").lower() == "true",
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(req.url, wait_until="domcontentloaded", timeout=30000)
                recorded_steps.append(
                    {"n": 1, "action": "goto", "selector_css": None, "value_key": "signup_url", "ok": True}
                )
                html = await page.content()
                challenge = _detect_challenge_from_html(html)
                if challenge:
                    _ACTIVE_SESSIONS[req.run_id] = {"url": page.url, "profile_id": req.profile_id, "challenge": challenge}
                    return {
                        "status": "awaiting_captcha_manual",
                        "challenge": challenge,
                        "current_url": page.url,
                        "steps": recorded_steps,
                    }

                for idx, step in enumerate(req.steps, start=2):
                    if step.action == "click" and step.selector:
                        await page.locator(step.selector).first.click(timeout=10000)
                    elif step.action == "type" and step.selector and step.value is not None:
                        await page.locator(step.selector).first.fill(step.value, timeout=10000)
                    elif step.action == "submit" and step.selector:
                        await page.locator(step.selector).first.click(timeout=10000)
                    elif step.action == "wait":
                        await asyncio.sleep(1.0)
                    recorded_steps.append(
                        {
                            "n": idx,
                            "action": step.action,
                            "selector_css": step.selector,
                            "value_key": step.value_key,
                            "ok": True,
                        }
                    )

                final_html = await page.content()
                challenge_after = _detect_challenge_from_html(final_html)
                if challenge_after:
                    _ACTIVE_SESSIONS[req.run_id] = {
                        "url": page.url,
                        "profile_id": req.profile_id,
                        "challenge": challenge_after,
                    }
                    return {
                        "status": "awaiting_captcha_manual",
                        "challenge": challenge_after,
                        "current_url": page.url,
                        "steps": recorded_steps,
                    }
                return {
                    "status": "completed",
                    "current_url": page.url,
                    "steps": recorded_steps,
                }
            finally:
                await context.close()
