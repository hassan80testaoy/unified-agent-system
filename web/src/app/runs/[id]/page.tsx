"use client";

import React, { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Check, KeyRound, Pause, Play, RefreshCw, ShieldAlert, SkipForward, Terminal, X } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch, runWebSocketUrl } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface RunDetailResp {
  run: {
    id: string;
    agent_key: string;
    agent_name_ar: string;
    status: string;
    progress_done: number;
    progress_total: number;
    cost_usd: number;
    error: string | null;
    summary_json: Record<string, unknown> | null;
    settings_used: Record<string, unknown>;
  };
  events: { id: string; ts: string; level: string; kind: string; message: string; data: Record<string, unknown> }[];
  items: { id: string; item_type: string; item_ref: string; status: string; review_state: string; result: Record<string, unknown> }[];
  pending_approvals: { id: string; kind: string; payload: Record<string, unknown> }[];
}

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const { t, locale, formatNum, formatDate } = useApp();

  const [detail, setDetail] = useState<RunDetailResp | null>(null);
  const [tab, setTab] = useState<"timeline" | "items" | "settings" | "report">("timeline");
  const [verificationCode, setVerificationCode] = useState("");
  const [tunnelInfo, setTunnelInfo] = useState<{ ssh_command: string; local_url: string } | null>(null);
  const [feedback, setFeedback] = useState("");

  const load = async () => {
    const res = await apiFetch<RunDetailResp>(`/api/runs/${params.id}`);
    setDetail(res);
    if (res.run.status === "awaiting_captcha_manual") {
      const cap = await apiFetch<{ tunnel: { ssh_command: string; local_url: string } }>(`/api/captcha/${params.id}/manual-solve`, {
        method: "POST",
      });
      setTunnelInfo(cap.tunnel);
    }
  };

  useEffect(() => {
    load().catch(() => null);
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(runWebSocketUrl(params.id));
      ws.onmessage = () => {
        load().catch(() => null);
      };
    } catch {
      // Fallback polling
    }
    const timer = setInterval(() => load().catch(() => null), 4000);
    return () => {
      clearInterval(timer);
      ws?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.id]);

  const handleConfirmCaptchaSolved = async () => {
    await apiFetch(`/api/captcha/${params.id}/confirm-solved`, { method: "POST" });
    setFeedback(locale === "ar" ? "تم استئناف التشغيل بعد الحل اليدوي." : "Run resumed after manual solve.");
    load();
  };

  const handleSkipCaptcha = async () => {
    await apiFetch(`/api/captcha/${params.id}/skip`, {
      method: "POST",
      body: JSON.stringify({ reason: "manual_skipped" }),
    });
    setFeedback(locale === "ar" ? "تم تخطي الموقع والانتقال للتالي." : "Site skipped with reason.");
    load();
  };

  const handleSubmitVerificationCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!verificationCode.trim()) return;
    const res = await apiFetch<{ message_ar: string; message_en: string }>(`/api/verification-codes/${params.id}`, {
      method: "POST",
      body: JSON.stringify({ code: verificationCode }),
    });
    setVerificationCode(""); // Immediately clear code from React state
    setFeedback(locale === "ar" ? res.message_ar : res.message_en);
    load();
  };

  const handleReviewItem = async (itemId: string, review_state: "accepted" | "redo" | "dropped") => {
    await apiFetch(`/api/runs/${params.id}/items/${itemId}/review`, {
      method: "POST",
      body: JSON.stringify({ review_state }),
    });
    load();
  };

  const controlRun = async (action: string) => {
    await apiFetch(`/api/runs/${params.id}/${action}`, { method: "POST" });
    load();
  };

  if (!detail) return <div className="p-6 text-sm text-muted">{t("common.loading")}</div>;
  const { run, events, items } = detail;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-borderline bg-surface p-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-bold">{locale === "ar" ? run.agent_name_ar || run.agent_key : run.agent_key}</h1>
            <StatusBadge status={run.status} />
          </div>
          <div className="mt-1 flex flex-wrap gap-4 text-xs text-muted">
            <bdi dir="ltr" className="font-mono">
              {run.id}
            </bdi>
            <span>
              {formatNum(run.progress_done)} / {formatNum(run.progress_total)}
            </span>
            <bdi dir="ltr">${run.cost_usd.toFixed(4)}</bdi>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <button onClick={() => controlRun("pause")} className="inline-flex items-center gap-1 rounded-lg border border-borderline px-3 py-2 text-xs font-semibold">
            <Pause className="h-3.5 w-3.5" />
            <span>{locale === "ar" ? "إيقاف مؤقت" : "Pause"}</span>
          </button>
          <button onClick={() => controlRun("resume")} className="inline-flex items-center gap-1 rounded-lg border border-borderline px-3 py-2 text-xs font-semibold">
            <Play className="h-3.5 w-3.5" />
            <span>{locale === "ar" ? "استئناف" : "Resume"}</span>
          </button>
          <button onClick={() => controlRun("cancel")} className="inline-flex items-center gap-1 rounded-lg border border-danger/40 px-3 py-2 text-xs font-semibold text-danger">
            <X className="h-3.5 w-3.5" />
            <span>{t("common.cancel")}</span>
          </button>
        </div>
      </div>

      {feedback && <div className="rounded-lg border border-primary/30 bg-primary/10 p-3 text-xs font-medium text-primary">{feedback}</div>}
      {run.error && <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-xs text-danger">{run.error}</div>}

      {/* Manual CAPTCHA noVNC Banner */}
      {run.status === "awaiting_captcha_manual" && (
        <div className="rounded-xl border-2 border-warning bg-amber-500/10 p-4 space-y-3">
          <div className="flex items-center gap-2 font-bold text-warning">
            <ShieldAlert className="h-5 w-5" />
            <span>{t("captcha.manual_title")}</span>
          </div>
          <p className="text-xs">{t("captcha.ssh_tunnel_label")}</p>
          {tunnelInfo && (
            <div className="rounded-lg border border-borderline bg-surface p-3 space-y-1">
              <div className="flex items-center gap-2 text-xs">
                <Terminal className="h-3.5 w-3.5 text-muted" />
                <bdi dir="ltr" className="font-mono font-semibold">
                  {tunnelInfo.ssh_command}
                </bdi>
              </div>
              <div className="text-xs">
                noVNC:{" "}
                <a href={tunnelInfo.local_url} target="_blank" rel="noreferrer" className="font-mono text-primary underline">
                  <bdi dir="ltr">{tunnelInfo.local_url}</bdi>
                </a>
              </div>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <button onClick={handleConfirmCaptchaSolved} className="inline-flex min-h-[44px] items-center gap-2 rounded-lg bg-success px-4 text-xs font-semibold text-white">
              <Check className="h-4 w-4" />
              <span>{t("captcha.mark_solved")}</span>
            </button>
            <button onClick={handleSkipCaptcha} className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-borderline bg-surface px-4 text-xs font-semibold">
              <SkipForward className="h-4 w-4" />
              <span>{t("captcha.skip_site")}</span>
            </button>
          </div>
        </div>
      )}

      {/* One-time Verification Code Ask Banner */}
      {run.status === "awaiting_verification_code" && (
        <form onSubmit={handleSubmitVerificationCode} className="rounded-xl border-2 border-primary bg-primary/10 p-4 space-y-3">
          <div className="flex items-center gap-2 font-bold text-primary">
            <KeyRound className="h-5 w-5" />
            <span>{t("google.code_prompt_title")}</span>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              dir="ltr"
              autoComplete="one-time-code"
              value={verificationCode}
              onChange={(e) => setVerificationCode(e.target.value)}
              placeholder={t("google.code_input_placeholder")}
              className="flex-1 rounded-lg border border-borderline bg-surface px-3 py-2.5 text-end font-mono text-sm outline-none focus:border-primary"
            />
            <button type="submit" className="min-h-[44px] rounded-lg bg-primary px-5 text-sm font-semibold text-white">
              {t("google.submit_code")}
            </button>
          </div>
        </form>
      )}

      {/* Tabs */}
      <div className="flex gap-2 border-b border-borderline pb-2 text-xs font-semibold">
        {(["timeline", "items", "settings", "report"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`rounded-lg px-3 py-2 ${tab === k ? "bg-primary text-white" : "text-muted hover:bg-surface"}`}
          >
            {k === "timeline"
              ? locale === "ar" ? "السجل اللحظي" : "Timeline"
              : k === "items"
              ? locale === "ar" ? "العناصر والمراجعة" : "Items & Review"
              : k === "settings"
              ? locale === "ar" ? "الإعدادات المستخدمة" : "Settings Used"
              : locale === "ar" ? "الملخص" : "Summary"}
          </button>
        ))}
      </div>

      {tab === "timeline" && (
        <div className="rounded-xl border border-borderline bg-surface p-4 space-y-2">
          {events.length === 0 ? (
            <div className="text-xs text-muted">{t("common.empty")}</div>
          ) : (
            events.map((ev) => (
              <div key={ev.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-borderline pb-2 text-xs last:border-0">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-canvas px-1.5 py-0.5 font-mono text-[11px]">{ev.kind}</span>
                  <span>{ev.message}</span>
                </div>
                <span className="text-[11px] text-muted">{formatDate(ev.ts)}</span>
              </div>
            ))
          )}
        </div>
      )}

      {tab === "items" && (
        <div className="overflow-x-auto rounded-xl border border-borderline bg-surface">
          <table className="w-full text-start text-xs">
            <thead className="border-b border-borderline bg-canvas text-muted">
              <tr>
                <th className="p-3 text-start">{locale === "ar" ? "الموقع / العنصر" : "Item"}</th>
                <th className="p-3 text-start">{locale === "ar" ? "الحالة" : "Status"}</th>
                <th className="p-3 text-start">{locale === "ar" ? "التفاصيل" : "Details"}</th>
                <th className="p-3 text-start">{locale === "ar" ? "المراجعة" : "Review"}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} className="border-b border-borderline last:border-0">
                  <td className="p-3 font-mono">
                    <bdi dir="ltr">{it.item_ref}</bdi>
                  </td>
                  <td className="p-3">
                    <StatusBadge status={it.status} />
                  </td>
                  <td className="p-3 text-muted">
                    <bdi dir="ltr">{String(it.result?.profile_url || it.result?.blocked_reason || "—")}</bdi>
                  </td>
                  <td className="p-3">
                    <div className="flex gap-1.5">
                      <button onClick={() => handleReviewItem(it.id, "accepted")} className={`rounded px-2 py-1 ${it.review_state === "accepted" ? "bg-success text-white" : "border border-borderline"}`}>
                        {locale === "ar" ? "قبول" : "Accept"}
                      </button>
                      <button onClick={() => handleReviewItem(it.id, "redo")} className={`rounded px-2 py-1 ${it.review_state === "redo" ? "bg-warning text-white" : "border border-borderline"}`}>
                        <RefreshCw className="h-3 w-3" />
                      </button>
                      <button onClick={() => handleReviewItem(it.id, "dropped")} className={`rounded px-2 py-1 ${it.review_state === "dropped" ? "bg-danger text-white" : "border border-borderline"}`}>
                        {locale === "ar" ? "إسقاط" : "Drop"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "settings" && (
        <pre dir="ltr" className="overflow-x-auto rounded-xl border border-borderline bg-surface p-4 font-mono text-xs">
          {JSON.stringify(run.settings_used, null, 2)}
        </pre>
      )}

      {tab === "report" && (
        <pre dir="ltr" className="overflow-x-auto rounded-xl border border-borderline bg-surface p-4 font-mono text-xs">
          {JSON.stringify(run.summary_json || {}, null, 2)}
        </pre>
      )}
    </div>
  );
}
