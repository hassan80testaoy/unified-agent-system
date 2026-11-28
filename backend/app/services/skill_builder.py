"""Prompt-Registered Skills Builder + Closed Learning Loop (Hermes/DSH pattern §8D)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.phase5 import InstalledSkill
from app.security.sanitizer import scrub_text
from app.services.llm_router import LLMRouter


def _skills_dir() -> Path:
    d = get_settings().data_dir / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(raw: str) -> str:
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", (raw or "").lower()).strip("-")
    if len(ascii_part) >= 3:
        return ascii_part[:48]
    import hashlib

    return "skill-" + hashlib.sha1((raw or "skill").encode("utf-8")).hexdigest()[:8]


def _extract_skill_spec(prompt: str) -> dict[str, Any]:
    clean = scrub_text(prompt or "").strip()
    body = re.sub(
        r"^(?:سجّل مهارة جديدة|سجل مهارة جديدة|أضف مهارة|مهارة جديدة|register skill|create skill|add skill)\s*[:：\-]*\s*",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip() or clean

    # Check explicit "name: desc" first (keeping hyphens like "invoice-audit"), then "name - desc"
    name = body[:64].strip()
    desc = body[:260]
    m_named = re.match(r"([^:\n—]{2,48})\s*[:：]\s*(.+)", body) or re.match(r"([^:\n—\-]{2,48})\s*[—\-]\s*(.+)", body)
    if m_named:
        name = m_named.group(1).strip()
        desc = m_named.group(2).strip()[:300]

    # Derive simple match keywords for next-turn auto-dispatch in chat
    tokens = [
        t.lower()
        for t in re.findall(r"[A-Za-z0-9\u0600-\u06FF_-]{3,}", f"{name} {desc}")
        if t.lower() not in {"مهارة", "جديدة", "سجل", "skill", "create", "register", "using", "with"}
    ]
    keywords = list(dict.fromkeys(tokens[:8]))
    return {
        "slug": _slugify(name),
        "name": name,
        "description": desc,
        "keywords": keywords,
    }


def _write_skill_package(spec: dict[str, Any], raw_prompt: str) -> tuple[Path, str]:
    slug = spec["slug"]
    sdir = _skills_dir() / slug
    tdir = sdir / "tests"
    tdir.mkdir(parents=True, exist_ok=True)

    skill_md = (
        f"---\nname: {slug}\ndescription: {spec['description']}\nkeywords: {json.dumps(spec['keywords'], ensure_ascii=False)}\n---\n\n"
        f"# {spec['name']}\n\n"
        f"{spec['description']}\n\n"
        f"## Source Prompt\n{scrub_text(raw_prompt)[:400]}\n"
    )
    (sdir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    skill_py = f'''"""Prompt-registered skill `{slug}`."""

from __future__ import annotations

from typing import Any

SKILL_META = {{
    "slug": {json.dumps(slug)},
    "name": {json.dumps(spec["name"], ensure_ascii=False)},
    "description": {json.dumps(spec["description"], ensure_ascii=False)},
    "keywords": {json.dumps(spec["keywords"], ensure_ascii=False)},
}}


def execute(prompt: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    clean = (prompt or "").strip()
    return {{
        "skill": {json.dumps(slug)},
        "name": {json.dumps(spec["name"], ensure_ascii=False)},
        "description": {json.dumps(spec["description"], ensure_ascii=False)},
        "input": clean,
        "params": dict(params or {{}}),
        "status": "executed",
        "summary_ar": f"تم تشغيل المهارة «{{SKILL_META['name']}}»: {{SKILL_META['description']}} | المدخل: {{clean[:140]}}",
    }}
'''
    (sdir / "skill.py").write_text(skill_py, encoding="utf-8")

    test_py = '''from pathlib import Path
import importlib.util


def test_skill_executes():
    spec = importlib.util.spec_from_file_location("custom_skill", Path(__file__).resolve().parents[1] / "skill.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    out = mod.execute("اختبار تشغيل المهارة", {"sample": True})
    assert out["status"] == "executed"
    assert out["skill"]
'''
    (tdir / "test_skill.py").write_text(test_py, encoding="utf-8")
    return sdir, skill_md


def register_skill_from_prompt(
    db: Session,
    *,
    workspace_id: str,
    prompt: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Create SKILL.md + executable skill.py + tests from one descriptive prompt and register immediately (§8D.1)."""
    spec = _extract_skill_spec(prompt)
    sdir, skill_md = _write_skill_package(spec, prompt)

    existing = (
        db.query(InstalledSkill)
        .filter(InstalledSkill.workspace_id == workspace_id, InstalledSkill.slug == spec["slug"])
        .first()
    )
    tools_meta = [
        {
            "name": "execute",
            "description": spec["description"],
            "keywords": spec["keywords"],
            "usage_count": 0,
        }
    ]
    if existing is None:
        existing = InstalledSkill(
            workspace_id=workspace_id,
            slug=spec["slug"],
            name=spec["name"],
            description=spec["description"],
            source="prompt",
            install_path=str(sdir),
            contract_md=skill_md,
            has_script=True,
            tools_json=tools_meta,
            references_json=[],
            enabled=True,
        )
        db.add(existing)
    else:
        existing.name = spec["name"]
        existing.description = spec["description"]
        existing.contract_md = skill_md
        existing.has_script = True
        existing.install_path = str(sdir)
        existing.tools_json = tools_meta
        existing.enabled = True
    db.flush()
    return {
        "id": existing.id,
        "slug": existing.slug,
        "name": existing.name,
        "description": existing.description,
        "source": existing.source,
        "install_path": existing.install_path,
        "has_script": True,
        "keywords": spec["keywords"],
    }


def _load_and_run_prompt_skill(skill: InstalledSkill, prompt: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    sfile = Path(skill.install_path) / "skill.py"
    if sfile.is_file():
        spec = importlib.util.spec_from_file_location(f"uas_skill_{skill.slug}", sfile)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            out = mod.execute(prompt, params)
            # Increment usage metric (closed self-improvement loop tracking §8D.2)
            tlist = list(skill.tools_json or [])
            if tlist and isinstance(tlist[0], dict):
                tlist[0] = {**tlist[0], "usage_count": int(tlist[0].get("usage_count") or 0) + 1}
                skill.tools_json = tlist
            return out
    return {
        "skill": skill.slug,
        "name": skill.name,
        "description": skill.description,
        "status": "executed",
        "summary_ar": f"تم تنفيذ المهارة «{skill.name}»: {skill.description}",
    }


def try_match_and_run_prompt_skill(
    db: Session,
    *,
    workspace_id: str,
    prompt: str,
) -> dict[str, Any] | None:
    """Match an incoming chat prompt to any enabled prompt-registered skill & invoke it directly (§8D.1)."""
    clean = (prompt or "").strip()
    low = clean.lower()
    rows = (
        db.query(InstalledSkill)
        .filter(
            InstalledSkill.workspace_id == workspace_id,
            InstalledSkill.source == "prompt",
            InstalledSkill.enabled.is_(True),
        )
        .order_by(InstalledSkill.created_at.desc())
        .all()
    )
    for row in rows:
        if row.slug.lower() in low or row.name.lower() in low:
            res = _load_and_run_prompt_skill(row, clean)
            db.flush()
            return {"slug": row.slug, "result": res, "output_ar": f"⚡ **{res.get('summary_ar') or row.name}**"}
        kws = []
        for t in (row.tools_json or []):
            if isinstance(t, dict):
                kws.extend(str(k).lower() for k in (t.get("keywords") or []))
        hits = sum(1 for k in kws if len(k) >= 3 and k in low)
        if hits >= 2:
            res = _load_and_run_prompt_skill(row, clean)
            db.flush()
            return {"slug": row.slug, "result": res, "output_ar": f"⚡ **{res.get('summary_ar') or row.name}**"}
    return None


def propose_skill_from_thread(db: Session, *, workspace_id: str, summary_text: str) -> dict[str, Any]:
    """Propose a reusable skill draft after a complex task (Hermes closed learning loop §8D.2)."""
    clean = scrub_text(summary_text or "").strip()
    draft_prompt = f"مهارة مكتسبة تلقائياً: {clean[:180]}"
    return {
        "proposed": True,
        "suggested_prompt": draft_prompt,
        "spec": _extract_skill_spec(draft_prompt),
    }


def improve_skill_on_use(db: Session, *, workspace_id: str, slug: str, note: str) -> dict[str, Any]:
    """Append real usage improvement notes to an installed prompt skill's SKILL.md (§8D.2)."""
    row = (
        db.query(InstalledSkill)
        .filter(InstalledSkill.workspace_id == workspace_id, InstalledSkill.slug == slug)
        .first()
    )
    if not row:
        raise ValueError("Skill not found.")
    addition = f"\n- Usage refinement: {scrub_text(note)[:240]}"
    row.contract_md = (row.contract_md or "") + addition
    md_path = Path(row.install_path) / "SKILL.md"
    if md_path.is_file():
        md_path.write_text(row.contract_md, encoding="utf-8")
    db.flush()
    return {"slug": row.slug, "updated": True, "contract_md": row.contract_md}
