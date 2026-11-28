"use client";

import React, { useEffect, useState } from "react";
import { Cpu, FileDown, Hash, Pin, Plus, Send, Share2, Users } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface BotItem {
  id: string;
  name: string;
  handle: string;
  avatar_emoji: string;
  role_title: string;
  personality: string;
  provider_id?: string | null;
  model_name: string;
  conversation_id: string;
  pinned: boolean;
  unread_count: number;
  hidden: boolean;
  is_lead: boolean;
  memory: { role: string; content: string; model_used?: string; latency_ms?: number }[];
}

interface ChannelItem {
  id: string;
  name: string;
  slug: string;
  context_type: string;
  shared_instructions: string;
  working_dir: string;
  lead_bot_id?: string | null;
  bot_ids: string[];
  transcript: { role: string; content: string; bot_name?: string; routing_reason?: string; model_used?: string }[];
}

interface RailProvider {
  provider_id: string;
  name: string;
  available: boolean;
  unavailable_reason?: string | null;
  models: { id: string; tier: string; is_default: boolean }[];
}

export default function TeamsBotsPage() {
  const { t, locale } = useApp();
  const isAr = locale === "ar";

  const [bots, setBots] = useState<BotItem[]>([]);
  const [channels, setChannels] = useState<ChannelItem[]>([]);
  const [rail, setRail] = useState<RailProvider[]>([]);
  const [viewMode, setViewMode] = useState<"bot" | "channel">("bot");
  const [activeBotId, setActiveBotId] = useState("");
  const [activeChannelId, setActiveChannelId] = useState("");
  const [msgInput, setMsgInput] = useState("");
  const [notice, setNotice] = useState("");

  // Context menu for bot actions
  const [ctxMenu, setCtxMenu] = useState<{ bot: BotItem; x: number; y: number } | null>(null);
  const [showModelRail, setShowModelRail] = useState(false);

  // Team Markdown Review & Share
  const [showInstallModal, setShowInstallModal] = useState(false);
  const [teamMd, setTeamMd] = useState("---\nname: Marketing Ops\nchief_of_staff:\n  name: Lead Coordinator\n  handle: lead\nbots:\n  - name: Copywriter\n    handle: writer\n    instructions: Draft crisp RTL copy.\nchannels:\n  - name: Campaigns\n    context_type: project\nconnectors:\n  - slack\nroutines:\n  - name: Morning Brief\n    cron: 0 9 * * *\n    prompt: Summarize daily campaign metrics\n---\n");
  const [teamPreview, setTeamPreview] = useState<Record<string, unknown> | null>(null);
  const [shareOut, setShareOut] = useState<string>("");

  const load = () => {
    apiFetch<{ bots: BotItem[] }>("/api/bots")
      .then((r) => {
        setBots(r.bots || []);
        if (!activeBotId && r.bots?.length) setActiveBotId(r.bots[0].id);
      })
      .catch(() => null);
    apiFetch<{ channels: ChannelItem[] }>("/api/channels")
      .then((r) => {
        setChannels(r.channels || []);
        if (!activeChannelId && r.channels?.length) setActiveChannelId(r.channels[0].id);
      })
      .catch(() => null);
    apiFetch<{ rail: RailProvider[] }>("/api/bots/model-picker")
      .then((r) => setRail(r.rail || []))
      .catch(() => null);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeBot = bots.find((b) => b.id === activeBotId) || null;
  const activeChannel = channels.find((c) => c.id === activeChannelId) || null;

  const sendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!msgInput.trim()) return;
    if (viewMode === "bot" && activeBot) {
      await apiFetch(`/api/bots/${activeBot.id}/chat`, { method: "POST", body: JSON.stringify({ content: msgInput }) });
    } else if (viewMode === "channel" && activeChannel) {
      await apiFetch(`/api/channels/${activeChannel.id}/messages`, { method: "POST", body: JSON.stringify({ content: msgInput }) });
    }
    setMsgInput("");
    load();
  };

  const runBotAction = async (bot: BotItem, act: string) => {
    setCtxMenu(null);
    if (act === "duplicate") {
      await apiFetch(`/api/bots/${bot.id}/duplicate`, { method: "POST" });
    } else if (act === "delete") {
      await apiFetch(`/api/bots/${bot.id}`, { method: "DELETE" });
    } else if (act === "copy_id") {
      await navigator.clipboard?.writeText(bot.conversation_id).catch(() => null);
      setNotice(`Conversation ID copied: ${bot.conversation_id}`);
      return;
    } else if (act === "edit") {
      const nextName = window.prompt(isAr ? "الاسم الجديد:" : "New name:", bot.name);
      if (nextName && nextName.trim()) {
        await apiFetch(`/api/bots/${bot.id}`, { method: "PATCH", body: JSON.stringify({ name: nextName.trim() }) });
      }
    } else {
      await apiFetch(`/api/bots/${bot.id}/action`, { method: "POST", body: JSON.stringify({ action: act }) });
    }
    load();
  };

  const switchBotBrain = async (providerId: string, modelId: string) => {
    if (!activeBot) return;
    await apiFetch(`/api/bots/${activeBot.id}/model`, {
      method: "PATCH",
      body: JSON.stringify({ provider_id: providerId, model_name: modelId }),
    });
    setShowModelRail(false);
    setNotice(`${activeBot.name} -> ${providerId} (${modelId})`);
    load();
  };

  const previewTeam = async () => {
    const res = await apiFetch<{ preview: Record<string, unknown> }>("/api/teams/preview", {
      method: "POST",
      body: JSON.stringify({ raw_md: teamMd }),
    });
    setTeamPreview(res.preview);
  };

  const confirmInstallTeam = async () => {
    await apiFetch("/api/teams/install", {
      method: "POST",
      body: JSON.stringify({ raw_md: teamMd, confirmed: true }),
    });
    setShowInstallModal(false);
    setTeamPreview(null);
    setNotice(isAr ? "تم تثبيت الفريق مع إبقاء الموصلات OFF والروتينات PAUSED." : "Team installed with connectors OFF & routines PAUSED.");
    load();
  };

  const shareDepartment = async (target: "folder" | "github") => {
    const res = await apiFetch<{ markdown: string; saved_path?: string }>("/api/teams/share", {
      method: "POST",
      body: JSON.stringify({ title: "My Shared Team", target }),
    });
    setShareOut(res.saved_path ? `Saved to: ${res.saved_path}\n\n${res.markdown}` : res.markdown);
  };

  return (
    <div className="space-y-4" onClick={() => ctxMenu && setCtxMenu(null)}>
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-borderline bg-surface p-3">
        <div className="flex items-center gap-2">
          <Users className="h-5 w-5 text-primary" />
          <h1 className="text-base font-bold">{t("phase5.teams_title")}</h1>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <button
            type="button"
            onClick={() => setShowInstallModal((v) => !v)}
            className="inline-flex items-center gap-1 rounded-lg border border-borderline bg-canvas px-3 py-1.5 font-semibold hover:border-primary"
          >
            <FileDown className="h-3.5 w-3.5" />
            <span>{isAr ? "تثبيت فريق (Markdown)" : "Install Team (.md)"}</span>
          </button>
          <button
            type="button"
            onClick={() => shareDepartment("folder")}
            className="inline-flex items-center gap-1 rounded-lg border border-borderline bg-canvas px-3 py-1.5 font-semibold hover:border-primary"
          >
            <Share2 className="h-3.5 w-3.5" />
            <span>{isAr ? "مشاركة حزمة آمنة" : "Share Sanitized Package"}</span>
          </button>
        </div>
      </div>

      {notice && <div className="rounded-xl border border-primary/40 bg-primary/10 p-2.5 text-xs text-primary">{notice}</div>}

      {showInstallModal && (
        <div className="rounded-xl border border-primary/40 bg-surface p-4 space-y-3 text-xs">
          <div className="font-bold">{isAr ? "معاينة وتثبيت فريق من ملف Markdown (شاشة مراجعة إلزامية)" : "Portable Team Markdown Review"}</div>
          <textarea dir="ltr" rows={6} value={teamMd} onChange={(e) => setTeamMd(e.target.value)} className="w-full rounded border border-borderline bg-canvas p-2 text-end font-mono" />
          <div className="flex gap-2">
            <button type="button" onClick={previewTeam} className="rounded bg-canvas border border-borderline px-3 py-1.5 font-semibold">
              {isAr ? "مراجعة قبل التثبيت" : "Preview Before Installing"}
            </button>
            {teamPreview && (
              <button type="button" onClick={confirmInstallTeam} className="rounded bg-primary px-3 py-1.5 font-semibold text-white">
                {isAr ? "تأكيد التثبيت" : "Confirm Install"}
              </button>
            )}
          </div>
          {teamPreview && <pre className="max-h-48 overflow-auto rounded bg-canvas p-2 font-mono text-[11px]">{JSON.stringify(teamPreview, null, 2)}</pre>}
        </div>
      )}

      {shareOut && <pre className="max-h-48 overflow-auto rounded-xl border border-borderline bg-surface p-3 font-mono text-[11px]">{shareOut}</pre>}

      <div className="grid min-h-[520px] gap-4 lg:grid-cols-[270px_1fr]">
        {/* Contact-Style Bot Roster + Channels */}
        <aside className="space-y-4 rounded-xl border border-borderline bg-surface p-3 text-xs">
          <div className="space-y-1.5">
            <div className="flex items-center justify-between font-bold text-muted">
              <span>{isAr ? "البوتات (كليك يمين للخيارات)" : "Bots (Right-click for actions)"}</span>
              <Plus className="h-3.5 w-3.5" />
            </div>
            {bots.map((b) => (
              <button
                key={b.id}
                type="button"
                onClick={() => {
                  setViewMode("bot");
                  setActiveBotId(b.id);
                }}
                onContextMenu={(ev) => {
                  ev.preventDefault();
                  setCtxMenu({ bot: b, x: ev.clientX, y: ev.clientY });
                }}
                className={`flex w-full items-center justify-between rounded-lg border px-2.5 py-2 text-start ${
                  viewMode === "bot" && activeBotId === b.id ? "border-primary bg-primary/10" : "border-borderline bg-canvas"
                }`}
              >
                <div className="flex items-center gap-2 truncate">
                  <span>{b.avatar_emoji}</span>
                  <div className="truncate">
                    <div className="font-bold truncate">{b.name}</div>
                    <div className="font-mono text-[10px] text-muted">{b.provider_id || "auto"} · {b.model_name}</div>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  {b.pinned && <Pin className="h-3 w-3 text-primary" />}
                  {b.unread_count > 0 && <span className="rounded-full bg-primary px-1.5 text-[10px] text-white">{b.unread_count}</span>}
                </div>
              </button>
            ))}
          </div>

          <div className="space-y-1.5 border-t border-borderline pt-3">
            <div className="font-bold text-muted">{isAr ? "القنوات (Work / Personal / Project)" : "Channels"}</div>
            {channels.map((ch) => (
              <button
                key={ch.id}
                type="button"
                onClick={() => {
                  setViewMode("channel");
                  setActiveChannelId(ch.id);
                }}
                className={`flex w-full items-center justify-between rounded-lg border px-2.5 py-2 text-start ${
                  viewMode === "channel" && activeChannelId === ch.id ? "border-primary bg-primary/10" : "border-borderline bg-canvas"
                }`}
              >
                <div className="flex items-center gap-1.5 truncate">
                  <Hash className="h-3.5 w-3.5 text-primary" />
                  <span className="font-semibold truncate">{ch.name}</span>
                </div>
                <span className="font-mono text-[10px] text-muted">{ch.context_type}</span>
              </button>
            ))}
          </div>
        </aside>

        {/* Active Bot or Channel Pane */}
        <section className="flex flex-col rounded-xl border border-borderline bg-surface p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-borderline pb-2.5">
            <div>
              <div className="font-bold">
                {viewMode === "bot" && activeBot ? `${activeBot.avatar_emoji} ${activeBot.name} (@${activeBot.handle})` : activeChannel?.name || ""}
              </div>
              {viewMode === "channel" && activeChannel && (
                <div className="text-[11px] text-muted">{activeChannel.shared_instructions}</div>
              )}
            </div>
            {viewMode === "bot" && activeBot && (
              <button
                type="button"
                onClick={() => setShowModelRail((v) => !v)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-borderline bg-canvas px-2.5 py-1 font-mono text-[11px] hover:border-primary"
              >
                <Cpu className="h-3.5 w-3.5 text-primary" />
                <span>{activeBot.provider_id} / {activeBot.model_name}</span>
              </button>
            )}
          </div>

          {showModelRail && viewMode === "bot" && activeBot && (
            <div className="my-2 grid gap-2 rounded-xl border border-borderline bg-canvas p-3 sm:grid-cols-3">
              {rail.map((rp) => (
                <div key={rp.provider_id} className={`rounded border p-2 ${rp.available ? "border-borderline bg-surface" : "border-borderline/40 opacity-50"}`}>
                  <div className="font-bold">{rp.name}</div>
                  {!rp.available && <div className="text-[10px] text-danger">{rp.unavailable_reason}</div>}
                  <div className="mt-1 flex flex-wrap gap-1">
                    {rp.models.slice(0, 6).map((m) => (
                      <button
                        key={m.id}
                        type="button"
                        disabled={!rp.available}
                        onClick={() => switchBotBrain(rp.provider_id, m.id)}
                        className="rounded border border-borderline bg-canvas px-1.5 py-0.5 font-mono text-[10px] hover:border-primary"
                      >
                        {m.id}{m.is_default ? "*" : ""}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="flex-1 space-y-2 overflow-y-auto py-3">
            {viewMode === "bot" &&
              (activeBot?.memory || []).map((m, idx) => (
                <div key={idx} className={`rounded-lg border p-2.5 ${m.role === "user" ? "ms-auto max-w-[80%] border-primary/30 bg-primary/10" : "me-auto max-w-[85%] border-borderline bg-canvas"}`}>
                  <div className="whitespace-pre-wrap">{m.content}</div>
                  {m.model_used && <div className="mt-1 font-mono text-[10px] text-muted">{m.model_used} · {m.latency_ms ?? 0}ms</div>}
                </div>
              ))}

            {viewMode === "channel" &&
              (activeChannel?.transcript || []).map((m, idx) => (
                <div key={idx} className={`rounded-lg border p-2.5 ${m.role === "user" ? "ms-auto max-w-[80%] border-primary/30 bg-primary/10" : "me-auto max-w-[85%] border-borderline bg-canvas"}`}>
                  {m.bot_name && <div className="mb-1 text-[10px] font-bold text-primary">{m.bot_name} ({m.routing_reason})</div>}
                  <div className="whitespace-pre-wrap">{m.content}</div>
                </div>
              ))}
          </div>

          <form onSubmit={sendMessage} className="flex gap-2 border-t border-borderline pt-2.5">
            <input
              value={msgInput}
              onChange={(e) => setMsgInput(e.target.value)}
              placeholder={viewMode === "channel" ? (isAr ? "أرسل رسالة (استخدم @mention أو اتركها لتوجيه Jev)..." : "Message channel (@handle or Jev auto-route)...") : (isAr ? "راسل البوت..." : "Message bot...")}
              className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2"
            />
            <button type="submit" className="inline-flex items-center gap-1 rounded-lg bg-primary px-4 py-2 font-semibold text-white">
              <Send className="h-3.5 w-3.5" />
              <span>{t("chat.send")}</span>
            </button>
          </form>
        </section>
      </div>

      {/* Right-click context menu */}
      {ctxMenu && (
        <div
          style={{ top: ctxMenu.y, insetInlineStart: ctxMenu.x }}
          className="fixed z-50 w-48 space-y-1 rounded-xl border border-borderline bg-surface p-2 text-xs shadow-xl"
        >
          <button type="button" onClick={() => runBotAction(ctxMenu.bot, ctxMenu.bot.pinned ? "unpin" : "pin")} className="block w-full rounded px-2 py-1 text-start hover:bg-canvas">
            {ctxMenu.bot.pinned ? (isAr ? "إلغاء التثبيت" : "Unpin") : (isAr ? "تثبيت" : "Pin")}
          </button>
          <button type="button" onClick={() => runBotAction(ctxMenu.bot, ctxMenu.bot.unread_count > 0 ? "mark_read" : "mark_unread")} className="block w-full rounded px-2 py-1 text-start hover:bg-canvas">
            {isAr ? "تحديد كغير مقروء" : "Mark Unread"}
          </button>
          <button type="button" onClick={() => runBotAction(ctxMenu.bot, "edit")} className="block w-full rounded px-2 py-1 text-start hover:bg-canvas">
            {isAr ? "تعديل الملف" : "Edit Profile"}
          </button>
          <button type="button" onClick={() => runBotAction(ctxMenu.bot, "duplicate")} className="block w-full rounded px-2 py-1 text-start hover:bg-canvas">
            {isAr ? "تكرار" : "Duplicate"}
          </button>
          <button type="button" onClick={() => runBotAction(ctxMenu.bot, "copy_id")} className="block w-full rounded px-2 py-1 text-start hover:bg-canvas">
            {isAr ? "نسخ Conversation ID" : "Copy Conversation ID"}
          </button>
          <button type="button" onClick={() => runBotAction(ctxMenu.bot, "hide")} className="block w-full rounded px-2 py-1 text-start hover:bg-canvas">
            {isAr ? "إخفاء" : "Hide"}
          </button>
          <button type="button" onClick={() => runBotAction(ctxMenu.bot, "delete")} className="block w-full rounded px-2 py-1 text-start text-danger hover:bg-canvas">
            {isAr ? "حذف" : "Delete"}
          </button>
        </div>
      )}
    </div>
  );
}
