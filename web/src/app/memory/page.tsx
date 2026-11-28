"use client";

import React, { useEffect, useState } from "react";
import { Brain, Download, Edit3, Plus, Search, Trash2 } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface MemoryEntry {
  id: string;
  category: string;
  content: string;
  allow_training?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export default function MemoryPage() {
  const { t, locale, formatDate } = useApp();
  const isAr = locale === "ar";

  const [items, setItems] = useState<MemoryEntry[]>([]);
  const [memoryMd, setMemoryMd] = useState("");
  const [userMd, setUserMd] = useState("");
  const [category, setCategory] = useState("fact");
  const [content, setContent] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [memHits, setMemHits] = useState<Array<{ id: string; category: string; content: string; score: number }>>([]);
  const [sessHits, setSessHits] = useState<Array<{ thread_id: string; role: string; snippet: string }>>([]);
  const [confirmWipe, setConfirmWipe] = useState(false);
  const [notice, setNotice] = useState("");

  const load = async () => {
    const res = await apiFetch<{
      memories?: MemoryEntry[];
      memory?: MemoryEntry[];
      memory_md?: string;
      user_md?: string;
    }>("/api/memory");
    setItems(res.memories || res.memory || []);
    setMemoryMd(res.memory_md || "");
    setUserMd(res.user_md || "");
  };

  useEffect(() => {
    load().catch(() => null);
  }, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!content.trim()) return;
    await apiFetch("/api/memory", {
      method: "POST",
      body: JSON.stringify({ category, content: content.trim(), allow_training: false }),
    });
    setContent("");
    setNotice(isAr ? "تم حفظ الذاكرة وتحديث MEMORY.md / USER.md" : "Memory saved & Markdown mirror updated.");
    await load();
  };

  const handleSaveEdit = async (id: string) => {
    if (!editContent.trim()) return;
    await apiFetch(`/api/memory/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ content: editContent.trim() }),
    });
    setEditingId(null);
    setEditContent("");
    await load();
  };

  const handleDelete = async (id: string) => {
    await apiFetch(`/api/memory/${id}`, { method: "DELETE" });
    await load();
  };

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    const res = await apiFetch<{
      memory_hits: Array<{ id: string; category: string; content: string; score: number }>;
      session_hits: Array<{ thread_id: string; role: string; snippet: string }>;
    }>("/api/memory/search", {
      method: "POST",
      body: JSON.stringify({ query: searchQuery.trim() }),
    });
    setMemHits(res.memory_hits || []);
    setSessHits(res.session_hits || []);
  };

  const handleExport = async () => {
    const data = await apiFetch<Record<string, unknown>>("/api/memory/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "unified-agent-memory-export.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const handlePurgeAll = async () => {
    const res = await apiFetch<{ removed_count: number }>("/api/memory/purge/all", { method: "DELETE" });
    setConfirmWipe(false);
    setMemHits([]);
    setNotice(isAr ? `تم حذف كل الذاكرة (${res.removed_count})` : `Purged all memory (${res.removed_count} entries).`);
    await load();
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold">
            <Brain className="h-5 w-5 text-primary" />
            <span>{t("phase8.memory_title")}</span>
          </h1>
          <p className="text-xs text-muted">{t("phase8.memory_sub")}</p>
        </div>

        <div className="flex flex-wrap gap-2 text-xs">
          <button
            type="button"
            onClick={handleExport}
            className="inline-flex items-center gap-1.5 rounded-xl border border-borderline bg-surface px-3 py-2 font-semibold hover:border-primary"
          >
            <Download className="h-4 w-4" />
            <span>{isAr ? "تصدير كامل JSON" : "Export All"}</span>
          </button>

          {!confirmWipe ? (
            <button
              type="button"
              onClick={() => setConfirmWipe(true)}
              className="inline-flex items-center gap-1.5 rounded-xl border border-danger/40 px-3 py-2 font-semibold text-danger hover:bg-danger/10"
            >
              <Trash2 className="h-4 w-4" />
              <span>{isAr ? "حذف كل الذاكرة" : "Purge All Memory"}</span>
            </button>
          ) : (
            <div className="flex items-center gap-1.5 rounded-xl border border-danger bg-danger/10 px-2.5 py-1.5">
              <span className="font-semibold text-danger">{isAr ? "تأكيد المسح النهائي؟" : "Confirm full wipe?"}</span>
              <button
                type="button"
                onClick={handlePurgeAll}
                className="rounded-lg bg-danger px-2.5 py-1 font-bold text-white"
              >
                {t("common.delete")}
              </button>
              <button
                type="button"
                onClick={() => setConfirmWipe(false)}
                className="rounded-lg border border-borderline bg-surface px-2.5 py-1"
              >
                {t("common.cancel")}
              </button>
            </div>
          )}
        </div>
      </div>

      {notice && (
        <div className="rounded-xl border border-primary/30 bg-primary/5 px-4 py-2 text-xs text-primary">{notice}</div>
      )}

      {/* Search & Add Grid */}
      <div className="grid gap-4 lg:grid-cols-2">
        <form onSubmit={handleAdd} className="bento-card space-y-3 p-4">
          <div className="flex items-center gap-2 text-sm font-bold">
            <Plus className="h-4 w-4 text-primary" />
            <span>{isAr ? "إضافة حقيقة / تفضيل للذاكرة" : "Add Fact or Preference"}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs"
            >
              <option value="fact">{isAr ? "حقيقة / قرار" : "Fact / Decision"}</option>
              <option value="preference">{isAr ? "تفضيل شخصي (USER.md)" : "Preference (USER.md)"}</option>
              <option value="habit">{isAr ? "عادة عمل (USER.md)" : "Habit (USER.md)"}</option>
              <option value="identity">{isAr ? "هوية / براند" : "Identity"}</option>
            </select>
            <input
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={isAr ? "مثال: أفضّل الردود العربية المختصرة مع الكود أولاً..." : "Example: Prefer concise RTL summaries..."}
              className="min-w-[200px] flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs outline-none focus:border-primary"
            />
            <button type="submit" className="rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white">
              {t("common.save")}
            </button>
          </div>
        </form>

        <form onSubmit={handleSearch} className="bento-card space-y-3 p-4">
          <div className="flex items-center gap-2 text-sm font-bold">
            <Search className="h-4 w-4 text-primary" />
            <span>{isAr ? "بحث FTS5 + الدلالي في الجلسات والذاكرة" : "FTS5 & Vector Cross-Session Search"}</span>
          </div>
          <div className="flex gap-2">
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={isAr ? "ابحث عن أي موضوع أو قرار سابق..." : "Search any past topic or fact..."}
              className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs outline-none focus:border-primary"
            />
            <button type="submit" className="rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white">
              {isAr ? "بحث" : "Search"}
            </button>
          </div>
          {(memHits.length > 0 || sessHits.length > 0) && (
            <div className="space-y-1.5 border-t border-borderline pt-2 text-xs">
              {memHits.map((h) => (
                <div key={h.id} className="rounded-lg bg-canvas px-3 py-1.5">
                  <span className="font-mono text-[10px] text-primary">[{h.category} · {h.score}] </span>
                  <span>{h.content}</span>
                </div>
              ))}
              {sessHits.map((s, idx) => (
                <div key={`${s.thread_id}-${idx}`} className="rounded-lg border border-primary/20 bg-primary/5 px-3 py-1.5">
                  <span className="font-mono text-[10px] text-muted">session:{s.thread_id.slice(0, 8)} ({s.role}) · </span>
                  <span>{s.snippet}</span>
                </div>
              ))}
            </div>
          )}
        </form>
      </div>

      {/* Saved Memories List */}
      <div className="bento-card space-y-3 p-4">
        <div className="text-sm font-bold">
          {isAr ? `السجلات المحفوظة (${items.length})` : `Stored Entries (${items.length})`}
        </div>
        {items.length === 0 ? (
          <div className="rounded-lg border border-dashed border-borderline p-4 text-center text-xs text-muted">
            {t("common.empty")}
          </div>
        ) : (
          <div className="space-y-2">
            {items.map((m) => (
              <div key={m.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-borderline bg-canvas p-3 text-xs">
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2 text-[10px] text-muted">
                    <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-primary">{m.category}</span>
                    <span>{formatDate(m.updated_at || m.created_at)}</span>
                  </div>
                  {editingId === m.id ? (
                    <div className="flex gap-2 pt-1">
                      <input
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        className="flex-1 rounded-lg border border-primary bg-surface px-2.5 py-1.5 text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => handleSaveEdit(m.id)}
                        className="rounded-lg bg-primary px-3 py-1 font-semibold text-white"
                      >
                        {t("common.save")}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingId(null)}
                        className="rounded-lg border border-borderline px-2.5 py-1"
                      >
                        {t("common.cancel")}
                      </button>
                    </div>
                  ) : (
                    <div>{m.content}</div>
                  )}
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(m.id);
                      setEditContent(m.content);
                    }}
                    className="rounded-lg border border-borderline p-1.5 text-muted hover:text-primary"
                    title={isAr ? "تعديل" : "Edit"}
                  >
                    <Edit3 className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(m.id)}
                    className="rounded-lg border border-borderline p-1.5 text-danger hover:bg-danger/10"
                    title={t("common.delete")}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Markdown Mirrors: MEMORY.md & USER.md */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="bento-card space-y-2 p-4">
          <div className="font-mono text-xs font-bold text-primary">MEMORY.md</div>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-xl border border-borderline bg-canvas p-3 text-xs leading-relaxed">
            {memoryMd || "# Empty"}
          </pre>
        </div>
        <div className="bento-card space-y-2 p-4">
          <div className="font-mono text-xs font-bold text-primary">USER.md</div>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-xl border border-borderline bg-canvas p-3 text-xs leading-relaxed">
            {userMd || "# Empty"}
          </pre>
        </div>
      </div>
    </div>
  );
}
