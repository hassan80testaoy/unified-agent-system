# AGENTS.md — Unified Agent System

## Required Execution Workflow
- Follow **AEO v2.0** (`.agents/skills/aeo/SKILL.md`) on every task: Understand -> Explore -> Decide -> Split -> Test -> Fix.
- Keep working state in `.aeo/` (`PLAN.md`, `DECISIONS.md`, `EVIDENCE.md`) during Standard/Deep tasks; `.aeo/` is gitignored.

## Non-Negotiable Project Rules
1. **Security & Secrets:** Never commit `.env`, database files (`*.db`), encryption keys (`*.key`), or credentials. Secrets are Fernet-encrypted at rest and scrubbed from logs/events/reports.
2. **CAPTCHA Policy:** Human-in-the-Loop ONLY via `CaptchaSolver` plugin interface (`backend/app/captcha/solver.py` + `ManualSolver` over localhost `127.0.0.1:6080` noVNC). Zero automated solver integration, token injection, or fingerprint spoofing.
3. **Google Account Policy (§2.3):** Owner-controlled via encrypted Vault entry. Verification codes are single-use in-memory only — NEVER stored or logged.
4. **Arabic-first RTL:** Use logical CSS/Tailwind properties ONLY (`ms-`, `me-`, `ps-`, `pe-`, `start-`, `end-`, `text-start`). Zero physical `left`/`right` utilities. Keep `web/src/i18n/ar.json` and `en.json` keys identical.
5. **Verification Commands:**
   - Security & RTL check: `.\backend\.venv\Scripts\python.exe scripts\verify-security.py`
   - Backend tests: `cd backend && .\.venv\Scripts\pytest.exe tests -v`
   - Web production build: `cd web && node .\node_modules\next\dist\bin\next build`
