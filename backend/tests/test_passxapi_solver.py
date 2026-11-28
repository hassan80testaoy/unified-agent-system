"""Tests for PassXAPI solver plugin — MOCK MODE ONLY, no real API key, no network.

Run from the repo root after copying the plugin in:
    cd backend && .\\.venv\\Scripts\\pytest.exe tests/test_passxapi_solver.py -v

These tests never call passxapi.com. The client is driven by httpx.MockTransport,
and the solver's PassXAPIClient is monkeypatched for the success path.
"""

from __future__ import annotations

import httpx
import pytest

from app.captcha.solver import CaptchaChallenge, get_captcha_solver
from app.captcha.solvers import passxapi as px
from app.captcha.solvers.passxapi import PassXAPIClient, PassXAPISolver
from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    """Isolate from a real .env: env vars beat the .env file in Settings.

    A .env at the repo root (e.g. CAPTCHA_SOLVER=chain, RESIDENTIAL_PROXY
    set) would otherwise leak into "no key / no proxy" tests, and the
    Settings cache would keep a key set by an earlier test.
    """
    get_settings.cache_clear()
    for env in ("PASSXAPI_API_KEY", "PASSXAPI_PROXY", "RESIDENTIAL_PROXY",
                "CAPTCHA_PLUGIN_DIR", "CAPTCHA_SOLVER"):
        monkeypatch.setenv(env, "")
    yield
    get_settings.cache_clear()


def _challenge(**kw):
    base = dict(
        run_id="run-1", site="example.com", challenge_type="recaptcha",
        page_url="https://example.com/signup", site_key="SITEKEY-123",
        metadata={"proxy": "http://user:pass@residential.proxy:8080"},
    )
    base.update(kw)
    return CaptchaChallenge(**base)


def test_registered_and_selectable(monkeypatch):
    monkeypatch.setenv("PASSXAPI_API_KEY", "test-key")
    solver = get_captcha_solver("passxapi")
    assert isinstance(solver, PassXAPISolver)


def test_task_type_and_token_field_mapping():
    assert px.task_type_for(_challenge()) == "recaptcha_v2"
    assert px.task_type_for(_challenge(challenge_type="turnstile")) == "cloudflare_turnstile"
    assert px.task_type_for(_challenge(challenge_type="hcaptcha")) == "hcaptcha"
    assert px.task_type_for(_challenge(challenge_type="image_captcha")) is None
    assert px.token_field_for("recaptcha") == "g-recaptcha-response"
    assert px.token_field_for("turnstile") == "cf-turnstile-response"
    assert px.token_field_for("hcaptcha") == "h-captcha-response"


def test_detect_extracts_sitekey():
    solver = PassXAPISolver()
    ch = solver.detect('<div class="g-recaptcha" data-sitekey="ABC-999"></div>')
    assert ch is not None
    assert ch.challenge_type == "recaptcha"
    assert ch.site_key == "ABC-999"


def test_phone_is_never_solved(monkeypatch):
    monkeypatch.setenv("PASSXAPI_API_KEY", "test-key")
    res = PassXAPISolver().solve(_challenge(challenge_type="phone_verify"))
    assert res.status == "phone_verify_blocked"
    assert res.cost_usd == 0.0


def test_no_key_falls_back_to_manual(monkeypatch):
    # setenv "" (not delenv): a real .env could otherwise supply the key.
    monkeypatch.setenv("PASSXAPI_API_KEY", "")
    get_settings.cache_clear()
    res = PassXAPISolver().solve(_challenge())
    assert res.status == "awaiting_captcha_manual"
    assert res.blocked_reason == "passxapi_no_key"


def test_no_proxy_falls_back_to_manual(monkeypatch):
    monkeypatch.setenv("PASSXAPI_API_KEY", "test-key")
    # setenv "" (not delenv): a real .env could otherwise supply the proxy.
    monkeypatch.setenv("PASSXAPI_PROXY", "")
    monkeypatch.setenv("RESIDENTIAL_PROXY", "")
    get_settings.cache_clear()
    ch = _challenge(metadata={})
    res = PassXAPISolver().solve(ch)
    assert res.status == "awaiting_captcha_manual"
    assert res.blocked_reason == "passxapi_no_proxy"


def test_no_sitekey_falls_back_to_manual(monkeypatch):
    monkeypatch.setenv("PASSXAPI_API_KEY", "test-key")
    res = PassXAPISolver().solve(_challenge(site_key=None))
    assert res.status == "awaiting_captcha_manual"
    assert res.blocked_reason == "passxapi_no_sitekey"


def test_client_submit_poll_mock():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/task/submit":
            assert request.headers["x-api-key"] == "test-key"
            return httpx.Response(200, json={"task_id": "t-1"})
        if request.url.path == "/task/result/t-1":
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(200, json={"status": "PENDING"})
            return httpx.Response(200, json={"status": "SUCCESS", "result": {"token": "TOKEN-XYZ"}})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    client = PassXAPIClient("test-key", client=httpx.Client(transport=transport))
    result = client.solve_task(
        task_type="recaptcha_v2", target_url="https://example.com/signup",
        site_key="SITEKEY-123", proxy="http://user:pass@residential.proxy:8080",
        poll_interval=0.01, timeout_sec=5,
    )
    assert result["token"] == "TOKEN-XYZ"
    assert calls["n"] == 2


def test_solver_success_mocked(monkeypatch):
    monkeypatch.setenv("PASSXAPI_API_KEY", "test-key")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def solve_task(self, **kw):
            return {"token": "TOKEN-XYZ"}

        def close(self):
            pass

    monkeypatch.setattr(px, "PassXAPIClient", FakeClient)
    res = PassXAPISolver().solve(_challenge())
    assert res.status == "passed"
    assert res.method == "passxapi"
    assert res.cost_usd == pytest.approx(0.001)
    assert res.instructions["token_field"] == "g-recaptcha-response"
    # Token is present in-memory for immediate injection, and must NOT appear
    # in the plain status/blocked_reason fields.
    assert res.instructions["token"] == "TOKEN-XYZ"
    assert "TOKEN-XYZ" not in str(res.status)
    assert "TOKEN-XYZ" not in str(res.blocked_reason)


def test_solver_failure_falls_back_to_manual(monkeypatch):
    monkeypatch.setenv("PASSXAPI_API_KEY", "test-key")

    class FailingClient:
        def __init__(self, *a, **k):
            pass

        def solve_task(self, **kw):
            raise RuntimeError("vendor down")

        def close(self):
            pass

    monkeypatch.setattr(px, "PassXAPIClient", FailingClient)
    res = PassXAPISolver().solve(_challenge())
    assert res.status == "skipped"
    assert res.blocked_reason == "passxapi_failed"
    assert res.cost_usd == 0.0
