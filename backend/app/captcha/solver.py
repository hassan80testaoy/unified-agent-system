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
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from app.config import get_settings, load_yaml_config


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


def discover_solvers() -> dict[str, type[CaptchaSolver]]:
    import app.captcha.solvers as solvers_pkg

    for module_info in pkgutil.iter_modules(solvers_pkg.__path__, prefix="app.captcha.solvers."):
        importlib.import_module(module_info.name)
    return dict(_SOLVER_REGISTRY)


def get_configured_solver_name() -> str:
    settings = get_settings()
    providers_cfg = load_yaml_config("providers.example.yaml")
    for entry in providers_cfg.get("providers", []):
        if entry.get("kind") == "captcha" and entry.get("enabled", True):
            return str(entry.get("solver") or settings.captcha_solver or "manual")
    return settings.captcha_solver or "manual"


def get_captcha_solver(name: str | None = None) -> CaptchaSolver:
    discover_solvers()
    selected = (name or get_configured_solver_name() or "manual").strip().lower()
    if selected not in _SOLVER_REGISTRY:
        selected = "manual"
    cls = _SOLVER_REGISTRY[selected]
    return cls()
