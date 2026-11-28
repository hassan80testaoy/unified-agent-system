"""Phase 7 tests: §7.0 Proxy direct fallback & health, §7A.1 Goal Planner, §7A.2 GSC Reporter, §7A.3 Site Auditor."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.agents import Goal
from app.models.seo import GscMetric
from app.services.browser_agent import BrowserWorkerClient
from app.services.proxy_health import reset_proxy_health_cache


def test_proxy_401_limit_falls_back_once_to_direct_and_updates_runtime_status(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    reset_proxy_health_cache()

    monkeypatch.setenv("BROWSER_WORKER_URL", "http://127.0.0.1:7080")
    monkeypatch.setenv("RESIDENTIAL_PROXY", "http://tok:super=true@pm.scrape.do:8080")
    get_settings.cache_clear()

    # Probe returns live ok so runtime-status and browser_agent treat worker as online
    monkeypatch.setattr(
        "app.services.runtime_checks.probe_browser_worker",
        lambda url: {"ok": True, "url": url, "status_code": 200, "error": None},
    )

    calls: list[dict[str, Any]] = []

    async def fake_call(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path == "/interactive/close":
            return {"ok": True}
        calls.append({"path": path, "payload": dict(payload)})
        if path == "/interactive/open" and not payload.get("bypass_proxy"):
            # Scrape.do monthly request limit exceeded (HTTP 401 body)
            return {
                "ok": True,
                "http_status": 401,
                "network_path": "proxy",
                "url": payload["url"],
                "title": "",
                "visible_text": '{"URL":"https://dashboard.scrape.do","StatusCode":401,"Message":["Monthly request limit exceeded"]}',
                "links": [],
                "inputs": [],
                "buttons": [],
                "challenge": None,
                "screenshot_url": "/api/browser/screenshots/s1/step_001_open.png",
            }
        return {
            "ok": True,
            "http_status": 200,
            "network_path": "direct",
            "url": "https://www.google.com/",
            "title": "Google",
            "visible_text": "Google Search",
            "links": [{"text": "About", "href": "https://about.google/"}],
            "inputs": [],
            "buttons": [],
            "challenge": None,
            "screenshot_url": "/api/browser/screenshots/s1/step_002_open.png",
        }

    monkeypatch.setattr(BrowserWorkerClient, "call", fake_call)

    tid = client.post("/api/chat/threads", json={"title": "Proxy fallback"}, headers=headers).json()["thread"]["id"]
    res = client.post(
        f"/api/chat/threads/{tid}/messages",
        json={"content": "افتح جوجل", "mock_mode": False},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    cp = body["assistant_message"]["card_payload"]
    assert cp["status"] == "completed"
    assert cp["network_path"] == "direct_fallback"
    assert "401" in (cp.get("proxy_fallback_reason") or "").lower()
    assert cp["page_title"] == "Google"
    assert cp["is_mock"] is False
    assert not body["assistant_message"]["content"].startswith("محاكاة — ")

    open_calls = [c for c in calls if c["path"] == "/interactive/open"]
    assert len(open_calls) == 2
    assert open_calls[0]["payload"]["bypass_proxy"] is False
    assert open_calls[1]["payload"]["bypass_proxy"] is True

    rs = client.get("/api/system/runtime-status", headers=headers)
    assert rs.status_code == 200
    ph = rs.json()["proxy_health"]
    assert ph["configured"] is True
    assert ph["status"] == "degraded_limit"
    assert ph["last_fell_back_to_direct"] is True
    assert "401" in (ph["last_error"] or "")


def test_goal_planner_one_sentence_plan_executes_agent_steps_and_feeds_0800_report(
    owner_session: dict,
    temp_db_session: Session,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    res = client.post(
        "/api/goals/plan-from-text",
        json={"sentence": "عايز موقعي يطلع الأول على كلمة سيو تقني", "execute_auto_steps": True},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    goal = data["goal"]
    assert goal["status"] == "active"
    assert len(goal["steps"]) >= 3
    owners = {s["owner"] for s in goal["steps"]}
    assert "agent" in owners and "user" in owners
    assert any(s["status"] == "done" and s["owner"] == "agent" for s in goal["steps"])
    assert goal["progress"]["completed_steps"] >= 1

    # Persisted in Goal DB table
    db_goal = temp_db_session.get(Goal, goal["id"])
    assert db_goal is not None and db_goal.workspace_id == ws_id

    # Daily 08:00 report reads from the exact same Goal record
    rep = client.post(
        "/api/reports/daily-0800/run",
        json={"enforce_hour": False, "post_to_chat": False},
        headers=headers,
    )
    assert rep.status_code == 200
    rbody = rep.json()
    assert rbody["sent"] is True
    assert any("عايز موقعي" in item for item in rbody["sections"]["done"])


def test_gsc_honest_empty_state_and_real_live_api_report(
    owner_session: dict,
    temp_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    # Disconnected -> honest empty state, ZERO invented numbers
    empty_res = client.get("/api/gsc/report", headers=headers)
    assert empty_res.status_code == 200
    eb = empty_res.json()
    assert eb["connected"] is False
    assert eb["totals"] is None
    assert eb["rows"] == []
    assert eb["missing_connection"] == "gsc_oauth"
    assert "/settings" in eb["settings_step"]

    # Connect read-only OAuth credentials
    conn_res = client.post(
        "/api/gsc/connect",
        json={
            "site_url": "https://moaazhassan.com/",
            "account_email": "owner@moaazhassan.com",
            "access_token": "ya29.test_readonly_token",
        },
        headers=headers,
    )
    assert conn_res.status_code == 200
    assert conn_res.json()["connected"] is True

    def fake_gsc_post(url: str, json=None, headers=None, timeout=None):
        assert "searchAnalytics/query" in url
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=req,
            json={
                "rows": [
                    {"keys": ["خبير سيو", "https://moaazhassan.com/"], "clicks": 42, "impressions": 610, "ctr": 0.0688, "position": 3.4},
                    {"keys": ["سيو تقني", "https://moaazhassan.com/seo"], "clicks": 18, "impressions": 300, "ctr": 0.06, "position": 5.2},
                ]
            },
        )

    monkeypatch.setattr("app.services.gsc_service.httpx.post", fake_gsc_post)
    live_res = client.post("/api/gsc/report", json={"days": 28}, headers=headers)
    assert live_res.status_code == 200
    lb = live_res.json()
    assert lb["connected"] is True
    assert lb["totals"]["clicks"] == 60
    assert lb["totals"]["impressions"] == 910
    assert len(lb["top_queries"]) == 2
    assert lb["top_queries"][0]["query"] == "خبير سيو"
    assert temp_db_session.query(GscMetric).filter(GscMetric.workspace_id == ws_id).count() == 2


def test_site_auditor_real_fetch_findings_with_evidence_and_backlink_check(
    owner_session: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    monkeypatch.setattr("app.services.site_auditor.resolve_browser_worker_url", lambda: "")

    class _AuditHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_HEAD(self):  # noqa: N802
            if self.path == "/broken-page":
                self.send_response(404)
            else:
                self.send_response(200)
            self.end_headers()

        def do_GET(self):  # noqa: N802
            if self.path == "/robots.txt":
                raw = b"User-agent: *\nAllow: /\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if self.path == "/profile":
                raw = (
                    b"<!doctype html><html><body>"
                    b"<a href='https://moaazhassan.com/'>SEO Expert</a>"
                    b"</body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if self.path == "/broken-page":
                self.send_response(404)
                self.end_headers()
                return

            html = (
                "<!doctype html><html><head>\n"
                "<title>Hi</title>\n"
                "<meta name='robots' content='noindex,nofollow'>\n"
                "<script src='/blocking.js'></script>\n"
                "</head><body>\n"
                "<a href='/broken-page'>Broken Link</a>\n"
                "</body></html>\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _AuditHandler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{port}"
        res = client.post(
            "/api/audit/site",
            json={
                "url": f"{base}/",
                "profile_url": f"{base}/profile",
                "target_backlink_url": "https://moaazhassan.com",
            },
            headers=headers,
        )
        assert res.status_code == 200
        audit = res.json()["audit"]
        assert audit["score"] < 80
        fids = {f["id"]: f for f in audit["findings"]}
        assert "noindex_detected" in fids
        assert "missing_meta_description" in fids
        assert "render_blocking_scripts" in fids
        assert any(k.startswith("broken_link_") for k in fids)
        assert "backlink_live_verified" in fids

        for f in audit["findings"]:
            assert f["evidence"]["page"]
            assert f["fix_ar"]
        assert fids["noindex_detected"]["evidence"]["line"] == 3
    finally:
        srv.shutdown()
        srv.server_close()


def test_interactive_worker_bypass_proxy_strips_env_browser_proxy_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    bw_dir = str((Path(__file__).resolve().parents[2] / "browser-worker").resolve())
    if bw_dir not in sys.path:
        sys.path.insert(0, bw_dir)
    import interactive_worker

    monkeypatch.setenv("BROWSER_PROXY_URL", "http://tok:x@proxy.scrape.do:8080")
    with_proxy = interactive_worker._build_interactive_launch_kwargs(
        tmp_path / "p1",
        "http://tok:x@proxy.scrape.do:8080",
        bypass_proxy=False,
        headless=True,
    )
    assert isinstance(with_proxy.get("proxy"), dict)
    assert "scrape.do" in with_proxy["proxy"]["server"]

    bypassed = interactive_worker._build_interactive_launch_kwargs(
        tmp_path / "p2",
        "http://tok:x@proxy.scrape.do:8080",
        bypass_proxy=True,
        headless=True,
    )
    assert "proxy" not in bypassed
    assert "ignore_https_errors" not in bypassed
    assert bypassed["headless"] is True
