"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Bot,
  LayoutDashboard,
  Minimize2,
  Plus,
  RotateCcw,
  Send,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface ChatThread {
  id: string;
  title: string;
  mode_key: string;
  project_key: string;
  approval_mode: string;
  compacted_summary?: string | null;
  status: string;
}

interface ChatMsg {
  id: string;
  seq: number;
  role: "user" | "assistant" | "system";
  content: string;
  intent?: string | null;
  card_type?: string | null;
  card_payload?: Record<string, unknown> | null;
  created_at: string;
}

interface ModeItem {
  key: string;
  name_ar: string;
  name_en: string;
  is_default: boolean;
}

export default function ChatHomePage() {
  const { t, locale } = useApp();
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [activeThread, setActiveThread] = useState<ChatThread | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [modes, setModes] = useState<ModeItem[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  const loadThreads = async () => {
    const res = await apiFetch<{ threads: ChatThread[] }>("/api/chat/threads").catch(() => ({ threads: [] }));
    setThreads(res.threads);
    if (!activeId && res.threads.length > 0) {
      setActiveId(res.threads[0].id);
    }
  };

  const loadModes = async () => {
    const res = await apiFetch<{ modes: ModeItem[] }>("/api/modes").catch(() => ({ modes: [] }));
    setModes(res.modes);
  };

  const loadThreadDetail = async (tid: string) => {
    if (!tid) return;
    const res = await apiFetch<{ thread: ChatThread; messages: ChatMsg[] }>(`/api/chat/threads/${tid}`).catch(() => null);
    if (res) {
      setActiveThread(res.thread);
      setMessages(res.messages);
    }
  };

  useEffect(() => {
    loadThreads();
    loadModes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (activeId) {
      loadThreadDetail(activeId);
    } else {
      setActiveThread(null);
      setMessages([]);
    }
  }, [activeId]);

  useEffect(() => {
    if (!activeId || typeof window === "undefined") return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/api/ws/threads/${activeId}`);
    ws.onmessage = () => {
      loadThreadDetail(activeId);
    };
    return () => {
      try {
        ws.close();
      } catch {
        // ignore close errors
      }
    };
  }, [activeId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const startNewThread = async () => {
    const defaultMode = modes.find((m) => m.is_default)?.key || "delegate";
    const res = await apiFetch<{ thread: ChatThread }>("/api/chat/threads", {
      method: "POST",
      body: JSON.stringify({ title: locale === "ar" ? "محادثة جديدة" : "New Chat", mode_key: defaultMode }),
    });
    setActiveId(res.thread.id);
    await loadThreads();
    await loadThreadDetail(res.thread.id);
  };

  const sendMessage = async (textOverride?: string) => {
    const text = (textOverride ?? input).trim();
    if (!text || sending) return;
    setSending(true);
    setNotice("");
    try {
      let tid = activeId;
      if (!tid) {
        const defaultMode = modes.find((m) => m.is_default)?.key || "delegate";
        const created = await apiFetch<{ thread: ChatThread }>("/api/chat/threads", {
          method: "POST",
          body: JSON.stringify({ title: text.slice(0, 60), mode_key: defaultMode }),
        });
        tid = created.thread.id;
        setActiveId(tid);
      }
      if (!textOverride) setInput("");
      await apiFetch(`/api/chat/threads/${tid}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: text, mock_mode: true }),
      });
      await loadThreads();
      await loadThreadDetail(tid);
    } finally {
      setSending(false);
    }
  };

  const updateThreadSetting = async (patch: { mode_key?: string; approval_mode?: string }) => {
    if (!activeId) return;
    const res = await apiFetch<{ thread: ChatThread }>(`/api/chat/threads/${activeId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    setActiveThread(res.thread);
    setThreads((prev) => prev.map((th) => (th.id === res.thread.id ? res.thread : th)));
  };

  const handleCompact = async () => {
    if (!activeId) return;
    await apiFetch(`/api/chat/threads/${activeId}/compact`, { method: "POST" });
    setNotice(locale === "ar" ? "تم ضغط السياق في سجل الجلسة." : "Context compacted in session log.");
    await loadThreadDetail(activeId);
  };

  const handleUndo = async () => {
    if (!activeId) return;
    const res = await apiFetch<{ rewound_count: number }>(`/api/chat/threads/${activeId}/undo`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    setNotice(
      locale === "ar"
        ? `تم التراجع عن ${res.rewound_count} رسالة في الجلسة والسجل.`
        : `Rewound ${res.rewound_count} message(s) in thread & log.`
    );
    await loadThreadDetail(activeId);
  };

  const starters =
    locale === "ar"
      ? [
          "سجل لي حساب في موقع medium.com بالإيميل name@gmail.com واعمل باك لينك",
          "شوف إيميلي",
          "أرخص طيران للقاهرة بكرة",
          "راقب الروابط وأخبرني بالروابط المفقودة",
        ]
      : [
          "Register profile on medium.com with name@gmail.com and create a backlink",
          "Check my inbox",
          "Cheapest flights to Cairo tomorrow",
          "Check links and report missing or downgraded links",
        ];

  return (
    <div className="grid min-h-[calc(100vh-8rem)] gap-4 lg:grid-cols-[260px_1fr]">
      {/* Threads Roster */}
      <aside className="flex flex-col rounded-xl border border-borderline bg-surface p-3">
        <div className="mb-3 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={startNewThread}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white"
          >
            <Plus className="h-4 w-4" />
            <span>{t("chat.new_thread")}</span>
          </button>
          <Link
            href="/dashboard"
            className="rounded-lg border border-borderline bg-canvas p-2 text-muted hover:text-primary"
            title={t("nav.dashboard")}
          >
            <LayoutDashboard className="h-4 w-4" />
          </Link>
        </div>

        <div className="flex-1 space-y-1.5 overflow-y-auto">
          {threads.length === 0 ? (
            <div className="rounded-lg border border-dashed border-borderline p-3 text-center text-xs text-muted">
              {t("common.empty")}
            </div>
          ) : (
            threads.map((th) => (
              <button
                key={th.id}
                type="button"
                onClick={() => setActiveId(th.id)}
                className={`w-full rounded-lg border px-3 py-2 text-start text-xs transition ${
                  th.id === activeId
                    ? "border-primary bg-primary/10 font-semibold text-primary"
                    : "border-borderline bg-canvas hover:border-primary/40"
                }`}
              >
                <div className="truncate">{th.title}</div>
                <div className="mt-0.5 text-[10px] text-muted">{th.mode_key}</div>
              </button>
            ))
          )}
        </div>
      </aside>

      {/* Main Chat Pane */}
      <section className="flex flex-col rounded-xl border border-borderline bg-surface">
        {/* Top Controls */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-borderline p-3">
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-primary" />
            <div>
              <h1 className="text-sm font-bold">{activeThread?.title || t("chat.title")}</h1>
              <p className="text-[11px] text-muted">{t("common.honesty_badge")}</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="flex items-center gap-1.5 rounded-lg border border-borderline bg-canvas px-2 py-1">
              <span className="text-muted">{t("chat.mode_label")}:</span>
              <select
                value={activeThread?.mode_key || "delegate"}
                onChange={(e) => updateThreadSetting({ mode_key: e.target.value })}
                className="bg-transparent font-semibold outline-none"
              >
                {modes.length > 0 ? (
                  modes.map((m) => (
                    <option key={m.key} value={m.key}>
                      {locale === "ar" ? m.name_ar : m.name_en}
                    </option>
                  ))
                ) : (
                  <option value="delegate">delegate</option>
                )}
              </select>
            </label>

            <label className="flex items-center gap-1.5 rounded-lg border border-borderline bg-canvas px-2 py-1">
              <span className="text-muted">{t("chat.approval_label")}:</span>
              <select
                value={activeThread?.approval_mode || "ask_first"}
                onChange={(e) => updateThreadSetting({ approval_mode: e.target.value })}
                className="bg-transparent font-semibold outline-none"
              >
                <option value="ask_first">{locale === "ar" ? "اسأل أولاً" : "Ask first"}</option>
                <option value="read_only">{locale === "ar" ? "قراءة فقط" : "Read only"}</option>
                <option value="full_access">{locale === "ar" ? "صلاحية كاملة" : "Full access"}</option>
              </select>
            </label>

            <button
              type="button"
              onClick={handleCompact}
              disabled={!activeId}
              className="inline-flex items-center gap-1 rounded-lg border border-borderline bg-canvas px-2.5 py-1 hover:border-primary disabled:opacity-40"
            >
              <Minimize2 className="h-3.5 w-3.5" />
              <span>{t("chat.compact")}</span>
            </button>

            <button
              type="button"
              onClick={handleUndo}
              disabled={!activeId}
              className="inline-flex items-center gap-1 rounded-lg border border-borderline bg-canvas px-2.5 py-1 hover:border-warning disabled:opacity-40"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              <span>{t("chat.undo")}</span>
            </button>
          </div>
        </div>

        {notice && (
          <div className="border-b border-borderline bg-primary/5 px-4 py-2 text-xs text-primary">{notice}</div>
        )}

        {/* Messages Stream */}
        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {messages.length === 0 ? (
            <div className="mx-auto max-w-2xl space-y-4 py-8 text-center">
              <Sparkles className="mx-auto h-8 w-8 text-primary" />
              <div className="text-base font-bold">{t("chat.title")}</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {starters.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => sendMessage(s)}
                    className="rounded-xl border border-borderline bg-canvas p-3 text-start text-xs hover:border-primary"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div
                key={m.id}
                className={`flex flex-col gap-1.5 rounded-xl border p-3 text-xs ${
                  m.role === "user"
                    ? "ms-auto max-w-[85%] border-primary/30 bg-primary/10"
                    : "me-auto max-w-[90%] border-borderline bg-canvas"
                }`}
              >
                <div className="flex items-center justify-between gap-2 text-[10px] text-muted">
                  <span className="font-bold uppercase">{m.role === "user" ? (locale === "ar" ? "أنت" : "You") : "Agent"}</span>
                  {m.intent && (
                    <span className="rounded bg-surface px-1.5 py-0.5 font-mono">
                      {t("chat.intent_label")}: {m.intent}
                    </span>
                  )}
                </div>
                <div className="whitespace-pre-wrap leading-relaxed">{m.content}</div>

                {m.card_type && (
                  <div className="mt-1 rounded-lg border border-warning/40 bg-amber-500/5 p-2.5 text-[11px]">
                    <div className="mb-1 flex items-center gap-1.5 font-bold text-warning">
                      <ShieldAlert className="h-3.5 w-3.5" />
                      <span>{m.card_type}</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Link href="/approvals" className="rounded bg-warning px-2.5 py-1 font-semibold text-white">
                        {locale === "ar" ? "فتح بطاقة الإجراء / الموافقة" : "Open Action / Approval"}
                      </Link>
                      {m.card_type === "captcha_manual" && (
                        <Link href="/runs" className="rounded border border-borderline bg-surface px-2.5 py-1">
                          noVNC / Runs
                        </Link>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
          <div ref={endRef} />
        </div>

        {/* Composer */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            sendMessage();
          }}
          className="flex gap-2 border-t border-borderline p-3"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t("chat.placeholder")}
            className="flex-1 rounded-xl border border-borderline bg-canvas px-3 py-2.5 text-xs outline-none focus:border-primary"
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            className="inline-flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
            <span>{t("chat.send")}</span>
          </button>
        </form>
      </section>
    </div>
  );
}
