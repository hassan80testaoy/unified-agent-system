"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sparkles, Play, ShieldCheck } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch, ApiError } from "@/lib/api";

interface AgentItem {
  id: string;
  key: string;
  name_ar: string;
  name_en: string;
  description_ar: string;
  input_schema: {
    required?: string[];
    properties?: Record<
      string,
      { type: string; title?: string; description?: string; default?: unknown; enum?: string[]; minimum?: number; maximum?: number }
    >;
  };
}

interface PreviewResp {
  item_count: number;
  est_cost_usd: number;
  est_minutes: number;
  requires_approval: boolean;
  approval_reasons: string[];
}

export default function NewTaskPage() {
  const router = useRouter();
  const { t, locale, formatNum } = useApp();

  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [requestText, setRequestText] = useState("");
  const [dispatchMsg, setDispatchMsg] = useState("");
  const [formValues, setFormValues] = useState<Record<string, unknown>>({});
  const [mode, setMode] = useState<"once" | "repeat">("once");
  const [preview, setPreview] = useState<PreviewResp | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch<{ agents: AgentItem[] }>("/api/agents").then((res) => {
      setAgents(res.agents);
      if (res.agents.length > 0) {
        const first = res.agents[0];
        setSelectedAgentId(first.id);
        initDefaults(first);
      }
    });
  }, []);

  const initDefaults = (agent: AgentItem) => {
    const defaults: Record<string, unknown> = {};
    const props = agent.input_schema?.properties || {};
    for (const [k, def] of Object.entries(props)) {
      if (def.default !== undefined) defaults[k] = def.default;
      else if (def.type === "boolean") defaults[k] = false;
      else if (def.type === "integer") defaults[k] = 10;
      else defaults[k] = "";
    }
    setFormValues(defaults);
  };

  const selectedAgent = agents.find((a) => a.id === selectedAgentId);

  const handleSelectAgent = (id: string) => {
    setSelectedAgentId(id);
    const ag = agents.find((a) => a.id === id);
    if (ag) initDefaults(ag);
    setPreview(null);
  };

  const handleDispatch = async () => {
    setDispatchMsg("");
    const res = await apiFetch<{ matched: boolean; agent_id?: string; message_ar: string; message_en: string }>("/api/tasks/dispatch", {
      method: "POST",
      body: JSON.stringify({ request_text: requestText }),
    });
    setDispatchMsg(locale === "ar" ? res.message_ar : res.message_en);
    if (res.matched && res.agent_id) {
      handleSelectAgent(res.agent_id);
    }
  };

  const handlePreview = async () => {
    if (!selectedAgent) return;
    setError("");
    try {
      const res = await apiFetch<PreviewResp>("/api/tasks/preview", {
        method: "POST",
        body: JSON.stringify({ agent_id: selectedAgent.id, request_text: requestText, input_json: formValues }),
      });
      setPreview(res);
    } catch (err) {
      const e2 = err as ApiError;
      setError(locale === "ar" ? e2.message_ar : e2.message_en);
    }
  };

  const handleStart = async () => {
    if (!selectedAgent) return;
    setError("");
    try {
      const res = await apiFetch<{ run: { id: string } }>("/api/tasks", {
        method: "POST",
        body: JSON.stringify({
          agent_id: selectedAgent.id,
          request_text: requestText,
          input_json: formValues,
          mode,
        }),
      });
      router.push(`/runs/${res.run.id}`);
    } catch (err) {
      const e2 = err as ApiError;
      setError(locale === "ar" ? e2.message_ar : e2.message_en);
    }
  };

  const props = selectedAgent?.input_schema?.properties || {};

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-xl font-bold">{t("tasks.title")}</h1>

      {error && <div className="rounded-xl border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{error}</div>}

      {/* Free-text dispatcher */}
      <div className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
        <label className="block text-sm font-semibold">{t("tasks.free_text_label")}</label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            type="text"
            value={requestText}
            onChange={(e) => setRequestText(e.target.value)}
            placeholder={t("tasks.free_text_placeholder")}
            className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2.5 text-sm outline-none focus:border-primary"
          />
          <button
            type="button"
            onClick={handleDispatch}
            className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg border border-primary px-4 text-sm font-semibold text-primary hover:bg-primary/10"
          >
            <Sparkles className="h-4 w-4" />
            <span>{t("tasks.match_btn")}</span>
          </button>
        </div>
        {dispatchMsg && <div className="text-xs font-medium text-primary">{dispatchMsg}</div>}
      </div>

      {/* Agent selector + dynamic schema form */}
      <div className="rounded-xl border border-borderline bg-surface p-4 space-y-4">
        <div>
          <label className="mb-1 block text-xs font-semibold text-muted">{t("tasks.select_agent")}</label>
          <select
            value={selectedAgentId}
            onChange={(e) => handleSelectAgent(e.target.value)}
            className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2.5 text-sm outline-none focus:border-primary"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {locale === "ar" ? a.name_ar : a.name_en} ({a.key})
              </option>
            ))}
          </select>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          {Object.entries(props).map(([key, field]) => {
            const val = formValues[key];
            if (field.type === "boolean") {
              return (
                <label key={key} className="flex items-center gap-2 rounded-lg border border-borderline bg-canvas p-3 text-sm sm:col-span-2">
                  <input
                    type="checkbox"
                    checked={Boolean(val)}
                    onChange={(e) => setFormValues((prev) => ({ ...prev, [key]: e.target.checked }))}
                    className="h-4 w-4"
                  />
                  <span>{field.title || key}</span>
                </label>
              );
            }
            if (field.enum) {
              return (
                <div key={key}>
                  <label className="mb-1 block text-xs font-medium">{field.title || key}</label>
                  <select
                    value={String(val ?? "")}
                    onChange={(e) => setFormValues((prev) => ({ ...prev, [key]: e.target.value }))}
                    className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-sm"
                  >
                    {field.enum.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </select>
                </div>
              );
            }
            return (
              <div key={key}>
                <label className="mb-1 block text-xs font-medium">{field.title || key}</label>
                <input
                  type={field.type === "integer" ? "number" : "text"}
                  dir={key.includes("domain") || key.includes("url") ? "ltr" : undefined}
                  value={String(val ?? "")}
                  onChange={(e) =>
                    setFormValues((prev) => ({
                      ...prev,
                      [key]: field.type === "integer" ? Number(e.target.value) : e.target.value,
                    }))
                  }
                  className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-start text-sm outline-none focus:border-primary"
                />
              </div>
            );
          })}
        </div>

        <div className="flex items-center gap-4 border-t border-borderline pt-3 text-sm">
          <label className="flex items-center gap-2">
            <input type="radio" checked={mode === "once"} onChange={() => setMode("once")} />
            <span>{t("tasks.mode_once")}</span>
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" checked={mode === "repeat"} onChange={() => setMode("repeat")} />
            <span>{t("tasks.mode_repeat")}</span>
          </label>
        </div>

        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            onClick={handlePreview}
            className="min-h-[44px] rounded-lg border border-borderline px-4 text-sm font-semibold hover:bg-canvas"
          >
            {t("tasks.preview_title")}
          </button>
          <button
            type="button"
            onClick={handleStart}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-lg bg-primary px-5 text-sm font-semibold text-white"
          >
            {preview?.requires_approval ? <ShieldCheck className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            <span>{preview?.requires_approval ? t("common.send_approval") : t("common.start")}</span>
          </button>
        </div>
      </div>

      {preview && (
        <div className="rounded-xl border border-primary/30 bg-primary/5 p-4 text-sm">
          <div className="font-bold mb-2">{t("tasks.preview_title")}</div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>
              {t("tasks.est_items")}: <strong>{formatNum(preview.item_count)}</strong>
            </div>
            <div>
              {t("tasks.est_cost")}: <bdi dir="ltr">${preview.est_cost_usd.toFixed(3)}</bdi>
            </div>
            <div>
              {t("tasks.est_time")}: <strong>{formatNum(preview.est_minutes)}m</strong>
            </div>
          </div>
          {preview.requires_approval && (
            <div className="mt-2 text-xs font-semibold text-warning">
              {locale === "ar" ? "يتطلب موافقة بشرية قبل البدء:" : "Requires approval:"} {preview.approval_reasons.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
