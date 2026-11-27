"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck, KeyRound } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const { t, locale, toggleLocale } = useApp();

  const [initialized, setInitialized] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [workspaceName, setWorkspaceName] = useState("مكتبي للسيو");
  const [totpCode, setTotpCode] = useState("");
  const [totpSecret, setTotpSecret] = useState<{ secret: string; provisioning_uri: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiFetch<{ initialized: boolean }>("/api/auth/status")
      .then((res) => setInitialized(res.initialized))
      .catch(() => setInitialized(true));
  }, []);

  const handleSetup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = await apiFetch<{
        access_token: string;
        totp_setup: { secret: string; provisioning_uri: string } | null;
      }>("/api/auth/setup", {
        method: "POST",
        body: JSON.stringify({ email, password, workspace_name: workspaceName, enable_2fa: true }),
      });
      localStorage.setItem("uas_token", res.access_token);
      if (res.totp_setup) {
        setTotpSecret(res.totp_setup);
      } else {
        router.replace("/");
      }
    } catch (err) {
      const e2 = err as ApiError;
      setError(locale === "ar" ? e2.message_ar : e2.message_en);
    } finally {
      setBusy(false);
    }
  };

  const handleVerifyTotpSetup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await apiFetch("/api/auth/2fa/verify", {
        method: "POST",
        body: JSON.stringify({ totp_code: totpCode }),
      });
      router.replace("/");
    } catch (err) {
      const e2 = err as ApiError;
      setError(locale === "ar" ? e2.message_ar : e2.message_en);
    } finally {
      setBusy(false);
    }
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = await apiFetch<{ access_token: string }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password, totp_code: totpCode || null }),
      });
      localStorage.setItem("uas_token", res.access_token);
      router.replace("/");
    } catch (err) {
      const e2 = err as ApiError;
      setError(locale === "ar" ? e2.message_ar : e2.message_en);
    } finally {
      setBusy(false);
    }
  };

  if (initialized === null) {
    return <div className="flex min-h-screen items-center justify-center text-muted">{t("common.loading")}</div>;
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-borderline bg-surface p-6 shadow-sm">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-6 w-6 text-primary" />
            <h1 className="text-lg font-bold">
              {totpSecret ? t("auth.totp_bind_title") : initialized ? t("auth.login_title") : t("auth.setup_title")}
            </h1>
          </div>
          <button
            type="button"
            onClick={toggleLocale}
            className="rounded-lg border border-borderline px-2.5 py-1 text-xs font-semibold"
          >
            {t("topbar.lang_toggle")}
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">
            {error}
          </div>
        )}

        {totpSecret ? (
          <form onSubmit={handleVerifyTotpSetup} className="space-y-4">
            <p className="text-xs text-muted">{t("auth.totp_bind_hint")}</p>
            <div className="rounded-lg border border-borderline bg-canvas p-3">
              <div className="text-xs text-muted">Secret (Base32):</div>
              <bdi dir="ltr" className="block font-mono text-sm font-bold text-primary">
                {totpSecret.secret}
              </bdi>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">{t("auth.totp_code")}</label>
              <input
                type="text"
                dir="ltr"
                required
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                placeholder="123456"
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2.5 text-end font-mono text-sm outline-none focus:border-primary"
              />
            </div>
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={busy}
                className="flex-1 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white"
              >
                {t("auth.totp_verify_btn")}
              </button>
              <button
                type="button"
                onClick={() => router.replace("/")}
                className="rounded-lg border border-borderline px-4 py-2.5 text-sm text-muted"
              >
                {locale === "ar" ? "لاحقاً" : "Later"}
              </button>
            </div>
          </form>
        ) : !initialized ? (
          <form onSubmit={handleSetup} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium">{t("auth.workspace_name")}</label>
              <input
                type="text"
                required
                value={workspaceName}
                onChange={(e) => setWorkspaceName(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2.5 text-sm outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">{t("auth.email")}</label>
              <input
                type="email"
                dir="ltr"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="owner@yourdomain.com"
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2.5 text-end text-sm outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">{t("auth.password")}</label>
              <input
                type="password"
                dir="ltr"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2.5 text-end text-sm outline-none focus:border-primary"
              />
            </div>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white"
            >
              {t("auth.setup_submit")}
            </button>
          </form>
        ) : (
          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium">{t("auth.email")}</label>
              <input
                type="email"
                dir="ltr"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2.5 text-end text-sm outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">{t("auth.password")}</label>
              <input
                type="password"
                dir="ltr"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2.5 text-end text-sm outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="mb-1 flex items-center gap-1 text-xs font-medium">
                <KeyRound className="h-3.5 w-3.5 text-muted" />
                <span>{t("auth.totp_code")}</span>
              </label>
              <input
                type="text"
                dir="ltr"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                placeholder="000000"
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2.5 text-end font-mono text-sm outline-none focus:border-primary"
              />
            </div>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white"
            >
              {t("auth.login_submit")}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
