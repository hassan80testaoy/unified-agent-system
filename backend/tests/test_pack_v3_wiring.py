"""Pack v3 wiring tests — the two silent-failure gaps found 2026-11-28.

Patch 9: CAPTCHA_PLUGIN_DIR from .env alone must load stealth plugins.
  On a direct Windows run (uvicorn, no Docker) .env values never reach
  os.environ, so the loader's os.environ-only read left stealth mode off
  with no error — discovery returned ['manual'] only.

Patch 10: RESIDENTIAL_PROXY from .env must reach the solvers AND the
  browser. Before v3 nothing passed it to the browser: the backlink agent
  read proxy only from task_input["proxy"] and the chat path never passed
  one, so the browser ran direct while the solver (if keyed) solved via
  the proxy IP — token/IP mismatch. And a MITM proxy such as Scrape.do
  Proxy Mode re-signs TLS, which Chromium rejects unless the worker
  ignores HTTPS errors for proxied sessions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.captcha.solver import CaptchaChallenge, CaptchaResult, discover_solvers
from app.captcha.solvers import passxapi as px_mod
from app.captcha.solvers import task_api as ta_mod
from app.captcha.solvers.capmonster import CapMonsterSolver
from app.config import get_settings

DUMMY_SOLVER_SRC = '''\
from app.captcha.solver import CaptchaChallenge, CaptchaResult, register_solver


@register_solver("dummyprobe")
class DummyProbeSolver:
    def detect(self, page):
        return None

    def solve(self, challenge: CaptchaChallenge) -> CaptchaResult:
        return CaptchaResult(status="skipped", method="dummyprobe")

    def on_manual_solved(self, run_id: str) -> None:
        pass
'''


@pytest.fixture
def _fresh_settings(monkeypatch):
    # Empty env vars (not just deleted): env beats the .env file in
    # Settings, so a real .env at the repo root cannot leak into tests.
    for env in ("CAPTCHA_PLUGIN_DIR", "RESIDENTIAL_PROXY", "CAPTCHA_SOLVER"):
        monkeypatch.setenv(env, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _challenge() -> CaptchaChallenge:
    return CaptchaChallenge(
        run_id="r1", site="example.com",
        challenge_type="recaptcha", page_url="https://example.com/",
    )


def test_settings_has_new_fields(_fresh_settings):
    assert getattr(get_settings(), "captcha_plugin_dir", None) == ""
    assert getattr(get_settings(), "residential_proxy", None) == ""


def test_loader_reads_plugin_dir_from_settings(monkeypatch, tmp_path, _fresh_settings):
    """Stealth plugins load with the dir ONLY in Settings (.env), no env var."""
    plugin_dir = tmp_path / "captcha-plugins"
    plugin_dir.mkdir()
    (plugin_dir / "dummyprobe.py").write_text(DUMMY_SOLVER_SRC, encoding="utf-8")
    monkeypatch.delenv("CAPTCHA_PLUGIN_DIR", raising=False)
    import app.captcha.solver as solver_mod

    monkeypatch.setattr(
        solver_mod, "get_settings",
        lambda: SimpleNamespace(captcha_plugin_dir=str(plugin_dir)),
    )
    try:
        found = discover_solvers()
    finally:
        solver_mod._SOLVER_REGISTRY.pop("dummyprobe", None)
        sys.modules.pop("app.captcha.solvers.dummyprobe", None)
        sys.modules.pop("_captcha_plugin_dummyprobe", None)
    assert "dummyprobe" in found
    assert "manual" in found


def test_taskapi_proxy_falls_back_to_settings(monkeypatch, _fresh_settings):
    monkeypatch.delenv("RESIDENTIAL_PROXY", raising=False)
    monkeypatch.delenv("CAPMONSTER_PROXY", raising=False)
    monkeypatch.setattr(
        ta_mod, "get_settings",
        lambda: SimpleNamespace(residential_proxy="http://tok:super=true@pm.scrape.do:8080"),
    )
    assert CapMonsterSolver()._proxy(_challenge()) == (
        "http://tok:super=true@pm.scrape.do:8080"
    )


def test_passxapi_proxy_falls_back_to_settings(monkeypatch, _fresh_settings):
    monkeypatch.delenv("RESIDENTIAL_PROXY", raising=False)
    monkeypatch.delenv("PASSXAPI_PROXY", raising=False)
    monkeypatch.setattr(
        px_mod, "get_settings",
        lambda: SimpleNamespace(residential_proxy="http://tok:super=true@pm.scrape.do:8080"),
    )
    assert px_mod._get_proxy(_challenge()) == (
        "http://tok:super=true@pm.scrape.do:8080"
    )


def _load_worker_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "browser-worker" / "worker.py"
    spec = importlib.util.spec_from_file_location("bw_worker_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["bw_worker_under_test"] = module
    spec.loader.exec_module(module)
    return module


def test_worker_launch_kwargs_with_mitm_proxy(monkeypatch):
    worker = _load_worker_module()
    monkeypatch.delenv("BROWSER_PROXY_URL", raising=False)
    monkeypatch.delenv("BROWSER_IGNORE_HTTPS_ERRORS", raising=False)
    kwargs = worker._launch_kwargs(Path("/tmp/profiles"), "http://tok:super=true@pm.scrape.do:8080")
    assert kwargs["proxy"]["server"] == "http://pm.scrape.do:8080"
    assert kwargs["proxy"]["username"] == "tok"
    assert kwargs["proxy"]["password"] == "super=true"
    assert kwargs["ignore_https_errors"] is True


def test_worker_launch_kwargs_without_proxy(monkeypatch):
    worker = _load_worker_module()
    monkeypatch.delenv("BROWSER_PROXY_URL", raising=False)
    monkeypatch.delenv("BROWSER_IGNORE_HTTPS_ERRORS", raising=False)
    kwargs = worker._launch_kwargs(Path("/tmp/profiles"), None)
    assert "proxy" not in kwargs
    assert "ignore_https_errors" not in kwargs


def test_worker_launch_kwargs_respects_opt_out(monkeypatch):
    worker = _load_worker_module()
    monkeypatch.delenv("BROWSER_PROXY_URL", raising=False)
    monkeypatch.setenv("BROWSER_IGNORE_HTTPS_ERRORS", "false")
    kwargs = worker._launch_kwargs(Path("/tmp/profiles"), "http://tok:super=true@pm.scrape.do:8080")
    assert "proxy" in kwargs
    assert "ignore_https_errors" not in kwargs
