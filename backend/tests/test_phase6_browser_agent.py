"""Phase 6 Tests (§6.0–§6B): Live Browser Worker, General Browser Agent, Mock-Prefix Honesty & E2E Playwright Loop."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any

from openpyxl import load_workbook
import pytest
from sqlalchemy.orm import Session

import app.services.browser_loop as bl_mod
import app.services.browser_worker_manager as bwm_mod
import app.services.llm_router as router_mod
from app.config import REPO_ROOT, get_settings
from app.models.backlinks import BlLink, BlStep
from app.models.mailboxes import Mailbox
from app.models.providers import Provider
from app.models.v3_harness import ChatMessage
from app.security.sanitizer import scrub_text
from app.services.accounts_sheet import get_accounts_spreadsheet_path
from app.services.browser_agent import check_gmail_or_mailbox_activation
from app.services.browser_loop import run_flagship_backlink_loop
from app.services.intent_router import classify_intent
from app.services.llm_router import _LAST_ACTUAL_MODEL, LLMRouter
from app.services.mailbox import add_or_update_mailbox
from app.services.session_log import read_log


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _parse_sse(raw_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cur_ev = "message"
    cur_data: list[str] = []
    for line in raw_text.splitlines():
        if line.startswith("event:"):
            cur_ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            cur_data.append(line.split(":", 1)[1].strip())
        elif not line.strip() and cur_data:
            events.append({"event": cur_ev, "data": json.loads("\n".join(cur_data))})
            cur_ev = "message"
            cur_data = []
    if cur_data:
        events.append({"event": cur_ev, "data": json.loads("\n".join(cur_data))})
    return events


def test_runtime_status_truthful_worker_probe(owner_session: dict, temp_db_session: Session, monkeypatch: pytest.MonkeyPatch):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    # 1) Unset BROWSER_WORKER_URL -> false
    monkeypatch.setenv("BROWSER_WORKER_URL", "")
    get_settings.cache_clear()
    r_empty = client.get("/api/system/runtime-status", headers=headers)
    assert r_empty.status_code == 200
    assert r_empty.json()["browser_worker"] is False

    # 2) Dead URL string set -> MUST STILL report browser_worker: False truthfully
    dead_port = _free_port()
    monkeypatch.setenv("BROWSER_WORKER_URL", f"http://127.0.0.1:{dead_port}")
    get_settings.cache_clear()
    r_dead = client.get("/api/system/runtime-status", headers=headers)
    assert r_dead.status_code == 200
    assert r_dead.json()["browser_worker"] is False
    assert r_dead.json()["default_mock_mode"] is True

    # 3) Live /health server + enabled LLM -> browser_worker: True & real_chat_ready: True
    class _HealthHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                payload = json.dumps({"status": "ok", "active_sessions": 0}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_response(404)
                self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        prov = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "freellmapi").first()
        assert prov is not None
        prov.enabled = True
        temp_db_session.commit()

        monkeypatch.setenv("BROWSER_WORKER_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        get_settings.cache_clear()
        r_live = client.get("/api/system/runtime-status", headers=headers)
        assert r_live.status_code == 200
        body = r_live.json()
        assert body["llm_provider"] is True
        assert body["browser_worker"] is True
        assert body["real_chat_ready"] is True
        assert body["default_mock_mode"] is False
    finally:
        srv.shutdown()
        srv.server_close()
        monkeypatch.setenv("BROWSER_WORKER_URL", "")
        get_settings.cache_clear()


def test_intent_router_general_browser_vs_backlink_and_regressions():
    g = classify_intent("افتح جوجل")
    assert g["intent"] == "browser"
    assert g["entities"].get("site_hint") == "google"

    wiki = classify_intent("خش على موقع wikipedia.org")
    assert wiki["intent"] == "browser"
    assert wiki["entities"].get("domain") == "wikipedia.org"

    srch = classify_intent("دوّر على Playwright Python")
    assert srch["intent"] == "browser"
    assert srch["entities"].get("query") == "Playwright Python"

    bl = classify_intent("سجّل باك لينك في موقع gravatar.com بالإيميل real@mydomain.io")
    assert bl["intent"] == "backlink"
    assert bl["entities"].get("domain") == "gravatar.com"
    assert bl["entities"].get("email") == "real@mydomain.io"

    # Existing intents stay intact
    assert classify_intent("أرخص طيران للقاهرة بكرة")["intent"] == "flights"
    assert classify_intent("شوف إيميلي")["intent"] == "agent-mail"
    assert classify_intent("build me a todo app")["intent"] == "coding"

    # Search redirect links (DDG uddg= / Google /url?q=) unwrap cleanly
    from app.services.browser_agent import _filter_organic_links

    unwrapped = _filter_organic_links(
        [
            {"text": "Playwright Python Docs", "href": "//duckduckgo.com/l/?uddg=https%3A%2F%2Fplaywright.dev%2Fpython%2F"},
            {"text": "PyPI Playwright", "href": "https://www.google.com/url?q=https%3A%2F%2Fpypi.org%2Fproject%2Fplaywright%2F"},
            {"text": "Google Privacy", "href": "https://policies.google.com/privacy"},
        ]
    )
    assert unwrapped == [
        {"text": "Playwright Python Docs", "href": "https://playwright.dev/python/"},
        {"text": "PyPI Playwright", "href": "https://pypi.org/project/playwright/"},
    ]


def test_mock_prefix_honesty_on_real_vs_simulated_chat(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    prov = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "freellmapi").first()
    assert prov is not None
    prov.enabled = True
    temp_db_session.commit()

    def fake_sync(provider, model, api_key, prompt, system_prompt, json_schema=None):
        _LAST_ACTUAL_MODEL.set("nvidia/nemotron-3.5-super-v1:free")
        return ("إجابة حقيقية من النموذج بدون محاكاة.", 10, 8)

    async def fake_stream(provider, model, api_key, prompt, system_prompt):
        if provider.kind == "llm-mock":
            yield {"type": "token", "text": "تمت المعالجة عبر المزود المحلي التجريبي.", "model": "mock-cheap-v1", "is_mock": True}
            yield {"type": "meta", "model": "mock-cheap-v1", "tokens_in": 5, "tokens_out": 5, "is_mock": True}
            return
        yield {"type": "token", "text": "إجابة بث ", "model": "nvidia/nemotron-3.5-super-v1:free", "is_mock": False}
        yield {"type": "token", "text": "حقيقية!", "model": "nvidia/nemotron-3.5-super-v1:free", "is_mock": False}
        yield {"type": "meta", "model": "nvidia/nemotron-3.5-super-v1:free", "tokens_in": 10, "tokens_out": 4, "is_mock": False}

    monkeypatch.setattr(router_mod, "_call_provider_http", fake_sync)
    monkeypatch.setattr(router_mod, "_stream_provider_http", fake_stream)

    tid = client.post("/api/chat/threads", json={"title": "صدق المحاكاة"}, headers=headers).json()["thread"]["id"]

    # Even when client sends mock_mode=True, REAL provider output MUST NOT carry "محاكاة — "
    sync_res = client.post(
        f"/api/chat/threads/{tid}/messages",
        json={"content": "ما هي عاصمة مصر؟", "mock_mode": True},
        headers=headers,
    )
    assert sync_res.status_code == 200
    msg_sync = sync_res.json()["assistant_message"]
    assert not msg_sync["content"].startswith("محاكاة — ")
    assert msg_sync["card_payload"]["is_mock"] is False

    sse_res = client.post(
        f"/api/chat/threads/{tid}/messages/stream",
        json={"content": "اشرح بإيجاز", "mock_mode": True},
        headers=headers,
    )
    assert sse_res.status_code == 200
    events = _parse_sse(sse_res.text)
    tok_text = "".join(e["data"]["token"] for e in events if e["event"] == "token")
    done_ev = next(e for e in events if e["event"] == "done")
    assert not tok_text.startswith("محاكاة — ")
    assert done_ev["data"]["is_mock"] is False

    # Now disable real providers so mock-local fallback answers -> MUST carry "محاكاة — " & is_mock=True
    for p in temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.kind != "llm-mock").all():
        p.enabled = False
    temp_db_session.commit()

    mock_res = client.post(
        f"/api/chat/threads/{tid}/messages/stream",
        json={"content": "سؤال تجريبي", "mock_mode": False},
        headers=headers,
    )
    assert mock_res.status_code == 200
    m_events = _parse_sse(mock_res.text)
    m_text = "".join(e["data"]["token"] for e in m_events if e["event"] == "token")
    m_done = next(e for e in m_events if e["event"] == "done")
    assert m_text.startswith("محاكاة — ")
    assert m_done["data"]["is_mock"] is True


def test_browser_unavailable_honest_stop_never_claims_cant_browse(owner_session: dict, monkeypatch: pytest.MonkeyPatch):
    client = owner_session["client"]
    headers = owner_session["headers"]

    monkeypatch.setenv("BROWSER_WORKER_URL", "")
    get_settings.cache_clear()

    tid = client.post("/api/chat/threads", json={"title": "توقف صادق"}, headers=headers).json()["thread"]["id"]
    res = client.post(
        f"/api/chat/threads/{tid}/messages",
        json={"content": "افتح جوجل", "mock_mode": False},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["intent"] == "browser"
    asst = body["assistant_message"]
    assert asst["card_payload"]["status"] == "browser_unavailable"
    assert asst["card_payload"]["runtime_status"]["browser_worker"] is False
    assert "browser_worker" in asst["content"]
    assert "I can't browse" not in asst["content"]
    assert not asst["content"].startswith("محاكاة — ")


def test_real_playwright_browser_agent_e2e_open_search_gmail_activation_and_backlink(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]
    user_id = owner_session["data"]["user"]["id"]
    settings = get_settings()

    # 1) Start an authentic local multi-page web app (landing + search + signup -> email activate -> login -> profile backlink)
    state: dict[str, Any] = {"activated": False, "logged_in": False, "website": "", "bio": "", "username": "moaaz_profile"}

    class _MockSiteHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def _send_html(self, html: str, status: int = 200, cookie: str | None = None):
            raw = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send_html(
                    "<!doctype html><html><head><title>Portal Demo Home</title></head>"
                    "<body><h1>Welcome to Portal Demo</h1>"
                    "<p>Discover developers & blogs.</p>"
                    "<a href='/signup'>Register Account</a> | "
                    "<a href='https://example.org/docs'>Developer Docs</a></body></html>"
                )
            elif path == "/signup":
                self._send_html(
                    "<!doctype html><html><head><title>Sign Up - Portal Demo</title></head>"
                    "<body><h1>Create Profile</h1>"
                    "<form method='post' action='/signup'>"
                    "<label for='em'>Email</label><input id='em' name='email' type='email'/>"
                    "<label for='pw'>Password</label><input id='pw' name='password' type='password'/>"
                    "<label for='un'>Username</label><input id='un' name='username' type='text'/>"
                    "<button id='btn-signup' type='submit'>Register Now</button>"
                    "</form></body></html>"
                )
            elif path == "/activate":
                if "token=ACT999" in self.path:
                    state["activated"] = True
                    self._send_html(
                        "<!doctype html><html><head><title>Account Activated</title></head>"
                        "<body><h1>Email Verified!</h1><a href='/login'>Proceed to Login</a></body></html>"
                    )
                else:
                    self._send_html("<html><body>Invalid token</body></html>", status=400)
            elif path == "/login":
                self._send_html(
                    "<!doctype html><html><head><title>Login - Portal Demo</title></head>"
                    "<body><h1>Member Login</h1>"
                    "<form method='post' action='/login'>"
                    "<input id='lem' name='email' type='email'/>"
                    "<input id='lpw' name='password' type='password'/>"
                    "<button id='btn-login' type='submit'>Sign In</button>"
                    "</form></body></html>"
                )
            elif path == "/profile/edit":
                self._send_html(
                    "<!doctype html><html><head><title>Edit Profile - Portal Demo</title></head>"
                    "<body><h1>Update Your Backlink</h1>"
                    "<form method='post' action='/profile/edit'>"
                    "<input id='web' name='website' type='url'/>"
                    "<textarea id='bio' name='bio'></textarea>"
                    "<button id='btn-save' type='submit'>Save Profile</button>"
                    "</form></body></html>"
                )
            elif path.startswith("/u/"):
                w = state["website"] or "#"
                b = state["bio"] or ""
                self._send_html(
                    f"<!doctype html><html><head><title>{state['username']} Public Profile</title></head>"
                    f"<body><h1>{state['username']}</h1><p>{b}</p>"
                    f"<a id='home-link' href='{w}'>خبير سيو معاذ حسن</a></body></html>"
                )
            else:
                self._send_html("<html><body>Not found</body></html>", status=404)

        def do_POST(self):  # noqa: N802
            from urllib.parse import parse_qs

            ln = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(ln).decode("utf-8", errors="replace")
            form = parse_qs(body)
            path = self.path.split("?", 1)[0]
            if path == "/signup":
                if form.get("username"):
                    state["username"] = form["username"][0]
                self._send_html(
                    "<!doctype html><html><head><title>Check Your Email</title></head>"
                    "<body><h1>Activation email sent! Please confirm your inbox.</h1></body></html>"
                )
            elif path == "/login":
                if not state["activated"]:
                    self._send_html("<html><body>Please activate email first.</body></html>", status=403)
                    return
                self.send_response(302)
                self.send_header("Location", "/profile/edit")
                self.send_header("Set-Cookie", "sid=demo_ok; Path=/")
                self.end_headers()
            elif path == "/profile/edit":
                state["website"] = (form.get("website") or [""])[0]
                state["bio"] = (form.get("bio") or [""])[0]
                self.send_response(302)
                self.send_header("Location", f"/u/{state['username']}")
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

    target_srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockSiteHandler)
    target_port = target_srv.server_address[1]
    base_url = f"http://127.0.0.1:{target_port}"
    threading.Thread(target=target_srv.serve_forever, daemon=True).start()

    # 2) Launch REAL interactive Playwright browser worker in-process on an ephemeral port
    monkeypatch.setenv("PROFILES_DIR", str(settings.browser_profiles_dir))
    monkeypatch.setenv("ARTIFACTS_DIR", str(settings.artifacts_dir))
    sys.path.insert(0, str(REPO_ROOT / "browser-worker"))
    import interactive_worker as iw_mod
    import uvicorn

    iw_mod.PROFILES_DIR = settings.browser_profiles_dir.resolve()
    iw_mod.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    iw_mod.BROWSER_ARTIFACTS_DIR = (settings.artifacts_dir / "browser").resolve()
    iw_mod.BROWSER_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    worker_port = _free_port()
    config = uvicorn.Config(iw_mod.app, host="127.0.0.1", port=worker_port, log_level="warning")
    worker_server = uvicorn.Server(config)
    worker_thread = threading.Thread(target=worker_server.run, daemon=True)
    worker_thread.start()

    worker_base = f"http://127.0.0.1:{worker_port}"
    for _ in range(40):
        try:
            if bwm_mod.probe_browser_worker(worker_base, timeout=0.5)["ok"]:
                break
        except Exception:
            pass
        time.sleep(0.15)

    try:
        # Enable real LLM provider & point BROWSER_WORKER_URL to real worker
        prov = temp_db_session.query(Provider).filter(Provider.workspace_id == ws_id, Provider.id == "freellmapi").first()
        assert prov is not None
        prov.enabled = True
        temp_db_session.commit()

        monkeypatch.setenv("BROWSER_WORKER_URL", worker_base)
        get_settings.cache_clear()

        assert bwm_mod.probe_browser_worker(worker_base)["ok"] is True

        # 2A) Test general chat opening the real site via SSE stream
        def fake_browser_done(self, **kwargs):
            return {
                "provider_id": "freellmapi",
                "model": "qwen/qwen3-coder:free",
                "requested_model": "auto:fast",
                "tier": "cheap",
                "task_type": kwargs.get("task_type"),
                "output": None,
                "raw_text": json.dumps(
                    {
                        "thought": "Page loaded cleanly.",
                        "tool": "done",
                        "args": {"summary_ar": "تم فتح الموقع الحي بنجاح ورصد عنوان الصفحة والروابط."},
                    }
                ),
                "tokens_in": 10,
                "tokens_out": 10,
                "cost_usd": 0.0,
                "latency_ms": 20,
                "cached": False,
                "is_mock": False,
            }

        monkeypatch.setattr(LLMRouter, "route", fake_browser_done)

        tid = client.post("/api/chat/threads", json={"title": "تصفح حقيقي"}, headers=headers).json()["thread"]["id"]
        open_sse = client.post(
            f"/api/chat/threads/{tid}/messages/stream",
            json={"content": f"افتح {base_url}/", "mock_mode": False},
            headers=headers,
        )
        assert open_sse.status_code == 200
        ev_list = _parse_sse(open_sse.text)
        prog_events = [e for e in ev_list if e["event"] == "progress"]
        done_ev = next(e for e in ev_list if e["event"] == "done")
        assert len(prog_events) >= 1
        cp = done_ev["data"]["card_payload"]
        assert cp["is_mock"] is False
        assert cp["page_title"] == "Portal Demo Home"
        assert len(cp["screenshots"]) >= 1
        assert not done_ev["data"]["content"].startswith("محاكاة — ")

        # Verify screenshot URL serves real PNG bytes over authenticated API endpoint
        sc_resp = client.get(cp["screenshots"][0], headers=headers)
        assert sc_resp.status_code == 200
        assert sc_resp.content[:8] == b"\x89PNG\r\n\x1a\n"

        # Verify NDJSON log recorded real browser_step entry
        assert any(entry.get("type") == "browser_step" for entry in read_log(tid))

        # 2B) Test Gmail / Mailbox same-session verification extraction wipes ephemeral secret
        msg = EmailMessage()
        msg["From"] = "noreply@127.0.0.1"
        msg["To"] = "moaaz.real@mydomain.io"
        msg["Subject"] = "Activate your account"
        msg["Message-ID"] = "<act999@127.0.0.1>"
        msg["Date"] = format_datetime(datetime.now(timezone.utc))
        msg.set_content(f"Verify here: {base_url}/activate?token=ACT999\nCode: 482910\n")

        act_found = check_gmail_or_mailbox_activation(
            temp_db_session,
            workspace_id=ws_id,
            site_domain="127.0.0.1",
            target_email="moaaz.real@mydomain.io",
            raw_messages_override=[msg.as_bytes()],
        )
        assert act_found is not None
        assert act_found["email_code"] == "482910"
        assert f"{base_url}/activate?token=ACT999" == act_found["activation_url"]
        # Secret sanitizer redacts the active ephemeral code automatically
        assert "482910" not in scrub_text("OTP received 482910 now")

        # 2C) Full 9-step real Playwright Flagship Backlink Loop against local target server
        raw_email_bytes = msg.as_bytes()

        class _FakeImap:
            def __init__(self, *a, **kw):
                pass

            def login(self, *a, **kw):
                return ("OK", [])

            def select(self, *a, **kw):
                return ("OK", [b"1"])

            def search(self, *a, **kw):
                return ("OK", [b"1"])

            def fetch(self, *a, **kw):
                return ("OK", [(b"1 (RFC822)", raw_email_bytes)])

            def logout(self):
                return ("BYE", [])

        monkeypatch.setattr("imaplib.IMAP4_SSL", _FakeImap)

        mb = add_or_update_mailbox(
            temp_db_session,
            workspace_id=ws_id,
            user_id=user_id,
            email_addr="moaaz.real@mydomain.io",
            app_password="app-pass-not-real",
            imap_host="imap.example.org",
            run_connection_test=False,
        )
        assert mb.id

        scripted_actions = iter(
            [
                {"tool": "browser_type", "args": {"selector": "#em", "text": "__EMAIL__"}},
                {"tool": "browser_type", "args": {"selector": "#pw", "text": "__PASSWORD__", "secret": True}},
                {"tool": "browser_type", "args": {"selector": "#un", "text": "moaaz_live", "submit_selector": "#btn-signup"}},
                {"tool": "gmail_check_activation", "args": {"site_domain": f"127.0.0.1:{target_port}"}},
                {"tool": "browser_open", "args": {"url": f"{base_url}/login"}},
                {"tool": "browser_type", "args": {"selector": "#lem", "text": "__EMAIL__"}},
                {"tool": "browser_type", "args": {"selector": "#lpw", "text": "__PASSWORD__", "secret": True, "submit_selector": "#btn-login"}},
                {"tool": "browser_type", "args": {"selector": "#web", "text": "__TARGET_URL__"}},
                {"tool": "browser_type", "args": {"selector": "#bio", "text": "__BIO__", "submit_selector": "#btn-save"}},
                {"tool": "finish_backlink", "args": {"profile_url": f"{base_url}/u/moaaz_live"}},
            ]
        )

        def fake_backlink_route(self, **kwargs):
            act = next(scripted_actions)
            return {
                "provider_id": "freellmapi",
                "model": "qwen/qwen3-coder:free",
                "requested_model": "auto:fast",
                "tier": "cheap",
                "task_type": kwargs.get("task_type"),
                "output": None,
                "raw_text": json.dumps(act),
                "tokens_in": 12,
                "tokens_out": 8,
                "cost_usd": 0.0,
                "latency_ms": 15,
                "cached": False,
                "is_mock": False,
            }

        monkeypatch.setattr(LLMRouter, "route", fake_backlink_route)

        bl_res = asyncio.run(
            run_flagship_backlink_loop(
                temp_db_session,
                workspace_id=ws_id,
                user_id=user_id,
                domain=f"127.0.0.1:{target_port}",
                email="moaaz.real@mydomain.io",
                mock_mode=False,
                target_url=f"{base_url}/signup",
                thread_id=tid,
            )
        )
        assert bl_res["status"] == "done", bl_res
        assert bl_res["is_mock"] is False
        assert bl_res["profile_url"] == f"{base_url}/u/moaaz_live"
        assert bl_res["link_type"] == "dofollow"
        assert len(bl_res["screenshots"]) >= 3
        assert state["activated"] is True

        # Verify DB BlLink(live=1), BlStep rows, screenshots served, and data/accounts.xlsx recorded
        link_rows = temp_db_session.query(BlLink).filter(BlLink.workspace_id == ws_id, BlLink.live == 1).all()
        assert len(link_rows) >= 1
        assert temp_db_session.query(BlStep).filter(BlStep.workspace_id == ws_id).count() >= 5

        sc_prof = client.get(bl_res["screenshots"][-1], headers=headers)
        assert sc_prof.status_code == 200
        assert sc_prof.content[:8] == b"\x89PNG\r\n\x1a\n"

        # Password & email OTP never leaked in ChatMessage or BlStep or NDJSON log
        wb = load_workbook(get_accounts_spreadsheet_path())
        rows = list(wb.active.iter_rows(values_only=True))
        live_rows = [r for r in rows[1:] if r[3] == f"{base_url}/u/moaaz_live"]
        assert len(live_rows) >= 1
        recorded_pw = str(live_rows[-1][2])
        all_chat = " ".join(m.content for m in temp_db_session.query(ChatMessage).all())
        assert recorded_pw not in all_chat
        assert "482910" not in all_chat
        assert "ACT999" not in json.dumps(read_log(tid))
    finally:
        worker_server.should_exit = True
        target_srv.shutdown()
        target_srv.server_close()
        monkeypatch.setenv("BROWSER_WORKER_URL", "")
        get_settings.cache_clear()
