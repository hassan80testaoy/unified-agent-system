"""Fernet encryption, masking helpers, and server-side cryptographic password generation (F3 / Rule 3)."""

from __future__ import annotations

import os
import secrets
import string
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _load_or_init_fernet_key() -> bytes:
    settings = get_settings()
    for candidate in (settings.vault_master_key, settings.mailbox_enc_key):
        if candidate and candidate.strip():
            return candidate.strip().encode("utf-8")

    secret_file = Path("/run/secrets/vault_master_key")
    if secret_file.exists():
        return secret_file.read_text(encoding="utf-8").strip().encode("utf-8")

    if settings.is_production:
        raise RuntimeError(
            "VAULT_MASTER_KEY or MAILBOX_ENC_KEY must be set in production (.env or Docker secret)."
        )

    dev_key_file = settings.data_dir / ".dev_master.key"
    if dev_key_file.exists():
        return dev_key_file.read_text(encoding="utf-8").strip().encode("utf-8")

    generated = Fernet.generate_key()
    dev_key_file.write_text(generated.decode("utf-8"), encoding="utf-8")
    try:
        os.chmod(dev_key_file, 0o600)
    except OSError:
        pass
    return generated


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    return Fernet(_load_or_init_fernet_key())


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str | bytes | None) -> str:
    if not ciphertext:
        return ""
    token = ciphertext.encode("utf-8") if isinstance(ciphertext, str) else ciphertext
    try:
        return get_fernet().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt secret with current master key") from exc


def mask_email(email_addr: str) -> str:
    if "@" not in email_addr:
        return "***"
    local, domain = email_addr.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*" if local else "*"
    else:
        masked_local = local[0] + ("*" * min(4, len(local) - 2)) + local[-1]
    return f"{masked_local}@{domain}"


def mask_last4(secret: str) -> str:
    if not secret:
        return ""
    return secret[-4:] if len(secret) >= 4 else "*" * len(secret)


def generate_site_password(length: int = 20) -> str:
    """Server-side cryptographic password generator — never delegated to an LLM."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_"
    required = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*()-_"),
    ]
    rest = [secrets.choice(alphabet) for _ in range(max(12, length) - len(required))]
    chars = required + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
