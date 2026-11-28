"use client";

import React, { useEffect, useState } from "react";
import { MessageCircle, Mic, Send, Settings2 } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface GatewayChannel {
  id: string;
  channel: string;
  enabled: boolean;
  continuity_mode: string;
  status: string;
  configured_fields: string[];
  bot_username: string;
  phone_number: string;
  last_event_at: string | null;
  setup_hint_ar: string;
}

interface GatewayLog {
  id: string;
  channel: string;
  direction: string;
  external_peer_id: string;
  thread_id: string | null;
  content_preview: string;
  media_kind: string;
  transcript: string;
  status: string;
  created_at: string | null;
}

export default function MessagingPage() {
  const { t, locale, formatDate } = useApp();
  const isAr = locale === "ar";

  const [channels, setChannels] = useState<GatewayChannel[]>([]);
  const [logs, setLogs] = useState<GatewayLog[]>([]);
  const [tokenInputs, setTokenInputs] = useState<Record<string, string>>({});
  const [idInputs, setIdInputs] = useState<Record<string, string>>({});

  const [simChannel, setSimChannel] = useState("telegram");
  const [simPeerId, setSimPeerId] = useState("owner");
  const [simText, setSimText] = useState("");
  const [simMock, setSimMock] = useState(true);
  const [voiceHint, setVoiceHint] = useState("");
  const [voiceOut, setVoiceOut] = useState("");
  const [notice, setNotice] = useState("");

  const load = async () => {
    const [chRes, logRes] = await Promise.all([
      apiFetch<{ channels: GatewayChannel[] }>("/api/messaging/channels"),
      apiFetch<{ logs: GatewayLog[] }>("/api/messaging/logs"),
    ]);
    setChannels(chRes.channels || []);
    setLogs(logRes.logs || []);
  };

  useEffect(() => {
    load().catch(() => null);
  }, []);

  const handleSaveChannel = async (ch: string, enabled: boolean) => {
    const cfg: Record<string, string> = {};
    if (ch === "telegram") {
      if (tokenInputs[ch]) cfg.bot_token = tokenInputs[ch].trim();
      if (idInputs[ch]) cfg.bot_username = idInputs[ch].trim();
    } else if (ch === "whatsapp") {
      if (tokenInputs[ch]) cfg.access_token = tokenInputs[ch].trim();
      if (idInputs[ch]) cfg.phone_number = idInputs[ch].trim();
    } else {
      if (idInputs[ch]) cfg.account_label = idInputs[ch].trim();
    }
    await apiFetch(`/api/messaging/channels/${ch}`, {
      method: "PUT",
      body: JSON.stringify({ enabled, config: cfg, continuity_mode: "shared_owner_thread" }),
    });
    setNotice(isAr ? `تم حفظ إعدادات قناة ${ch}` : `Saved configuration for ${ch}`);
    await load();
  };

  const handleSimulateInbound = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!simText.trim()) return;
    const res = await apiFetch<{ thread_id: string; reply: string }>(`/api/messaging/webhook/${simChannel}`, {
      method: "POST",
      body: JSON.stringify({
        peer_id: simPeerId.trim() || "owner",
        text: simText.trim(),
        mock_mode: simMock,
      }),
    });
    setSimText("");
    setNotice(
      isAr
        ? `تم توجيه الرسالة عبر ${simChannel} لنفس المحادثة المشتركة (${res.thread_id.slice(0, 8)}): ${res.reply.slice(0, 120)}`
        : `Routed via ${simChannel} to shared thread ${res.thread_id.slice(0, 8)}: ${res.reply.slice(0, 120)}`
    );
    await load();
  };

  const handleTranscribeVoice = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await apiFetch<{ transcript: string }>("/api/voice/transcribe", {
      method: "POST",
      body: JSON.stringify({ transcript_hint: voiceHint }),
    });
    setVoiceOut(res.transcript || "");
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold">
          <MessageCircle className="h-5 w-5 text-primary" />
          <span>{t("phase8.messaging_title")}</span>
        </h1>
        <p className="text-xs text-muted">{t("phase8.messaging_sub")}</p>
      </div>

      {notice && (
        <div className="rounded-xl border border-primary/30 bg-primary/5 px-4 py-2 text-xs text-primary">{notice}</div>
      )}

      {/* Guided Channel Setup Cards */}
      <div className="grid gap-4 md:grid-cols-2">
        {channels.map((ch) => (
          <div key={ch.id} className="bento-card space-y-3 p-4 text-xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Settings2 className="h-4 w-4 text-primary" />
                <span className="text-sm font-bold uppercase">{ch.channel}</span>
              </div>
              <StatusBadge status={ch.enabled ? "ok" : "disconnected"} />
            </div>
            <p className="text-muted">{ch.setup_hint_ar}</p>
            <div className="grid gap-2 sm:grid-cols-2">
              <input
                value={idInputs[ch.channel] ?? (ch.bot_username || ch.phone_number || "")}
                onChange={(e) => setIdInputs((prev) => ({ ...prev, [ch.channel]: e.target.value }))}
                placeholder={ch.channel === "telegram" ? "@bot_username" : ch.channel === "whatsapp" ? "+201..." : "label / mode"}
                className="rounded-lg border border-borderline bg-canvas px-2.5 py-1.5"
              />
              <input
                type="password"
                value={tokenInputs[ch.channel] || ""}
                onChange={(e) => setTokenInputs((prev) => ({ ...prev, [ch.channel]: e.target.value }))}
                placeholder={isAr ? "Token / Secret (مشفّر)" : "Token / Secret (encrypted)"}
                className="rounded-lg border border-borderline bg-canvas px-2.5 py-1.5"
              />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <span className="font-mono text-[10px] text-muted">continuity: {ch.continuity_mode}</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => handleSaveChannel(ch.channel, true)}
                  className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-white"
                >
                  {isAr ? "حفظ وتفعيل" : "Save & Enable"}
                </button>
                {ch.enabled && (
                  <button
                    type="button"
                    onClick={() => handleSaveChannel(ch.channel, false)}
                    className="rounded-lg border border-borderline px-3 py-1.5"
                  >
                    {isAr ? "تعطيل" : "Disable"}
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Shared-Thread Inbound Test + Voice STT */}
      <div className="grid gap-4 lg:grid-cols-2">
        <form onSubmit={handleSimulateInbound} className="bento-card space-y-3 p-4 text-xs">
          <div className="flex items-center justify-between font-bold">
            <span className="flex items-center gap-1.5">
              <Send className="h-4 w-4 text-primary" />
              <span>{isAr ? "اختبار الاستمرارية عبر القنوات (Same Thread)" : "Cross-Platform Continuity Test"}</span>
            </span>
            <label className="flex items-center gap-1 font-normal text-muted">
              <input type="checkbox" checked={simMock} onChange={(e) => setSimMock(e.target.checked)} />
              <span>{isAr ? "محاكاة" : "Mock"}</span>
            </label>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <select
              value={simChannel}
              onChange={(e) => setSimChannel(e.target.value)}
              className="rounded-lg border border-borderline bg-canvas px-2.5 py-2"
            >
              <option value="telegram">telegram</option>
              <option value="whatsapp">whatsapp</option>
              <option value="email">email</option>
              <option value="voice">voice</option>
            </select>
            <input
              value={simPeerId}
              onChange={(e) => setSimPeerId(e.target.value)}
              placeholder="peer_id (default: owner)"
              className="rounded-lg border border-borderline bg-canvas px-2.5 py-2"
            />
          </div>
          <div className="flex gap-2">
            <input
              value={simText}
              onChange={(e) => setSimText(e.target.value)}
              placeholder={isAr ? "رسالة واردة من تيليجرام أو واتساب..." : "Inbound message from Telegram or WhatsApp..."}
              className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2"
            />
            <button type="submit" className="rounded-lg bg-primary px-4 py-2 font-semibold text-white">
              {isAr ? "إرسال للبوابة" : "Dispatch"}
            </button>
          </div>
        </form>

        <form onSubmit={handleTranscribeVoice} className="bento-card space-y-3 p-4 text-xs">
          <div className="flex items-center gap-1.5 font-bold">
            <Mic className="h-4 w-4 text-primary" />
            <span>{isAr ? "تفريغ الرسائل الصوتية (STT)" : "Voice Note Transcription"}</span>
          </div>
          <div className="flex gap-2">
            <input
              value={voiceHint}
              onChange={(e) => setVoiceHint(e.target.value)}
              placeholder={isAr ? "نص أو إشارة صوتية لاختبار STT..." : "Audio transcript hint..."}
              className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2"
            />
            <button type="submit" className="rounded-lg bg-primary px-4 py-2 font-semibold text-white">
              {isAr ? "تفريغ" : "Transcribe"}
            </button>
          </div>
          {voiceOut && <div className="rounded-lg bg-canvas p-2.5">{voiceOut}</div>}
        </form>
      </div>

      {/* Unified Logs */}
      <div className="bento-card space-y-2 p-4 text-xs">
        <div className="text-sm font-bold">{isAr ? "سجل رسائل القنوات الموحدة" : "Unified Gateway Event Log"}</div>
        {logs.length === 0 ? (
          <div className="rounded-lg border border-dashed border-borderline p-4 text-center text-muted">{t("common.empty")}</div>
        ) : (
          <div className="space-y-1.5">
            {logs.map((lg) => (
              <div key={lg.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-borderline bg-canvas px-3 py-2">
                <div>
                  <span className="font-mono font-bold text-primary">[{lg.channel}/{lg.direction}] </span>
                  <span className="font-mono text-[10px] text-muted">thread:{lg.thread_id?.slice(0, 8) || "-"} · </span>
                  <span>{lg.content_preview || lg.transcript}</span>
                </div>
                <span className="text-[10px] text-muted">{formatDate(lg.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
