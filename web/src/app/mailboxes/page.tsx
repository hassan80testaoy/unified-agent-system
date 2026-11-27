"use client";

import React, { useEffect, useState } from "react";
import { Mail, RefreshCw, Trash2, Info } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch, ApiError } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface MailboxRow {
  id: string;
  email: string;
  provider: string;
  imap_host: string;
  imap_port: number;
  supports_plus: boolean;
  status: string;
  last_tested_at: string | null;
  last_error: string | null;
}

export default function MailboxesPage() {
  const { t, locale, formatDate } = useApp();
  const [mailboxes, setMailboxes] = useState<MailboxRow[]>([]);
  const [email, setEmail] = useState("");
  const [appPassword, setAppPassword] = useState("");
  const [imapHost, setImapHost] = useState("");
  const [testOnConnect, setTestOnConnect] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => {
    apiFetch<{ mailboxes: MailboxRow[] }>("/api/mailboxes").then((res) => setMailboxes(res.mailboxes)).catch(() => null);
  };

  useEffect(() => {
    load();
  }, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await apiFetch("/api/mailboxes", {
        method: "POST",
        body: JSON.stringify({
          email,
          app_password: appPassword,
          imap_host: imapHost || null,
          test_on_connect: testOnConnect,
        }),
      });
      setEmail("");
      setAppPassword("");
      setImapHost("");
      load();
    } catch (err) {
      const e2 = err as ApiError;
      setError(locale === "ar" ? e2.message_ar : e2.message_en);
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async (id: string) => {
    await apiFetch(`/api/mailboxes/${id}/test`, { method: "POST" });
    load();
  };

  const handleDisconnect = async (id: string) => {
    await apiFetch(`/api/mailboxes/${id}`, { method: "DELETE" });
    load();
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">{t("nav.mailboxes")}</h1>

      {error && <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-xs text-danger">{error}</div>}

      <div className="grid gap-6 lg:grid-cols-2">
        <form onSubmit={handleAdd} className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Mail className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "ربط صندوق بريد جديد (IMAP قراءة فقط)" : "Connect Mailbox (Read-Only IMAP)"}</span>
          </h2>
          <div>
            <label className="mb-1 block text-xs font-medium">{t("auth.email")}</label>
            <input
              type="email"
              dir="ltr"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="work@gmail.com"
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-end text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium">App Password</label>
            <input
              type="password"
              dir="ltr"
              required
              value={appPassword}
              onChange={(e) => setAppPassword(e.target.value)}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-end text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium">{locale === "ar" ? "خادم IMAP (اختياري — يُكتشف تلقائياً)" : "IMAP Host (auto-detected if empty)"}</label>
            <input
              type="text"
              dir="ltr"
              value={imapHost}
              onChange={(e) => setImapHost(e.target.value)}
              placeholder="imap.gmail.com"
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono text-xs"
            />
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={testOnConnect} onChange={(e) => setTestOnConnect(e.target.checked)} />
            <span>{locale === "ar" ? "اختبر فتح INBOX قراءة فقط فوراً" : "Test read-only INBOX on connect"}</span>
          </label>
          <button type="submit" disabled={busy} className="min-h-[44px] rounded-lg bg-primary px-5 text-sm font-semibold text-white">
            {t("common.test")} & {t("common.save")}
          </button>
        </form>

        <div className="rounded-xl border border-borderline bg-surface p-4 space-y-2 text-xs">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Info className="h-4 w-4 text-info" />
            <span>{locale === "ar" ? "إرشادات المزودين والأمان" : "Provider & Security Notes"}</span>
          </h2>
          <ul className="list-disc space-y-1.5 ps-4 text-muted">
            <li>{locale === "ar" ? "Gmail: يتطلب تفعيل التحقق بخطوتين ثم إنشاء App Password من حساب Google." : "Gmail: requires 2-Step Verification + an App Password."}</li>
            <li>{locale === "ar" ? "Outlook / Hotmail: قد تغلق مايكروسوفت الدخول بالباسورد على بعض حسابات العمل." : "Outlook: some Workspace accounts require OAuth."}</li>
            <li>{locale === "ar" ? "القراءة فقط (readonly=True): لا إرسال ولا حذف ولا تخزين للرسائل." : "Read-only INBOX: never sends or deletes emails."}</li>
            <li>{locale === "ar" ? "زر فصل الصندوق يمسح الباسورد المشفّر فوراً." : "Disconnect wipes the Fernet-encrypted password immediately."}</li>
          </ul>
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-borderline bg-surface">
        <table className="w-full text-start text-xs">
          <thead className="border-b border-borderline bg-canvas text-muted">
            <tr>
              <th className="p-3 text-start">{t("auth.email")}</th>
              <th className="p-3 text-start">IMAP</th>
              <th className="p-3 text-start">Plus Alias</th>
              <th className="p-3 text-start">{locale === "ar" ? "الحالة" : "Status"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "آخر اختبار" : "Tested"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "إجراءات" : "Actions"}</th>
            </tr>
          </thead>
          <tbody>
            {mailboxes.map((m) => (
              <tr key={m.id} className="border-b border-borderline last:border-0">
                <td className="p-3 font-mono font-semibold">
                  <bdi dir="ltr">{m.email}</bdi>
                </td>
                <td className="p-3 font-mono text-muted">
                  <bdi dir="ltr">
                    {m.imap_host}:{m.imap_port}
                  </bdi>
                </td>
                <td className="p-3">{m.supports_plus ? "✓ +alias" : "—"}</td>
                <td className="p-3">
                  <StatusBadge status={m.status} />
                  {m.last_error && <div className="mt-1 text-[11px] text-danger">{m.last_error}</div>}
                </td>
                <td className="p-3 text-muted">{formatDate(m.last_tested_at)}</td>
                <td className="p-3">
                  <div className="flex gap-1.5">
                    <button onClick={() => handleTest(m.id)} className="inline-flex items-center gap-1 rounded border border-borderline px-2 py-1">
                      <RefreshCw className="h-3 w-3" />
                      <span>{t("common.test")}</span>
                    </button>
                    <button onClick={() => handleDisconnect(m.id)} className="inline-flex items-center gap-1 rounded border border-danger/30 px-2 py-1 text-danger">
                      <Trash2 className="h-3 w-3" />
                      <span>{t("common.disconnect")}</span>
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
