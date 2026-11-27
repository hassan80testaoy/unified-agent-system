"""Central configuration loader — no hardcoded secrets, model names, or prices."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def _resolve_repo_env() -> str | None:
    root_env = REPO_ROOT / ".env"
    if root_env.exists():
        return str(root_env)
    backend_env = BACKEND_DIR / ".env"
    if backend_env.exists():
        return str(backend_env)
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_repo_env(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    app_host: str = "127.0.0.1"
    api_port: int = 8010
    domain: str = "localhost"

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'unified_agent.db').as_posix()}"
    redis_url: str = ""

    vault_master_key: str = ""
    mailbox_enc_key: str = ""
    jwt_secret_key: str = ""
    backup_enc_key: str = ""

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    gemini_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    budget_usd_daily_anthropic: float = 5.0
    budget_usd_daily_openai: float = 3.0

    # Captcha plugin configuration (manual human-in-the-loop only)
    captcha_solver: str = "manual"
    captcha_max_spend_usd: float = 0.0
    captcha_manual_timeout_sec: int = 900

    # Mail & Google Account OAuth
    mail_mode: str = "imap"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:3010/api/google-accounts/oauth/callback"
    verification_code_timeout_sec: int = 600

    # Optional Telegram notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Browser worker & noVNC tunnel display settings
    browser_worker_url: str = ""
    vps_ssh_host: str = "deploy@YOUR_SERVER_IP"
    novnc_local_port: int = 6080

    # Directories
    data_dir: Path = BACKEND_DIR / "data"
    config_dir: Path = REPO_ROOT / "config"
    browser_profiles_dir: Path = BACKEND_DIR / "data" / "browser_profiles"
    artifacts_dir: Path = BACKEND_DIR / "data" / "artifacts"

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def killswitch_file(self) -> Path:
        return REPO_ROOT / "STOP"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.browser_profiles_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def load_yaml_config(filename: str) -> dict[str, Any]:
    """Load YAML config from config/ directory without hardcoded defaults."""
    settings = get_settings()
    candidates = [
        settings.config_dir / filename,
        Path("/app/config") / filename,
        REPO_ROOT / "config" / filename,
    ]
    for path in candidates:
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    return {}


def get_env_secret_by_ref(auth_ref: str) -> str | None:
    """Resolve `env:VAR_NAME` references without storing plaintext keys in config files."""
    if not auth_ref or auth_ref == "none":
        return None
    if auth_ref.startswith("env:"):
        var_name = auth_ref.split(":", 1)[1].strip()
        return os.environ.get(var_name) or None
    return None
