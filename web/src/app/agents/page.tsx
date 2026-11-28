"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { RefreshCw, Settings, Play, Sparkles, Plus, Pause, Trash2, GitBranch, Search, Ban } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface CustomAgentItem {
  id: string;
  key: string;
  name_ar: string;
  name_en: string;
  description_ar: string;
  mode_key: string;
  skills: string[];
  is_custom: boolean;
  status: string;
  enabled: boolean;
}

interface DelegationItem {
  id: string;
  parent_agent_id: string;
  child_agent_id: string;
  trust_bundle_id: string | null;
  depth: number;
  prompt: string;
  status: string;
  result_summary: string;
}

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

  // §8F Custom Named Agents + i2i Delegations
  const [customAgents, setCustomAgents] = useState<CustomAgentItem[]>([]);
  const [delegations, setDelegations] = useState<DelegationItem[]>([]);
  const [newAgentName, setNewAgentName] = useState("");
  const [newAgentDesc, setNewAgentDesc] = useState("");
  const [newAgentSkills, setNewAgentSkills] = useState("");
  const [delParentId, setDelParentId] = useState("lead");
  const [delChildId, setDelChildId] = useState("");
  const [delPrompt, setDelPrompt] = useState("");
  const [delSearch, setDelSearch] = useState("");

  const load = () => {
    apiFetch<{ agents: AgentCard[]; catalog: CatalogEntry[] }>("/api/agents").then((res) => {
      setAgents(res.agents);
      setCatalog(res.catalog);
    });
    apiFetch<{ platform_skills_count: number; site_playbooks_count: number }>("/api/skills")
      .then(setSkillsSummary)
      .catch(() => null);
    apiFetch<{ agents: CustomAgentItem[] }>("/api/agents/custom")
      .then((res) => setCustomAgents(res.agents || []))
      .catch(() => null);
    apiFetch<{ delegations: DelegationItem[] }>(`/api/agents/delegations?q=${encodeURIComponent(delSearch)}`)
      .then((res) => setDelegations(res.delegations || []))
      .catch(() => null);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCreateCustomAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newAgentName.trim()) return;
    await apiFetch("/api/agents/custom", {
      method: "POST",
      body: JSON.stringify({
        name: newAgentName.trim(),
        description: newAgentDesc.trim(),
        skills: newAgentSkills
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      }),
    });
    setNewAgentName("");
    setNewAgentDesc("");
    setNewAgentSkills("");
    load();
  };

  const handleTogglePauseCustom = async (ag: CustomAgentItem) => {
    await apiFetch(`/api/agents/custom/${ag.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: ag.status === "paused" ? "idle" : "paused" }),
    });
    load();
  };

  const handleDeleteCustom = async (id: string) => {
    await apiFetch(`/api/agents/custom/${id}`, { method: "DELETE" });
    load();
  };

  const handleDelegate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!delChildId.trim() || !delPrompt.trim()) return;
    await apiFetch("/api/agents/delegate", {
      method: "POST",
      body: JSON.stringify({
        parent_agent_id: delParentId.trim() || "lead",
        child_agent_id: delChildId.trim(),
        prompt: delPrompt.trim(),
      }),
    });
    setDelPrompt("");
    load();
  };

  const handleCancelDelegation = async (id: string) => {
    await apiFetch(`/api/agents/delegations/${id}/cancel`, { method: "POST" });
    load();
  };

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

      {/* §8F Custom Named Agents CRUD + Inter-Agent Delegation (i2i) */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="bento-card space-y-3 p-4 text-xs">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Plus className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "إنشاء وإدارة الوكلاء المخصصين" : "Custom Named Agents"}</span>
          </h2>
          <form onSubmit={handleCreateCustomAgent} className="space-y-2">
            <div className="grid gap-2 sm:grid-cols-2">
              <input
                value={newAgentName}
                onChange={(e) => setNewAgentName(e.target.value)}
                placeholder={locale === "ar" ? "اسم الوكيل (مثلاً: باحث عقاري)" : "Agent name"}
                className="rounded-lg border border-borderline bg-canvas px-2.5 py-1.5"
              />
              <input
                value={newAgentSkills}
                onChange={(e) => setNewAgentSkills(e.target.value)}
                placeholder={locale === "ar" ? "المهارات: deep-research,files" : "skills: deep-research,files"}
                className="rounded-lg border border-borderline bg-canvas px-2.5 py-1.5"
              />
            </div>
            <div className="flex gap-2">
              <input
                value={newAgentDesc}
                onChange={(e) => setNewAgentDesc(e.target.value)}
                placeholder={locale === "ar" ? "وصف الدور..." : "Role description..."}
                className="flex-1 rounded-lg border border-borderline bg-canvas px-2.5 py-1.5"
              />
              <button type="submit" className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-white">
                {locale === "ar" ? "إنشاء" : "Create"}
              </button>
            </div>
          </form>

          <div className="space-y-2 pt-1">
            {customAgents.filter((a) => a.is_custom).map((ag) => (
              <div key={ag.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-borderline bg-canvas p-2.5">
                <div>
                  <div className="font-bold">{locale === "ar" ? ag.name_ar : ag.name_en} <span className="font-mono text-[10px] text-primary">({ag.key})</span></div>
                  <div className="text-[11px] text-muted">{ag.description_ar} · status: {ag.status}</div>
                </div>
                <div className="flex items-center gap-1.5">
                  <button type="button" onClick={() => handleTogglePauseCustom(ag)} className="rounded border border-borderline p-1.5 hover:border-primary" title={ag.status === "paused" ? "Resume" : "Pause"}>
                    {ag.status === "paused" ? <Play className="h-3.5 w-3.5 text-success" /> : <Pause className="h-3.5 w-3.5 text-warning" />}
                  </button>
                  <button type="button" onClick={() => handleDeleteCustom(ag.id)} className="rounded border border-borderline p-1.5 text-danger hover:bg-danger/10">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="bento-card space-y-3 p-4 text-xs">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <GitBranch className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "تفويض المهام بين الوكلاء (Signed I2IBundle)" : "Inter-Agent Delegation (I2I)"}</span>
          </h2>
          <form onSubmit={handleDelegate} className="space-y-2">
            <div className="grid gap-2 sm:grid-cols-2">
              <input
                value={delParentId}
                onChange={(e) => setDelParentId(e.target.value)}
                placeholder="Parent agent id/key (lead)"
                className="rounded-lg border border-borderline bg-canvas px-2.5 py-1.5"
              />
              <select
                value={delChildId}
                onChange={(e) => setDelChildId(e.target.value)}
                className="rounded-lg border border-borderline bg-canvas px-2.5 py-1.5"
              >
                <option value="">{locale === "ar" ? "اختر الوكيل الفرعي..." : "Select target agent..."}</option>
                {customAgents.map((a) => (
                  <option key={a.id} value={a.id}>{locale === "ar" ? a.name_ar : a.name_en} ({a.key})</option>
                ))}
              </select>
            </div>
            <div className="flex gap-2">
              <input
                value={delPrompt}
                onChange={(e) => setDelPrompt(e.target.value)}
                placeholder={locale === "ar" ? "وصف المهمة المفوضة..." : "Delegated prompt..."}
                className="flex-1 rounded-lg border border-borderline bg-canvas px-2.5 py-1.5"
              />
              <button type="submit" className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-white">
                {locale === "ar" ? "تفويض" : "Delegate"}
              </button>
            </div>
          </form>

          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute top-1/2 start-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
              <input
                value={delSearch}
                onChange={(e) => setDelSearch(e.target.value)}
                placeholder={locale === "ar" ? "بحث في سجل التفويضات..." : "Search delegations..."}
                className="w-full rounded-lg border border-borderline bg-canvas ps-8 pe-2.5 py-1"
              />
            </div>
            <button type="button" onClick={load} className="rounded-lg border border-borderline px-2.5 py-1">
              {locale === "ar" ? "بحث" : "Filter"}
            </button>
          </div>

          <div className="max-h-48 space-y-1.5 overflow-y-auto">
            {delegations.map((d) => (
              <div key={d.id} className="flex items-start justify-between gap-2 rounded-lg border border-borderline bg-canvas p-2">
                <div className="space-y-0.5">
                  <div className="font-mono text-[10px] text-primary">
                    {d.parent_agent_id.slice(0, 8)} → {d.child_agent_id.slice(0, 8)} · depth:{d.depth} · {d.status}
                    {d.trust_bundle_id ? ` · i2i:${d.trust_bundle_id.slice(0, 8)}` : ""}
                  </div>
                  <div>{d.prompt}</div>
                  {d.result_summary && <div className="text-[11px] text-muted">{d.result_summary.slice(0, 140)}</div>}
                </div>
                {d.status !== "canceled" && (
                  <button type="button" onClick={() => handleCancelDelegation(d.id)} className="rounded border border-danger/30 p-1 text-danger" title="Cancel">
                    <Ban className="h-3 w-3" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
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
