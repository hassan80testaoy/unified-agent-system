"""CAPTCHA Plugin Interface (`CaptchaSolver`) — Human-in-the-Loop default.

Design rules:
- Single stable `CaptchaSolver(Protocol)` interface: `detect`, `solve`, `on_manual_solved`.
- Default and sole built-in implementation is `ManualSolver` (human-in-the-loop via localhost noVNC).
- Adding another solver plugin later requires only a new module under `captcha/solvers/`
  plus one config line in `providers.yaml` / `.env` (`CAPTCHA_SOLVER`), with zero edits to
  the Backlink agent, Runs engine, WebSocket hub, or database schema.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from app.config import get_settings, load_yaml_config

logger = logging.getLogger(__name__)


@dataclass
class CaptchaChallenge:
    run_id: str
    site: str
    challenge_type: str  # recaptcha | turnstile | hcaptcha | cloudflare | image_captcha | phone_verify
    page_url: str
    site_key: str | None = None
    blocked_reason: str = "captcha_manual"
    screenshot_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    proxy: str | None = None


@dataclass
class CaptchaResult:
    status: str  # awaiting_captcha_manual | passed | skipped | timeout | phone_verify_blocked
    method: str = "manual"
    blocked_reason: str | None = None
    duration_ms: int = 0
    cost_usd: float = 0.0
    instructions: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CaptchaSolver(Protocol):
    """Plugin interface implemented by CAPTCHA handlers."""

    def detect(self, page: Any) -> CaptchaChallenge | None:
        ...

    def solve(self, challenge: CaptchaChallenge) -> CaptchaResult:
        ...

    def on_manual_solved(self, run_id: str) -> None:
        ...


_SOLVER_REGISTRY: dict[str, type[CaptchaSolver]] = {}


def register_solver(name: str) -> Callable[[type[CaptchaSolver]], type[CaptchaSolver]]:
    def _decorator(cls: type[CaptchaSolver]) -> type[CaptchaSolver]:
        _SOLVER_REGISTRY[name] = cls
        return cls

    return _decorator


def _load_external_plugins() -> None:
    """Load optional solver plugins from a directory OUTSIDE the repo.

    The owner keeps automated-solver plugins outside the project tree
    (CAPTCHA_PLUGIN_DIR, e.g. a sibling folder) so the project itself stays
    Human-in-the-Loop only per AGENTS.md rule 2. Each .py file in that dir is
    imported and registered under its package name in sys.modules, so
    `from app.captcha.solvers import <name>` keeps working. task_api.py (the
    shared base) is loaded first. Nothing is loaded when neither the env var
    nor the .env setting is set.
    """
    import importlib.util
    import sys
    from pathlib import Path

    plugin_dir = os.environ.get("CAPTCHA_PLUGIN_DIR", "").strip()
    if not plugin_dir:
        # Fall back to the .env value: Settings loads .env, but a direct
        # Windows run (uvicorn, no Docker) never puts .env into os.environ,
        # so .env alone would silently leave stealth mode off.
        try:
            plugin_dir = (get_settings().captcha_plugin_dir or "").strip()
        except Exception:  # noqa: BLE001 - a broken Settings must not break discovery
            plugin_dir = ""
    if not plugin_dir:
        return
    path = Path(plugin_dir)
    if not path.is_dir():
        logger.warning("CAPTCHA_PLUGIN_DIR is set but not a directory: %s", plugin_dir)
        return
    files = sorted(path.glob("*.py"), key=lambda p: p.name)
    files.sort(key=lambda p: p.name != "task_api.py")
    for f in files:
        mod_name = f"app.captcha.solvers.{f.stem}"
        if mod_name in sys.modules:
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"_captcha_plugin_{f.stem}", f)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 — a broken plugin must not kill discovery
            logger.warning("Skipping captcha plugin %s: %s", f.name, type(exc).__name__)


def discover_solvers() -> dict[str, type[CaptchaSolver]]:
    import app.captcha.solvers as solvers_pkg

    for module_info in pkgutil.iter_modules(solvers_pkg.__path__, prefix="app.captcha.solvers."):
        importlib.import_module(module_info.name)
    _load_external_plugins()
    return dict(_SOLVER_REGISTRY)


def get_configured_solver_name() -> str:
    """Resolve active solver plugin with explicit precedence:
    1) Live CAPTCHA_SOLVER env var
    2) providers.yaml active captcha entry
    3) Settings.captcha_solver (from .env) when non-default
    4) providers.example.yaml active captcha entry
    5) Default 'manual'
    """
    env_solver = os.environ.get("CAPTCHA_SOLVER", "").strip()
    if env_solver:
        return env_solver

    for cfg_name in ("providers.yaml",):
        providers_cfg = load_yaml_config(cfg_name)
        for entry in providers_cfg.get("providers", []):
            if entry.get("kind") == "captcha" and entry.get("enabled", True):
                if entry.get("solver"):
                    return str(entry.get("solver"))

    settings = get_settings()
    if settings.captcha_solver and settings.captcha_solver.strip() != "manual":
        return settings.captcha_solver.strip()

    example_cfg = load_yaml_config("providers.example.yaml")
    for entry in example_cfg.get("providers", []):
        if entry.get("kind") == "captcha" and entry.get("enabled", True):
            if entry.get("solver"):
                return str(entry.get("solver"))

    return settings.captcha_solver or "manual"


def get_captcha_solver(name: str | None = None) -> CaptchaSolver:
    discover_solvers()
    selected = (name or get_configured_solver_name() or "manual").strip().lower()
    if selected not in _SOLVER_REGISTRY:
        selected = "manual"
    cls = _SOLVER_REGISTRY[selected]
    return cls()


async def dispatch_solve(solver: CaptchaSolver, challenge: CaptchaChallenge) -> CaptchaResult:
    """Invoke any registered CaptchaSolver (sync or async) with safe fallback on exception."""
    try:
        res = solver.solve(challenge)
        if inspect.isawaitable(res):
            res = await res
        if isinstance(res, CaptchaResult):
            return res
        return CaptchaResult(
            status="fallback_manual",
            method=getattr(solver, "name", type(solver).__name__),
            blocked_reason="invalid_solver_result",
            cost_usd=0.0,
        )
    except Exception as exc:
        logger.warning("CAPTCHA solver plugin error (%s): %s", type(solver).__name__, type(exc).__name__)
        return CaptchaResult(
            status="fallback_manual",
            method=getattr(solver, "name", type(solver).__name__),
            blocked_reason=f"solver_error:{type(exc).__name__}",
            cost_usd=0.0,
        )
