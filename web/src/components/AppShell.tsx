"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  Bell,
  Bot,
  CheckSquare,
  FileBarChart,
  Globe,
  KeyRound,
  LayoutDashboard,
  Link2,
  LogOut,
  Mail,
  Menu,
  Moon,
  OctagonAlert,
  Play,
  PlusCircle,
  Search,
  Settings,
  Sun,
  Upload,
  Users,
  X,
} from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface MeResponse {
  user: { id: string; email: string; role: string; locale: string };
  workspace: { id: string; name: string };
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { t, locale, toggleLocale, dark, toggleDark } = useApp();

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [killswitchActive, setKillswitchActive] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [authChecked, setAuthChecked] = useState(false);

  const isPublicPage = pathname === "/login" || pathname.startsWith("/vault/");

  useEffect(() => {
    if (isPublicPage) {
      setAuthChecked(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = await apiFetch<MeResponse>("/api/me");
        if (cancelled) return;
        setMe(data);
        setAuthChecked(true);
        const [ks, notes] = await Promise.all([
          apiFetch<{ active: boolean }>("/api/killswitch").catch(() => ({ active: false })),
          apiFetch<{ notifications: { read_at: string | null }[] }>("/api/notifications").catch(() => ({ notifications: [] })),
        ]);
        if (!cancelled) {
          setKillswitchActive(Boolean(ks.active));
          setUnreadCount(notes.notifications.filter((n) => !n.read_at).length);
        }
      } catch {
        if (!cancelled) {
          router.replace("/login");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pathname, isPublicPage, router]);

  if (isPublicPage) {
    return <main className="min-h-screen bg-canvas">{children}</main>;
  }

  if (!authChecked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas text-muted">
        {t("common.loading")}
      </div>
    );
  }

  const navItems = [
    { href: "/", label: t("nav.dashboard"), icon: LayoutDashboard },
    { href: "/agents", label: t("nav.agents"), icon: Bot },
    { href: "/tasks/new", label: t("nav.new_task"), icon: PlusCircle },
    { href: "/runs", label: t("nav.runs"), icon: Activity },
    { href: "/approvals", label: t("nav.approvals"), icon: CheckSquare },
    { href: "/backlinks/sites", label: t("nav.backlink_sites"), icon: Globe },
    { href: "/backlinks/accounts", label: t("nav.backlink_accounts"), icon: Users },
    { href: "/backlinks/links", label: t("nav.backlink_links"), icon: Link2 },
    { href: "/backlinks/settings", label: t("nav.backlink_identity"), icon: KeyRound },
    { href: "/backlinks/import", label: t("nav.backlink_import"), icon: Upload },
    { href: "/mailboxes", label: t("nav.mailboxes"), icon: Mail },
    { href: "/reports", label: t("nav.reports"), icon: FileBarChart },
    { href: "/settings", label: t("nav.settings"), icon: Settings },
  ];

  const handleKillswitch = async () => {
    const next = !killswitchActive;
    const res = await apiFetch<{ active: boolean }>("/api/killswitch", {
      method: "POST",
      body: JSON.stringify({ active: next, reason: next ? "Emergency stop from topbar" : "Resumed from topbar" }),
    });
    setKillswitchActive(res.active);
  };

  const handleLogout = async () => {
    await apiFetch("/api/auth/logout", { method: "POST" }).catch(() => null);
    localStorage.removeItem("uas_token");
    router.replace("/login");
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      router.push(`/backlinks/sites?q=${encodeURIComponent(searchQuery.trim())}`);
    }
  };

  return (
    <div className="flex min-h-screen bg-canvas text-ink">
      {/* Mobile backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar (starts on RIGHT in RTL, LEFT in LTR automatically via start-0 & border-e) */}
      <aside
        className={`fixed inset-y-0 start-0 z-40 flex flex-col border-e border-borderline bg-surface transition-all duration-200 lg:static ${
          sidebarOpen ? "translate-x-0" : "max-lg:hidden"
        } ${collapsed ? "w-20" : "w-64"}`}
      >
        <div className="flex h-16 items-center justify-between border-b border-borderline px-4">
          {!collapsed && (
            <div className="flex flex-col">
              <span className="text-sm font-bold text-primary">{t("app_name")}</span>
              <span className="text-[11px] text-muted">{me?.workspace.name || "Workspace"}</span>
            </div>
          )}
          <button
            type="button"
            onClick={() => {
              if (window.innerWidth < 1024) setSidebarOpen(false);
              else setCollapsed((c) => !c);
            }}
            className="touch-target flex items-center justify-center rounded-lg text-muted hover:bg-canvas"
            aria-label="Toggle sidebar"
          >
            {sidebarOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          {navItems.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(item.href + "/");
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setSidebarOpen(false)}
                className={`flex min-h-[44px] items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-primary text-white font-semibold"
                    : "text-ink hover:bg-canvas"
                }`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!collapsed && <span className="truncate">{item.label}</span>}
              </Link>
            );
          })}
        </nav>

        {!collapsed && (
          <div className="border-t border-borderline p-3 text-[11px] text-muted">
            {t("common.no_secrets_notice")}
          </div>
        )}
      </aside>

      {/* Main Content */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Topbar */}
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between gap-3 border-b border-borderline bg-surface px-4">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              className="touch-target flex items-center justify-center rounded-lg border border-borderline lg:hidden"
              aria-label="Open menu"
            >
              <Menu className="h-5 w-5" />
            </button>
            <form onSubmit={handleSearchSubmit} className="relative hidden sm:block">
              <Search className="pointer-events-none absolute top-1/2 start-3 h-4 w-4 -translate-y-1/2 text-muted" />
              <input
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t("topbar.search_placeholder")}
                className="h-10 w-64 rounded-lg border border-borderline bg-canvas ps-9 pe-3 text-sm outline-none focus:border-primary"
              />
            </form>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleKillswitch}
              className={`inline-flex min-h-[40px] items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                killswitchActive
                  ? "bg-danger text-white"
                  : "border border-danger/40 text-danger hover:bg-danger/10"
              }`}
            >
              {killswitchActive ? <Play className="h-3.5 w-3.5" /> : <OctagonAlert className="h-3.5 w-3.5" />}
              <span>{killswitchActive ? t("topbar.killswitch_resume") : t("topbar.killswitch_trigger")}</span>
            </button>

            <Link
              href="/approvals"
              className="touch-target relative flex items-center justify-center rounded-lg border border-borderline hover:bg-canvas"
              title={t("topbar.notifications")}
            >
              <Bell className="h-4 w-4" />
              {unreadCount > 0 && (
                <span className="absolute -top-1 end-0 rounded-full bg-danger px-1.5 text-[10px] font-bold text-white">
                  {unreadCount}
                </span>
              )}
            </Link>

            <button
              type="button"
              onClick={toggleLocale}
              className="touch-target rounded-lg border border-borderline px-2.5 text-xs font-semibold hover:bg-canvas"
            >
              {locale === "ar" ? "EN" : "عربي"}
            </button>

            <button
              type="button"
              onClick={toggleDark}
              className="touch-target flex items-center justify-center rounded-lg border border-borderline hover:bg-canvas"
              aria-label="Toggle theme"
            >
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>

            <button
              type="button"
              onClick={handleLogout}
              className="touch-target flex items-center justify-center rounded-lg border border-borderline text-muted hover:text-danger"
              title={t("topbar.logout")}
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </header>

        {killswitchActive && (
          <div className="border-b border-danger/30 bg-danger/10 px-4 py-2 text-xs font-semibold text-danger">
            {t("topbar.killswitch_active")}
          </div>
        )}

        <main className="flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
