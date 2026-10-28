import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { storage } from "../lib/storage";
import de from "./de.json";
import en from "./en.json";
import es from "./es.json";
import fr from "./fr.json";
import it from "./it.json";
import nl from "./nl.json";
import ru from "./ru.json";
import zh from "./zh.json";

// UI strings: English is the default and the fallback; Archie answers in any language on his own. The other seven
// were translated once with a model from en.json and reviewed (task L-2d); keep the keys of en.json in all of them.
export const DICTIONARIES = { en, fr, es, de, it, nl, zh, ru } as const;
export type Lang = keyof typeof DICTIONARIES;
export type MessageKey = keyof typeof en;
export const LANGS = Object.keys(DICTIONARIES) as Lang[];

/** Each language in its own name, for the language menu. */
export const LANG_NAMES: Record<Lang, string> = {
  en: "English", fr: "Français", es: "Español", de: "Deutsch", it: "Italiano", nl: "Nederlands", zh: "中文", ru: "Русский",
};

const LANG_KEY = "statseat.lang";

export function isLang(v: string | undefined | null): v is Lang {
  return !!v && v in DICTIONARIES;
}

export function translate(lang: Lang, key: string, vars?: Record<string, string | number>): string {
  const dict = DICTIONARIES[lang] as Record<string, string>;
  let s = dict[key] ?? (en as Record<string, string>)[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v));
  return s;
}

interface I18n {
  lang: Lang;
  setLang: (l: Lang) => void;
  /** Accepts any string so that keys built from contract values (phases, sections, events) also work. */
  t: (key: MessageKey | (string & {}), vars?: Record<string, string | number>) => string;
  /** A server text (always English) translated by its id, e.g. `chart.momentum`; the server's text when no key exists. */
  tOr: (key: string, fallback: string) => string;
}

const Ctx = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const saved = storage.get(LANG_KEY);
  const [lang, setLangState] = useState<Lang>(isLang(saved) ? saved : "en");
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    storage.set(LANG_KEY, l);
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const t = useCallback<I18n["t"]>((key, vars) => translate(lang, key, vars), [lang]);
  const tOr = useCallback<I18n["tOr"]>((key, fallback) => {
    const s = translate(lang, key);
    return s === key ? fallback : s;
  }, [lang]);
  const value = useMemo(() => ({ lang, setLang, t, tOr }), [lang, setLang, t, tOr]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const v = useContext(Ctx);
  if (!v) throw new Error("useI18n outside I18nProvider");
  return v;
}
