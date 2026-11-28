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
  Square,
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
  model_used?: string | null;
  latency_ms?: number | null;
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
  const [streamContent, setStreamContent] = useState("");
  const [streamModel, setStreamModel] = useState("");
  const [notice, setNotice] = useState("");
  const [mockMode, setMockMode] = useState(false);
  const [runtimeReady, setRuntimeReady] = useState<{ default_mock_mode: boolean; missing_for_real_backlink?: string[] } | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

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

  const loadRuntimeStatus = async () => {
    const rs = await apiFetch<{ default_mock_mode: boolean; missing_for_real_backlink?: string[] }>(
      "/api/system/runtime-status"
    ).catch(() => null);
    if (rs) {
      setRuntimeReady(rs);
      setMockMode(Boolean(rs.default_mock_mode));
    }
  };

  useEffect(() => {
    loadThreads();
    loadModes();
    loadRuntimeStatus();
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

  const stopStreaming = async () => {
    abortRef.current?.abort();
    if (activeId) {
      await apiFetch(`/api/chat/threads/${activeId}/cancel`, { method: "POST" }).catch(() => null);
    }
    setSending(false);
  };

  const sendMessage = async (textOverride?: string) => {
    const text = (textOverride ?? input).trim();
    if (!text || sending) return;
    setSending(true);
    setStreamContent("");
    setStreamModel("");
    setNotice("");
    const controller = new AbortController();
    abortRef.current = controller;
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

      setMessages((prev) => [
        ...prev,
        {
          id: `temp-${Date.now()}`,
          seq: (prev[prev.length - 1]?.seq || 0) + 1,
          role: "user",
          content: text,
          created_at: new Date().toISOString(),
        },
      ]);

      const tok = typeof window !== "undefined" ? localStorage.getItem("uas_token") : null;
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (tok) headers["Authorization"] = `Bearer ${tok}`;

      const resp = await fetch(`/api/chat/threads/${tid}/messages/stream`, {
        method: "POST",
        headers,
        credentials: "include",
        signal: controller.signal,
        body: JSON.stringify({ content: text, mock_mode: mockMode }),
      });

      if (!resp.ok || !resp.body) {
        await apiFetch(`/api/chat/threads/${tid}/messages`, {
          method: "POST",
          body: JSON.stringify({ content: text, mock_mode: mockMode }),
        });
      } else {
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() || "";
          for (const frame of frames) {
            let evName = "message";
            let dataLine = "";
            for (const ln of frame.split("\n")) {
              if (ln.startsWith("event:")) evName = ln.slice(6).trim();
              else if (ln.startsWith("data:")) dataLine += ln.slice(5).trim();
            }
            if (!dataLine) continue;
            try {
              const parsed = JSON.parse(dataLine);
              if (evName === "token" && parsed.token) {
                setStreamContent((prev) => prev + String(parsed.token));
                if (parsed.model) setStreamModel(String(parsed.model));
              } else if (evName === "done" || evName === "aborted") {
                if (parsed.model_used) setStreamModel(String(parsed.model_used));
              }
            } catch {
              // ignore malformed SSE chunk
            }
          }
        }
      }

      await loadThreads();
      await loadThreadDetail(tid);
    } catch {
      if (activeId) await loadThreadDetail(activeId);
    } finally {
      abortRef.current = null;
      setStreamContent("");
      setStreamModel("");
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
              onClick={() => setMockMode((v) => !v)}
              data-testid="execution-mode-toggle"
              className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 font-semibold ${
                mockMode
                  ? "border-warning bg-amber-500/15 text-warning"
                  : "border-success bg-success/15 text-success"
              }`}
            >
              <span>
                {mockMode
                  ? locale === "ar"
                    ? "وضع محاكاة (Simulation)"
                    : "Simulation Mode"
                  : locale === "ar"
                  ? "وضع حقيقي (Real)"
                  : "Real Mode"}
              </span>
            </button>

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

        {mockMode && (
          <div className="border-b border-warning/40 bg-amber-500/10 px-4 py-2 text-xs font-semibold text-warning">
            وضع محاكاة — لا يتم إنشاء حسابات حقيقية ولا روابط حية
            {runtimeReady?.missing_for_real_backlink && runtimeReady.missing_for_real_backlink.length > 0 && (
              <span className="ms-2 font-normal text-muted">
                ({runtimeReady.missing_for_real_backlink.join(" · ")})
              </span>
            )}
          </div>
        )}

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

                {m.role === "assistant" && (() => {
                  const mod = m.model_used || (m.card_payload?.model_used as string | undefined);
                  const msRaw = m.latency_ms ?? (m.card_payload?.latency_ms as number | undefined);
                  if (!mod && msRaw === undefined) return null;
                  const latFmt = msRaw !== undefined ? (msRaw >= 1000 ? `${(msRaw / 1000).toFixed(1)}s` : `${msRaw}ms`) : "";
                  return (
                    <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-muted">
                      {mod && <bdi dir="ltr">{mod}</bdi>}
                      {mod && latFmt && <span>·</span>}
                      {latFmt && <bdi dir="ltr">{latFmt}</bdi>}
                    </div>
                  );
                })()}

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
          {sending && (
            <div className="me-auto flex max-w-[90%] flex-col gap-1.5 rounded-xl border border-primary/40 bg-canvas p-3 text-xs">
              <div className="flex items-center justify-between gap-2 text-[10px] text-primary">
                <span className="font-bold uppercase">Agent · {t("phase5.streaming")}</span>
                {streamModel && <bdi dir="ltr" className="font-mono text-muted">{streamModel}</bdi>}
              </div>
              <div className="whitespace-pre-wrap leading-relaxed">{streamContent || "..."}</div>
            </div>
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
          {sending && (
            <button
              type="button"
              onClick={stopStreaming}
              className="inline-flex items-center gap-1.5 rounded-xl bg-danger px-4 py-2 text-xs font-semibold text-white"
            >
              <Square className="h-3.5 w-3.5" />
              <span>{t("phase5.stop_stream")}</span>
            </button>
          )}
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
