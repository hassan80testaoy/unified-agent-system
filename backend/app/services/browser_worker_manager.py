"""Playwright Browser Worker lifecycle manager & truthful live health probe (§6.0.1)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from typing import Any

import httpx

from app.config import REPO_ROOT, get_settings

_WORKER_PROC: subprocess.Popen[bytes] | None = None
_EFFECTIVE_WORKER_URL: str = ""


def resolve_browser_worker_url() -> str:
    """Return configured BROWSER_WORKER_URL or the auto-started local worker URL."""
    configured = (get_settings().browser_worker_url or "").strip().rstrip("/")
    if configured:
        return configured
    return _EFFECTIVE_WORKER_URL.strip().rstrip("/")


def probe_browser_worker(url: str | None = None, *, timeout: float = 1.5) -> dict[str, Any]:
    """Verify the Playwright browser worker actually answers HTTP 200 `/health` with status=='ok'."""
    target = (url if url is not None else resolve_browser_worker_url()).strip().rstrip("/")
    if not target:
        return {"ok": False, "url": "", "error": "browser_worker_url_not_set"}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{target}/health")
        if resp.status_code != 200:
            return {"ok": False, "url": target, "error": f"http_{resp.status_code}"}
        body = resp.json()
        if isinstance(body, dict) and body.get("status") == "ok":
            return {
                "ok": True,
                "url": target,
                "status_code": 200,
                "active_sessions": body.get("active_sessions", 0),
                "lease_locked": bool(body.get("lease_locked", False)),
            }
        return {"ok": False, "url": target, "error": "invalid_health_payload"}
    except Exception as exc:
        return {"ok": False, "url": target, "error": f"{type(exc).__name__}: {exc}"}


def ensure_local_browser_worker_started() -> str | None:
    """Auto-start `browser-worker/interactive_worker.py` in local dev if unset/not yet running."""
    global _WORKER_PROC, _EFFECTIVE_WORKER_URL

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None

    settings = get_settings()
    if settings.is_production:
        return None

    configured = (settings.browser_worker_url or "").strip().rstrip("/")
    candidate = configured or "http://127.0.0.1:7080"

    # Already reachable (started by dev-windows.ps1, Docker, or previous reload)
    if probe_browser_worker(candidate, timeout=1.0)["ok"]:
        _EFFECTIVE_WORKER_URL = candidate
        return candidate

    # Do not spawn locally if owner pointed BROWSER_WORKER_URL at a remote host
    if configured and not configured.startswith(("http://127.0.0.1", "http://localhost")):
        return None

    worker_dir = REPO_ROOT / "browser-worker"
    worker_entry = worker_dir / "interactive_worker.py"
    if not worker_entry.exists():
        return None
    if importlib.util.find_spec("playwright") is None:
        return None

    port = "7080"
    if configured:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(configured)
            if parsed.port:
                port = str(parsed.port)
        except Exception:
            port = "7080"

    env = os.environ.copy()
    env.setdefault("ARTIFACTS_DIR", str(settings.artifacts_dir))
    env.setdefault("PROFILES_DIR", str(settings.browser_profiles_dir))
    env.setdefault("BROWSER_HEADLESS", os.environ.get("BROWSER_HEADLESS", "true"))
    if settings.residential_proxy:
        env.setdefault("RESIDENTIAL_PROXY", settings.residential_proxy)
        env.setdefault("BROWSER_PROXY_URL", settings.residential_proxy)

    log_dir = settings.data_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_fh = (log_dir / "browser_worker.log").open("ab")
    try:
        _WORKER_PROC = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "interactive_worker:app",
                "--host",
                "127.0.0.1",
                "--port",
                port,
            ],
            cwd=str(worker_dir),
            env=env,
            stdout=log_fh,
            stderr=log_fh,
        )
    except Exception:
        log_fh.close()
        return None

    local_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if _WORKER_PROC.poll() is not None:
            break
        if probe_browser_worker(local_url, timeout=0.6)["ok"]:
            _EFFECTIVE_WORKER_URL = local_url
            return local_url
        time.sleep(0.25)
    return None


def stop_local_browser_worker() -> None:
    global _WORKER_PROC, _EFFECTIVE_WORKER_URL
    if _WORKER_PROC is not None:
        try:
            if _WORKER_PROC.poll() is None:
                _WORKER_PROC.terminate()
                _WORKER_PROC.wait(timeout=3)
        except Exception:
            try:
                _WORKER_PROC.kill()
            except Exception:
                pass
        _WORKER_PROC = None
    _EFFECTIVE_WORKER_URL = ""
