"use client";

import React, { useEffect, useState } from "react";
import { Play, Plus, Sparkles, Terminal } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface CodedSkillSpec {
  slug: string;
  title_ar: string;
  category: string;
  requires_approval: boolean;
  schema: Record<string, unknown>;
  guide: string;
}

interface PromptSkillRow {
  id: string;
  slug: string;
  name: string;
  description: string;
  source: string;
  install_path: string;
  has_script: boolean;
  enabled: boolean;
}

export default function SkillsPage() {
  const { t, locale } = useApp();
  const isAr = locale === "ar";

  const [coded, setCoded] = useState<CodedSkillSpec[]>([]);
  const [promptSkills, setPromptSkills] = useState<PromptSkillRow[]>([]);
  const [skillPrompt, setSkillPrompt] = useState("");
  const [improveNotes, setImproveNotes] = useState<Record<string, string>>({});
  const [execJson, setExecJson] = useState<Record<string, string>>({});
  const [execOut, setExecOut] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");

  const load = async () => {
    const res = await apiFetch<{ coded_skills: CodedSkillSpec[]; prompt_skills: PromptSkillRow[] }>("/api/skills/coded");
    setCoded(res.coded_skills || []);
    setPromptSkills(res.prompt_skills || []);
  };

  useEffect(() => {
    load().catch(() => null);
  }, []);

  const handleRegisterSkill = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!skillPrompt.trim()) return;
    const res = await apiFetch<{ skill: { name: string; slug: string } }>("/api/skills/register-from-prompt", {
      method: "POST",
      body: JSON.stringify({ prompt: skillPrompt.trim() }),
    });
    setSkillPrompt("");
    setNotice(
      isAr
        ? `تم إنشاء المهارة "${res.skill.name}" (${res.skill.slug}) وهي جاهزة في المحادثة التالية.`
        : `Registered prompt skill "${res.skill.name}" (${res.skill.slug}) for next chat turn.`
    );
    await load();
  };

  const handleImprove = async (slug: string) => {
    const note = (improveNotes[slug] || "").trim();
    if (!note) return;
    await apiFetch(`/api/skills/${slug}/improve`, {
      method: "POST",
      body: JSON.stringify({ note }),
    });
    setImproveNotes((prev) => ({ ...prev, [slug]: "" }));
    setNotice(isAr ? `تم تحسين المهارة ${slug}` : `Updated skill ${slug}`);
    await load();
  };

  const handleExecuteCoded = async (slug: string) => {
    let parsed: Record<string, unknown> = {};
    const raw = (execJson[slug] || "{}").trim();
    try {
      parsed = JSON.parse(raw || "{}");
    } catch {
      setExecOut((prev) => ({ ...prev, [slug]: "Invalid JSON params" }));
      return;
    }
    try {
      const res = await apiFetch<Record<string, unknown>>(`/api/skills/coded/${slug}/execute`, {
        method: "POST",
        body: JSON.stringify({ params: parsed }),
      });
      setExecOut((prev) => ({ ...prev, [slug]: JSON.stringify(res, null, 2) }));
    } catch (err) {
      setExecOut((prev) => ({ ...prev, [slug]: String(err) }));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold">
          <Sparkles className="h-5 w-5 text-primary" />
          <span>{t("phase8.skills_title")}</span>
        </h1>
        <p className="text-xs text-muted">{t("phase8.skills_sub")}</p>
      </div>

      {notice && (
        <div className="rounded-xl border border-primary/30 bg-primary/5 px-4 py-2 text-xs text-primary">{notice}</div>
      )}

      {/* Register Skill by Prompt */}
      <form onSubmit={handleRegisterSkill} className="bento-card space-y-3 p-4 text-xs">
        <div className="flex items-center gap-2 text-sm font-bold">
          <Plus className="h-4 w-4 text-primary" />
          <span>{isAr ? "تسجيل مهارة جديدة بالوصف (تنشئ SKILL.md + skill.py + tests)" : "Register Skill by Prompt"}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            value={skillPrompt}
            onChange={(e) => setSkillPrompt(e.target.value)}
            placeholder={
              isAr
                ? "مثال: ملخص اجتماعات: عند ذكر تلخيص اجتماع، أنشئ قائمة مهام مع المسؤول والتاريخ"
                : "Example: meeting-brief: Summarize meeting notes into owners and deadlines"
            }
            className="min-w-[260px] flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2"
          />
          <button type="submit" className="rounded-lg bg-primary px-4 py-2 font-semibold text-white">
            {isAr ? "تسجيل وتفعيل فوري" : "Register Skill"}
          </button>
        </div>
      </form>

      {/* Prompt-Registered Custom Skills */}
      {promptSkills.length > 0 && (
        <div className="bento-card space-y-3 p-4 text-xs">
          <div className="text-sm font-bold">{isAr ? "المهارات المسجلة بالوصف (Closed Learning Loop)" : "Prompt-Registered Skills"}</div>
          <div className="grid gap-3 md:grid-cols-2">
            {promptSkills.map((sk) => (
              <div key={sk.id} className="space-y-2 rounded-xl border border-borderline bg-canvas p-3">
                <div className="flex items-center justify-between font-bold">
                  <span>{sk.name}</span>
                  <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-[10px] text-primary">{sk.slug}</span>
                </div>
                <p className="text-muted">{sk.description}</p>
                <div className="flex gap-2">
                  <input
                    value={improveNotes[sk.slug] || ""}
                    onChange={(e) => setImproveNotes((prev) => ({ ...prev, [sk.slug]: e.target.value }))}
                    placeholder={isAr ? "ملاحظة تحسين المهارة..." : "Self-improvement note..."}
                    className="flex-1 rounded-lg border border-borderline bg-surface px-2.5 py-1"
                  />
                  <button
                    type="button"
                    onClick={() => handleImprove(sk.slug)}
                    className="rounded-lg border border-primary/40 px-2.5 py-1 font-semibold text-primary hover:bg-primary/10"
                  >
                    {isAr ? "تطوير" : "Improve"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 13 Coded Platform Skills */}
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm font-bold">
          <Terminal className="h-4 w-4 text-primary" />
          <span>{isAr ? `المهارات البرمجية المدمجة (${coded.length})` : `Built-in Coded Platform Skills (${coded.length})`}</span>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {coded.map((c) => (
            <div key={c.slug} className="bento-card space-y-2.5 p-4 text-xs">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <span className="text-sm font-bold">{c.title_ar}</span>
                  <span className="ms-2 font-mono text-[11px] text-primary">{c.slug}</span>
                </div>
                <span className="rounded-full border border-borderline bg-canvas px-2 py-0.5 text-[10px]">
                  {c.category} {c.requires_approval ? "· approval" : ""}
                </span>
              </div>
              <p className="text-muted">{c.guide}</p>
              <div className="flex gap-2">
                <input
                  dir="ltr"
                  value={execJson[c.slug] || "{}"}
                  onChange={(e) => setExecJson((prev) => ({ ...prev, [c.slug]: e.target.value }))}
                  className="flex-1 rounded-lg border border-borderline bg-canvas px-2.5 py-1.5 font-mono text-[11px]"
                />
                <button
                  type="button"
                  onClick={() => handleExecuteCoded(c.slug)}
                  className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 font-semibold text-white"
                >
                  <Play className="h-3.5 w-3.5" />
                  <span>{isAr ? "تشغيل" : "Run"}</span>
                </button>
              </div>
              {execOut[c.slug] && (
                <pre dir="ltr" className="max-h-36 overflow-auto rounded-lg border border-borderline bg-canvas p-2 font-mono text-[10px]">
                  {execOut[c.slug]}
                </pre>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
