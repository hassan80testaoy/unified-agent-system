"use client";

import React, { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Lock, CheckCircle2 } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface VaultMeta {
  status: string;
  kind: string;
  label: string;
  allowed_sites: string[];
  reason: string;
  expires_at: string;
}

export default function VaultSubmissionPage() {
  const params = useParams<{ token: string }>();
  const { locale } = useApp();
  const [meta, setMeta] = useState<VaultMeta | null>(null);
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch<VaultMeta>(`/api/vault/request/${params.token}`)
      .then(setMeta)
      .catch(() => setError(locale === "ar" ? "رابط الخزنة غير صالح أو منتهي الصلاحية." : "Invalid or expired vault link."));
  }, [params.token, locale]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await apiFetch(`/api/vault/submit/${params.token}`, {
        method: "POST",
        body: JSON.stringify({ username, secret }),
      });
      setSecret("");
      setDone(true);
    } catch {
      setError(locale === "ar" ? "فشل حفظ السر في الخزنة." : "Failed to save vault secret.");
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-borderline bg-surface p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-2">
          <Lock className="h-5 w-5 text-primary" />
          <h1 className="text-base font-bold">
            {locale === "ar" ? "إدخال آمن لمرة واحدة (Blind-Fill Vault)" : "One-Time Blind-Fill Vault"}
          </h1>
        </div>

        {done ? (
          <div className="space-y-3 rounded-xl border border-success/30 bg-success/10 p-4 text-sm text-success">
            <div className="flex items-center gap-2 font-semibold">
              <CheckCircle2 className="h-5 w-5" />
              <span>{locale === "ar" ? "تم الحفظ وتشفير البيانات بنجاح." : "Encrypted and stored securely."}</span>
            </div>
            <p className="text-xs text-muted">
              {locale === "ar"
                ? "تم إبطال هذا الرابط ولن يظهر السر في أي شات أو سجل."
                : "This one-time link is now closed. Secrets are never exposed to models or logs."}
            </p>
          </div>
        ) : error ? (
          <div className="rounded-xl border border-danger/30 bg-danger/10 p-4 text-sm text-danger">{error}</div>
        ) : !meta ? (
          <div className="text-sm text-muted">...</div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="rounded-lg border border-borderline bg-canvas p-3 text-xs">
              <div className="font-semibold">{meta.label}</div>
              <div className="text-muted">{meta.reason}</div>
              {meta.allowed_sites.length > 0 && (
                <div className="mt-1">
                  {locale === "ar" ? "النطاقات المسموحة:" : "Pinned domains:"}{" "}
                  <bdi dir="ltr" className="font-mono">
                    {meta.allowed_sites.join(", ")}
                  </bdi>
                </div>
              )}
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">
                {locale === "ar" ? "البريد أو اسم المستخدم" : "Username / Email"}
              </label>
              <input
                type="text"
                dir="ltr"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-end text-sm outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">
                {locale === "ar" ? "السر / كلمة المرور (Write-only)" : "Secret / Password (Write-only)"}
              </label>
              <input
                type="password"
                dir="ltr"
                required
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-end text-sm outline-none focus:border-primary"
              />
            </div>
            <button type="submit" className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white">
              {locale === "ar" ? "حفظ مشفر في الخزنة" : "Save Encrypted to Vault"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
