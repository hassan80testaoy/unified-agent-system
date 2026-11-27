#!/usr/bin/env python3
"""Static & policy verification script for Unified Agent System (F1-F5, CAPTCHA policy, RTL parity)."""

from __future__ import annotations

import json
import pathlib
import re
import sys
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def check_docker_compose() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose.get("services", {})
    assert "caddy" in services and "prod" in services["caddy"].get("profiles", []), "caddy must be in prod profile"
    assert "ollama" in services and "local-llm" in services["ollama"].get("profiles", []), "ollama must be in local-llm profile"
    for svc in ("postgres", "redis"):
        if services[svc].get("ports"):
            fail(f"Service '{svc}' must NOT expose public ports")
    bw = services["browser-worker"]
    if bw.get("ports") != ["127.0.0.1:6080:6080"]:
        fail(f"browser-worker ports must be ['127.0.0.1:6080:6080'], got {bw.get('ports')}")
    if bw.get("shm_size") != "1gb":
        fail("browser-worker must define shm_size: '1gb'")
    for name, svc in services.items():
        if "no-new-privileges:true" not in svc.get("security_opt", []):
            fail(f"Service '{name}' missing security_opt no-new-privileges:true")
    ok("docker-compose.yml profiles, localhost-only noVNC, hidden DB/Redis ports, and security_opt verified")


def check_env_example() -> None:
    txt = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    if "CAPTCHA_SOLVER=manual" not in txt or "CAPTCHA_MAX_SPEND_USD=0" not in txt:
        fail(".env.example must set CAPTCHA_SOLVER=manual and CAPTCHA_MAX_SPEND_USD=0")
    if re.search(r"sk-ant-[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z\-_]{20,}", txt):
        fail(".env.example contains a real API key pattern")
    ok(".env.example captcha config and absence of hardcoded keys verified")


def check_no_bypass_code() -> None:
    forbidden = re.compile(r"passxapi|g-recaptcha-response|h-captcha-response|cf-turnstile-response", re.I)
    for folder in ("backend/app", "browser-worker"):
        for path in (REPO_ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh", ".json", ".yaml", ".yml"}:
                content = path.read_text(encoding="utf-8", errors="replace")
                if forbidden.search(content):
                    fail(f"Forbidden automated CAPTCHA/token-injection pattern found in {path}")
    ok("Zero automated CAPTCHA solver or token-injection code verified")


def check_verification_model() -> None:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.models.google_account import VerificationRequest

    cols = set(VerificationRequest.__table__.columns.keys())
    forbidden_cols = {"code", "verification_code", "otp", "token"}
    if cols & forbidden_cols:
        fail(f"VerificationRequest must NEVER store code values: {cols & forbidden_cols}")
    ok("VerificationRequest schema verified (no code value column)")


def check_rtl_and_i18n() -> None:
    src_root = REPO_ROOT / "web" / "src"
    phys_pat = re.compile(r"\b(ml-|mr-|pl-|pr-|left-|right-|text-left|text-right|border-l|border-r|rounded-l|rounded-r)\b")
    for path in src_root.rglob("*"):
        if path.is_file() and path.suffix in {".ts", ".tsx", ".css"}:
            for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if phys_pat.search(line):
                    fail(f"Physical CSS/Tailwind property found in {path}:{idx}: {line.strip()}")

    def flatten(d: dict, prefix: str = "") -> set[str]:
        out: set[str] = set()
        for k, v in d.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out |= flatten(v, full)
            else:
                out.add(full)
        return out

    ar_keys = flatten(json.loads((src_root / "i18n" / "ar.json").read_text(encoding="utf-8")))
    en_keys = flatten(json.loads((src_root / "i18n" / "en.json").read_text(encoding="utf-8")))
    if ar_keys != en_keys:
        fail(f"i18n key mismatch between ar.json and en.json: {ar_keys ^ en_keys}")
    ok(f"RTL logical-only CSS & i18n parity verified ({len(ar_keys)} keys)")


def main() -> None:
    check_docker_compose()
    check_env_example()
    check_no_bypass_code()
    check_verification_model()
    check_rtl_and_i18n()
    print("\nALL SECURITY & POLICY VERIFICATIONS PASSED.")


if __name__ == "__main__":
    main()
