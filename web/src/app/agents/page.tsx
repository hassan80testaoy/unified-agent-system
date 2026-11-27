"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { RefreshCw, Settings, Play, Sparkles } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface AgentCard {
  id: string;
  key: string;
  name_ar: string;
  name_en: string;
  version: string;
  description_ar: string;
  description_en: string;
  daily_limit: number;
  daily_budget_usd: number;
  enabled: boolean;
  priority: string;
  tools_allowed: string[];
}

interface CatalogEntry {
  key: string;
  name_ar: string;
  name_en: string;
  priority: string;
  category: string;
  description_ar: string;
  status: string;
}

export default function AgentsPage() {
  const { t, locale, formatNum } = useApp();
  const [agents, setAgents] = useState<AgentCard[]>([]);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [skillsSummary, setSkillsSummary] = useState<{ platform_skills_count: number; site_playbooks_count: number } | null>(null);
  const [discovering, setDiscovering] = useState(false);

  const load = () => {
    apiFetch<{ agents: AgentCard[]; catalog: CatalogEntry[] }>("/api/agents").then((res) => {
      setAgents(res.agents);
      setCatalog(res.catalog);
    });
    apiFetch<{ platform_skills_count: number; site_playbooks_count: number }>("/api/skills")
      .then(setSkillsSummary)
      .catch(() => null);
  };

  useEffect(() => {
    load();
  }, []);

  const handleDiscover = async () => {
    setDiscovering(true);
    try {
      await apiFetch("/api/agents/discover", { method: "POST" });
      load();
    } finally {
      setDiscovering(false);
    }
  };

  const toggleEnabled = async (agent: AgentCard) => {
    await apiFetch(`/api/agents/${agent.id}/settings`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !agent.enabled }),
    });
    load();
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">{t("nav.agents")}</h1>
          {skillsSummary && (
            <p className="text-xs text-muted">
              {locale === "ar"
                ? `نواة Instinct: ${formatNum(skillsSummary.platform_skills_count)} مهارة منصة + ${formatNum(skillsSummary.site_playbooks_count)} دليل مواقع`
                : `Instinct core: ${skillsSummary.platform_skills_count} platform skills + ${skillsSummary.site_playbooks_count} site playbooks`}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={handleDiscover}
          disabled={discovering}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-borderline bg-surface px-4 text-sm font-semibold hover:border-primary"
        >
          <RefreshCw className={`h-4 w-4 ${discovering ? "animate-spin" : ""}`} />
          <span>{locale === "ar" ? "اكتشف الوكلاء (Plugins)" : "Discover Plugins"}</span>
        </button>
      </div>

      {/* Installed plugin agents */}
      <div className="grid gap-4 md:grid-cols-2">
        {agents.map((a) => (
          <div key={a.id} className="rounded-xl border border-borderline bg-surface p-4 flex flex-col justify-between gap-4">
            <div>
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h2 className="text-base font-bold">{locale === "ar" ? a.name_ar : a.name_en}</h2>
                  <bdi dir="ltr" className="font-mono text-xs text-muted">
                    {a.key} · v{a.version} · {a.priority}
                  </bdi>
                </div>
                <StatusBadge status={a.enabled ? "active" : "disabled"} />
              </div>
              <p className="mt-2 text-xs text-muted">{locale === "ar" ? a.description_ar : a.description_en}</p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {a.tools_allowed.map((tool) => (
                  <span key={tool} className="rounded bg-canvas px-2 py-0.5 font-mono text-[11px] text-muted">
                    {tool}
                  </span>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-borderline pt-3 text-xs">
              <div className="text-muted">
                {locale === "ar" ? "الحد اليومي:" : "Daily limit:"} {formatNum(a.daily_limit)} ·{" "}
                <bdi dir="ltr">${a.daily_budget_usd}</bdi>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => toggleEnabled(a)}
                  className="rounded-lg border border-borderline px-3 py-1.5 font-semibold hover:bg-canvas"
                >
                  {a.enabled ? (locale === "ar" ? "إيقاف" : "Disable") : locale === "ar" ? "تفعيل" : "Enable"}
                </button>
                <Link
                  href={`/agents/${a.id}`}
                  className="inline-flex items-center gap-1 rounded-lg border border-borderline px-3 py-1.5 font-semibold hover:bg-canvas"
                >
                  <Settings className="h-3.5 w-3.5" />
                  <span>{locale === "ar" ? "التفويض والحدود" : "Delegation"}</span>
                </Link>
                <Link
                  href="/tasks/new"
                  className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 font-semibold text-white"
                >
                  <Play className="h-3.5 w-3.5" />
                  <span>{locale === "ar" ? "مهمة" : "Task"}</span>
                </Link>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Upcoming 20-Agent SEO Catalog */}
      <div className="space-y-3">
        <h2 className="flex items-center gap-2 text-base font-bold">
          <Sparkles className="h-4 w-4 text-primary" />
          <span>{locale === "ar" ? "كتالوج الوكلاء المقترحة (قريباً — Phase 2–5)" : "Upcoming SEO Agent Catalog (Phases 2–5)"}</span>
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {catalog.map((c) => (
            <div key={c.key} className="rounded-xl border border-dashed border-borderline bg-surface/60 p-3 text-xs">
              <div className="flex items-center justify-between font-bold">
                <span>{locale === "ar" ? c.name_ar : c.name_en}</span>
                <span className="rounded bg-canvas px-1.5 py-0.5 font-mono text-[10px]">{c.priority}</span>
              </div>
              <p className="mt-1.5 text-muted">{c.description_ar}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
