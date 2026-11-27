"use client";

import React, { useEffect, useState } from "react";
import { KeyRound, Shield, Cpu, Brain, Globe2, Trash2, CheckCircle2 } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface ProviderItem {
  id: string;
  name: string;
  kind: string;
  api_base: string;
  budget_usd_daily: number;
  budget_usd_monthly: number;
  enabled: boolean;
  has_key: boolean;
  key_last4: string;
}

interface GoogleAccountItem {
  id: string;
  label: string;
  email_masked: string;
  oauth_scopes: string;
  imap_ok: boolean;
  oauth_ok: boolean;
  status: string;
  active_profile_id: string | null;
}

interface MemoryItem {
  id: string;
  category: string;
  content: string;
  allow_training: boolean;
}

export default function SettingsPage() {
  const { t, locale, numeralStyle, setNumeralStyle, calendarType, setCalendarType } = useApp();

  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccountItem[]>([]);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [tunnelCmd, setTunnelCmd] = useState("");
  const [keyEdits, setKeyEdits] = useState<Record<string, string>>({});
  const [statusMsg, setStatusMsg] = useState("");

  // Google Account Vault form
  const [gLabel, setGLabel] = useState("");
  const [gEmail, setGEmail] = useState("");
  const [gPassword, setGPassword] = useState("");
  const [gAllowSend, setGAllowSend] = useState(false);

  // Memory add
  const [memText, setMemText] = useState("");

  const load = () => {
    apiFetch<{ providers: ProviderItem[] }>("/api/providers").then((r) => setProviders(r.providers)).catch(() => null);
    apiFetch<{ google_accounts: GoogleAccountItem[] }>("/api/google-accounts").then((r) => setGoogleAccounts(r.google_accounts)).catch(() => null);
    apiFetch<{ memory: MemoryItem[] }>("/api/memory").then((r) => setMemories(r.memory)).catch(() => null);
    apiFetch<{ tunnel: { ssh_command: string } }>("/api/settings").then((r) => setTunnelCmd(r.tunnel?.ssh_command || "")).catch(() => null);
  };

  useEffect(() => {
    load();
  }, []);

  const saveProviderKey = async (providerId: string) => {
    const val = (keyEdits[providerId] || "").trim();
    if (!val) return;
    await apiFetch(`/api/settings/keys/${providerId}`, {
      method: "PUT",
      body: JSON.stringify({ secret: val }),
    });
    setKeyEdits((prev) => ({ ...prev, [providerId]: "" }));
    setStatusMsg(locale === "ar" ? `تم حفظ مفتاح ${providerId} مشفراً.` : `Saved encrypted key for ${providerId}.`);
    load();
  };

  const toggleProvider = async (p: ProviderItem) => {
    await apiFetch(`/api/providers/${p.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !p.enabled }),
    });
    load();
  };

  const testProvider = async (providerId: string) => {
    const res = await apiFetch<{ ok: boolean; error?: string; latency_ms?: number }>(`/api/providers/${providerId}/test`, {
      method: "POST",
    });
    setStatusMsg(res.ok ? `${providerId}: OK (${res.latency_ms ?? 0}ms)` : `${providerId}: ${res.error}`);
  };

  const addGoogleAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    await apiFetch("/api/google-accounts", {
      method: "POST",
      body: JSON.stringify({
        label: gLabel || "حساب جوجل",
        email: gEmail,
        password: gPassword || null,
        allow_gmail_send: gAllowSend,
      }),
    });
    setGLabel("");
    setGEmail("");
    setGPassword("");
    setStatusMsg(locale === "ar" ? "تم حفظ حساب Google مشفراً مع بروفايل متصفح مخصص." : "Google account encrypted & profile initialized.");
    load();
  };

  const addMemoryFact = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!memText.trim()) return;
    await apiFetch("/api/memory", {
      method: "POST",
      body: JSON.stringify({ category: "preference", content: memText, allow_training: false }),
    });
    setMemText("");
    load();
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">{t("nav.settings")}</h1>

      {statusMsg && (
        <div className="flex items-center gap-2 rounded-xl border border-success/30 bg-success/10 p-3 text-xs font-semibold text-success">
          <CheckCircle2 className="h-4 w-4" />
          <span>{statusMsg}</span>
        </div>
      )}

      {/* 1. Google Accounts (§2.3) */}
      <section className="rounded-xl border border-borderline bg-surface p-4 space-y-4">
        <div>
          <h2 className="flex items-center gap-2 text-base font-bold">
            <Shield className="h-4 w-4 text-primary" />
            <span>{t("google.title")}</span>
          </h2>
          <p className="mt-1 text-xs text-muted">
            {locale === "ar"
              ? "يُستخدم الحساب فقط للتسجيل/الدخول وقراءة رسائل التفعيل في المواقع التي تحددها أنت. لا يغيّر الوكيل كلمة المرور أو إعدادات الأمان أبداً."
              : "Used strictly to sign up/in and read verification mail on sites you assign. Never alters security settings."}
          </p>
        </div>

        <form onSubmit={addGoogleAccount} className="grid gap-3 rounded-xl border border-borderline bg-canvas p-4 sm:grid-cols-2">
          <div className="sm:col-span-2 text-xs font-bold">{t("google.add_title")}</div>
          <input
            type="text"
            required
            value={gLabel}
            onChange={(e) => setGLabel(e.target.value)}
            placeholder={t("google.label_field")}
            className="rounded-lg border border-borderline bg-surface px-3 py-2 text-xs"
          />
          <input
            type="email"
            dir="ltr"
            required
            value={gEmail}
            onChange={(e) => setGEmail(e.target.value)}
            placeholder="name@gmail.com"
            className="rounded-lg border border-borderline bg-surface px-3 py-2 text-end text-xs"
          />
          <input
            type="password"
            dir="ltr"
            value={gPassword}
            onChange={(e) => setGPassword(e.target.value)}
            placeholder={t("google.password_field")}
            className="rounded-lg border border-borderline bg-surface px-3 py-2 text-end text-xs"
          />
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={gAllowSend} onChange={(e) => setGAllowSend(e.target.checked)} />
            <span>{locale === "ar" ? "السماح بإرسال تحقق فقط (افتراضي مغلق)" : "Allow verification send (default OFF)"}</span>
          </label>
          <button type="submit" className="min-h-[40px] rounded-lg bg-primary px-4 text-xs font-semibold text-white sm:col-span-2">
            {t("common.save")}
          </button>
        </form>

        <div className="space-y-2">
          {googleAccounts.map((g) => (
            <div key={g.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-borderline bg-canvas p-3 text-xs">
              <div>
                <div className="font-bold">
                  {g.label} — <bdi dir="ltr" className="font-mono">{g.email_masked}</bdi>
                </div>
                <div className="text-muted">
                  scopes: <bdi dir="ltr">{g.oauth_scopes}</bdi> · profile: <bdi dir="ltr">{g.active_profile_id || "—"}</bdi>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={g.status} />
                <button
                  onClick={() => apiFetch(`/api/google-accounts/${g.id}/test`, { method: "POST" }).then(load)}
                  className="rounded border border-borderline bg-surface px-2.5 py-1"
                >
                  {t("common.test")}
                </button>
                <button
                  onClick={() => apiFetch(`/api/google-accounts/${g.id}/revoke-session`, { method: "POST" }).then(load)}
                  className="rounded border border-warning/40 bg-surface px-2.5 py-1 text-warning"
                >
                  {t("google.revoke_session")}
                </button>
                <button
                  onClick={() => apiFetch(`/api/google-accounts/${g.id}/disconnect`, { method: "POST" }).then(load)}
                  className="rounded border border-danger/40 bg-surface px-2.5 py-1 text-danger"
                >
                  {t("common.disconnect")}
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* 2. Multi-Provider Registry & API Keys */}
      <section className="rounded-xl border border-borderline bg-surface p-4 space-y-4">
        <div>
          <h2 className="flex items-center gap-2 text-base font-bold">
            <Cpu className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "مزودو الذكاء الاصطناعي والراوتر (API Keys فقط)" : "Multi-Provider LLM Registry (API Keys Only)"}</span>
          </h2>
          <p className="mt-1 text-xs text-muted">
            {locale === "ar"
              ? "مفاتيح الـ API تُحفظ مشفرة Write-only (يظهر آخر 4 أحرف فقط). لا يتم استخدام اشتراكات Claude/ChatGPT الشخصية كباك إند."
              : "Write-only Fernet encrypted API keys (last 4 shown). Subscriptions are never used as backend providers."}
          </p>
        </div>

        <div className="space-y-2">
          {providers.map((p) => (
            <div key={p.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-borderline bg-canvas p-3 text-xs">
              <div>
                <div className="font-bold">
                  {p.name} (<bdi dir="ltr" className="font-mono">{p.id}</bdi>)
                </div>
                <div className="text-muted">
                  kind: {p.kind} · daily cap: ${p.budget_usd_daily} · {p.has_key ? `key ****${p.key_last4}` : "no saved key"}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {p.kind === "llm" && (
                  <>
                    <input
                      type="password"
                      dir="ltr"
                      value={keyEdits[p.id] || ""}
                      onChange={(e) => setKeyEdits((prev) => ({ ...prev, [p.id]: e.target.value }))}
                      placeholder="API Key..."
                      className="w-40 rounded border border-borderline bg-surface px-2 py-1 text-end font-mono"
                    />
                    <button onClick={() => saveProviderKey(p.id)} className="rounded bg-primary px-2.5 py-1 text-white">
                      <KeyRound className="inline h-3 w-3 me-1" />
                      {t("common.save")}
                    </button>
                  </>
                )}
                <button onClick={() => testProvider(p.id)} className="rounded border border-borderline bg-surface px-2.5 py-1">
                  {t("common.test")}
                </button>
                <button onClick={() => toggleProvider(p)} className={`rounded px-2.5 py-1 font-semibold ${p.enabled ? "bg-success/15 text-success" : "bg-surface text-muted"}`}>
                  {p.enabled ? t("status.active") : t("status.disabled")}
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* 3. Manual CAPTCHA & noVNC Tunnel */}
      <section className="rounded-xl border border-borderline bg-surface p-4 space-y-2 text-xs">
        <h2 className="text-sm font-bold">{locale === "ar" ? "الكابتشا اليدوية (noVNC خلف SSH Tunnel فقط)" : "Manual CAPTCHA (noVNC behind SSH Tunnel)"}</h2>
        <div className="text-muted">CAPTCHA_SOLVER=manual · CAPTCHA_MAX_SPEND_USD=0</div>
        {tunnelCmd && (
          <div className="rounded-lg border border-borderline bg-canvas p-2.5 font-mono">
            <bdi dir="ltr">{tunnelCmd}</bdi>
          </div>
        )}
      </section>

      {/* 4. Memory & Locale / Numerals / Calendar */}
      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Brain className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "ذاكرة الوكيل المشفرة (تُصدّر وتُحذف بالكامل)" : "Encrypted Memory (Exportable & Deletable)"}</span>
          </h2>
          <form onSubmit={addMemoryFact} className="flex gap-2">
            <input
              type="text"
              value={memText}
              onChange={(e) => setMemText(e.target.value)}
              placeholder={locale === "ar" ? "أضف تفضيل أو حقيقة..." : "Add preference or fact..."}
              className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs"
            />
            <button type="submit" className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white">
              {t("common.save")}
            </button>
          </form>
          <div className="space-y-1.5">
            {memories.map((m) => (
              <div key={m.id} className="flex items-center justify-between rounded bg-canvas p-2 text-xs">
                <span>{m.content}</span>
                <button onClick={() => apiFetch(`/api/memory/${m.id}`, { method: "DELETE" }).then(load)} className="text-danger">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Globe2 className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "الأرقام والتقويم والمنطقة الزمنية" : "Numerals, Calendar & Timezone"}</span>
          </h2>
          <div>
            <label className="mb-1 block font-medium">{locale === "ar" ? "شكل الأرقام" : "Numeral Style"}</label>
            <select
              value={numeralStyle}
              onChange={(e) => setNumeralStyle(e.target.value as "western" | "arabic_indic")}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2"
            >
              <option value="western">0-9 (Western — الافتراضي للجداول)</option>
              <option value="arabic_indic">٠-٩ (Arabic-Indic)</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block font-medium">{locale === "ar" ? "التقويم" : "Calendar"}</label>
            <select
              value={calendarType}
              onChange={(e) => setCalendarType(e.target.value as "gregorian" | "hijri")}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2"
            >
              <option value="gregorian">{locale === "ar" ? "ميلادي (افتراضي)" : "Gregorian (Default)"}</option>
              <option value="hijri">{locale === "ar" ? "هجري" : "Hijri"}</option>
            </select>
          </div>
        </section>
      </div>
    </div>
  );
}
