import { useState, type FormEvent } from "react";
import { APP_NAME } from "../config";
import { api, ApiError } from "../api/client";
import type { Session } from "../api/types";
import { LanguageSwitch } from "./LanguageSwitch";
import { useI18n } from "../i18n";
import { errorText } from "./Status";

/**
 * The code in the link of a ticket's QR (`…?ticket=<code>`), if any. It only fills the field: the fan still taps
 * "Enter". The parameter is then removed from the address bar, so the code does not stay in the history.
 */
function ticketFromUrl(): string {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("ticket");
  if (!code) return "";
  url.searchParams.delete("ticket");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  return code.trim().toUpperCase();
}

/** Entry with the ticket code. The tier and the match come from the server, never from the app. */
export function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const { t } = useI18n();
  const [code, setCode] = useState(ticketFromUrl);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!code.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      onLogin(await api.login(code.trim()));
    } catch (err) {
      setError(errorText(t, err instanceof ApiError ? err : new ApiError(0, {})));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <div className="login-lang"><LanguageSwitch /></div>
      <img src="/icon-512.png" alt="" className="login-logo" />
      <h1>{APP_NAME}</h1>
      <p className="muted">{t("app.tagline")}</p>
      <form onSubmit={submit} className="login-form">
        <label htmlFor="code">{t("login.title")}</label>
        <input
          id="code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder={t("login.placeholder")} autoComplete="off" autoCapitalize="characters" spellCheck={false}
          inputMode="text" aria-describedby="code-hint"
        />
        <p id="code-hint" className="small muted">{t("login.hint")}</p>
        {error && <p className="bad" role="alert">{error}</p>}
        <button className="btn big" disabled={busy || !code.trim()}>{busy ? t("login.loading") : t("login.submit")}</button>
      </form>
    </main>
  );
}
