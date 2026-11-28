"""Interactive Playwright Browser Worker (Phase 6 General Browser Agent).

Extends the untouched `worker.app` FastAPI application with persistent,
session-scoped interactive browser tools (open, read, click, type, scroll,
screenshot, close) and PNG screenshot artifacts. Preserves ZERO fingerprint
spoofing and ZERO automated CAPTCHA token injection outside explicit owner
plugins.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from worker import PROFILES_DIR, _detect_challenge_from_html, _launch_kwargs, app

_DEFAULT_ARTIFACTS_DIR = (Path(__file__).resolve().parent.parent / "backend" / "data" / "artifacts").resolve()
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", str(_DEFAULT_ARTIFACTS_DIR))).resolve()
BROWSER_ARTIFACTS_DIR = ARTIFACTS_DIR / "browser"
BROWSER_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

_INTERACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSIONS_META_LOCK = asyncio.Lock()
_SESSION_IDLE_TTL_SEC = 600.0


def _safe_id(raw: str, fallback: str = "session") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", (raw or "").strip()).strip("_")[:64]
    return cleaned or fallback


def _resolve_proxy(req_proxy: str | None) -> str | None:
    return (
        (req_proxy or "").strip()
        or os.environ.get("BROWSER_PROXY_URL", "").strip()
        or os.environ.get("RESIDENTIAL_PROXY", "").strip()
        or None
    )


def _resolve_headless(req_headless: bool | None) -> bool:
    if req_headless is not None:
        return bool(req_headless)
    # Interactive worker defaults to headless on local Windows/VPS so chat
    # browsing works seamlessly out of the box unless headed/noVNC is set.
    return os.environ.get("BROWSER_HEADLESS", "true").strip().lower() not in {"0", "false", "no"}


async def _close_session_record(rec: dict[str, Any]) -> None:
    try:
        ctx = rec.get("context")
        if ctx is not None:
            await ctx.close()
    except Exception:
        pass
    try:
        pw = rec.get("pw")
        if pw is not None:
            await pw.stop()
    except Exception:
        pass


async def _cleanup_stale_sessions() -> None:
    now = time.monotonic()
    stale_keys: list[str] = []
    async with _SESSIONS_META_LOCK:
        for key, rec in _INTERACTIVE_SESSIONS.items():
            if (now - float(rec.get("last_used") or now)) > _SESSION_IDLE_TTL_SEC:
                stale_keys.append(key)
        popped = [_INTERACTIVE_SESSIONS.pop(k) for k in stale_keys]
    for rec in popped:
        await _close_session_record(rec)


def _build_interactive_launch_kwargs(
    profile_dir: Path,
    eff_proxy: str | None,
    *,
    bypass_proxy: bool,
    headless: bool | None,
) -> dict[str, Any]:
    """Build Chromium launch kwargs while strictly enforcing bypass_proxy over env BROWSER_PROXY_URL."""
    kwargs = _launch_kwargs(profile_dir, None if bypass_proxy else eff_proxy)
    if bypass_proxy:
        kwargs.pop("proxy", None)
        kwargs.pop("ignore_https_errors", None)
    elif isinstance(kwargs.get("proxy"), dict):
        kwargs["proxy"].setdefault("bypass", "localhost,127.0.0.1")
    kwargs["headless"] = _resolve_headless(headless)
    return kwargs


async def _get_or_create_session(
    *,
    session_id: str,
    profile_id: str,
    proxy: str | None,
    headless: bool | None,
    bypass_proxy: bool = False,
) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    await _cleanup_stale_sessions()
    sid = _safe_id(session_id)
    pid = _safe_id(profile_id, fallback="default")
    eff_proxy = None if bypass_proxy else _resolve_proxy(proxy)
    want_proxy_used = bool(eff_proxy)

    stale_rec: dict[str, Any] | None = None
    async with _SESSIONS_META_LOCK:
        existing = _INTERACTIVE_SESSIONS.get(sid)
        if existing is not None:
            if bool(existing.get("proxy_used")) == want_proxy_used:
                existing["last_used"] = time.monotonic()
                return existing
            stale_rec = _INTERACTIVE_SESSIONS.pop(sid, None)
    if stale_rec is not None:
        await _close_session_record(stale_rec)

    profile_dir = (PROFILES_DIR / f"interactive_{pid}_{sid[:16]}").resolve()
    if not str(profile_dir).startswith(str(PROFILES_DIR)):
        raise HTTPException(status_code=400, detail="Invalid profile_id")
    profile_dir.mkdir(parents=True, exist_ok=True)

    kwargs = _build_interactive_launch_kwargs(
        profile_dir,
        eff_proxy,
        bypass_proxy=bool(bypass_proxy),
        headless=headless,
    )

    pw = await async_playwright().start()
    try:
        context = await pw.chromium.launch_persistent_context(**kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
    except Exception as exc:
        try:
            await pw.stop()
        except Exception:
            pass
        raise HTTPException(status_code=503, detail=f"Chromium launch failed: {type(exc).__name__}: {exc}") from exc

    rec = {
        "session_id": sid,
        "profile_id": pid,
        "proxy_used": want_proxy_used,
        "pw": pw,
        "context": context,
        "page": page,
        "lock": asyncio.Lock(),
        "step": 0,
        "last_used": time.monotonic(),
    }
    async with _SESSIONS_META_LOCK:
        _INTERACTIVE_SESSIONS[sid] = rec
    return rec


async def _snapshot_page(
    page: Any,
    session_id: str,
    action: str,
    step_num: int,
    *,
    capture_screenshot: bool = True,
    full_page: bool = False,
) -> dict[str, Any]:
    dom = await page.evaluate(
        """() => {
          const vis = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            return st && st.display !== 'none' && st.visibility !== 'hidden';
          };
          const buildSel = (el) => {
            if (el.id) return '#' + CSS.escape(el.id);
            const tag = el.tagName.toLowerCase();
            const nm = el.getAttribute('name');
            if (nm) return `${tag}[name="${nm.replace(/"/g, '\\"')}"]`;
            const tp = el.getAttribute('type');
            if (tp) return `${tag}[type="${tp.replace(/"/g, '\\"')}"]`;
            const ph = el.getAttribute('placeholder');
            if (ph) return `${tag}[placeholder="${ph.replace(/"/g, '\\"')}"]`;
            return tag;
          };
          const text = ((document.body && document.body.innerText) || '').replace(/\\n{3,}/g, '\\n\\n').trim().slice(0, 3500);
          const links = [];
          const seenHref = new Set();
          const linkNodes = Array.from(
            document.querySelectorAll('#search a[href], #rso a[href], main a[href], article a[href], a[href]')
          );
          for (const a of linkNodes) {
            const href = a.href || '';
            if (!href.startsWith('http') || seenHref.has(href)) continue;
            const lt = (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
            if (!lt) continue;
            seenHref.add(href);
            links.push({ text: lt.slice(0, 120), href });
            if (links.length >= 60) break;
          }
          const inputs = [];
          for (const el of Array.from(document.querySelectorAll('input, textarea, select'))) {
            if (!vis(el) || (el.getAttribute('type') || '').toLowerCase() === 'hidden') continue;
            let lbl = el.getAttribute('aria-label') || '';
            if (!lbl && el.id) {
              const lEl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
              if (lEl) lbl = lEl.innerText || '';
            }
            inputs.push({
              selector: buildSel(el),
              tag: el.tagName.toLowerCase(),
              type: el.getAttribute('type') || el.tagName.toLowerCase(),
              name: el.getAttribute('name') || '',
              id: el.id || '',
              placeholder: el.getAttribute('placeholder') || '',
              label: lbl.trim().slice(0, 80),
            });
            if (inputs.length >= 25) break;
          }
          const buttons = [];
          for (const el of Array.from(document.querySelectorAll('button, input[type="submit"], a[role="button"]'))) {
            if (!vis(el)) continue;
            const bt = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
            if (!bt) continue;
            buttons.push({ selector: buildSel(el), text: bt.slice(0, 80) });
            if (buttons.length >= 20) break;
          }
          return { title: document.title || '', visible_text: text, links, inputs, buttons };
        }"""
    )

    try:
        html = await page.content()
        challenge = _detect_challenge_from_html(html)
    except Exception:
        challenge = None

    screenshot_filename: str | None = None
    screenshot_url: str | None = None
    if capture_screenshot:
        safe_sid = _safe_id(session_id)
        safe_act = _safe_id(action, fallback="step")
        out_dir = BROWSER_ARTIFACTS_DIR / safe_sid
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot_filename = f"step_{step_num:03d}_{safe_act}.png"
        out_path = out_dir / screenshot_filename
        try:
            await page.screenshot(path=str(out_path), full_page=full_page, timeout=15000)
            screenshot_url = f"/api/browser/screenshots/{safe_sid}/{screenshot_filename}"
        except Exception:
            screenshot_filename = None
            screenshot_url = None

    return {
        "session_id": _safe_id(session_id),
        "step": step_num,
        "url": page.url,
        "title": dom.get("title") or "",
        "visible_text": dom.get("visible_text") or "",
        "links": dom.get("links") or [],
        "inputs": dom.get("inputs") or [],
        "buttons": dom.get("buttons") or [],
        "challenge": challenge,
        "screenshot_filename": screenshot_filename,
        "screenshot_url": screenshot_url,
    }


class InteractiveOpenRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    profile_id: str = "default"
    url: str = Field(min_length=1, max_length=2048)
    proxy: str | None = None
    bypass_proxy: bool = False
    headless: bool | None = None
    wait_ms: int = 800


class InteractiveReadRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    capture_screenshot: bool = False


class InteractiveClickRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    selector: str | None = None
    text: str | None = None
    wait_ms: int = 1000


class InteractiveTypeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    selector: str | None = None
    label: str | None = None
    text: str = ""
    press_enter: bool = False
    submit_selector: str | None = None
    secret: bool = False
    wait_ms: int = 800


class InteractiveScrollRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    direction: str = "down"
    amount: int = 700


class InteractiveScreenshotRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    full_page: bool = False


class InteractiveCloseRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)


@app.get("/interactive/health")
async def interactive_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "interactive": True,
        "active_interactive_sessions": len(_INTERACTIVE_SESSIONS),
    }


@app.post("/interactive/open")
async def interactive_open(req: InteractiveOpenRequest) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright as _  # noqa: F401
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"Playwright unavailable: {exc}")

    target_url = req.url.strip()
    if not target_url.startswith(("http://", "https://", "data:")):
        target_url = f"https://{target_url}"

    rec = await _get_or_create_session(
        session_id=req.session_id,
        profile_id=req.profile_id,
        proxy=req.proxy,
        headless=req.headless,
        bypass_proxy=bool(req.bypass_proxy),
    )
    async with rec["lock"]:
        rec["last_used"] = time.monotonic()
        page = rec["page"]
        try:
            resp = await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            http_status = int(resp.status) if resp is not None else None
            if req.wait_ms > 0:
                await page.wait_for_timeout(min(req.wait_ms, 5000))
            rec["step"] = int(rec["step"]) + 1
            snap = await _snapshot_page(page, req.session_id, "open", rec["step"], capture_screenshot=True)
            return {
                "ok": True,
                "http_status": http_status,
                "network_path": "proxy" if rec.get("proxy_used") else "direct",
                **snap,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Browser navigation failed ({target_url}): {type(exc).__name__}: {exc}",
            ) from exc


async def _require_session(session_id: str) -> dict[str, Any]:
    sid = _safe_id(session_id)
    async with _SESSIONS_META_LOCK:
        rec = _INTERACTIVE_SESSIONS.get(sid)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Interactive session '{sid}' is not open. Call /interactive/open first.")
    rec["last_used"] = time.monotonic()
    return rec


@app.post("/interactive/read")
async def interactive_read(req: InteractiveReadRequest) -> dict[str, Any]:
    rec = await _require_session(req.session_id)
    async with rec["lock"]:
        rec["step"] = int(rec["step"]) + 1
        snap = await _snapshot_page(
            rec["page"], req.session_id, "read", rec["step"], capture_screenshot=req.capture_screenshot
        )
        return {"ok": True, **snap}


@app.post("/interactive/click")
async def interactive_click(req: InteractiveClickRequest) -> dict[str, Any]:
    rec = await _require_session(req.session_id)
    async with rec["lock"]:
        page = rec["page"]
        try:
            clicked = False
            if req.selector:
                loc = page.locator(req.selector).first
                await loc.click(timeout=10000)
                clicked = True
            elif req.text:
                for strategy in (
                    lambda: page.get_by_role("button", name=req.text).first,
                    lambda: page.get_by_role("link", name=req.text).first,
                    lambda: page.get_by_text(req.text).first,
                ):
                    try:
                        await strategy().click(timeout=4000)
                        clicked = True
                        break
                    except Exception:
                        continue
            if not clicked:
                raise HTTPException(status_code=400, detail="Either selector or matching visible text is required to click.")
            if req.wait_ms > 0:
                await page.wait_for_timeout(min(req.wait_ms, 5000))
            rec["step"] = int(rec["step"]) + 1
            snap = await _snapshot_page(page, req.session_id, "click", rec["step"], capture_screenshot=True)
            return {"ok": True, **snap}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Browser action failed: {type(exc).__name__}: {exc}",
            ) from exc


@app.post("/interactive/type")
async def interactive_type(req: InteractiveTypeRequest) -> dict[str, Any]:
    rec = await _require_session(req.session_id)
    async with rec["lock"]:
        page = rec["page"]
        try:
            loc = None
            if req.selector:
                loc = page.locator(req.selector).first
            elif req.label:
                for strategy in (
                    lambda: page.get_by_label(req.label).first,
                    lambda: page.get_by_placeholder(req.label).first,
                ):
                    try:
                        cand = strategy()
                        if await cand.count() > 0:
                            loc = cand
                            break
                    except Exception:
                        continue
            if loc is None:
                loc = page.locator("input:visible, textarea:visible").first
            await loc.fill(req.text, timeout=10000)
            if req.press_enter:
                await loc.press("Enter")
            if req.submit_selector:
                await page.locator(req.submit_selector).first.click(timeout=10000)
            if req.wait_ms > 0:
                await page.wait_for_timeout(min(req.wait_ms, 5000))
            rec["step"] = int(rec["step"]) + 1
            snap = await _snapshot_page(page, req.session_id, "type", rec["step"], capture_screenshot=True)
            return {"ok": True, "secret_redacted": bool(req.secret), **snap}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Browser action failed: {type(exc).__name__}: {exc}",
            ) from exc


@app.post("/interactive/scroll")
async def interactive_scroll(req: InteractiveScrollRequest) -> dict[str, Any]:
    rec = await _require_session(req.session_id)
    async with rec["lock"]:
        page = rec["page"]
        delta = abs(int(req.amount or 700))
        if (req.direction or "down").lower() == "up":
            delta = -delta
        await page.evaluate("(dy) => window.scrollBy(0, dy)", delta)
        await page.wait_for_timeout(300)
        rec["step"] = int(rec["step"]) + 1
        snap = await _snapshot_page(page, req.session_id, "scroll", rec["step"], capture_screenshot=True)
        return {"ok": True, **snap}


@app.post("/interactive/screenshot")
async def interactive_screenshot(req: InteractiveScreenshotRequest) -> dict[str, Any]:
    rec = await _require_session(req.session_id)
    async with rec["lock"]:
        rec["step"] = int(rec["step"]) + 1
        snap = await _snapshot_page(
            rec["page"], req.session_id, "screenshot", rec["step"], capture_screenshot=True, full_page=req.full_page
        )
        return {"ok": True, **snap}


@app.post("/interactive/close")
async def interactive_close(req: InteractiveCloseRequest) -> dict[str, Any]:
    sid = _safe_id(req.session_id)
    async with _SESSIONS_META_LOCK:
        rec = _INTERACTIVE_SESSIONS.pop(sid, None)
    if rec is not None:
        await _close_session_record(rec)
    return {"ok": True, "closed": rec is not None, "session_id": sid}
