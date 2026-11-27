"use client";

import React, { useEffect, useState } from "react";
import { Download, Eye, ListOrdered, X } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch, ApiError } from "@/lib/api";

interface AccountRow {
  id: string;
  domain: string;
  email_alias: string | null;
  username: string | null;
  has_password: boolean;
  profile_url: string | null;
  verified_at: string | null;
}

interface StepRow {
  n: number;
  action: string;
  selector_css: string | null;
  el_id: string | null;
  el_class: string | null;
  value_key: string | null;
  ok: boolean;
}

export default function BacklinkAccountsPage() {
  const { t, locale, formatDate } = useApp();
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [revealedPw, setRevealedPw] = useState<{ accountId: string; password: string } | null>(null);
  const [totpPromptId, setTotpPromptId] = useState<string | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [stepsAccountId, setStepsAccountId] = useState<string | null>(null);
  const [steps, setSteps] = useState<StepRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch<{ accounts: AccountRow[] }>("/api/backlinks/accounts").then((res) => setAccounts(res.accounts)).catch(() => null);
  }, []);

  const revealPassword = async (accountId: string, code?: string) => {
    setError("");
    try {
      const res = await apiFetch<{ password: string }>(`/api/backlinks/accounts/${accountId}/reveal`, {
        method: "POST",
        body: JSON.stringify({ totp_code: code || null }),
      });
      setRevealedPw({ accountId, password: res.password });
      setTotpPromptId(null);
      setTotpCode("");
    } catch (err) {
      const e2 = err as ApiError;
      if (e2.code === "totp_required") {
        setTotpPromptId(accountId);
      } else {
        setError(locale === "ar" ? e2.message_ar : e2.message_en);
      }
    }
  };

  const openSteps = async (accountId: string) => {
    const res = await apiFetch<{ steps: StepRow[] }>(`/api/backlinks/accounts/${accountId}/steps`);
    setStepsAccountId(accountId);
    setSteps(res.steps);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">{t("nav.backlink_accounts")}</h1>
          <p className="text-xs text-muted">
            {locale === "ar"
              ? "كلمات المرور مشفرة ولا تُصدّر في ملفات CSV أبداً؛ الإظهار يتم بتأكيد وتسجيل في audit_log."
              : "Passwords are encrypted and NEVER included in exports; reveal is audited."}
          </p>
        </div>
        <a
          href="/api/backlinks/export?table=accounts"
          className="inline-flex min-h-[40px] items-center gap-2 rounded-lg border border-borderline bg-surface px-3 text-xs font-semibold"
        >
          <Download className="h-4 w-4" />
          <span>{locale === "ar" ? "تصدير بدون باسوردات" : "Export CSV (No Passwords)"}</span>
        </a>
      </div>

      {error && <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-xs text-danger">{error}</div>}

      {totpPromptId && (
        <div className="rounded-xl border border-warning bg-amber-500/10 p-4 space-y-2 text-xs">
          <div className="font-bold">{locale === "ar" ? "أدخل رمز 2FA لكشف كلمة المرور:" : "Enter 2FA code to reveal password:"}</div>
          <div className="flex gap-2">
            <input
              type="text"
              dir="ltr"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              className="rounded border border-borderline bg-surface px-3 py-2 font-mono"
              placeholder="123456"
            />
            <button onClick={() => revealPassword(totpPromptId, totpCode)} className="rounded bg-primary px-4 py-2 font-semibold text-white">
              {t("common.approve")}
            </button>
          </div>
        </div>
      )}

      {revealedPw && (
        <div className="flex items-center justify-between rounded-xl border border-primary bg-primary/10 p-3 text-xs">
          <div>
            {locale === "ar" ? "كلمة المرور:" : "Password:"}{" "}
            <bdi dir="ltr" className="font-mono font-bold">
              {revealedPw.password}
            </bdi>
          </div>
          <button onClick={() => setRevealedPw(null)} className="rounded border border-borderline px-2 py-1">
            {locale === "ar" ? "إخفاء" : "Hide"}
          </button>
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-borderline bg-surface">
        <table className="w-full text-start text-xs">
          <thead className="border-b border-borderline bg-canvas text-muted">
            <tr>
              <th className="p-3 text-start">{locale === "ar" ? "الموقع" : "Site"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "البريد / Alias" : "Email / Alias"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "رابط البروفايل" : "Profile URL"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "التفعيل" : "Verified"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "إجراءات" : "Actions"}</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => (
              <tr key={a.id} className="border-b border-borderline last:border-0">
                <td className="p-3 font-mono font-semibold">
                  <bdi dir="ltr">{a.domain}</bdi>
                </td>
                <td className="p-3 font-mono">
                  <bdi dir="ltr">{a.email_alias || "—"}</bdi>
                </td>
                <td className="p-3 font-mono text-primary">
                  <bdi dir="ltr">{a.profile_url || "—"}</bdi>
                </td>
                <td className="p-3 text-muted">{formatDate(a.verified_at)}</td>
                <td className="p-3">
                  <div className="flex gap-1.5">
                    {a.has_password && (
                      <button
                        onClick={() => revealPassword(a.id)}
                        className="inline-flex items-center gap-1 rounded border border-borderline px-2 py-1 hover:border-primary"
                      >
                        <Eye className="h-3.5 w-3.5" />
                        <span>{locale === "ar" ? "إظهار" : "Reveal"}</span>
                      </button>
                    )}
                    <button
                      onClick={() => openSteps(a.id)}
                      className="inline-flex items-center gap-1 rounded border border-borderline px-2 py-1 hover:border-primary"
                    >
                      <ListOrdered className="h-3.5 w-3.5" />
                      <span>{locale === "ar" ? "الخطوات" : "Steps"}</span>
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {stepsAccountId && (
        <div className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold">{locale === "ar" ? "الخطوات المسجلة (bl_steps)" : "Recorded Steps (bl_steps)"}</h2>
            <div className="flex gap-2">
              <a
                href={`/api/backlinks/accounts/${stepsAccountId}/steps/export`}
                className="rounded border border-borderline px-3 py-1 text-xs font-semibold"
              >
                {locale === "ar" ? "تصدير JSON" : "Export JSON"}
              </a>
              <button onClick={() => setStepsAccountId(null)} className="rounded border border-borderline p-1">
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-start text-xs">
              <thead className="border-b border-borderline bg-canvas text-muted">
                <tr>
                  <th className="p-2 text-start">#</th>
                  <th className="p-2 text-start">action</th>
                  <th className="p-2 text-start">selector / id / class</th>
                  <th className="p-2 text-start">value_key</th>
                </tr>
              </thead>
              <tbody>
                {steps.map((s) => (
                  <tr key={s.n} className="border-b border-borderline last:border-0">
                    <td className="p-2">{s.n}</td>
                    <td className="p-2 font-mono">{s.action}</td>
                    <td className="p-2 font-mono">
                      <bdi dir="ltr">{s.selector_css || s.el_id || s.el_class || "—"}</bdi>
                    </td>
                    <td className="p-2 font-mono text-primary">{s.value_key || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
