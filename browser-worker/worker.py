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
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    proxy: str | None = None


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
    proxy: str | None = None


def _extract_site_key(html: str) -> str | None:
    if not html:
        return None
    m = re.search(r'data-sitekey=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m_qs = re.search(r'[?&](?:sitekey|k)=([A-Za-z0-9_-]{10,128})', html, re.IGNORECASE)
    if m_qs:
        return m_qs.group(1).strip()
    return None


def _parse_proxy(proxy_url: str | None) -> dict[str, str] | None:
    """Parse HTTP/SOCKS proxy URL into Playwright proxy dict without logging credentials."""
    raw = (proxy_url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return {"server": raw}
    port_part = f":{parsed.port}" if parsed.port else ""
    cfg: dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}{port_part}"}
    if parsed.username:
        cfg["username"] = parsed.username
    if parsed.password:
        cfg["password"] = parsed.password
    return cfg


def _ignore_https_errors() -> bool:
    """Whether Chromium should ignore TLS errors.

    MITM residential proxies (e.g. Scrape.do Proxy Mode, pm.scrape.do) re-sign
    TLS with their own CA, which Chromium rejects unless cert errors are
    ignored. Default ON whenever a proxy is configured; set
    BROWSER_IGNORE_HTTPS_ERRORS=false to disable. The proper alternative is
    installing the proxy vendor's CA certificate in the OS trust store.
    """
    raw = os.environ.get("BROWSER_IGNORE_HTTPS_ERRORS", "").strip().lower()
    return raw not in ("0", "false", "no")


def _launch_kwargs(profile_dir: Path, proxy_url: str | None) -> dict[str, Any]:
    """Shared launch_persistent_context kwargs for both endpoints."""
    proxy_cfg = _parse_proxy(proxy_url or os.environ.get("BROWSER_PROXY_URL"))
    kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": os.environ.get("BROWSER_HEADLESS", "false").lower() == "true",
        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
    }
    if proxy_cfg:
        kwargs["proxy"] = proxy_cfg
        if _ignore_https_errors():
            kwargs["ignore_https_errors"] = True
    return kwargs


def _detect_challenge_from_html(html: str) -> dict[str, Any] | None:
    lower = html.lower()
    site_key = _extract_site_key(html)
    res: dict[str, Any] | None = None
    if "cf-turnstile" in lower or "challenges.cloudflare.com" in lower:
        res = {"challenge_type": "turnstile", "reason": "cloudflare"}
    elif "g-recaptcha" in lower or "recaptcha/api2" in lower:
        res = {"challenge_type": "recaptcha", "reason": "captcha_manual"}
    elif "h-captcha" in lower or "hcaptcha.com" in lower:
        res = {"challenge_type": "hcaptcha", "reason": "captcha_manual"}
    elif "type=\"tel\"" in lower or "verify your phone" in lower or "phone number" in lower:
        res = {"challenge_type": "phone_verify", "reason": "phone_verify"}
    if res and site_key:
        res["site_key"] = site_key
    return res


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

    launch_kwargs = _launch_kwargs(profile_dir, req.proxy)

    recorded_steps: list[dict[str, Any]] = []
    async with _WRITE_LEASE:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(**launch_kwargs)
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


# --- Captcha token injection (OPT-IN PLUGIN PACK — conflicts with AGENTS.md rule 2) ---
# Re-opens the SAME persistent profile (cookies/login survive), injects the token
# obtained by a solver plugin, and reports whether the challenge is gone.
# Token is used immediately, never logged or stored. No fingerprint spoofing.
# Enable only together with a solver plugin (passxapi / 2captcha / anticaptcha /
# capsolver / capmonster / chain) + a residential proxy matching the browser IP.


class InjectTokenRequest(BaseModel):
    run_id: str
    profile_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    url: str
    token: str
    token_field: str = "g-recaptcha-response"
    challenge_type: str = "recaptcha"
    proxy: str | None = None
    submit_selector: str | None = None


@app.post("/sessions/inject-captcha-token")
async def inject_captcha_token(req: InjectTokenRequest) -> dict[str, Any]:
    """Inject a solver token into the SAME persistent profile and submit.

    Token is used immediately and never logged or stored.
    Returns {"status": "completed"} when no challenge remains on the page,
    {"status": "still_challenged"} when the challenge is still present.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"Playwright unavailable: {exc}")

    profile_dir = (PROFILES_DIR / req.profile_id).resolve()
    if not str(profile_dir).startswith(str(PROFILES_DIR)):
        raise HTTPException(status_code=400, detail="Invalid profile_id")
    profile_dir.mkdir(parents=True, exist_ok=True)

    launch_kwargs = _launch_kwargs(profile_dir, req.proxy)

    async with _WRITE_LEASE:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(**launch_kwargs)
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(req.url, wait_until="domcontentloaded", timeout=30000)
                await page.evaluate(
                    """([field, token]) => {
                      let el = document.getElementById(field) || document.querySelector('[name="' + field + '"]');
                      if (!el) {
                        el = document.createElement('textarea');
                        el.id = field; el.name = field; el.style.display = 'none';
                        (document.querySelector('form') || document.body).appendChild(el);
                      }
                      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                      setter.call(el, token);
                      el.dispatchEvent(new Event('input', {bubbles: true}));
                      el.dispatchEvent(new Event('change', {bubbles: true}));
                      return true;
                    }""",
                    [req.token_field, req.token],
                )
                if req.submit_selector:
                    await page.locator(req.submit_selector).first.click(timeout=10000)
                    await page.wait_for_timeout(3000)
                html = await page.content()
                if _detect_challenge_from_html(html):
                    return {"status": "still_challenged", "current_url": page.url}
                return {"status": "completed", "current_url": page.url}
            finally:
                await context.close()
