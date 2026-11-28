"""ManualSolver — Human-in-the-Loop CAPTCHA & challenge handler via localhost-only noVNC.

Workflow:
1. `detect(page)` inspects DOM / page structure (dict or Playwright Page) for CAPTCHA,
   Cloudflare challenge, image CAPTCHA, or phone verification requests.
2. `solve(challenge)` pauses the run on `awaiting_captcha_manual`, returns SSH tunnel
   instructions for `127.0.0.1:6080` noVNC, and never injects tokens or spoofs fingerprints.
3. `on_manual_solved(run_id)` unblocks any waiting manual CAPTCHA gate so the run resumes.
4. `record_captcha_attempt(...)` & `get_captcha_stats(...)` track real measured outcomes
   (passed / skipped / timeout) in `captcha_attempts`.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.captcha.solver import CaptchaChallenge, CaptchaResult, register_solver
from app.config import get_settings
from app.models.agents import Approval, Run
from app.models.captcha import CaptchaAttempt
from app.services.events import emit_and_broadcast_run_event
from app.services.notifications import create_notification, send_telegram_alert


class ManualCaptchaCoordinator:
    """In-memory async signal hub for manual noVNC resolution or user skip per run."""

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[str]] = {}
        self._started_at: dict[str, float] = {}

    def register_waiter(self, run_id: str) -> asyncio.Future[str]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._waiters[run_id] = fut
        self._started_at[run_id] = time.monotonic()
        return fut

    def mark_solved(self, run_id: str) -> bool:
        fut = self._waiters.get(run_id)
        if fut is not None and not fut.done():
            fut.set_result("passed")
            return True
        return False

    def mark_skipped(self, run_id: str, reason: str = "manual_skipped") -> bool:
        fut = self._waiters.get(run_id)
        if fut is not None and not fut.done():
            fut.set_result(f"skipped:{reason}")
            return True
        return False

    def elapsed_ms(self, run_id: str) -> int:
        start = self._started_at.pop(run_id, None)
        if start is None:
            return 0
        return int((time.monotonic() - start) * 1000)

    def clear(self, run_id: str) -> None:
        self._waiters.pop(run_id, None)
        self._started_at.pop(run_id, None)


manual_captcha_coordinator = ManualCaptchaCoordinator()


def novnc_tunnel_instructions() -> dict[str, Any]:
    settings = get_settings()
    port = settings.novnc_local_port
    return {
        "ssh_command": f"ssh -L {port}:127.0.0.1:{port} {settings.vps_ssh_host}",
        "local_url": f"http://127.0.0.1:{port}/vnc.html",
        "localhost_only": True,
    }


def _extract_site_key(html: str) -> str | None:
    """Passively extract public widget data-sitekey from challenge HTML if present."""
    if not html:
        return None
    m = re.search(r'data-sitekey=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m_qs = re.search(r'[?&](?:sitekey|k)=([A-Za-z0-9_-]{10,128})', html, re.IGNORECASE)
    if m_qs:
        return m_qs.group(1).strip()
    return None


@register_solver("manual")
class ManualSolver:
    """Default & sole built-in solver: human completes challenge in live noVNC browser."""

    name = "manual"

    def detect(self, page: Any) -> CaptchaChallenge | None:
        """Detect challenge from either a structured dict (mock/worker result) or Playwright page."""
        if isinstance(page, dict):
            ch_type = page.get("challenge_type")
            if not ch_type:
                return None
            return CaptchaChallenge(
                run_id=str(page.get("run_id") or ""),
                site=str(page.get("site") or ""),
                challenge_type=str(ch_type),
                page_url=str(page.get("page_url") or ""),
                site_key=page.get("site_key"),
                blocked_reason=str(page.get("blocked_reason") or ("phone_verify" if ch_type == "phone_verify" else "captcha_manual")),
                screenshot_path=page.get("screenshot_path"),
                metadata=dict(page.get("metadata") or {}),
            )

        # Fallback duck-typing for Playwright synchronous page or HTML string
        html = ""
        page_url = ""
        if isinstance(page, str):
            html = page
        elif hasattr(page, "content"):
            try:
                html = page.content()
                page_url = getattr(page, "url", "")
            except Exception:
                return None

        lower = html.lower()
        detected_type = None
        blocked_reason = "captcha_manual"
        if "cf-turnstile" in lower or "challenges.cloudflare.com" in lower:
            detected_type = "turnstile"
            blocked_reason = "cloudflare"
        elif "g-recaptcha" in lower or "recaptcha/api2" in lower:
            detected_type = "recaptcha"
        elif "h-captcha" in lower or "hcaptcha.com" in lower:
            detected_type = "hcaptcha"
        elif 'type="tel"' in lower or "verify your phone" in lower:
            detected_type = "phone_verify"
            blocked_reason = "phone_verify"
        elif "captcha" in lower and "<img" in lower:
            detected_type = "image_captcha"
            blocked_reason = "image_captcha"

        if not detected_type:
            return None

        return CaptchaChallenge(
            run_id="",
            site=page_url,
            challenge_type=detected_type,
            page_url=page_url,
            site_key=_extract_site_key(html),
            blocked_reason=blocked_reason,
        )

    def solve(self, challenge: CaptchaChallenge) -> CaptchaResult:
        """Return awaiting_captcha_manual state & SSH tunnel instructions (spend is always $0.00)."""
        if challenge.challenge_type == "phone_verify":
            return CaptchaResult(
                status="phone_verify_blocked",
                method="manual",
                blocked_reason="phone_verify",
                cost_usd=0.0,
                instructions=novnc_tunnel_instructions(),
            )
        return CaptchaResult(
            status="awaiting_captcha_manual",
            method="manual",
            blocked_reason=challenge.blocked_reason,
            cost_usd=0.0,
            instructions=novnc_tunnel_instructions(),
        )

    def on_manual_solved(self, run_id: str) -> None:
        manual_captcha_coordinator.mark_solved(run_id)


async def pause_for_manual_captcha(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    run: Run,
    challenge: CaptchaChallenge,
    timeout_sec: int | None = None,
) -> CaptchaResult:
    """Pause run on `awaiting_captcha_manual`, notify dashboard + Telegram, and wait or timeout."""
    settings = get_settings()
    wait_sec = timeout_sec or settings.captcha_manual_timeout_sec
    solver = ManualSolver()
    initial = solver.solve(challenge)

    # Phone verification is never bypassed/faked; stop site immediately with reason
    if initial.status == "phone_verify_blocked":
        attempt = CaptchaAttempt(
            workspace_id=workspace_id,
            run_id=run.id,
            site=challenge.site,
            challenge_type=challenge.challenge_type,
            method="manual",
            proxy_type="direct",
            duration_ms=0,
            cost_usd=0.0,
            result="skipped",
            blocked_reason="phone_verify",
        )
        db.add(attempt)
        db.commit()
        await emit_and_broadcast_run_event(
            db,
            workspace_id=workspace_id,
            run_id=run.id,
            level="warn",
            kind="captcha_phone_blocked",
            message=f"تم تخطي الموقع {challenge.site} لأنه يتطلب تحقق بالهاتف (غير مسموح تلقائياً).",
            data={"site": challenge.site, "blocked_reason": "phone_verify"},
        )
        return initial

    tunnel = novnc_tunnel_instructions()
    run.status = "awaiting_captcha_manual"
    approval = Approval(
        workspace_id=workspace_id,
        run_id=run.id,
        kind="captcha_manual",
        payload={
            "site": challenge.site,
            "challenge_type": challenge.challenge_type,
            "page_url": challenge.page_url,
            "tunnel": tunnel,
        },
        status="pending",
    )
    db.add(approval)
    create_notification(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        kind="captcha_manual",
        title=f"مطلوب حل كابتشا يدوياً — {challenge.site}",
        body=f"التحدي: {challenge.challenge_type}. افتح المتصفح الحي عبر نفق SSH ثم اضغط تم الحل.",
        link=f"/runs/{run.id}",
    )
    db.commit()

    await emit_and_broadcast_run_event(
        db,
        workspace_id=workspace_id,
        run_id=run.id,
        level="needs_user",
        kind="awaiting_captcha_manual",
        message=f"بانتظار حل الكابتشا يدوياً للموقع {challenge.site} ({challenge.challenge_type}) عبر noVNC.",
        data={"site": challenge.site, "challenge_type": challenge.challenge_type, "tunnel": tunnel},
    )
    await send_telegram_alert(
        f"[Unified Agent] Manual CAPTCHA needed\nSite: {challenge.site}\nType: {challenge.challenge_type}\nRun: {run.id}\nTunnel: {tunnel['ssh_command']}"
    )

    fut = manual_captcha_coordinator.register_waiter(run.id)
    try:
        outcome = await asyncio.wait_for(fut, timeout=wait_sec)
        elapsed = manual_captcha_coordinator.elapsed_ms(run.id)
        if outcome == "passed":
            approval.status = "approved"
            run.status = "running"
            attempt = CaptchaAttempt(
                workspace_id=workspace_id,
                run_id=run.id,
                site=challenge.site,
                challenge_type=challenge.challenge_type,
                method="manual",
                proxy_type="direct",
                duration_ms=elapsed,
                cost_usd=0.0,
                result="passed",
                solved_by=user_id,
            )
            db.add(attempt)
            db.commit()
            return CaptchaResult(status="passed", method="manual", duration_ms=elapsed, cost_usd=0.0, instructions=tunnel)

        reason = outcome.split(":", 1)[1] if ":" in outcome else "manual_skipped"
        approval.status = "rejected"
        attempt = CaptchaAttempt(
            workspace_id=workspace_id,
            run_id=run.id,
            site=challenge.site,
            challenge_type=challenge.challenge_type,
            method="manual",
            proxy_type="direct",
            duration_ms=elapsed,
            cost_usd=0.0,
            result="skipped",
            blocked_reason=reason,
        )
        db.add(attempt)
        db.commit()
        return CaptchaResult(
            status="skipped",
            method="manual",
            blocked_reason=reason,
            duration_ms=elapsed,
            cost_usd=0.0,
            instructions=tunnel,
        )
    except asyncio.TimeoutError:
        elapsed = manual_captcha_coordinator.elapsed_ms(run.id)
        approval.status = "expired"
        attempt = CaptchaAttempt(
            workspace_id=workspace_id,
            run_id=run.id,
            site=challenge.site,
            challenge_type=challenge.challenge_type,
            method="manual",
            proxy_type="direct",
            duration_ms=elapsed,
            cost_usd=0.0,
            result="timeout",
            blocked_reason="manual_timeout",
        )
        db.add(attempt)
        db.commit()
        return CaptchaResult(
            status="timeout",
            method="manual",
            blocked_reason="manual_timeout",
            duration_ms=elapsed,
            cost_usd=0.0,
            instructions=tunnel,
        )
    finally:
        manual_captcha_coordinator.clear(run.id)


def get_captcha_stats(db: Session, workspace_id: str) -> dict[str, Any]:
    rows = db.query(CaptchaAttempt).filter(CaptchaAttempt.workspace_id == workspace_id).all()
    total = len(rows)
    passed = sum(1 for r in rows if r.result == "passed")
    skipped = sum(1 for r in rows if r.result == "skipped")
    timeout = sum(1 for r in rows if r.result == "timeout")
    pending = sum(1 for r in rows if r.result == "pending")

    by_type: dict[str, dict[str, int]] = {}
    by_site: dict[str, dict[str, int]] = {}
    for r in rows:
        t_bucket = by_type.setdefault(r.challenge_type, {"total": 0, "passed": 0, "skipped": 0, "timeout": 0})
        t_bucket["total"] += 1
        if r.result in t_bucket:
            t_bucket[r.result] += 1

        s_bucket = by_site.setdefault(r.site, {"total": 0, "passed": 0, "skipped": 0, "timeout": 0})
        s_bucket["total"] += 1
        if r.result in s_bucket:
            s_bucket[r.result] += 1

    resolved = passed + skipped + timeout
    return {
        "solver_mode": "manual",
        "max_spend_usd": get_settings().captcha_max_spend_usd,
        "total": total,
        "passed": passed,
        "skipped": skipped,
        "timeout": timeout,
        "pending": pending,
        "pass_rate_pct": round((passed / resolved) * 100, 1) if resolved else 0.0,
        "by_type": by_type,
        "by_site": by_site,
        "tunnel": novnc_tunnel_instructions(),
    }
