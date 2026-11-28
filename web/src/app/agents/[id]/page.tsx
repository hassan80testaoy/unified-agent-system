"use client";

import React, { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Save, Layers } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch, ApiError } from "@/lib/api";

interface ProviderRow {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
}

export default function AgentDetailPage() {
  const params = useParams<{ id: string }>();
  const { locale } = useApp();

  const [agent, setAgent] = useState<{
    id: string;
    key: string;
    name_ar: string;
    name_en: string;
    daily_limit: number;
    daily_budget_usd: number;
    needs_approval: boolean;
  } | null>(null);

  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [orchProvider, setOrchProvider] = useState("mock-local");
  const [orchTier, setOrchTier] = useState("strong");
  const [orchModel, setOrchModel] = useState("auto");
  const [revProvider, setRevProvider] = useState("ollama-local");
  const [revTier, setRevTier] = useState("mid");
  const [revModel, setRevModel] = useState("auto");
  const [workers, setWorkers] = useState<Record<string, { provider: string; tier: string; model?: string }>>({});
  const [dailyLimit, setDailyLimit] = useState(100);
  const [dailyBudget, setDailyBudget] = useState(3.0);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([
      apiFetch<{
        agent: typeof agent;
        delegation: {
          orchestrator: { provider?: string; tier?: string; model?: string };
          workers: Record<string, { provider: string; tier: string; model?: string }>;
          reviewer: { provider?: string; tier?: string; model?: string };
        };
      }>(`/api/agents/${params.id}`),
      apiFetch<{ providers: ProviderRow[] }>("/api/providers"),
    ]).then(([det, provs]) => {
      if (det.agent) {
        setAgent(det.agent);
        setDailyLimit(det.agent.daily_limit);
        setDailyBudget(det.agent.daily_budget_usd);
      }
      setOrchProvider(det.delegation.orchestrator?.provider || "mock-local");
      setOrchTier(det.delegation.orchestrator?.tier || "strong");
      setOrchModel(det.delegation.orchestrator?.model || "auto");
      setRevProvider(det.delegation.reviewer?.provider || "ollama-local");
      setRevTier(det.delegation.reviewer?.tier || "mid");
      setRevModel(det.delegation.reviewer?.model || "auto");
      setWorkers(det.delegation.workers || {});
      setProviders(provs.providers.filter((p) => p.kind !== "captcha"));
    });
  }, [params.id]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg("");
    setErr("");
    try {
      await apiFetch(`/api/agents/${params.id}/settings`, {
        method: "PATCH",
        body: JSON.stringify({ daily_limit: dailyLimit, daily_budget_usd: dailyBudget }),
      });
      await apiFetch(`/api/agents/${params.id}/providers`, {
        method: "PUT",
        body: JSON.stringify({
          orchestrator: { provider: orchProvider, tier: orchTier, model: orchModel || "auto" },
          workers,
          reviewer: { provider: revProvider, tier: revTier, model: revModel || "auto" },
        }),
      });
      setMsg(locale === "ar" ? "تم حفظ إعدادات الوكيل وتوزيع المزودين بنجاح." : "Agent limits and provider delegation saved.");
    } catch (error) {
      const apiErr = error as ApiError;
      setErr(locale === "ar" ? apiErr.message_ar : apiErr.message_en);
    }
  };

  if (!agent) return <div className="p-6 text-sm text-muted">...</div>;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-bold">{locale === "ar" ? agent.name_ar : agent.name_en}</h1>
        <bdi dir="ltr" className="font-mono text-xs text-muted">
          {agent.key}
        </bdi>
      </div>

      {msg && <div className="rounded-lg border border-success/30 bg-success/10 p-3 text-sm text-success">{msg}</div>}
      {err && <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{err}</div>}

      <form onSubmit={handleSave} className="space-y-6 rounded-xl border border-borderline bg-surface p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs font-medium">{locale === "ar" ? "الحد اليومي" : "Daily Limit"}</label>
            <input
              type="number"
              value={dailyLimit}
              onChange={(e) => setDailyLimit(Number(e.target.value))}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium">{locale === "ar" ? "الميزانية اليومية (USD)" : "Daily Budget (USD)"}</label>
            <input
              type="number"
              step="0.1"
              value={dailyBudget}
              onChange={(e) => setDailyBudget(Number(e.target.value))}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-sm"
            />
          </div>
        </div>

        <div className="border-t border-borderline pt-4 space-y-4">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Layers className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "توزيع العقل المدبر والعمال والمراجع (Delegation)" : "Orchestrator, Workers & Reviewer Delegation"}</span>
          </h2>
          <p className="text-xs text-muted">
            {locale === "ar"
              ? "مسموح باستخدام نفس المزود والموديل للعقل المدبر والمنفّذين في نفس المهمة؛ الفصل الإلزامي يقتصر فقط على اختلاف مزود المراجع."
              : "Using the same provider/model for Orchestrator and Workers is allowed; only Reviewer must come from a different provider."}
          </p>

          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs font-medium">{locale === "ar" ? "العقل المدبر (Orchestrator)" : "Orchestrator Provider"}</label>
              <select
                value={orchProvider}
                onChange={(e) => setOrchProvider(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-sm"
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.id})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">{locale === "ar" ? "المستوى (Tier)" : "Tier"}</label>
              <select
                value={orchTier}
                onChange={(e) => setOrchTier(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-sm"
              >
                <option value="strong">strong</option>
                <option value="mid">mid</option>
                <option value="cheap">cheap</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">{locale === "ar" ? "الموديل (اختياري / auto)" : "Model (optional / auto)"}</label>
              <input
                type="text"
                dir="ltr"
                value={orchModel}
                onChange={(e) => setOrchModel(e.target.value)}
                placeholder="auto"
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono text-sm"
              />
            </div>
          </div>

          <div className="space-y-2">
            <div className="text-xs font-semibold text-muted">{locale === "ar" ? "العمال حسب نوع المهمة (Workers)" : "Workers by Task Type"}</div>
            {Object.entries(workers).map(([taskType, cfg]) => (
              <div key={taskType} className="grid gap-2 rounded-lg border border-borderline bg-canvas p-3 sm:grid-cols-4">
                <div className="flex items-center font-mono text-xs font-semibold">{taskType}</div>
                <select
                  value={cfg.provider}
                  onChange={(e) =>
                    setWorkers((prev) => ({ ...prev, [taskType]: { ...prev[taskType], provider: e.target.value } }))
                  }
                  className="rounded border border-borderline bg-surface px-2 py-1.5 text-xs"
                >
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.id}
                    </option>
                  ))}
                </select>
                <select
                  value={cfg.tier}
                  onChange={(e) =>
                    setWorkers((prev) => ({ ...prev, [taskType]: { ...prev[taskType], tier: e.target.value } }))
                  }
                  className="rounded border border-borderline bg-surface px-2 py-1.5 text-xs"
                >
                  <option value="cheap">cheap</option>
                  <option value="mid">mid</option>
                  <option value="strong">strong</option>
                </select>
                <input
                  type="text"
                  dir="ltr"
                  value={cfg.model || "auto"}
                  onChange={(e) =>
                    setWorkers((prev) => ({ ...prev, [taskType]: { ...prev[taskType], model: e.target.value } }))
                  }
                  placeholder="auto"
                  className="rounded border border-borderline bg-surface px-2 py-1.5 text-end font-mono text-xs"
                />
              </div>
            ))}
          </div>

          <div className="grid gap-3 sm:grid-cols-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
            <div>
              <label className="mb-1 block text-xs font-semibold">
                {locale === "ar" ? "المراجع (يجب أن يختلف عن المنفّذ)" : "Reviewer (must differ from producer)"}
              </label>
              <select
                value={revProvider}
                onChange={(e) => setRevProvider(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-surface px-3 py-2 text-sm"
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.id})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold">{locale === "ar" ? "مستوى المراجع" : "Reviewer Tier"}</label>
              <select
                value={revTier}
                onChange={(e) => setRevTier(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-surface px-3 py-2 text-sm"
              >
                <option value="mid">mid</option>
                <option value="strong">strong</option>
                <option value="cheap">cheap</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold">{locale === "ar" ? "موديل المراجع" : "Reviewer Model"}</label>
              <input
                type="text"
                dir="ltr"
                value={revModel}
                onChange={(e) => setRevModel(e.target.value)}
                placeholder="auto"
                className="w-full rounded-lg border border-borderline bg-surface px-3 py-2 text-end font-mono text-sm"
              />
            </div>
          </div>
        </div>

        <button type="submit" className="inline-flex min-h-[44px] items-center gap-2 rounded-lg bg-primary px-5 text-sm font-semibold text-white">
          <Save className="h-4 w-4" />
          <span>{locale === "ar" ? "حفظ الإعدادات" : "Save Settings"}</span>
        </button>
      </form>
    </div>
  );
}
