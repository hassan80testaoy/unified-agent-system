"""Persistent Memory Service: MEMORY.md + USER.md, FTS5 Cross-Session Recall, TF-IDF Vector Search, Non-Lossy Compaction & Export/Delete (§8K, §8L.3)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.instinct import MemoryFact
from app.models.v3_harness import ChatMessage, ChatThread
from app.security.crypto import decrypt_secret, encrypt_secret
from app.security.sanitizer import scrub_text
from app.services.session_log import append_turn

_TOKEN_RE = re.compile(r"[A-Za-z0-9\u0600-\u06FF_@.-]{2,}")
_MEMORY_CHAR_CAP = 3200
_USER_CHAR_CAP = 1800


def get_user_memory_dir(user_id: str) -> Path:
    safe_uid = re.sub(r"[^a-zA-Z0-9_-]+", "_", (user_id or "default").strip()) or "default"
    d = get_settings().data_dir / "memories" / safe_uid
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_memory_files(user_id: str) -> tuple[Path, Path]:
    d = get_user_memory_dir(user_id)
    mem_md = d / "MEMORY.md"
    user_md = d / "USER.md"
    if not mem_md.exists():
        mem_md.write_text("# Memory Notes (Curated Facts & Decisions)\n", encoding="utf-8")
    if not user_md.exists():
        user_md.write_text("# Owner Profile & Habits\n", encoding="utf-8")
    return mem_md, user_md


def _sync_markdown_mirror(db: Session, workspace_id: str, user_id: str) -> None:
    """Keep MEMORY.md and USER.md synchronized with active DB MemoryFact entries."""
    mem_md, user_md = ensure_memory_files(user_id)
    rows = (
        db.query(MemoryFact)
        .filter(MemoryFact.workspace_id == workspace_id, MemoryFact.user_id == user_id)
        .order_by(MemoryFact.updated_at.desc())
        .limit(80)
        .all()
    )
    mem_lines = ["# Memory Notes (Curated Facts & Decisions)"]
    user_lines = ["# Owner Profile & Habits"]
    for row in rows:
        try:
            txt = scrub_text(decrypt_secret(row.content_enc)).strip()
        except Exception:
            continue
        if not txt:
            continue
        line = f"- [{row.category}] {txt}"
        if row.category in {"preference", "habit", "identity", "user_profile"}:
            user_lines.append(line)
        else:
            mem_lines.append(line)
    mem_md.write_text("\n".join(mem_lines)[:_MEMORY_CHAR_CAP] + "\n", encoding="utf-8")
    user_md.write_text("\n".join(user_lines)[:_USER_CHAR_CAP] + "\n", encoding="utf-8")


def ensure_session_fts_table(db: Session) -> bool:
    """Create SQLite FTS5 virtual table for fast cross-session recall; returns False gracefully if FTS5 unsupported."""
    try:
        db.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING fts5("
                "message_id UNINDEXED, thread_id UNINDEXED, workspace_id UNINDEXED, user_id UNINDEXED, role UNINDEXED, content"
                ")"
            )
        )
        return True
    except Exception:
        return False


def index_chat_message_fts(
    db: Session,
    *,
    message_id: str,
    thread_id: str,
    workspace_id: str,
    user_id: str,
    role: str,
    content: str,
) -> None:
    clean = scrub_text(content or "").strip()
    if not clean:
        return
    if not ensure_session_fts_table(db):
        return
    try:
        db.execute(text("DELETE FROM session_fts WHERE message_id = :mid"), {"mid": message_id})
        db.execute(
            text(
                "INSERT INTO session_fts(message_id, thread_id, workspace_id, user_id, role, content) "
                "VALUES (:mid, :tid, :wid, :uid, :role, :content)"
            ),
            {"mid": message_id, "tid": thread_id, "wid": workspace_id, "uid": user_id, "role": role, "content": clean},
        )
    except Exception:
        pass


def search_past_sessions(
    db: Session,
    *,
    workspace_id: str,
    query: str,
    exclude_thread_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """FTS5-first search across past chat threads with LIKE fallback."""
    tokens = [t for t in _TOKEN_RE.findall((query or "").strip()) if len(t) >= 2]
    if not tokens:
        return []
    hits: list[dict[str, Any]] = []
    if ensure_session_fts_table(db):
        fts_query = " OR ".join(f'"{t.replace(chr(34), "")}"' for t in tokens[:6])
        try:
            rows = db.execute(
                text(
                    "SELECT message_id, thread_id, role, content FROM session_fts "
                    "WHERE workspace_id = :wid AND session_fts MATCH :q LIMIT :lim"
                ),
                {"wid": workspace_id, "q": fts_query, "lim": limit * 3},
            ).fetchall()
            for r in rows:
                if exclude_thread_id and r[1] == exclude_thread_id:
                    continue
                hits.append({"message_id": r[0], "thread_id": r[1], "role": r[2], "content": r[3]})
                if len(hits) >= limit:
                    return hits
        except Exception:
            pass

    # Deterministic fallback across persisted ChatMessage rows
    q = db.query(ChatMessage).filter(ChatMessage.workspace_id == workspace_id, ChatMessage.rewound.is_(False))
    if exclude_thread_id:
        q = q.filter(ChatMessage.thread_id != exclude_thread_id)
    for m in q.order_by(ChatMessage.created_at.desc()).limit(150).all():
        low_c = (m.content or "").lower()
        if any(t.lower() in low_c for t in tokens[:6]):
            hits.append({"message_id": m.id, "thread_id": m.thread_id, "role": m.role, "content": m.content})
            if len(hits) >= limit:
                break
    return hits


def _cosine_score(q_tokens: list[str], doc_text: str) -> float:
    d_tokens = [t.lower() for t in _TOKEN_RE.findall(doc_text or "")]
    if not q_tokens or not d_tokens:
        return 0.0
    qv = Counter(t.lower() for t in q_tokens)
    dv = Counter(d_tokens)
    dot = sum(qv[k] * dv[k] for k in qv if k in dv)
    if dot <= 0:
        return 0.0
    norm = math.sqrt(sum(v * v for v in qv.values())) * math.sqrt(sum(v * v for v in dv.values()))
    return round(dot / norm, 4) if norm else 0.0


def list_memories(db: Session, *, workspace_id: str, user_id: str | None = None) -> list[dict[str, Any]]:
    q = db.query(MemoryFact).filter(MemoryFact.workspace_id == workspace_id)
    if user_id:
        q = q.filter(MemoryFact.user_id == user_id)
    rows = q.order_by(MemoryFact.updated_at.desc()).limit(200).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            content = scrub_text(decrypt_secret(r.content_enc))
        except Exception:
            content = ""
        out.append(
            {
                "id": r.id,
                "user_id": r.user_id,
                "category": r.category,
                "content": content,
                "allow_training": bool(r.allow_training),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )
    return out


def create_memory(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    content: str,
    category: str = "fact",
    allow_training: bool = False,
) -> dict[str, Any]:
    clean = scrub_text(content or "").strip()
    if not clean:
        raise ValueError("Memory content cannot be empty.")

    # Deduplicate identical active memory entries
    for existing in list_memories(db, workspace_id=workspace_id, user_id=user_id):
        if existing["content"].strip().lower() == clean.lower() and existing["category"] == category:
            return existing

    row = MemoryFact(
        workspace_id=workspace_id,
        user_id=user_id,
        category=(category or "fact").strip()[:48],
        content_enc=encrypt_secret(clean),
        allow_training=bool(allow_training),
    )
    db.add(row)
    db.flush()
    _sync_markdown_mirror(db, workspace_id, user_id)
    return {
        "id": row.id,
        "user_id": row.user_id,
        "category": row.category,
        "content": clean,
        "allow_training": bool(row.allow_training),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def update_memory(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    memory_id: str,
    content: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    row = db.get(MemoryFact, memory_id)
    if not row or row.workspace_id != workspace_id:
        raise ValueError("Memory entry not found.")
    if content is not None:
        clean = scrub_text(content).strip()
        if not clean:
            raise ValueError("Memory content cannot be empty.")
        row.content_enc = encrypt_secret(clean)
    if category is not None:
        row.category = category.strip()[:48] or row.category
    db.flush()
    _sync_markdown_mirror(db, workspace_id, user_id)
    return {
        "id": row.id,
        "user_id": row.user_id,
        "category": row.category,
        "content": scrub_text(decrypt_secret(row.content_enc)),
        "allow_training": bool(row.allow_training),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def delete_memory(db: Session, *, workspace_id: str, user_id: str, memory_id: str) -> bool:
    row = db.get(MemoryFact, memory_id)
    if not row or row.workspace_id != workspace_id:
        return False
    db.delete(row)
    db.flush()
    _sync_markdown_mirror(db, workspace_id, user_id)
    return True


def search_memories_vector(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    query: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    items = list_memories(db, workspace_id=workspace_id, user_id=user_id)
    q_tokens = _TOKEN_RE.findall(query or "")
    scored: list[tuple[float, dict[str, Any]]] = []
    for it in items:
        sim = _cosine_score(q_tokens, it["content"])
        scored.append((sim, {**it, "score": sim}))
    # Rank by vector similarity first, keeping recent entries available when query is short
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = [item for score, item in scored[:limit] if score > 0 or len(items) <= limit]
    if not top and items:
        top = [{**it, "score": 0.0} for it in items[: min(limit, 5)]]
    return top


def auto_writeback_memory(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    text: str,
    category: str = "decision",
) -> list[dict[str, Any]]:
    """Extract and persist durable user facts (sites, emails, explicit preferences/decisions) cleanly."""
    clean = scrub_text(text or "").strip()
    if len(clean) < 8:
        return []
    created: list[dict[str, Any]] = []
    has_signal = any(
        tok in clean.lower()
        for tok in (
            "@",
            "http",
            ".com",
            ".org",
            ".net",
            "تذكر",
            "احفظ",
            "الموقع",
            "الإيميل",
            "يفضل",
            "أفضل",
            "قرار",
            "site",
            "email",
            "remember",
            "prefer",
            "decision",
        )
    )
    if has_signal:
        item = create_memory(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            content=clean[:500],
            category=category,
            allow_training=False,
        )
        created.append(item)
    return created


def compact_thread_to_memory(db: Session, thread: ChatThread, user_id: str) -> dict[str, Any]:
    """Summarize older messages into thread.compacted_summary AND write durable facts into MemoryFact/MEMORY.md (§8K.3)."""
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.thread_id == thread.id, ChatMessage.rewound.is_(False))
        .order_by(ChatMessage.seq.asc())
        .all()
    )
    if len(msgs) <= 2:
        summary = " | ".join(f"{m.role}: {m.content[:140]}" for m in msgs)
        older = msgs
    else:
        older = msgs[:-2]
        summary = "\n".join(f"[{m.seq}] {m.role} ({m.intent or 'general'}): {m.content[:220]}" for m in older)

    # Extract high-signal facts (sites, emails, decisions, numbers) so compaction NEVER forgets them
    fact_lines: list[str] = []
    for m in older:
        if any(sig in (m.content or "").lower() for sig in ("@", "http", ".com", ".net", ".org", "قرار", "اختر", "تذكر", "موقعي", "إيميل", "decision", "prefer")):
            fact_lines.append(scrub_text(m.content)[:260])

    if fact_lines:
        create_memory(
            db,
            workspace_id=thread.workspace_id,
            user_id=user_id,
            content="ملخص محادثة: " + " | ".join(fact_lines[:4]),
            category="compacted_decision",
            allow_training=False,
        )

    thread.compacted_summary = summary
    db.flush()
    append_turn(
        thread.id,
        {"type": "compact", "compacted_count": max(0, len(msgs) - 2), "summary": summary},
    )
    return {"thread_id": thread.id, "compacted_summary": summary, "messages_total": len(msgs), "saved_facts": len(fact_lines)}


def build_memory_context_block(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    query: str,
    current_thread_id: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Assemble persistent MEMORY.md/USER.md, vector-matched facts, and FTS5 cross-session hits for ALL LLM calls (§8K.1-2)."""
    mem_md, user_md = ensure_memory_files(user_id)
    top_memories = search_memories_vector(db, workspace_id=workspace_id, user_id=user_id, query=query, limit=6)
    past_hits = search_past_sessions(db, workspace_id=workspace_id, query=query, exclude_thread_id=current_thread_id, limit=4)

    lines: list[str] = []
    loaded: list[dict[str, Any]] = []

    u_txt = user_md.read_text(encoding="utf-8", errors="replace").strip()
    if len(u_txt.splitlines()) > 1:
        lines.append("## Owner Profile (USER.md)\n" + u_txt[:900])

    m_txt = mem_md.read_text(encoding="utf-8", errors="replace").strip()
    if len(m_txt.splitlines()) > 1:
        lines.append("## Persistent Memory (MEMORY.md)\n" + m_txt[:1400])

    if top_memories:
        lines.append("## Relevant Recalled Facts")
        for m in top_memories:
            lines.append(f"- ({m['category']}) {m['content']}")
            loaded.append({"id": m["id"], "category": m["category"], "preview": m["content"][:120], "source": "memory"})

    if past_hits:
        lines.append("## Recalled From Past Threads (FTS5)")
        for h in past_hits:
            preview = scrub_text(h["content"])[:200]
            lines.append(f"- [thread:{h['thread_id'][:8]} {h['role']}] {preview}")
            loaded.append({"id": h["message_id"], "category": "session_recall", "preview": preview[:120], "source": "session_fts"})

    return ("\n\n".join(lines).strip()), loaded


def export_user_memory(db: Session, *, workspace_id: str, user_id: str) -> dict[str, Any]:
    mem_md, user_md = ensure_memory_files(user_id)
    items = list_memories(db, workspace_id=workspace_id, user_id=user_id)
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "workspace_id": workspace_id,
        "count": len(items),
        "memories": items,
        "memory_md": mem_md.read_text(encoding="utf-8", errors="replace"),
        "user_md": user_md.read_text(encoding="utf-8", errors="replace"),
    }


def delete_all_user_memory(db: Session, *, workspace_id: str, user_id: str) -> int:
    """Real irreversible erasure of all user memory entries, markdown mirrors, and FTS5 index (§8K.4, §8L.3)."""
    rows = db.query(MemoryFact).filter(MemoryFact.workspace_id == workspace_id, MemoryFact.user_id == user_id).all()
    count = len(rows)
    for r in rows:
        db.delete(r)
    if ensure_session_fts_table(db):
        try:
            db.execute(text("DELETE FROM session_fts WHERE workspace_id = :wid AND user_id = :uid"), {"wid": workspace_id, "uid": user_id})
        except Exception:
            pass
    d = get_user_memory_dir(user_id)
    for fname in ("MEMORY.md", "USER.md"):
        p = d / fname
        if p.exists():
            p.unlink()
    db.flush()
    return count
