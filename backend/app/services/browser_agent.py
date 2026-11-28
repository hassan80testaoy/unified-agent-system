"""General Browser Agent Service — Real Playwright Tool Loop + Gmail Same-Session Activation (§6.0 & §6A)."""

from __future__ import annotations

import asyncio
import imaplib
import json
import time
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import new_uuid
from app.models.google_account import GoogleAccount
from app.models.mailboxes import Mailbox
from app.security.crypto import decrypt_secret
from app.security.sanitizer import discard_ephemeral_secret, register_ephemeral_secret, scrub_text
from app.services.browser_worker_manager import resolve_browser_worker_url
from app.services.llm_router import LLMRouter
from app.services.mailbox import fetch_and_find_verification_imap, find_verification_from_messages
from app.services.runtime_checks import get_runtime_readiness
from app.services.session_log import append_turn

_SITE_HINTS: dict[str, str] = {
    "google": "https://www.google.com",
    "جوجل": "https://www.google.com",
    "قوقل": "https://www.google.com",
    "wikipedia": "https://ar.wikipedia.org",
    "ويكيبيديا": "https://ar.wikipedia.org",
    "youtube": "https://www.youtube.com",
    "يوتيوب": "https://www.youtube.com",
    "github": "https://github.com",
    "جيت هاب": "https://github.com",
    "bing": "https://www.bing.com",
    "duckduckgo": "https://html.duckduckgo.com/html/",
}

_BROWSER_TOOL_SYSTEM_PROMPT = (
    "You are a live General Browser Agent inside Unified Agent System operating a REAL Playwright Chromium browser.\n"
    "You HAVE direct browser control through these tools:\n"
    "1. browser_open: {\"url\": \"https://...\"}\n"
    "2. browser_read: {}\n"
    "3. browser_click: {\"selector\": \"css selector\", \"text\": \"optional visible button/link text\"}\n"
    "4. browser_type: {\"selector\": \"css\", \"label\": \"optional field label/placeholder\", \"text\": \"value\", \"press_enter\": false, \"submit_selector\": \"optional css\"}\n"
    "5. browser_scroll: {\"direction\": \"down\"|\"up\", \"amount\": 700}\n"
    "6. browser_screenshot: {\"full_page\": false}\n"
    "7. gmail_check_activation: {\"site_domain\": \"example.com\"}\n"
    "8. done: {\"summary_ar\": \"التقرير العربي الواضح بالنتائج الحقيقية والروابط التي تمت رؤيتها فقط\", \"profile_url\": \"optional url\"}\n\n"
    "CRITICAL HONESTY & CAPABILITY RULES:\n"
    "- NEVER say 'I cannot browse the internet' or refuse browsing — the browser is live right now.\n"
    "- NEVER invent URLs, page titles, or facts not present in the latest DOM snapshot.\n"
    "- For simple 'open <site>' or search queries, once the page/results are already visible with links in the snapshot, call tool=\"done\" immediately and present a clean Arabic summary citing the real page title, URL, and real links.\n"
    "- Reply ONLY with a single JSON object: {\"thought\": \"...\", \"tool\": \"...\", \"args\": {...}}."
)


class BrowserWorkerClient:
    """Async HTTP client to the Playwright interactive worker endpoints."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.strip().rstrip("/")

    async def call(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("browser_worker_url_not_set")
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{self.base_url}{path}", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"worker_http_{resp.status_code}: {resp.text[:240]}")
        return resp.json()

    async def open(self, *, session_id: str, url: str, profile_id: str = "default", proxy: str | None = None) -> dict[str, Any]:
        return await self.call(
            "/interactive/open",
            {"session_id": session_id, "profile_id": profile_id, "url": url, "proxy": proxy},
        )

    async def read(self, *, session_id: str, capture_screenshot: bool = False) -> dict[str, Any]:
        return await self.call("/interactive/read", {"session_id": session_id, "capture_screenshot": capture_screenshot})

    async def click(self, *, session_id: str, selector: str | None = None, text: str | None = None) -> dict[str, Any]:
        return await self.call("/interactive/click", {"session_id": session_id, "selector": selector, "text": text})

    async def type(
        self,
        *,
        session_id: str,
        selector: str | None = None,
        label: str | None = None,
        text: str = "",
        press_enter: bool = False,
        submit_selector: str | None = None,
        secret: bool = False,
    ) -> dict[str, Any]:
        return await self.call(
            "/interactive/type",
            {
                "session_id": session_id,
                "selector": selector,
                "label": label,
                "text": text,
                "press_enter": press_enter,
                "submit_selector": submit_selector,
                "secret": secret,
            },
        )

    async def scroll(self, *, session_id: str, direction: str = "down", amount: int = 700) -> dict[str, Any]:
        return await self.call("/interactive/scroll", {"session_id": session_id, "direction": direction, "amount": amount})

    async def screenshot(self, *, session_id: str, full_page: bool = False) -> dict[str, Any]:
        return await self.call("/interactive/screenshot", {"session_id": session_id, "full_page": full_page})

    async def close(self, *, session_id: str) -> dict[str, Any]:
        try:
            return await self.call("/interactive/close", {"session_id": session_id})
        except Exception:
            return {"ok": False}


def check_gmail_or_mailbox_activation(
    db: Session,
    *,
    workspace_id: str,
    site_domain: str,
    target_email: str | None = None,
    raw_messages_override: list[bytes] | None = None,
) -> dict[str, Any] | None:
    """Inspect connected Google Account (Gmail IMAP) or Mailbox read-only for current task activation.

    Any activation_url or 1-time code is held ONLY in memory and registered as an ephemeral secret
    so sanitizer strips it from logs/DB/reports. Caller must call `discard_ephemeral_secret` after use.
    """
    clean_domain = site_domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0].split(":")[0]
    found: dict[str, Any] | None = None

    if raw_messages_override is not None:
        found = find_verification_from_messages(
            raw_messages_override,
            site_domain=clean_domain,
            target_address=(target_email or "").strip(),
        )
    else:
        g_accts = (
            db.query(GoogleAccount)
            .filter(GoogleAccount.workspace_id == workspace_id, GoogleAccount.status == "active")
            .all()
        )
        for ga in g_accts:
            if not ga.password_enc or not ga.email_enc:
                continue
            g_email = decrypt_secret(ga.email_enc)
            g_pw = decrypt_secret(ga.password_enc)
            register_ephemeral_secret(g_pw)
            client = None
            try:
                client = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=12)
                client.login(g_email, g_pw)
                client.select("INBOX", readonly=True)
                _, data = client.search(None, "UNSEEN")
                msg_nums = (data[0] or b"").split()[-15:]
                raw_list: list[bytes] = []
                for num in reversed(msg_nums):
                    typ, msg_data = client.fetch(num, "(RFC822)")
                    if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                        raw_list.append(msg_data[0][1])
                found = find_verification_from_messages(
                    raw_list,
                    site_domain=clean_domain,
                    target_address=(target_email or g_email).strip(),
                )
                if found:
                    break
            except Exception:
                pass
            finally:
                if client is not None:
                    try:
                        client.logout()
                    except Exception:
                        pass
                discard_ephemeral_secret(g_pw)
                del g_pw

        if found is None:
            mailboxes = (
                db.query(Mailbox)
                .filter(Mailbox.workspace_id == workspace_id, Mailbox.status != "disconnected")
                .all()
            )
            for mb in mailboxes:
                try:
                    found = fetch_and_find_verification_imap(
                        db,
                        mailbox_id=mb.id,
                        site_domain=clean_domain,
                        target_address=(target_email or mb.email).strip(),
                    )
                    if found:
                        break
                except Exception:
                    continue

    if found:
        if found.get("email_code"):
            register_ephemeral_secret(str(found["email_code"]))
        if found.get("activation_url"):
            register_ephemeral_secret(str(found["activation_url"]))
    return found


def _extract_json_obj(raw_text: str) -> dict[str, Any] | None:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return None


def _filter_organic_links(links: list[dict[str, Any]], search_host: str | None = None) -> list[dict[str, Any]]:
    skip_hosts = {
        "accounts.google.com",
        "support.google.com",
        "policies.google.com",
        "maps.google.com",
        "duckduckgo.com",
        "html.duckduckgo.com",
        "www.bing.com",
        "go.microsoft.com",
    }
    out: list[dict[str, Any]] = []
    seen_out: set[str] = set()
    for item in links:
        href = str(item.get("href") or "").strip()
        text = str(item.get("text") or "").strip()
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith("http") or not text:
            continue
        try:
            parsed = urlparse(href)
            host = (parsed.hostname or "").lower()
            if host in ("duckduckgo.com", "html.duckduckgo.com"):
                qs = parse_qs(parsed.query)
                if qs.get("uddg"):
                    href = unquote(qs["uddg"][0]).strip()
            elif "google." in host and parsed.path.startswith("/url"):
                qs = parse_qs(parsed.query)
                target = qs.get("q") or qs.get("url")
                if target:
                    href = unquote(target[0]).strip()
            if not href.startswith("http"):
                continue
            host = (urlparse(href).hostname or "").lower()
        except Exception:
            continue
        if not host or host in skip_hosts:
            continue
        if search_host and (host == search_host or host.endswith("." + search_host)):
            continue
        if "google." in host or "gstatic." in host:
            continue
        if href in seen_out:
            continue
        seen_out.add(href)
        out.append({"text": text[:120], "href": href})
        if len(out) >= 10:
            break
    return out


def _deterministic_summary_ar(snap: dict[str, Any], *, is_search: bool, query: str | None) -> str:
    title = str(snap.get("title") or "").strip() or "صفحة ويب"
    url = str(snap.get("url") or "").strip()
    links = _filter_organic_links(snap.get("links") or []) if is_search else list(snap.get("links") or [])[:8]
    snippet = scrub_text(str(snap.get("visible_text") or "")).strip()
    if len(snippet) > 600:
        snippet = snippet[:600].rsplit(" ", 1)[0] + "…"

    lines = [f"🌐 **تم الفتح في المتصفح الحي (Playwright):**"]
    if is_search and query:
        lines.append(f"🔎 **نتائج البحث عن:** `{query}`")
    lines.append(f"- **العنوان:** {title}")
    lines.append(f"- **الرابط:** {url}")
    if links:
        lines.append("\n🔗 **الروابط المرئية:**")
        for lk in links[:8]:
            lines.append(f"- [{lk['text']}]({lk['href']})")
    if snippet:
        lines.append(f"\n📄 **مقتطف مما يظهر في الصفحة:**\n{snippet}")
    return "\n".join(lines)


def _resolve_initial_target(entities: dict[str, Any]) -> tuple[str, bool]:
    if entities.get("url"):
        u = str(entities["url"]).strip()
        return (u if u.startswith(("http://", "https://")) else f"https://{u}"), False
    if entities.get("domain"):
        d = str(entities["domain"]).strip().removeprefix("https://").removeprefix("http://")
        return f"https://{d}", False
    hint = str(entities.get("site_hint") or "").strip().lower()
    if hint and hint in _SITE_HINTS and not entities.get("query"):
        return _SITE_HINTS[hint], False
    q = str(entities.get("query") or "").strip()
    if q:
        return f"https://www.google.com/search?q={quote_plus(q)}&hl=ar", True
    return "https://www.google.com", False


async def run_general_browser_agent(
    db: Session,
    *,
    thread_id: str,
    workspace_id: str,
    user_id: str,
    prompt: str,
    entities: dict[str, Any] | None = None,
    mock_mode: bool = False,
    max_steps: int = 8,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Browse and interact on ANY live website from plain chat using Playwright tools."""
    ent = dict(entities or {})
    clean_prompt = scrub_text(prompt).strip()
    initial_url, is_search = _resolve_initial_target(ent)
    query = str(ent.get("query") or "").strip() or None

    if mock_mode:
        stages = [
            {"step": 1, "tool": "browser_open", "status": "simulated", "detail": f"محاكاة — فتح {initial_url}"},
            {"step": 2, "tool": "browser_read", "status": "simulated", "detail": "محاكاة — قراءة عنوان الصفحة والروابط الظاهرة"},
        ]
        summary = (
            f"محاكاة — وضع المحاكاة مفعّل؛ لم يتم فتح متصفح حقيقي.\n"
            f"- الهدف المطلوب: `{initial_url}`\n"
            f"- انتقل إلى الوضع الحقيقي (Real) لتشغيل متصفح Playwright فعلياً والتقاط لقطات الشاشة الحية."
        )
        append_turn(thread_id, {"type": "browser_step", "tool": "browser_open", "url": initial_url, "is_mock": True})
        return {
            "status": "simulated",
            "is_mock": True,
            "current_url": initial_url,
            "page_title": "",
            "screenshots": [],
            "links": [],
            "stages": stages,
            "model_used": None,
            "latency_ms": 0,
            "report_ar": summary,
        }

    readiness = get_runtime_readiness(db, workspace_id)
    if not readiness.get("browser_worker"):
        err = readiness.get("browser_worker_error") or "browser_worker_offline"
        report_ar = (
            "⛔ **المتصفح الحقيقي غير متاح حالياً (توقف صادق بدون اختلاق):**\n"
            f"- حالة `browser_worker`: `false` ({err})\n"
            "- قم بتشغيل خادم المتصفح مع النظام عبر `scripts/dev-windows.ps1` أو اضبط `BROWSER_WORKER_URL` في `.env`."
        )
        return {
            "status": "browser_unavailable",
            "is_mock": False,
            "runtime_status": readiness,
            "missing": ["browser_worker"],
            "current_url": initial_url,
            "page_title": "",
            "screenshots": [],
            "links": [],
            "stages": [],
            "model_used": None,
            "latency_ms": 0,
            "report_ar": report_ar,
        }

    settings = get_settings()
    raw_proxy = (settings.residential_proxy or "").strip() or None
    proxy = None if initial_url.startswith(("http://127.0.0.1", "http://localhost")) else raw_proxy
    worker = BrowserWorkerClient(resolve_browser_worker_url())
    session_id = f"chat_{thread_id[:12]}_{new_uuid()[:6]}"
    stages: list[dict[str, Any]] = []
    screenshots: list[str] = []
    seen_links: list[dict[str, Any]] = []
    last_snap: dict[str, Any] = {}
    t0 = time.monotonic()
    model_used: str | None = None
    router_is_mock = False
    paused_status: str | None = None
    paused_card: str | None = None
    summary_override: str | None = None

    async def _record_step(step_no: int, tool: str, snap: dict[str, Any], note: str = "") -> None:
        nonlocal last_snap
        last_snap = snap
        sc_url = snap.get("screenshot_url")
        if sc_url and sc_url not in screenshots:
            screenshots.append(str(sc_url))
        cur_url = str(snap.get("url") or "")
        cur_title = str(snap.get("title") or "")
        detail = note or f"{tool}: {cur_title or cur_url}"
        entry = {
            "step": step_no,
            "tool": tool,
            "status": "ok",
            "url": cur_url,
            "title": cur_title,
            "screenshot_url": sc_url,
            "detail": scrub_text(detail),
        }
        stages.append(entry)
        append_turn(
            thread_id,
            {
                "type": "browser_step",
                "step": step_no,
                "tool": tool,
                "url": cur_url,
                "title": cur_title,
                "screenshot_url": sc_url,
            },
        )
        if on_progress:
            await on_progress(entry)

    try:
        # Step 1: Open requested URL or execute live search with automatic DDG/Bing fallback on captcha/empty
        step_counter = 1
        snap = await worker.open(session_id=session_id, url=initial_url, proxy=proxy)
        await _record_step(step_counter, "browser_open", snap)

        if is_search and query:
            organic = _filter_organic_links(snap.get("links") or [], search_host="google.com")
            if snap.get("challenge") or "/sorry" in str(snap.get("url") or "").lower() or not organic:
                ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
                try:
                    ddg_snap = await worker.open(session_id=session_id, url=ddg_url, proxy=proxy)
                    step_counter += 1
                    snap = ddg_snap
                    await _record_step(step_counter, "browser_open", snap, f"بحث عبر DuckDuckGo عن: {query}")
                    organic = _filter_organic_links(snap.get("links") or [], search_host="duckduckgo.com")
                except Exception:
                    pass
            if not organic:
                bing_url = f"https://www.bing.com/search?q={quote_plus(query)}"
                try:
                    bing_snap = await worker.open(session_id=session_id, url=bing_url, proxy=proxy)
                    step_counter += 1
                    snap = bing_snap
                    await _record_step(step_counter, "browser_open", snap, f"بحث عبر Bing عن: {query}")
                    organic = _filter_organic_links(snap.get("links") or [], search_host="bing.com")
                except Exception:
                    pass
            seen_links = organic
        else:
            seen_links = list(snap.get("links") or [])[:12]

        if snap.get("challenge") and not is_search:
            paused_status = "awaiting_captcha_manual"
            paused_card = "captcha_manual"
            summary_override = (
                f"🧩 **تم رصد تحدي كابتشا (`{snap['challenge'].get('challenge_type')}`) في الصفحة الحيّة:**\n"
                f"- الصفحة: {snap.get('url')}\n"
                "- تم إيقاف الخطوة بأمان بانتظار الحل اليدوي عبر noVNC أو مزود الكابتشا المُعد في الإعدادات."
            )

        # Iterative LLM tool loop (real Playwright state -> JSON tool choice)
        llm = LLMRouter(db, workspace_id)
        for _ in range(max(1, max_steps - step_counter)):
            if paused_status or (cancel_event and cancel_event.is_set()):
                break

            links_preview = _filter_organic_links(last_snap.get("links") or []) if is_search else list(last_snap.get("links") or [])[:12]
            obs_payload = {
                "user_request": clean_prompt,
                "current_url": last_snap.get("url"),
                "title": last_snap.get("title"),
                "visible_text": str(last_snap.get("visible_text") or "")[:1800],
                "links": links_preview[:12],
                "inputs": (last_snap.get("inputs") or [])[:12],
                "buttons": (last_snap.get("buttons") or [])[:10],
                "challenge": last_snap.get("challenge"),
                "steps_taken": len(stages),
                "max_steps": max_steps,
            }

            tool_choice: dict[str, Any] | None = None
            try:
                res = llm.route(
                    task_type="browser",
                    tier="cheap",
                    prompt=(
                        f"Live Browser Observation JSON:\n{json.dumps(obs_payload, ensure_ascii=False)}\n\n"
                        "Decide your next single tool call JSON (`browser_open`, `browser_read`, `browser_click`, "
                        "`browser_type`, `browser_scroll`, `browser_screenshot`, `gmail_check_activation`, or `done`)."
                    ),
                    system_prompt=_BROWSER_TOOL_SYSTEM_PROMPT,
                    use_cache=False,
                    allow_mock_fallback=True,
                )
                model_used = str(res.get("model") or model_used or "") or None
                router_is_mock = bool(router_is_mock or res.get("is_mock"))
                tool_choice = _extract_json_obj(str(res.get("raw_text") or ""))
            except Exception:
                tool_choice = None

            if not tool_choice:
                break

            t_name = str(tool_choice.get("tool") or "done").strip()
            t_args = dict(tool_choice.get("args") or {})
            if t_name == "done":
                summary_override = str(t_args.get("summary_ar") or "").strip() or None
                break

            step_counter += 1
            if t_name == "browser_open" and t_args.get("url"):
                snap = await worker.open(session_id=session_id, url=str(t_args["url"]), proxy=proxy)
                await _record_step(step_counter, "browser_open", snap)
            elif t_name == "browser_read":
                snap = await worker.read(session_id=session_id, capture_screenshot=False)
                await _record_step(step_counter, "browser_read", snap)
            elif t_name == "browser_click":
                snap = await worker.click(
                    session_id=session_id,
                    selector=t_args.get("selector"),
                    text=t_args.get("text"),
                )
                await _record_step(step_counter, "browser_click", snap)
            elif t_name == "browser_type":
                is_sec = bool(t_args.get("secret"))
                snap = await worker.type(
                    session_id=session_id,
                    selector=t_args.get("selector"),
                    label=t_args.get("label"),
                    text=str(t_args.get("text") or ""),
                    press_enter=bool(t_args.get("press_enter", False)),
                    submit_selector=t_args.get("submit_selector"),
                    secret=is_sec,
                )
                await _record_step(step_counter, "browser_type", snap, "إدخال نص في الحقل المحدد")
            elif t_name == "browser_scroll":
                snap = await worker.scroll(
                    session_id=session_id,
                    direction=str(t_args.get("direction") or "down"),
                    amount=int(t_args.get("amount") or 700),
                )
                await _record_step(step_counter, "browser_scroll", snap)
            elif t_name == "browser_screenshot":
                snap = await worker.screenshot(session_id=session_id, full_page=bool(t_args.get("full_page", False)))
                await _record_step(step_counter, "browser_screenshot", snap)
            elif t_name == "gmail_check_activation":
                dom_arg = str(t_args.get("site_domain") or ent.get("domain") or urlparse(str(last_snap.get("url") or "")).hostname or "")
                act = check_gmail_or_mailbox_activation(
                    db,
                    workspace_id=workspace_id,
                    site_domain=dom_arg,
                    target_email=ent.get("email"),
                )
                if act and act.get("activation_url"):
                    act_url = str(act["activation_url"])
                    try:
                        snap = await worker.open(session_id=session_id, url=act_url, proxy=proxy)
                        await _record_step(step_counter, "gmail_check_activation", snap, "فتح رابط التفعيل من البريد في نفس الجلسة")
                    finally:
                        discard_ephemeral_secret(act_url)
                        if act.get("email_code"):
                            discard_ephemeral_secret(str(act["email_code"]))
                else:
                    paused_status = "awaiting_verification_code"
                    paused_card = "verification_code"
                    summary_override = (
                        "📧 **يتطلب الموقع تفعيل البريد أو كود التحقق:**\n"
                        "- لم يتم العثور على رسالة تفعيل جديدة تلقائياً؛ يرجى ربط Gmail/IMAP من الإعدادات أو إدخال الكود في البطاقة."
                    )
                    break
            else:
                break

            seen_links = _filter_organic_links(last_snap.get("links") or []) if is_search else list(last_snap.get("links") or [])[:12]
            if last_snap.get("challenge"):
                paused_status = "awaiting_captcha_manual"
                paused_card = "captcha_manual"
                break

        # Final Arabic synthesis grounded in observed real browser output
        if summary_override:
            # Guard against simulated canned mock text replacing real browser results
            if router_is_mock and not is_search:
                summary_ar = _deterministic_summary_ar(last_snap, is_search=is_search, query=query)
            else:
                summary_ar = scrub_text(summary_override)
        else:
            summary_ar = _deterministic_summary_ar(last_snap, is_search=is_search, query=query)

        # If the site actually loaded in a real Chromium browser, NEVER let an LLM claim it can't browse
        low_sum = summary_ar.lower()
        if any(phrase in low_sum for phrase in ("i cannot browse", "can't browse", "لا أستطيع التصفح", "لا يمكنني تصفح")):
            summary_ar = _deterministic_summary_ar(last_snap, is_search=is_search, query=query)

        # Ensure screenshots & links are visible in plain text too
        if seen_links and not any(lk["href"] in summary_ar for lk in seen_links[:3]):
            summary_ar += "\n\n🔗 **الروابط الظاهرة:**\n" + "\n".join(
                f"- [{lk['text']}]({lk['href']})" for lk in seen_links[:8]
            )

        latency_ms = int((time.monotonic() - t0) * 1000)
        # Critical §6.0.6 honesty rule: when real Playwright browsed the live site, NEVER mark reply simulated
        is_mock_out = False
        return {
            "status": paused_status or "completed",
            "card_type": paused_card,
            "is_mock": is_mock_out,
            "current_url": last_snap.get("url") or initial_url,
            "page_title": last_snap.get("title") or "",
            "screenshots": screenshots,
            "links": seen_links[:10],
            "stages": stages,
            "model_used": model_used,
            "latency_ms": latency_ms,
            "report_ar": scrub_text(summary_ar),
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        err_report = (
            "⛔ **تعذر إتمام التصفح في المتصفح الحقيقي (توقف صادق):**\n"
            f"- الهدف: `{initial_url}`\n"
            f"- السبب التقني: `{type(exc).__name__}: {scrub_text(str(exc)[:220])}`"
        )
        return {
            "status": "browser_error",
            "is_mock": False,
            "current_url": last_snap.get("url") or initial_url,
            "page_title": last_snap.get("title") or "",
            "screenshots": screenshots,
            "links": seen_links[:10],
            "stages": stages,
            "model_used": model_used,
            "latency_ms": latency_ms,
            "report_ar": err_report,
        }
    finally:
        await worker.close(session_id=session_id)
