import { isLang, LANG_NAMES, LANGS, useI18n } from "../i18n";
import { Icon } from "./Icon";

/**
 * Language menu: eight UI languages, each in its own name (Archie answers in any language anyway). `compact` shows
 * only the globe and the language code, for the top bar; the native menu still lists the full names.
 */
export function LanguageSwitch({ compact = false }: { compact?: boolean }) {
  const { lang, setLang } = useI18n();
  return (
    <label className={`lang-select ${compact ? "compact" : ""}`}>
      <Icon name="globe" size={18} />
      {compact && <span aria-hidden>{lang.toUpperCase()}</span>}
      <select value={lang} onChange={(e) => isLang(e.target.value) && setLang(e.target.value)} aria-label="Language">
        {LANGS.map((l) => <option key={l} value={l}>{LANG_NAMES[l]}</option>)}
      </select>
    </label>
  );
}
