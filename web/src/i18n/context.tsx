"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import arDict from "./ar.json";
import enDict from "./en.json";

export type Locale = "ar" | "en";
export type NumeralStyle = "western" | "arabic_indic";
export type CalendarType = "gregorian" | "hijri";

const DICTS = { ar: arDict, en: enDict } as const;

interface AppStateContextValue {
  locale: Locale;
  dir: "rtl" | "ltr";
  dark: boolean;
  numeralStyle: NumeralStyle;
  calendarType: CalendarType;
  setLocale: (loc: Locale) => void;
  toggleLocale: () => void;
  toggleDark: () => void;
  setNumeralStyle: (s: NumeralStyle) => void;
  setCalendarType: (c: CalendarType) => void;
  t: (path: string) => string;
  formatNum: (val: number | string) => string;
  formatDate: (iso: string | null | undefined) => string;
}

const AppContext = createContext<AppStateContextValue | null>(null);

function lookup(obj: Record<string, unknown>, path: string): string {
  const parts = path.split(".");
  let cur: unknown = obj;
  for (const p of parts) {
    if (cur && typeof cur === "object" && p in cur) {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return path;
    }
  }
  return typeof cur === "string" ? cur : path;
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("ar");
  const [dark, setDark] = useState(false);
  const [numeralStyle, setNumeralStyle] = useState<NumeralStyle>("western");
  const [calendarType, setCalendarType] = useState<CalendarType>("gregorian");

  useEffect(() => {
    const savedLoc = (localStorage.getItem("uas_locale") as Locale | null) || "ar";
    const savedDark = localStorage.getItem("uas_dark") === "1";
    const savedNum = (localStorage.getItem("uas_numerals") as NumeralStyle | null) || "western";
    const savedCal = (localStorage.getItem("uas_calendar") as CalendarType | null) || "gregorian";
    setLocaleState(savedLoc);
    setDark(savedDark);
    setNumeralStyle(savedNum);
    setCalendarType(savedCal);
  }, []);

  useEffect(() => {
    const dir = locale === "ar" ? "rtl" : "ltr";
    document.documentElement.lang = locale;
    document.documentElement.dir = dir;
    localStorage.setItem("uas_locale", locale);
  }, [locale]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("uas_dark", dark ? "1" : "0");
  }, [dark]);

  const setLocale = (loc: Locale) => setLocaleState(loc);
  const toggleLocale = () => setLocaleState((prev) => (prev === "ar" ? "en" : "ar"));
  const toggleDark = () => setDark((prev) => !prev);

  const t = (path: string) => lookup(DICTS[locale] as unknown as Record<string, unknown>, path);

  const formatNum = (val: number | string): string => {
    const raw = String(val);
    if (numeralStyle !== "arabic_indic" || locale !== "ar") return raw;
    const indic = ["٠", "١", "٢", "٣", "٤", "٥", "٦", "٧", "٨", "٩"];
    return raw.replace(/\d/g, (d) => indic[Number(d)]);
  };

  const formatDate = (iso: string | null | undefined): string => {
    if (!iso) return "—";
    try {
      const dt = new Date(iso);
      const locTag =
        locale === "ar"
          ? calendarType === "hijri"
            ? "ar-SA-u-ca-islamic"
            : "ar-EG"
          : "en-US";
      return new Intl.DateTimeFormat(locTag, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(dt);
    } catch {
      return iso;
    }
  };

  return (
    <AppContext.Provider
      value={{
        locale,
        dir: locale === "ar" ? "rtl" : "ltr",
        dark,
        numeralStyle,
        calendarType,
        setLocale,
        toggleLocale,
        toggleDark,
        setNumeralStyle: (s) => {
          setNumeralStyle(s);
          localStorage.setItem("uas_numerals", s);
        },
        setCalendarType: (c) => {
          setCalendarType(c);
          localStorage.setItem("uas_calendar", c);
        },
        t,
        formatNum,
        formatDate,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside AppProvider");
  return ctx;
}
