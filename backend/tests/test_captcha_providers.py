"""Tests for the captcha provider pack — MOCK MODE ONLY, no real keys, no network.

Covers: 2Captcha, Anti-Captcha, CapSolver, CapMonster Cloud (all via the shared
TaskApi client, driven by httpx.MockTransport) + the ChainSolver fallback +
the browser-worker token injector (fake httpx.AsyncClient).

Run: cd backend && pytest tests/test_captcha_providers.py -v
"""

from __future__ import annotations

import httpx
import pytest

from app.captcha.solver import CaptchaChallenge, CaptchaResult, get_captcha_solver
from app.captcha.solvers import anticaptcha as ac
from app.captcha.solvers import capmonster as cm
from app.captcha.solvers import capsolver as cs
from app.captcha.solvers import chain as chain_mod
from app.captcha.solvers import twocaptcha as tc
from app.captcha.solvers.anticaptcha import AntiCaptchaSolver
from app.captcha.solvers.capmonster import CapMonsterSolver
from app.captcha.solvers.capsolver import CapSolverSolver
from app.captcha.solvers.chain import ChainSolver
from app.captcha.solvers.task_api import TaskApiClient
from app.captcha.solvers.twocaptcha import TwoCaptchaSolver
from app.config import get_settings
from app.services import captcha_inject

PROVIDERS = [
    (TwoCaptchaSolver, "2captcha", "TWOCAPTCHA_API_KEY", "twocaptcha"),
    (AntiCaptchaSolver, "anticaptcha", "ANTICAPTCHA_API_KEY", "anticaptcha"),
    (CapSolverSolver, "capsolver", "CAPSOLVER_API_KEY", "capsolver"),
    (CapMonsterSolver, "capmonster", "CAPMONSTER_API_KEY", "capmonster"),
]


def _challenge(**kw):
    base = dict(
        run_id="run-1", site="example.com", challenge_type="recaptcha",
        page_url="https://example.com/signup", site_key="SITEKEY-123",
        metadata={"proxy": "http://user:pass@residential.proxy:8080"},
    )
    base.update(kw)
    return CaptchaChallenge(**base)


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    """Each test gets a clean Settings cache so env changes take effect.

    Empty env vars are set (not just deleted) so a real .env at the repo
    root cannot leak into tests: env vars beat the .env file in Settings.
    """
    get_settings.cache_clear()
    for env in ("RESIDENTIAL_PROXY", "CAPTCHA_PLUGIN_DIR", "CAPTCHA_SOLVER",
                "PASSXAPI_API_KEY"):
        monkeypatch.setenv(env, "")
    yield
    monkeypatch.delenv("CAPTCHA_MAX_SPEND_USD", raising=False)
    for _, _, env, _ in PROVIDERS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv("PASSXAPI_API_KEY", raising=False)
    monkeypatch.delenv("CAPTCHA_SOLVER_CHAIN", raising=False)
    get_settings.cache_clear()


def _enable(monkeypatch, key_env, spend="5"):
    monkeypatch.setenv(key_env, "test-key")
    monkeypatch.setenv("CAPTCHA_MAX_SPEND_USD", spend)
    get_settings.cache_clear()


def _mock_client(monkeypatch, solver_cls, handler):
    transport = httpx.MockTransport(handler)

    def _make(self, api_key):
        return TaskApiClient(solver_cls.spec.base_url, api_key, client=httpx.Client(transport=transport))

    monkeypatch.setattr(solver_cls, "_make_client", _make)


def _ready_handler(token="TOKEN-XYZ", token_key="gRecaptchaResponse"):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": 123})
        if path == "/getTaskResult":
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(200, json={"errorId": 0, "status": "processing"})
            return httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {token_key: token}})
        return httpx.Response(404, json={})

    return handler, calls


@pytest.mark.parametrize("solver_cls,name,key_env,short", PROVIDERS)
def test_registered_and_selectable(solver_cls, name, key_env, short):
    solver = get_captcha_solver(name)
    assert isinstance(solver, solver_cls)
    assert solver.name == name


@pytest.mark.parametrize("solver_cls,name,key_env,short", PROVIDERS)
def test_task_type_mapping(solver_cls, name, key_env, short):
    solver = solver_cls()
    assert solver._build_task(_challenge(), "")["type"] in (
        "RecaptchaV2TaskProxyless", "ReCaptchaV2TaskProxyLess",
    )
    assert solver._build_task(_challenge(challenge_type="turnstile"), "")["type"] in (
        "TurnstileTaskProxyless", "AntiTurnstileTaskProxyLess", "TurnstileTask",
    )
    assert solver._build_task(_challenge(challenge_type="hcaptcha"), "")["type"] in (
        "HCaptchaTaskProxyless", "HCaptchaTaskProxyLess",
    )
    # Cloudflare WAF (cf_clearance) is NOT a simple token task -> unsupported.
    assert solver._build_task(_challenge(challenge_type="cloudflare"), "") is None
    # v3 carries an action for the score.
    v3 = solver._build_task(_challenge(challenge_type="recaptcha_v3", metadata={"action": "login"}), "")
    assert v3 is not None and v3.get("pageAction") == "login"
    # Proxy switches to the proxy task variant.
    proxied = solver._build_task(_challenge(), "http://user:pass@residential.proxy:8080")
    assert proxied is not None
    assert "Proxyless" not in proxied["type"] and "ProxyLess" not in proxied["type"]


@pytest.mark.parametrize("solver_cls,name,key_env,short", PROVIDERS)
def test_success_mocked(monkeypatch, solver_cls, name, key_env, short):
    _enable(monkeypatch, key_env)
    handler, calls = _ready_handler()
    _mock_client(monkeypatch, solver_cls, handler)
    res = solver_cls().solve(_challenge())
    assert res.status == "passed"
    assert res.method == name
    assert res.cost_usd > 0
    assert res.instructions["token"] == "TOKEN-XYZ"
    assert res.instructions["token_field"] == "g-recaptcha-response"
    assert calls["n"] == 2
    assert "TOKEN-XYZ" not in str(res.status)
    assert "TOKEN-XYZ" not in str(res.blocked_reason)


@pytest.mark.parametrize("solver_cls,name,key_env,short", PROVIDERS)
def test_turnstile_token_key(monkeypatch, solver_cls, name, key_env, short):
    _enable(monkeypatch, key_env)
    handler, _ = _ready_handler(token="TOKEN-T", token_key="token")
    _mock_client(monkeypatch, solver_cls, handler)
    res = solver_cls().solve(_challenge(challenge_type="turnstile"))
    assert res.status == "passed"
    assert res.instructions["token"] == "TOKEN-T"
    assert res.instructions["token_field"] == "cf-turnstile-response"


@pytest.mark.parametrize("solver_cls,name,key_env,short", PROVIDERS)
def test_vendor_error_falls_back(monkeypatch, solver_cls, name, key_env, short):
    _enable(monkeypatch, key_env)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errorId": 1, "errorCode": "ERROR_KEY_DOES_NOT_EXIST", "errorDescription": "bad key"})

    _mock_client(monkeypatch, solver_cls, handler)
    res = solver_cls().solve(_challenge())
    assert res.status == "skipped"
    assert res.blocked_reason == f"{name}_failed"
    assert res.cost_usd == 0.0


@pytest.mark.parametrize("solver_cls,name,key_env,short", PROVIDERS)
def test_no_key_falls_back_to_manual(monkeypatch, solver_cls, name, key_env, short):
    monkeypatch.setenv("CAPTCHA_MAX_SPEND_USD", "5")
    get_settings.cache_clear()
    res = solver_cls().solve(_challenge())
    assert res.status == "awaiting_captcha_manual"
    assert res.blocked_reason == f"{name}_no_key"


@pytest.mark.parametrize("solver_cls,name,key_env,short", PROVIDERS)
def test_no_sitekey_falls_back_to_manual(monkeypatch, solver_cls, name, key_env, short):
    _enable(monkeypatch, key_env)
    res = solver_cls().solve(_challenge(site_key=None))
    assert res.status == "awaiting_captcha_manual"
    assert res.blocked_reason == f"{name}_no_sitekey"


@pytest.mark.parametrize("solver_cls,name,key_env,short", PROVIDERS)
def test_phone_is_never_solved(monkeypatch, solver_cls, name, key_env, short):
    _enable(monkeypatch, key_env)
    res = solver_cls().solve(_challenge(challenge_type="phone_verify"))
    assert res.status == "phone_verify_blocked"
    assert res.cost_usd == 0.0


@pytest.mark.parametrize("solver_cls,name,key_env,short", PROVIDERS)
def test_spend_cap_zero_blocks_paid_solving(monkeypatch, solver_cls, name, key_env, short):
    monkeypatch.setenv(key_env, "test-key")
    monkeypatch.setenv("CAPTCHA_MAX_SPEND_USD", "0")
    get_settings.cache_clear()
    res = solver_cls().solve(_challenge())
    assert res.status == "awaiting_captcha_manual"
    assert res.blocked_reason == f"{name}_spend_cap_zero"


# --- ChainSolver ---------------------------------------------------------------

class _FakeSolver:
    def __init__(self, name, result):
        self.name = name
        self._result = result

    def solve(self, challenge):
        return self._result


def _passed(method, cost=0.001):
    return CaptchaResult(status="passed", method=method, cost_usd=cost,
                         instructions={"token": "T", "token_field": "g-recaptcha-response"})


def _manual(reason):
    return CaptchaResult(status="awaiting_captcha_manual", method="x", blocked_reason=reason, cost_usd=0.0)


def test_chain_registered():
    assert isinstance(get_captcha_solver("chain"), ChainSolver)


def test_chain_order_default(monkeypatch):
    monkeypatch.delenv("CAPTCHA_SOLVER_CHAIN", raising=False)
    get_settings.cache_clear()
    assert chain_mod.chain_order() == ["passxapi", "capmonster", "capsolver", "2captcha", "anticaptcha"]


def test_chain_skips_unconfigured_and_uses_next(monkeypatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_CHAIN", "passxapi,capmonster")
    get_settings.cache_clear()
    fakes = {
        "passxapi": _FakeSolver("passxapi", _manual("passxapi_no_key")),
        "capmonster": _FakeSolver("capmonster", _passed("capmonster")),
    }
    monkeypatch.setattr(chain_mod, "get_captcha_solver", lambda name: fakes[name])
    res = ChainSolver().solve(_challenge())
    assert res.status == "passed"
    assert res.method == "capmonster"


def test_chain_all_fail_falls_back_to_manual(monkeypatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_CHAIN", "passxapi,2captcha")
    get_settings.cache_clear()
    fakes = {
        "passxapi": _FakeSolver("passxapi", _manual("passxapi_no_key")),
        "2captcha": _FakeSolver("2captcha", CaptchaResult(status="skipped", method="2captcha", blocked_reason="2captcha_failed", cost_usd=0.0)),
    }
    monkeypatch.setattr(chain_mod, "get_captcha_solver", lambda name: fakes[name])
    res = ChainSolver().solve(_challenge())
    assert res.status == "awaiting_captcha_manual"
    assert res.method == "chain"


def test_chain_phone_is_never_solved():
    res = ChainSolver().solve(_challenge(challenge_type="phone_verify"))
    assert res.status == "phone_verify_blocked"


# --- captcha_inject helper -------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"status": "completed"}

    def json(self):
        return self._body


class _FakeAsyncClient:
    resp = _FakeResp()

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        assert "/sessions/inject-captcha-token" in url
        assert json["token"] == "TOKEN-XYZ"  # token travels in body, never in URL
        assert "TOKEN-XYZ" not in url
        return self.resp


@pytest.mark.asyncio
async def test_inject_completed(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.resp = _FakeResp(200, {"status": "completed"})
    out = await captcha_inject.inject_token_via_worker(
        worker_url="http://127.0.0.1:8090", run_id="r1", url="https://example.com/signup",
        token="TOKEN-XYZ",
    )
    assert out == "completed"


@pytest.mark.asyncio
async def test_inject_http_error(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.resp = _FakeResp(500, {})
    out = await captcha_inject.inject_token_via_worker(
        worker_url="http://127.0.0.1:8090", run_id="r1", url="https://example.com/signup",
        token="TOKEN-XYZ",
    )
    assert out == "error"


@pytest.mark.asyncio
async def test_inject_no_token_or_no_worker():
    assert await captcha_inject.inject_token_via_worker(
        worker_url="http://127.0.0.1:8090", run_id="r1", url="https://example.com/", token="",
    ) == "no_token"
    assert await captcha_inject.inject_token_via_worker(
        worker_url="", run_id="r1", url="https://example.com/", token="TOKEN-XYZ",
    ) == "unavailable"
