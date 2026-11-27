"""Hardened local-only execution bridge guardrails (Instinct Audit F1-F5 remediation)."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

ALLOWED_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class SecurityViolationError(PermissionError):
    pass


def validate_bridge_bind_host(host: str) -> None:
    """F1: Never allow an unauthenticated or root bridge on 0.0.0.0."""
    normalized = (host or "").strip().lower()
    if normalized not in ALLOWED_LOOPBACK_HOSTS:
        raise SecurityViolationError(
            f"Refusing to bind exec bridge on non-loopback host '{host}'. Only 127.0.0.1 / ::1 is allowed (F1)."
        )


def assert_non_root_runtime() -> None:
    """F1/F5: Tenant workloads & bridges must not run as uid 0 on POSIX systems."""
    geteuid = getattr(os, "geteuid", None)
    if callable(geteuid) and geteuid() == 0:
        raise SecurityViolationError("Refusing to execute tenant workload as root UID 0 (F1/F5).")


def verify_task_bearer_token(provided: str | None, expected: str) -> bool:
    """F1: Per-task bearer token verified in constant time."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def verify_controller_binary_hash(binary_path: Path, pinned_sha256: str) -> bool:
    """F5/Audit Phase-2: Trust manifest must live controller-side, verified by SHA-256."""
    if not binary_path.exists() or not pinned_sha256:
        return False
    digest = hashlib.sha256(binary_path.read_bytes()).hexdigest()
    return hmac.compare_digest(digest.lower(), pinned_sha256.strip().lower())
